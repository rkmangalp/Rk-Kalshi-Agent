"""Kalshi RSA-PSS request signing and local credential loading.

Public market reads stay unsigned. Authenticated portfolio GETs sign
``timestamp_ms + METHOD + path`` (path from the API root, no query string)
with RSA-PSS SHA-256 as documented by Kalshi.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

PROD_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
DEMO_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
# Also supported by Kalshi: api.elections.kalshi.com and demo-api.kalshi.co.
ENV_KEY_ID = "KALSHI_API_KEY_ID"
ENV_KEY_PATH = "KALSHI_PRIVATE_KEY_PATH"
ENV_KEY_PEM = "KALSHI_PRIVATE_KEY"
ENV_ENVIRONMENT = "KALSHI_ENVIRONMENT"
ENV_BASE_URL = "KALSHI_BASE_URL"
MISSING_ENV_MESSAGE = (
    "set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env "
    "(copy .env.example; never commit .env or the .key file)"
)


class AccountAuthError(ValueError):
    """Invalid credentials or key material — never includes PEM text."""


def normalize_environment(value: object, default: str = "prod") -> str:
    text = str(value or default).strip().lower()
    if text in {"prod", "production", "live"}:
        return "prod"
    if text in {"demo", "sandbox"}:
        return "demo"
    raise AccountAuthError("environment must be demo or prod")


def base_url_for_environment(environment: str, override: str | None = None) -> str:
    if override:
        return str(override).rstrip("/")
    env = normalize_environment(environment)
    return DEMO_BASE_URL if env == "demo" else PROD_BASE_URL


def signing_path(base_url: str, path: str) -> str:
    """Full URL path from the API root, query string stripped."""
    joined = urljoin(base_url.rstrip("/") + "/", str(path).lstrip("/"))
    parsed = urlparse(joined)
    return (parsed.path or "/").split("?", 1)[0]


def load_private_key(pem: bytes | str, password: bytes | None = None) -> RSAPrivateKey:
    raw = pem.encode("utf-8") if isinstance(pem, str) else pem
    try:
        key = serialization.load_pem_private_key(raw, password=password, backend=default_backend())
    except ValueError as exc:
        raise AccountAuthError("private key is not a valid PEM RSA key") from exc
    if not isinstance(key, RSAPrivateKey):
        raise AccountAuthError("private key must be RSA")
    return key


def load_private_key_file(path: str | Path, password: bytes | None = None) -> RSAPrivateKey:
    key_path = Path(path).expanduser()
    if not key_path.is_file():
        raise AccountAuthError(f"private key file not found: {key_path}")
    try:
        return load_private_key(key_path.read_bytes(), password=password)
    except AccountAuthError:
        raise
    except OSError as exc:
        raise AccountAuthError(f"could not read private key file: {key_path.name}") from exc


def sign_pss(private_key: RSAPrivateKey, timestamp_ms: str, method: str, path: str) -> str:
    path_without_query = str(path).split("?", 1)[0]
    message = f"{timestamp_ms}{method.upper()}{path_without_query}".encode("utf-8")
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def auth_headers(
    api_key_id: str,
    private_key: RSAPrivateKey,
    method: str,
    path: str,
    timestamp_ms: str | None = None,
) -> dict[str, str]:
    stamp = timestamp_ms if timestamp_ms is not None else str(int(time.time() * 1000))
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": stamp,
        "KALSHI-ACCESS-SIGNATURE": sign_pss(private_key, stamp, method, path),
    }


def mask_key_id(api_key_id: str) -> str:
    text = (api_key_id or "").strip()
    if len(text) <= 4:
        return "••••" if text else ""
    return f"…{text[-4:]}"


def parse_dotenv(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            out[key] = value
    return out


def load_dotenv_file(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    env_path = Path(path) if path is not None else Path(".env")
    parsed = parse_dotenv(env_path)
    for key, value in parsed.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


@dataclass(frozen=True)
class KalshiCredentials:
    api_key_id: str
    private_key: RSAPrivateKey
    environment: str
    base_url: str
    key_path: str = ""

    def public_payload(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "base_url": self.base_url,
            "api_key_id_suffix": mask_key_id(self.api_key_id),
            "key_source": "file" if self.key_path else "pasted",
            "key_path": self.key_path,
        }


INCOMPLETE_PEM_MESSAGE = "paste the full PEM including END line, or use a file path"


def pem_is_complete(pem: str | None) -> bool:
    """True when text has matching BEGIN/END private-key lines (not BEGIN-only)."""
    text = (pem or "").strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    begin = any(
        line.upper().startswith("-----BEGIN") and "PRIVATE KEY" in line.upper() for line in lines
    )
    end = any(line.upper().startswith("-----END") and "PRIVATE KEY" in line.upper() for line in lines)
    return begin and end


def credentials_from_parts(
    api_key_id: str,
    *,
    environment: str = "prod",
    private_key_path: str | Path | None = None,
    private_key_pem: str | None = None,
    base_url: str | None = None,
) -> KalshiCredentials:
    key_id = (api_key_id or "").strip()
    if not key_id:
        raise AccountAuthError("API Key ID is required")
    pem = (private_key_pem or "").strip()
    path = str(private_key_path or "").strip()
    path_exists = bool(path) and Path(path).expanduser().is_file()
    if path_exists:
        key = load_private_key_file(path)
        stored_path = path
    elif pem_is_complete(pem):
        key = load_private_key(pem)
        stored_path = ""
    elif path:
        key = load_private_key_file(path)
        stored_path = path
    elif pem:
        raise AccountAuthError(INCOMPLETE_PEM_MESSAGE)
    else:
        raise AccountAuthError("provide a private key file path or paste the PEM key")
    env = normalize_environment(environment)
    return KalshiCredentials(
        api_key_id=key_id,
        private_key=key,
        environment=env,
        base_url=base_url_for_environment(env, base_url),
        key_path=stored_path,
    )


def credentials_from_env(environ: dict[str, str] | None = None) -> KalshiCredentials | None:
    env = environ if environ is not None else os.environ
    key_id = (env.get(ENV_KEY_ID) or "").strip()
    path = (env.get(ENV_KEY_PATH) or "").strip()
    pem = (env.get(ENV_KEY_PEM) or "").strip()
    if not key_id or not (path or pem):
        return None
    return credentials_from_parts(
        key_id,
        environment=env.get(ENV_ENVIRONMENT) or "prod",
        private_key_path=path or None,
        private_key_pem=pem or None,
        base_url=(env.get(ENV_BASE_URL) or "").strip() or None,
    )
