"""Read-only Kalshi account connect + portfolio views.

Connecting stores API credentials locally and fetches balance / positions /
fills / orders. It never enables live order placement.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from cryptography.hazmat.primitives import serialization

from rk_kalshi.auth import (
    ENV_BASE_URL,
    ENV_ENVIRONMENT,
    ENV_KEY_ID,
    ENV_KEY_PATH,
    MISSING_ENV_MESSAGE,
    AccountAuthError,
    KalshiCredentials,
    auth_headers,
    credentials_from_env,
    credentials_from_parts,
    load_dotenv_file,
    mask_key_id,
    signing_path,
)
from rk_kalshi.models import parse_count, parse_dollars
from rk_kalshi.state import local_now_iso

STORE_FILENAME = "kalshi_account.json"
KEY_FILENAME = "kalshi_private.key"
DEFAULT_LIMIT = 100
LIVE_TRADING_MESSAGE = "Live trading is coming soon — connect is read-only and does not place orders."


class AccountApiError(RuntimeError):
    """Authenticated Kalshi REST error. Message never includes secrets."""


class AccountNotConnectedError(RuntimeError):
    """Raised when a portfolio call is made without credentials."""


@dataclass
class AccountSnapshot:
    status: str = "disconnected"
    environment: str | None = None
    base_url: str | None = None
    api_key_id_suffix: str = ""
    key_path: str = ""
    message: str = ""
    last_ok_at: str | None = None
    last_error: str | None = None
    latency_ms: float | None = None
    read_only: bool = True
    live_trading_enabled: bool = False
    live_trading_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "environment": self.environment,
            "base_url": self.base_url,
            "api_key_id_suffix": self.api_key_id_suffix,
            "key_path": self.key_path,
            "message": self.message,
            "last_ok_at": self.last_ok_at,
            "last_error": self.last_error,
            "latency_ms": self.latency_ms,
            "read_only": True,
            "live_trading_enabled": False,
            "live_trading_available": False,
            "live_trading_label": "coming soon",
            "banner": _account_banner(self.status, self.environment),
            "paper_mode": True,
        }


def _account_banner(status: str, environment: str | None) -> str:
    if status == "connected":
        env = "DEMO" if environment == "demo" else "PRODUCTION"
        return f"LIVE ACCOUNT VIEW ({env}) — read-only Kalshi portfolio, not the paper desk"
    if status == "error":
        return "KALSHI ACCOUNT ERROR — paper desk is unchanged; live orders stay disabled"
    return "KALSHI ACCOUNT DISCONNECTED — paper desk only"


def _cents_to_dollars(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


def _http_error_message(exc: httpx.HTTPStatusError) -> str:
    detail = ""
    try:
        payload = exc.response.json()
        if isinstance(payload, dict):
            detail = str(payload.get("message") or payload.get("code") or "")
    except ValueError:
        detail = ""
    status = exc.response.status_code
    if status in {401, 403}:
        return "Kalshi rejected the signed request (check API Key ID, RSA key, and demo vs prod)"
    if detail:
        return f"Kalshi API {status}: {detail}"
    return f"Kalshi API {status}"


class KalshiSignedClient:
    """Authenticated GET client. POST/order methods stay disabled."""

    def __init__(
        self,
        credentials: KalshiCredentials,
        timeout_s: float = 15.0,
        client: httpx.Client | None = None,
    ):
        self.credentials = credentials
        self._owns_client = client is None
        self._http = client or httpx.Client(
            base_url=credentials.base_url.rstrip("/"),
            timeout=timeout_s,
            headers={"User-Agent": "rk-kalshi-agent/0.1 (+account-readonly)"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "KalshiSignedClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], float]:
        sign_path = signing_path(self.credentials.base_url, path)
        headers = auth_headers(
            self.credentials.api_key_id,
            self.credentials.private_key,
            "GET",
            sign_path,
        )
        started = time.perf_counter()
        response = self._http.get(path, params=params, headers=headers)
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AccountApiError(_http_error_message(exc)) from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise AccountApiError("Kalshi returned a non-object JSON body")
        return payload, latency_ms

    def post(self, *args: object, **kwargs: object) -> None:
        raise AccountApiError(LIVE_TRADING_MESSAGE)

    def create_order(self, *args: object, **kwargs: object) -> None:
        raise AccountApiError(LIVE_TRADING_MESSAGE)


def parse_balance(payload: dict[str, Any]) -> dict[str, Any]:
    dollars = parse_dollars(payload.get("balance_dollars"), default=0.0)
    if not dollars:
        cents = _cents_to_dollars(payload.get("balance"))
        if cents is not None:
            dollars = cents
    portfolio = _cents_to_dollars(payload.get("portfolio_value"))
    if portfolio is None:
        portfolio = parse_dollars(payload.get("portfolio_value_dollars"), default=0.0)
    return {
        "balance": dollars,
        "portfolio_value": portfolio,
        "updated_ts": payload.get("updated_ts"),
    }


def parse_position(row: dict[str, Any]) -> dict[str, Any] | None:
    contracts = parse_count(row.get("position_fp"), default=0.0)
    if contracts == 0.0:
        contracts = parse_count(row.get("position"), default=0.0)
    if contracts == 0.0:
        return None
    return {
        "ticker": str(row.get("ticker") or ""),
        "contracts": contracts,
        "side": "yes" if contracts > 0 else "no",
        "exposure": parse_dollars(row.get("market_exposure_dollars")),
        "realized_pnl": parse_dollars(row.get("realized_pnl_dollars")),
        "fees_paid": parse_dollars(row.get("fees_paid_dollars")),
        "total_traded": parse_dollars(row.get("total_traded_dollars")),
        "last_updated": row.get("last_updated_ts") or "",
    }


def parse_fill(row: dict[str, Any]) -> dict[str, Any]:
    ticker = str(row.get("ticker") or row.get("market_ticker") or "")
    return {
        "fill_id": str(row.get("fill_id") or row.get("trade_id") or ""),
        "order_id": str(row.get("order_id") or ""),
        "ticker": ticker,
        "outcome_side": str(row.get("outcome_side") or row.get("side") or ""),
        "book_side": str(row.get("book_side") or ""),
        "action": str(row.get("action") or ""),
        "count": parse_count(row.get("count_fp") or row.get("count")),
        "yes_price": parse_dollars(row.get("yes_price_dollars") or row.get("yes_price")),
        "fee": parse_dollars(row.get("fee_cost") or row.get("fee")),
        "is_taker": bool(row.get("is_taker")),
        "created_time": row.get("created_time") or "",
        "ts": row.get("ts"),
    }


def parse_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": str(row.get("order_id") or ""),
        "ticker": str(row.get("ticker") or ""),
        "status": str(row.get("status") or ""),
        "type": str(row.get("type") or ""),
        "outcome_side": str(row.get("outcome_side") or row.get("side") or ""),
        "book_side": str(row.get("book_side") or ""),
        "yes_price": parse_dollars(row.get("yes_price_dollars") or row.get("yes_price")),
        "fill_count": parse_count(row.get("fill_count_fp") or row.get("fill_count")),
        "remaining": parse_count(row.get("remaining_count_fp") or row.get("remaining_count")),
        "initial": parse_count(row.get("initial_count_fp") or row.get("initial_count")),
        "created_time": row.get("created_time") or "",
        "last_update_time": row.get("last_update_time") or "",
    }


@dataclass
class AccountStore:
    path: Path
    key_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.key_path = self.path.parent / KEY_FILENAME

    def load_record(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def save(self, credentials: KalshiCredentials, persist_pem: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key_path = credentials.key_path
        if persist_pem:
            pem = credentials.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            self.key_path.write_bytes(pem)
            _restrict_file(self.key_path)
            key_path = str(self.key_path)
        payload = {
            "environment": credentials.environment,
            "api_key_id": credentials.api_key_id,
            "private_key_path": key_path,
            "base_url": credentials.base_url,
            "updated_at": local_now_iso(),
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _restrict_file(self.path)

    def clear(self) -> None:
        for path in (self.path, self.key_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def credentials(self) -> KalshiCredentials | None:
        raw = self.load_record()
        if not raw:
            return None
        key_id = str(raw.get("api_key_id") or "").strip()
        key_path = str(raw.get("private_key_path") or "").strip()
        if not key_id or not key_path:
            return None
        return credentials_from_parts(
            key_id,
            environment=str(raw.get("environment") or "prod"),
            private_key_path=key_path,
            base_url=str(raw.get("base_url") or "") or None,
        )


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return


def default_store_path(state_dir: Path | None = None) -> Path:
    root = Path(state_dir) if state_dir is not None else Path("data")
    return root / STORE_FILENAME


class AccountService:
    """In-process Kalshi account connection. Thread-safe, read-only."""

    def __init__(
        self,
        store: AccountStore | None = None,
        store_path: Path | None = None,
        signed_client: KalshiSignedClient | None = None,
        timeout_s: float = 15.0,
        load_env: bool = True,
    ):
        self.store = store or AccountStore(store_path or default_store_path())
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._client = signed_client
        self._owns_client = signed_client is None
        self._snapshot = AccountSnapshot()
        if load_env:
            load_dotenv_file()
        if signed_client is not None:
            creds = signed_client.credentials
            self._snapshot = AccountSnapshot(
                status="connected",
                environment=creds.environment,
                base_url=creds.base_url,
                api_key_id_suffix=mask_key_id(creds.api_key_id),
                key_path=creds.key_path,
                message="Connected (injected client)",
                last_ok_at=local_now_iso(),
            )
        else:
            self._hydrate()

    def close(self) -> None:
        with self._lock:
            if self._owns_client and self._client is not None:
                self._client.close()
                self._client = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot.as_dict()

    def connect_from_env(self, environ: dict[str, str] | None = None) -> dict[str, Any]:
        """Connect using KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PATH from .env."""
        if environ is None:
            load_dotenv_file(override=True)
            environ = dict(os.environ)
        key_id = (environ.get(ENV_KEY_ID) or "").strip()
        path = (environ.get(ENV_KEY_PATH) or "").strip()
        if not key_id or not path:
            with self._lock:
                self._mark_error(MISSING_ENV_MESSAGE)
            raise AccountAuthError(MISSING_ENV_MESSAGE)
        try:
            creds = credentials_from_parts(
                key_id,
                environment=environ.get(ENV_ENVIRONMENT) or "prod",
                private_key_path=path,
                base_url=(environ.get(ENV_BASE_URL) or "").strip() or None,
            )
        except AccountAuthError as exc:
            with self._lock:
                self._mark_error(str(exc), api_key_id_suffix=mask_key_id(key_id))
            raise
        return self._activate(creds)

    def _activate(self, creds: KalshiCredentials) -> dict[str, Any]:
        client = KalshiSignedClient(creds, timeout_s=self.timeout_s)
        try:
            payload, latency_ms = client.get_json("/portfolio/balance")
            parse_balance(payload)
        except Exception as exc:
            client.close()
            with self._lock:
                self._mark_error(
                    str(exc),
                    environment=creds.environment,
                    api_key_id_suffix=mask_key_id(creds.api_key_id),
                )
            raise
        self.store.clear()
        with self._lock:
            if self._owns_client and self._client is not None:
                self._client.close()
            self._client = client
            self._owns_client = True
            self._mark_ok(creds, latency_ms, "Connected — read-only portfolio view (.env)")
        return self.snapshot()

    def disconnect(self) -> dict[str, Any]:
        self.store.clear()
        with self._lock:
            if self._owns_client and self._client is not None:
                self._client.close()
            self._client = None
            self._snapshot = AccountSnapshot(message="Disconnected — paper desk unchanged")
        return self.snapshot()

    def portfolio(self) -> dict[str, Any]:
        client = self._ensure_client()
        errors: dict[str, str] = {}
        balance: dict[str, Any] = {}
        positions: list[dict[str, Any]] = []
        fills: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        max_latency = 0.0

        try:
            raw, latency_ms = client.get_json("/portfolio/balance")
            max_latency = max(max_latency, latency_ms)
            balance = parse_balance(raw)
        except (AccountApiError, httpx.HTTPError) as exc:
            errors["balance"] = str(exc)

        try:
            raw, latency_ms = client.get_json(
                "/portfolio/positions",
                params={"limit": DEFAULT_LIMIT, "count_filter": "position"},
            )
            max_latency = max(max_latency, latency_ms)
            for row in raw.get("market_positions") or []:
                parsed = parse_position(row) if isinstance(row, dict) else None
                if parsed:
                    positions.append(parsed)
        except (AccountApiError, httpx.HTTPError) as exc:
            errors["positions"] = str(exc)

        try:
            raw, latency_ms = client.get_json("/portfolio/fills", params={"limit": DEFAULT_LIMIT})
            max_latency = max(max_latency, latency_ms)
            fills = [parse_fill(row) for row in (raw.get("fills") or []) if isinstance(row, dict)]
        except (AccountApiError, httpx.HTTPError) as exc:
            errors["fills"] = str(exc)

        try:
            raw, latency_ms = client.get_json("/portfolio/orders", params={"limit": DEFAULT_LIMIT})
            max_latency = max(max_latency, latency_ms)
            orders = [parse_order(row) for row in (raw.get("orders") or []) if isinstance(row, dict)]
        except (AccountApiError, httpx.HTTPError) as exc:
            errors["orders"] = str(exc)

        creds = client.credentials
        with self._lock:
            if errors.get("balance") and not balance:
                self._mark_error(str(errors["balance"]))
            else:
                self._mark_ok(creds, max_latency, "Connected — read-only portfolio view")
            snapshot = self._snapshot.as_dict()

        return {
            "account": snapshot,
            "balance": balance.get("balance"),
            "portfolio_value": balance.get("portfolio_value"),
            "updated_ts": balance.get("updated_ts"),
            "positions": positions,
            "fills": fills,
            "orders": orders,
            "counts": {
                "positions": len(positions),
                "fills": len(fills),
                "orders": len(orders),
            },
            "errors": errors,
            "latency_ms": round(max_latency, 3),
            "read_only": True,
            "live_enabled": False,
            "paper_mode": True,
            "view": "live_account",
        }

    def _hydrate(self) -> None:
        try:
            creds = credentials_from_env()
        except AccountAuthError as exc:
            self._snapshot = AccountSnapshot(status="error", message=str(exc), last_error=str(exc))
            return
        if creds is None:
            self._snapshot = AccountSnapshot(message=MISSING_ENV_MESSAGE)
            return
        self._snapshot = AccountSnapshot(
            status="disconnected",
            environment=creds.environment,
            base_url=creds.base_url,
            api_key_id_suffix=mask_key_id(creds.api_key_id),
            key_path=creds.key_path,
            message="Found .env keys — click Connect (read-only; live orders stay off)",
        )

    def _ensure_client(self) -> KalshiSignedClient:
        with self._lock:
            if self._client is not None:
                return self._client
        raise AccountNotConnectedError(
            "Kalshi account is not connected. Click Connect after setting "
            "KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env."
        )

    def _mark_ok(self, creds: KalshiCredentials, latency_ms: float, message: str) -> None:
        self._snapshot = AccountSnapshot(
            status="connected",
            environment=creds.environment,
            base_url=creds.base_url,
            api_key_id_suffix=mask_key_id(creds.api_key_id),
            key_path=creds.key_path,
            message=message,
            last_ok_at=local_now_iso(),
            latency_ms=round(float(latency_ms), 3),
        )

    def _mark_error(
        self,
        message: str,
        *,
        environment: str | None = None,
        api_key_id_suffix: str | None = None,
    ) -> None:
        current = self._snapshot
        self._snapshot = AccountSnapshot(
            status="error",
            environment=environment or current.environment,
            base_url=current.base_url,
            api_key_id_suffix=api_key_id_suffix if api_key_id_suffix is not None else current.api_key_id_suffix,
            key_path=current.key_path,
            message=message,
            last_ok_at=current.last_ok_at,
            last_error=message,
            latency_ms=current.latency_ms,
        )


def format_account_cli(portfolio: dict[str, Any] | None, status: dict[str, Any]) -> str:
    lines = [
        f"status: {status.get('status')}",
        f"environment: {status.get('environment') or '—'}",
        f"key: {status.get('api_key_id_suffix') or '—'}",
        f"read_only: true",
        f"live_trading: disabled ({status.get('live_trading_label')})",
        f"note: {status.get('message') or ''}",
    ]
    if not portfolio:
        return "\n".join(lines)
    lines.append(f"balance: ${float(portfolio.get('balance') or 0):.2f}")
    value = portfolio.get("portfolio_value")
    if value is not None:
        lines.append(f"portfolio_value: ${float(value):.2f}")
    lines.append(f"open_positions: {portfolio.get('counts', {}).get('positions', 0)}")
    for row in portfolio.get("positions") or []:
        lines.append(
            f"  {row.get('ticker')} {row.get('side')} {row.get('contracts')} "
            f"exposure={row.get('exposure')}"
        )
    lines.append(f"recent_fills: {portfolio.get('counts', {}).get('fills', 0)}")
    for row in (portfolio.get("fills") or [])[:10]:
        lines.append(
            f"  {row.get('created_time')} {row.get('ticker')} "
            f"{row.get('book_side') or row.get('outcome_side')} "
            f"{row.get('count')} @ {row.get('yes_price')}"
        )
    lines.append(f"orders: {portfolio.get('counts', {}).get('orders', 0)}")
    for row in (portfolio.get("orders") or [])[:10]:
        lines.append(
            f"  {row.get('status')} {row.get('ticker')} remain={row.get('remaining')} "
            f"@ {row.get('yes_price')}"
        )
    return "\n".join(lines)
