"""Signed Kalshi account client — mocked HTTP, no real keys."""

from __future__ import annotations

import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from rk_kalshi.account import (
    AccountApiError,
    AccountService,
    KalshiSignedClient,
    LIVE_TRADING_MESSAGE,
    format_account_cli,
    parse_balance,
    parse_fill,
    parse_order,
    parse_position,
)
from rk_kalshi.auth import (
    DEMO_BASE_URL,
    INCOMPLETE_PEM_MESSAGE,
    AccountAuthError,
    KalshiCredentials,
    auth_headers,
    credentials_from_env,
    credentials_from_parts,
    mask_key_id,
    pem_is_complete,
    sign_pss,
    signing_path,
)


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


def _creds(key=None, environment: str = "demo") -> KalshiCredentials:
    key = key or _rsa_key()
    return KalshiCredentials(
        api_key_id="a952bcbe-ec3b-4b5b-b8f9-11dae589608c",
        private_key=key,
        environment=environment,
        base_url=DEMO_BASE_URL,
        key_path="",
    )


def _verify(key, timestamp: str, method: str, path: str, signature_b64: str) -> None:
    message = f"{timestamp}{method.upper()}{path}".encode("utf-8")
    key.public_key().verify(
        base64.b64decode(signature_b64),
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


class SigningTests(unittest.TestCase):
    def test_signing_path_strips_query_and_keeps_trade_api_prefix(self):
        path = signing_path(DEMO_BASE_URL, "/portfolio/orders?limit=5")
        self.assertEqual(path, "/trade-api/v2/portfolio/orders")
        self.assertEqual(
            signing_path("https://demo-api.kalshi.co/trade-api/v2", "/portfolio/balance"),
            "/trade-api/v2/portfolio/balance",
        )

    def test_rsa_pss_signature_roundtrip(self):
        key = _rsa_key()
        path = "/trade-api/v2/portfolio/balance"
        headers = auth_headers("key-id", key, "GET", path, timestamp_ms="1703123456789")
        self.assertEqual(headers["KALSHI-ACCESS-KEY"], "key-id")
        self.assertEqual(headers["KALSHI-ACCESS-TIMESTAMP"], "1703123456789")
        _verify(key, "1703123456789", "GET", path, headers["KALSHI-ACCESS-SIGNATURE"])
        with_query = sign_pss(key, "1703123456789", "GET", path + "?limit=5")
        _verify(key, "1703123456789", "GET", path, with_query)

    def test_mask_key_id_never_echoes_full_secret(self):
        self.assertEqual(mask_key_id("a952bcbe-ec3b-4b5b-b8f9-11dae589608c"), "…608c")

    def test_credentials_from_parts_and_env(self):
        key = _rsa_key()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "kalshi.key"
            path.write_text(_pem(key), encoding="utf-8")
            creds = credentials_from_parts(
                "abc-1234",
                environment="demo",
                private_key_path=str(path),
            )
            self.assertEqual(creds.environment, "demo")
            self.assertEqual(creds.base_url, DEMO_BASE_URL)
            env_creds = credentials_from_env(
                {
                    "KALSHI_API_KEY_ID": "abc-1234",
                    "KALSHI_PRIVATE_KEY_PATH": str(path),
                    "KALSHI_ENVIRONMENT": "prod",
                }
            )
            self.assertIsNotNone(env_creds)
            self.assertEqual(env_creds.environment, "prod")
        self.assertIsNone(credentials_from_env({}))
        with self.assertRaises(AccountAuthError):
            credentials_from_parts("", private_key_pem=_pem(key))

    def test_existing_key_file_wins_over_begin_only_pem(self):
        key = _rsa_key()
        stub = "-----BEGIN RSA PRIVATE KEY-----"
        self.assertFalse(pem_is_complete(stub))
        self.assertTrue(pem_is_complete(_pem(key)))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "kalshi.key"
            path.write_text(_pem(key), encoding="utf-8")
            creds = credentials_from_parts(
                "abc-1234",
                environment="demo",
                private_key_path=str(path),
                private_key_pem=stub,
            )
            self.assertEqual(creds.key_path, str(path))
            with self.assertRaises(AccountAuthError) as ctx:
                credentials_from_parts("abc-1234", private_key_pem=stub)
            self.assertEqual(str(ctx.exception), INCOMPLETE_PEM_MESSAGE)
            missing = Path(tmp) / "missing.key"
            with self.assertRaises(AccountAuthError) as missing_ctx:
                credentials_from_parts(
                    "abc-1234",
                    private_key_path=str(missing),
                    private_key_pem=stub,
                )
            self.assertIn("not found", str(missing_ctx.exception))
            self.assertNotIn("provide a private key file path or paste", str(missing_ctx.exception))


class ParserTests(unittest.TestCase):
    def test_balance_prefers_dollars_and_converts_cents(self):
        parsed = parse_balance(
            {"balance": 12345, "balance_dollars": "123.4500", "portfolio_value": 20000}
        )
        self.assertAlmostEqual(parsed["balance"], 123.45)
        self.assertAlmostEqual(parsed["portfolio_value"], 200.0)
        cents_only = parse_balance({"balance": 500})
        self.assertAlmostEqual(cents_only["balance"], 5.0)

    def test_position_skips_flat_and_keeps_open(self):
        self.assertIsNone(parse_position({"ticker": "X", "position_fp": "0.00"}))
        row = parse_position(
            {
                "ticker": "KXATPMATCH-1",
                "position_fp": "3.00",
                "market_exposure_dollars": "1.50",
                "realized_pnl_dollars": "0.10",
                "fees_paid_dollars": "0.02",
                "total_traded_dollars": "1.60",
            }
        )
        self.assertEqual(row["side"], "yes")
        self.assertAlmostEqual(row["contracts"], 3.0)
        no_side = parse_position({"ticker": "Y", "position_fp": "-2.00"})
        self.assertEqual(no_side["side"], "no")

    def test_fill_and_order_normalize_legacy_fields(self):
        fill = parse_fill(
            {
                "trade_id": "t1",
                "market_ticker": "KX-1",
                "side": "yes",
                "book_side": "bid",
                "count_fp": "1.00",
                "yes_price_dollars": "0.4200",
                "fee_cost": "0.01",
                "is_taker": True,
                "created_time": "2026-09-07T00:00:00Z",
            }
        )
        self.assertEqual(fill["ticker"], "KX-1")
        self.assertEqual(fill["fill_id"], "t1")
        self.assertAlmostEqual(fill["yes_price"], 0.42)
        order = parse_order(
            {
                "order_id": "o1",
                "ticker": "KX-1",
                "status": "resting",
                "book_side": "ask",
                "remaining_count_fp": "2.00",
                "fill_count_fp": "1.00",
                "yes_price_dollars": "0.5500",
            }
        )
        self.assertEqual(order["status"], "resting")
        self.assertAlmostEqual(order["remaining"], 2.0)


class FakeSignedClient:
    def __init__(self, credentials, timeout_s: float = 15.0, client=None, orders_enabled: bool = False):
        self.credentials = credentials
        self.closed = False
        self.calls: list[str] = []
        self.created_orders: list[dict] = []
        self.orders_enabled = bool(orders_enabled)

    def close(self) -> None:
        self.closed = True

    def get_json(self, path: str, params=None):
        self.calls.append(path)
        if path == "/portfolio/balance":
            return {
                "balance": 10100,
                "balance_dollars": "101.00",
                "portfolio_value": 15000,
                "updated_ts": 1,
            }, 4.0
        if path == "/portfolio/positions":
            return {
                "market_positions": [
                    {"ticker": "KX-OPEN", "position_fp": "2.00", "market_exposure_dollars": "1.10"},
                    {"ticker": "KX-FLAT", "position_fp": "0.00"},
                ],
                "event_positions": [],
            }, 5.0
        if path == "/portfolio/fills":
            return {
                "fills": [
                    {
                        "fill_id": "f1",
                        "ticker": "KX-OPEN",
                        "book_side": "bid",
                        "outcome_side": "yes",
                        "count_fp": "1.00",
                        "yes_price_dollars": "0.5500",
                        "fee_cost": "0.01",
                        "created_time": "2026-09-07T01:00:00Z",
                    }
                ],
                "cursor": "",
            }, 6.0
        if path == "/portfolio/orders":
            return {
                "orders": [
                    {
                        "order_id": "o1",
                        "ticker": "KX-OPEN",
                        "status": "resting",
                        "book_side": "bid",
                        "remaining_count_fp": "1.00",
                        "fill_count_fp": "0.00",
                        "yes_price_dollars": "0.5400",
                        "created_time": "2026-09-07T01:01:00Z",
                    }
                ],
                "cursor": "",
            }, 3.0
        raise AssertionError(path)

    def post(self, *args, **kwargs):
        if not self.orders_enabled:
            raise AccountApiError(LIVE_TRADING_MESSAGE)
        return {}, 1.0

    def create_order(self, **kwargs):
        if not self.orders_enabled:
            raise AccountApiError(LIVE_TRADING_MESSAGE)
        self.created_orders.append(kwargs)
        contracts = kwargs.get("contracts", 1)
        price = kwargs.get("price", 0.5)
        return {
            "order_id": "live-1",
            "fill_count": f"{float(contracts):.2f}",
            "remaining_count": "0.00",
            "average_fill_price": f"{float(price):.4f}",
            "average_fee_paid": "0.0100",
            "ts_ms": 1,
        }, 5.0

    def cancel_order(self, order_id, ticker=None):
        if not self.orders_enabled:
            raise AccountApiError(LIVE_TRADING_MESSAGE)
        return {"order_id": order_id, "reduced_by": "0.00", "ts_ms": 1}, 1.0


class SignedClientHttpTests(unittest.TestCase):
    def test_signed_get_sends_headers_and_rejects_post(self):
        key = _rsa_key()
        creds = _creds(key)

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["KALSHI-ACCESS-KEY"], creds.api_key_id)
            self.assertTrue(request.headers["KALSHI-ACCESS-TIMESTAMP"])
            path = "/trade-api/v2/portfolio/balance"
            _verify(
                key,
                request.headers["KALSHI-ACCESS-TIMESTAMP"],
                "GET",
                path,
                request.headers["KALSHI-ACCESS-SIGNATURE"],
            )
            return httpx.Response(
                200,
                json={"balance_dollars": "10.00", "balance": 1000, "portfolio_value": 1000},
            )

        http = httpx.Client(transport=httpx.MockTransport(handler), base_url=DEMO_BASE_URL)
        client = KalshiSignedClient(creds, client=http)
        payload, latency_ms = client.get_json("/portfolio/balance")
        self.assertAlmostEqual(parse_balance(payload)["balance"], 10.0)
        self.assertGreaterEqual(latency_ms, 0.0)
        with self.assertRaises(AccountApiError) as ctx:
            client.post("/portfolio/events/orders", json={})
        self.assertIn("Live order placement is off", str(ctx.exception))
        with self.assertRaises(AccountApiError):
            client.create_order(ticker="T", side="buy", contracts=1, price=0.5)

    def test_signed_create_order_posts_v2_path(self):
        key = _rsa_key()
        creds = _creds(key)
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = request.content
            path = "/trade-api/v2/portfolio/events/orders"
            _verify(
                key,
                request.headers["KALSHI-ACCESS-TIMESTAMP"],
                "POST",
                path,
                request.headers["KALSHI-ACCESS-SIGNATURE"],
            )
            return httpx.Response(
                201,
                json={
                    "order_id": "ord-1",
                    "fill_count": "1.00",
                    "remaining_count": "0.00",
                    "average_fill_price": "0.5600",
                    "ts_ms": 1,
                },
            )

        http = httpx.Client(transport=httpx.MockTransport(handler), base_url=DEMO_BASE_URL)
        client = KalshiSignedClient(creds, client=http, orders_enabled=True)
        payload, latency_ms = client.create_order(
            ticker="HIGHNY-24JAN01-T60",
            side="buy",
            contracts=1,
            price=0.56,
        )
        self.assertEqual(payload["order_id"], "ord-1")
        self.assertGreaterEqual(latency_ms, 0.0)
        self.assertEqual(seen["method"], "POST")
        self.assertTrue(seen["url"].endswith("/portfolio/events/orders"))
        self.assertIn("bid", seen["body"].decode("utf-8"))

    def test_unauthorized_is_sanitized(self):
        creds = _creds()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "nope", "private_key": "SECRET"})

        http = httpx.Client(transport=httpx.MockTransport(handler), base_url=DEMO_BASE_URL)
        client = KalshiSignedClient(creds, client=http)
        with self.assertRaises(AccountApiError) as ctx:
            client.get_json("/portfolio/balance")
        self.assertIn("rejected the signed request", str(ctx.exception))
        self.assertNotIn("SECRET", str(ctx.exception))


class AccountServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.key = _rsa_key()
        self.key_path = self.root / "user.key"
        self.key_path.write_text(_pem(self.key), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_connect_from_local_loads_portfolio_without_writing_store(self):
        store_path = self.root / "kalshi_account.json"
        env = {
            "KALSHI_API_KEY_ID": "a952bcbe-ec3b-4b5b-b8f9-11dae589608c",
            "KALSHI_PRIVATE_KEY_PATH": str(self.key_path),
            "KALSHI_ENVIRONMENT": "demo",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("rk_kalshi.account.KalshiSignedClient", FakeSignedClient):
                service = AccountService(store_path=store_path, load_env=False)
                status = service.connect_from_local()
            self.assertEqual(status["status"], "connected")
            self.assertEqual(status["environment"], "demo")
            self.assertTrue(status["read_only"])
            self.assertFalse(status["live_trading_enabled"])
            self.assertIn("LIVE ACCOUNT VIEW", status["banner"])
            self.assertIn("DEMO", status["banner"])
            if store_path.exists():
                raw = store_path.read_text(encoding="utf-8")
                self.assertNotIn("BEGIN", raw)
                self.assertNotIn("PRIVATE KEY", raw)
            book = service.portfolio()
            self.assertAlmostEqual(book["balance"], 101.0)
            self.assertEqual(len(book["positions"]), 1)
            self.assertEqual(book["positions"][0]["ticker"], "KX-OPEN")
            self.assertEqual(book["counts"]["fills"], 1)
            self.assertEqual(book["orders"][0]["status"], "resting")
            self.assertFalse(book["live_enabled"])
            text = format_account_cli(book, book["account"])
            self.assertIn("balance: $101.00", text)
            disconnected = service.disconnect()
            self.assertEqual(disconnected["status"], "disconnected")

    def test_connect_from_local_requires_key_id_and_path(self):
        from rk_kalshi.account import MISSING_ENV_MESSAGE

        service = AccountService(store_path=self.root / "kalshi_account.json", load_env=False)
        with patch("rk_kalshi.account.credentials_from_env", return_value=None):
            with self.assertRaises(AccountAuthError) as ctx:
                service.connect_from_local()
        self.assertEqual(str(ctx.exception), MISSING_ENV_MESSAGE)
        self.assertEqual(service.snapshot()["status"], "error")

    def test_connect_does_not_persist_on_auth_failure(self):
        store_path = self.root / "kalshi_account.json"

        class Boom(FakeSignedClient):
            def get_json(self, path, params=None):
                raise AccountApiError("Kalshi rejected the signed request")

        env = {
            "KALSHI_API_KEY_ID": "abcd1234",
            "KALSHI_PRIVATE_KEY_PATH": str(self.key_path),
            "KALSHI_ENVIRONMENT": "demo",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("rk_kalshi.account.KalshiSignedClient", Boom):
                service = AccountService(store_path=store_path, load_env=False)
                with self.assertRaises(AccountApiError):
                    service.connect_from_local()
                snap = service.snapshot()
                self.assertEqual(snap["status"], "error")
                self.assertEqual(snap["environment"], "demo")
                self.assertIn("rejected", snap["message"])
        self.assertFalse(store_path.exists())

        missing = AccountService(store_path=self.root / "other.json", load_env=False)
        with patch.dict(
            "os.environ",
            {
                "KALSHI_API_KEY_ID": "abcd1234",
                "KALSHI_PRIVATE_KEY_PATH": str(self.root / "nope.key"),
                "KALSHI_ENVIRONMENT": "prod",
            },
            clear=False,
        ):
            with self.assertRaises(AccountAuthError):
                missing.connect_from_local()
        self.assertEqual(missing.snapshot()["status"], "error")
        self.assertIn("not found", missing.snapshot()["message"])

    def test_connect_from_local_uses_env_and_missing_message(self):
        from rk_kalshi.account import MISSING_ENV_MESSAGE

        store_path = self.root / "kalshi_account.json"
        empty = AccountService(store_path=store_path, load_env=False)
        with patch("rk_kalshi.account.credentials_from_env", return_value=None):
            with self.assertRaises(AccountAuthError) as ctx:
                empty.connect_from_local()
        self.assertIn(".env", str(ctx.exception))
        self.assertIn("never paste", str(ctx.exception).lower())
        self.assertEqual(empty.snapshot()["status"], "error")
        self.assertIn("never paste", empty.snapshot()["message"].lower())
        self.assertIn("never paste", MISSING_ENV_MESSAGE.lower())

        env = {
            "KALSHI_API_KEY_ID": "a952bcbe-ec3b-4b5b-b8f9-11dae589608c",
            "KALSHI_PRIVATE_KEY_PATH": str(self.key_path),
            "KALSHI_ENVIRONMENT": "demo",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("rk_kalshi.account.KalshiSignedClient", FakeSignedClient):
                service = AccountService(store_path=store_path, load_env=False)
                status = service.connect_from_local()
        self.assertEqual(status["status"], "connected")
        self.assertEqual(status["environment"], "demo")
        self.assertFalse(status["live_trading_enabled"])


if __name__ == "__main__":
    unittest.main()
