import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import httpx

from rk_kalshi.config import AppConfig
from rk_kalshi.dashboard import create_app
from rk_kalshi.journal import FillJournal
from rk_kalshi.models import Fill, MarketSnapshot
from rk_kalshi.schema import FILL_FIELDS, REQUIRED_FIELDS


class _ApiClient:
    """Sync wrapper around httpx's async ASGI transport (no httpx2 TestClient)."""

    def __init__(self, app):
        self.app = app

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method: str, url: str, **kwargs):
        async def _do():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(_do())

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)


def _client(app) -> _ApiClient:
    return _ApiClient(app)


def _market() -> MarketSnapshot:
    return MarketSnapshot(
        ticker="KXATPMATCH-26SEP06AAA-BBB",
        event_ticker="KXATPMATCH-26SEP06AAA",
        event_name="Ada vs Bea",
        title="Ada wins",
        yes_bid=0.410,
        yes_ask=0.430,
        last_price=0.420,
        volume=125.0,
        updated_ts=1_700_000_000.0,
        status="open",
        series_ticker="KXATPMATCH",
        occurrence_ts=1_700_000_000.0,
    )


def _fill() -> Fill:
    return Fill(
        timestamp="2026-09-06T02:00:00.000000Z",
        ticker="KXATPMATCH-26SEP06AAA-BBB",
        side="buy",
        fill_price=0.42,
        live_mid=0.42,
        edge_thesis="BUY YES demo: net edge 4.00¢ after costs",
        running_pnl=-0.02,
        event_name="Ada vs Bea",
        match_id="KXATPMATCH-26SEP06AAA",
        edge_cents=4.0,
        edge_bps=400.0,
        contracts=1,
        fee=0.02,
        cash_after=99.56,
        mode="paper",
        latency_ms=11.0,
        can_size_up=False,
        realized_delta=-0.02,
    )


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = AppConfig(
            fill_log_csv=root / "fills.csv",
            fill_log_jsonl=root / "fills.jsonl",
            state_path=root / "state.json",
            allow_size_up=False,
            min_fills_before_size_up=200,
            starting_cash=100.0,
            live_enabled=False,
            cycle_sleep_s=15.0,
        )
        self.client = MagicMock()
        self.client.list_markets.return_value = ([_market()], 18.5)
        self.app = create_app(self.cfg, client=self.client)
        self.http = _client(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_index_serves_paper_banner(self):
        response = self.http.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("PAPER MODE ONLY", response.text)
        self.assertIn("no live orders", response.text)
        self.assertNotIn("Place live order", response.text)
        self.assertIn("Start paper trading", response.text)
        self.assertIn("Live tennis matches only", response.text)
        self.assertIn("Bitcoin (buy and sell YES)", response.text)
        self.assertIn("local time", response.text)
        self.assertIn("fills-time-head", response.text)
        self.assertIn("app.js?v=", response.text)
        self.assertIn("no-store", (response.headers.get("cache-control") or "").lower())
        self.assertIn("starting-cash", response.text)
        self.assertIn("btn-stop", response.text)
        self.assertIn("btn-clear-logs", response.text)
        self.assertIn("Clear logs", response.text)
        self.assertIn("btn-clear-session", response.text)
        self.assertIn("contract-url", response.text)
        self.assertIn("contract-status", response.text)
        self.assertIn("kalshi.com/markets/kxatpmatch", response.text)
        self.assertIn("kxbtc15m/bitcoin-price-up-down", response.text)
        self.assertIn("kxatpchallengermatch", response.text)
        self.assertIn("Paste any Kalshi market or event URL", response.text)
        self.assertIn("Contract / match", response.text)
        self.assertIn("not a live Kalshi account", response.text)
        self.assertIn("Connect to see live trades", response.text)
        self.assertIn("btn-connect", response.text)
        self.assertIn("live-trades-panel", response.text)
        self.assertIn("Enable live trading (coming soon)", response.text)
        self.assertIn("account-banner", response.text)
        self.assertIn("Live account view", response.text)
        self.assertIn("paper desk", response.text.lower())
        self.assertIn("account-key-id", response.text)

    def test_health_and_status_lock_paper_mode(self):
        health = self.http.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["paper_mode"])
        self.assertFalse(health.json()["live_enabled"])

        status = self.http.get("/api/status")
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertTrue(body["paper_mode"])
        self.assertFalse(body["live_enabled"])
        self.assertFalse(body["live_trading_available"])
        self.assertFalse(body["can_size_up"])
        self.assertFalse(body["allow_size_up"])
        self.assertEqual(body["min_fills_before_size_up"], 200)
        self.assertFalse(body["killed"])
        self.assertIn("PAPER MODE ONLY", body["banner"])
        self.assertIn("no live orders", body["banner"])
        self.assertEqual(body["account"]["status"], "disconnected")
        self.assertFalse(body["account"]["live_trading_enabled"])
        self.assertAlmostEqual(body["starting_cash"], 100.0)
        self.assertAlmostEqual(body["max_dollars_per_ticker"], 5.0)
        self.assertAlmostEqual(body["daily_loss_limit"], 15.0)

    def test_markets_reuses_client(self):
        response = self.http.get("/api/markets")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertTrue(body["paper_mode"])
        row = body["markets"][0]
        self.assertEqual(row["ticker"], "KXATPMATCH-26SEP06AAA-BBB")
        self.assertEqual(row["event_name"], "Ada vs Bea")
        self.assertEqual(row["match_id"], "KXATPMATCH-26SEP06AAA")
        self.assertAlmostEqual(row["yes_bid"], 0.410)
        self.assertAlmostEqual(row["yes_ask"], 0.430)
        self.assertAlmostEqual(row["yes_mid"], 0.420)
        self.assertAlmostEqual(row["last_price"], 0.420)
        self.assertAlmostEqual(row["spread_cents"], 2.0)
        self.assertAlmostEqual(row["volume"], 125.0)
        self.assertIn("in_play", row)
        self.assertEqual(row["asset_class"], "tennis")
        self.client.list_markets.assert_called()

    def test_markets_keeps_near_money_bitcoin_and_drops_lottery_strikes(self):
        near = MarketSnapshot(
            ticker="KXBTC15M-26SEP060015-15",
            event_ticker="KXBTC15M-26SEP060015",
            event_name="BTC 15 min",
            title="Up",
            yes_bid=0.540,
            yes_ask=0.550,
            last_price=0.545,
            volume=80.0,
            updated_ts=1_700_000_000.0,
            status="active",
            series_ticker="KXBTC15M",
        )
        far = MarketSnapshot(
            ticker="KXBTCD-26SEP0601-T70099.99",
            event_ticker="KXBTCD-26SEP0601",
            event_name="BTC price",
            title="Above 70099",
            yes_bid=0.990,
            yes_ask=1.000,
            last_price=0.995,
            volume=10.0,
            updated_ts=1_700_000_000.0,
            status="active",
            series_ticker="KXBTCD",
        )
        self.client.list_markets.return_value = ([near, far], 9.0)
        response = self.http.get("/api/markets")
        self.assertEqual(response.status_code, 200)
        tickers = [row["ticker"] for row in response.json()["markets"]]
        self.assertEqual(tickers, [near.ticker])

    def test_fills_and_pnl_use_locked_schema(self):
        FillJournal(self.cfg.fill_log_csv, self.cfg.fill_log_jsonl).append(_fill())

        fills = self.http.get("/api/fills")
        self.assertEqual(fills.status_code, 200)
        payload = fills.json()
        self.assertEqual(payload["fields"], list(FILL_FIELDS))
        self.assertEqual(payload["count"], 1)
        row = payload["fills"][0]
        for name in REQUIRED_FIELDS:
            self.assertIn(name, row)
        self.assertEqual(row["timestamp"], "2026-09-06T02:00:00.000000Z")
        self.assertEqual(row["ticker"], "KXATPMATCH-26SEP06AAA-BBB")
        self.assertEqual(row["match_id"], "KXATPMATCH-26SEP06AAA")
        self.assertFalse(row["can_size_up"])
        self.assertAlmostEqual(row["fill_price"], 0.42)
        self.assertAlmostEqual(row["live_mid"], 0.42)

        pnl = self.http.get("/api/pnl")
        self.assertEqual(pnl.status_code, 200)
        summary = pnl.json()
        self.assertEqual(summary["fills"], 1)
        self.assertFalse(summary["can_size_up"])
        self.assertFalse(summary["killed"])
        self.assertTrue(summary["paper_mode"])
        self.assertFalse(summary["live_enabled"])
        self.assertAlmostEqual(summary["running_pnl"], -0.02)

    def test_run_once_uses_paper_runner(self):
        from rk_kalshi.state import new_state, save_state

        state = new_state(self.cfg, day="2026-09-06")
        state.ema["KXATPMATCH-26SEP06AAA-BBB"] = 0.60
        save_state(self.cfg, state)
        self.client.list_markets.return_value = (
            [
                MarketSnapshot(
                    ticker="KXATPMATCH-26SEP06AAA-BBB",
                    event_ticker="KXATPMATCH-26SEP06AAA",
                    event_name="Ada vs Bea",
                    title="Ada wins",
                    yes_bid=0.395,
                    yes_ask=0.405,
                    last_price=0.40,
                    volume=50.0,
                    updated_ts=1_700_000_000.0,
                    status="active",
                    series_ticker="KXATPMATCH",
                    occurrence_ts=time.time(),
                )
            ],
            12.0,
        )

        started = self.http.post("/api/run", json={"cycles": 1, "sleep_s": 0})
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["mode"], "paper")

        deadline = time.time() + 5
        snapshot = None
        while time.time() < deadline:
            snapshot = self.http.get("/api/run").json()
            if not snapshot["running"]:
                break
            time.sleep(0.05)
        self.assertIsNotNone(snapshot)
        self.assertFalse(snapshot["running"])
        self.assertIsNone(snapshot["last_error"])
        self.assertEqual(snapshot["cycles_done"], 1)
        self.assertGreaterEqual(snapshot["fills_this_run"], 1)
        self.assertTrue(any("PAPER MODE ONLY" in line for line in snapshot["logs"]))
        self.assertTrue(any("can_size_up" in line for line in snapshot["logs"]))

        fills = self.http.get("/api/fills").json()["fills"]
        self.assertGreaterEqual(len(fills), 1)
        self.assertFalse(fills[0]["can_size_up"])
        self.assertEqual(fills[0]["mode"], "paper")

    def test_run_rejects_live_and_busy(self):
        live = self.http.post("/api/run", json={"cycles": 1, "live": True})
        self.assertEqual(live.status_code, 422)

        live_mode = self.http.post("/api/run", json={"cycles": 1, "mode": "live"})
        self.assertEqual(live_mode.status_code, 422)

        blocker = MagicMock()
        blocker.run_once.side_effect = lambda: time.sleep(0.4) or []
        blocked = create_app(self.cfg, client=self.client, runner=blocker)
        with _client(blocked) as http:
            first = http.post("/api/run", json={"cycles": 1, "sleep_s": 0})
            self.assertEqual(first.status_code, 200)
            second = http.post("/api/run", json={"cycles": 1, "sleep_s": 0})
            self.assertEqual(second.status_code, 409)
            deadline = time.time() + 3
            while time.time() < deadline and http.get("/api/run").json()["running"]:
                time.sleep(0.05)

    def test_cli_dashboard_and_serve_still_keep_other_commands(self):
        from rk_kalshi.cli import main

        with patch("rk_kalshi.cli._cmd_dashboard", return_value=0) as dash:
            self.assertEqual(main(["dashboard", "--port", "8765"]), 0)
            dash.assert_called_once()
            self.assertEqual(main(["serve", "--host", "127.0.0.1"]), 0)
            self.assertEqual(dash.call_count, 2)

        with patch("rk_kalshi.cli._cmd_show_pnl", return_value=0) as pnl:
            self.assertEqual(main(["show-pnl"]), 0)
            pnl.assert_called_once()

        with patch("rk_kalshi.cli._cmd_paper_run", return_value=0) as run:
            self.assertEqual(main(["paper-run", "--once"]), 0)
            run.assert_called_once()

        with patch("rk_kalshi.cli._cmd_list", return_value=0) as listed:
            self.assertEqual(main(["list-tennis-markets"]), 0)
            listed.assert_called_once()

        with patch("rk_kalshi.cli._cmd_account", return_value=0) as account:
            self.assertEqual(main(["account"]), 0)
            account.assert_called_once()

    def test_start_applies_paper_bankroll_and_rejects_live(self):
        from rk_kalshi.state import load_state

        yaml_path = Path(self.tmp.name) / "config.yaml"
        yaml_path.write_text(
            "bankroll:\n  starting_cash: 100.0\n"
            "risk:\n  max_dollars_per_ticker: 5.0\n  daily_loss_limit: 15.0\n"
            "paper:\n  cycle_sleep_s: 15.0\n"
            "live:\n  enabled: false\n"
        )
        app = create_app(self.cfg, client=self.client, config_path=yaml_path)
        http = _client(app)

        live = http.post("/api/start", json={"starting_cash": 80, "live": True})
        self.assertEqual(live.status_code, 422)

        started = http.post(
            "/api/start",
            json={
                "starting_cash": 80,
                "max_dollars_per_ticker": 4,
                "daily_loss_limit": 12,
                "sleep_s": 10,
                "continuous": True,
                "mode": "paper",
            },
        )
        self.assertEqual(started.status_code, 200)
        body = started.json()
        self.assertTrue(body["paper_mode"])
        self.assertFalse(body["live_enabled"])
        self.assertAlmostEqual(body["session"]["starting_cash"], 80.0)
        self.assertAlmostEqual(body["session"]["max_dollars_per_ticker"], 4.0)
        self.assertFalse(body["session"]["can_size_up"])
        self.assertTrue(body["session"]["live_matches_only"])
        self.assertTrue(body["session"]["trade_bitcoin"])
        self.assertTrue(body["session"]["trade_tennis"])
        self.assertTrue(body["run"]["continuous"])
        self.assertTrue(body["run"]["running"])

        busy = http.post("/api/start", json={"starting_cash": 80, "sleep_s": 10})
        self.assertEqual(busy.status_code, 409)
        clear_busy = http.post("/api/clear")
        self.assertEqual(clear_busy.status_code, 409)
        self.assertIn("Stop", clear_busy.json()["detail"])

        stopped = http.post("/api/stop")
        self.assertEqual(stopped.status_code, 200)
        deadline = time.time() + 4
        snapshot = None
        while time.time() < deadline:
            snapshot = http.get("/api/run").json()
            if not snapshot["running"]:
                break
            time.sleep(0.05)
        self.assertIsNotNone(snapshot)
        self.assertFalse(snapshot["running"])
        self.assertTrue(any("stop requested" in line for line in snapshot["logs"]))

        state = load_state(app.state.service.cfg)
        self.assertAlmostEqual(state.starting_cash, 80.0)
        self.assertAlmostEqual(state.cash, 80.0)
        self.assertFalse(app.state.service.cfg.live_enabled)
        self.assertFalse(app.state.service.cfg.allow_size_up)

        session_file = Path(self.tmp.name) / "dashboard_session.json"
        self.assertTrue(session_file.exists())
        session = session_file.read_text()
        self.assertIn("80", session)
        self.assertIn('"live_enabled": false', session)
        self.assertIn("starting_cash: 80.0", yaml_path.read_text())

        status = http.get("/api/status").json()
        self.assertAlmostEqual(status["starting_cash"], 80.0)
        self.assertAlmostEqual(status["max_dollars_per_ticker"], 4.0)
        self.assertFalse(status["can_size_up"])
        self.assertTrue(status["trade_bitcoin"])
        self.assertTrue(status["trade_tennis"])

        empty = http.post(
            "/api/start",
            json={"starting_cash": 80, "trade_bitcoin": False, "trade_tennis": False},
        )
        self.assertEqual(empty.status_code, 400)

    def test_clear_logs_empties_buffer(self):
        self.http.post("/api/run", json={"cycles": 1, "sleep_s": 0})
        deadline = time.time() + 4
        while time.time() < deadline and self.http.get("/api/run").json()["running"]:
            time.sleep(0.05)
        before = self.http.get("/api/run").json()["logs"]
        self.assertGreater(len(before), 0)
        cleared = self.http.post("/api/logs/clear")
        self.assertEqual(cleared.status_code, 200)
        logs = cleared.json()["logs"]
        self.assertEqual(len(logs), 1)
        self.assertIn("logs cleared", logs[0])
        self.assertFalse(any("PAPER MODE ONLY" in line for line in logs))

    def test_contract_parse_and_clear_session(self):
        from rk_kalshi.state import load_state, new_state, save_state

        bad = self.http.post("/api/contract", json={"url": "https://example.com/foo"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("not a Kalshi link", bad.json()["detail"])
        after_bad = self.http.get("/api/status").json()["target"]
        self.assertFalse(after_bad["active"])
        self.assertIn("not a Kalshi link", after_bad["error"])
        self.assertNotEqual(after_bad["error"], "")

        series = self.http.post(
            "/api/contract",
            json={"url": "https://kalshi.com/markets/kxatpmatch"},
        )
        self.assertEqual(series.status_code, 400)
        self.assertIn("series page", series.json()["detail"])

        crypto = self.http.post(
            "/api/contract",
            json={
                "url": "https://kalshi.com/markets/kxbtc15m/bitcoin-price-up-down/kxbtc15m-26sep061845"
            },
        )
        self.assertEqual(crypto.status_code, 200)
        self.assertEqual(crypto.json()["target"]["event_ticker"], "KXBTC15M-26SEP061845")
        self.assertEqual(crypto.json()["target"]["asset_class"], "bitcoin")
        self.assertEqual(crypto.json()["target"]["error"], "")

        challenger = self.http.post(
            "/api/contract",
            json={
                "url": (
                    "https://kalshi.com/markets/kxatpchallengermatch/challenger-atp-/"
                    "kxatpchallengermatch-26sep06kimtam"
                )
            },
        )
        self.assertEqual(challenger.status_code, 200)
        self.assertEqual(
            challenger.json()["target"]["event_ticker"],
            "KXATPCHALLENGERMATCH-26SEP06KIMTAM",
        )
        self.assertEqual(challenger.json()["target"]["asset_class"], "tennis")

        ok = self.http.post(
            "/api/contract",
            json={
                "url": "https://kalshi.com/markets/kxatpmatch/atp-tennis-match/kxatpmatch-26sep06cerblo"
            },
        )
        self.assertEqual(ok.status_code, 200)
        target = ok.json()["target"]
        self.assertTrue(target["active"])
        self.assertEqual(target["event_ticker"], "KXATPMATCH-26SEP06CERBLO")
        self.assertEqual(target["match_id"], "KXATPMATCH-26SEP06CERBLO")
        self.assertTrue(ok.json()["paper_mode"])
        self.assertFalse(ok.json()["live_enabled"])
        listed = self.http.get("/api/contract")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["target"]["event_ticker"], "KXATPMATCH-26SEP06CERBLO")
        self.assertTrue(listed.json()["examples"])

        FillJournal(self.cfg.fill_log_csv, self.cfg.fill_log_jsonl).append(_fill())
        dirty = new_state(self.cfg, day="2026-09-06")
        dirty.cash = 90.0
        dirty.fill_count = 3
        save_state(self.cfg, dirty)
        self.assertEqual(self.http.get("/api/fills").json()["count"], 1)

        wiped = self.http.post("/api/clear")
        self.assertEqual(wiped.status_code, 200)
        self.assertTrue(wiped.json()["cleared"])
        self.assertFalse(wiped.json()["target"]["active"])
        self.assertIn("not a live Kalshi account", wiped.json()["note"])
        self.assertEqual(self.http.get("/api/fills").json()["count"], 0)
        state = load_state(self.cfg)
        self.assertEqual(state.fill_count, 0)
        self.assertAlmostEqual(state.cash, self.cfg.starting_cash)
        logs = wiped.json()["run"]["logs"]
        self.assertTrue(any("paper session cleared" in line for line in logs))

    def test_account_connect_is_read_only_and_rejects_live_trading(self):
        from tests.test_account import FakeSignedClient, _pem, _rsa_key
        from unittest.mock import patch

        live = self.http.post(
            "/api/account/connect",
            json={
                "api_key_id": "abcd",
                "private_key_pem": "x",
                "enable_live_trading": True,
            },
        )
        self.assertEqual(live.status_code, 422)

        live_mode = self.http.post(
            "/api/account/connect",
            json={"api_key_id": "abcd", "private_key_pem": "x", "mode": "live"},
        )
        self.assertEqual(live_mode.status_code, 422)

        missing = self.http.get("/api/account/portfolio")
        self.assertEqual(missing.status_code, 409)

        key = _rsa_key()
        with patch("rk_kalshi.account.KalshiSignedClient", FakeSignedClient):
            connected = self.http.post(
                "/api/account/connect",
                json={
                    "environment": "demo",
                    "api_key_id": "a952bcbe-ec3b-4b5b-b8f9-11dae589608c",
                    "private_key_pem": _pem(key),
                    "enable_live_trading": False,
                    "mode": "paper",
                },
            )
        self.assertEqual(connected.status_code, 200)
        body = connected.json()
        self.assertFalse(body["live_enabled"])
        self.assertTrue(body["read_only"])
        self.assertEqual(body["account"]["status"], "connected")
        self.assertIn("read-only", body["note"].lower())

        book = self.http.get("/api/account/portfolio")
        self.assertEqual(book.status_code, 200)
        portfolio = book.json()
        self.assertAlmostEqual(portfolio["balance"], 101.0)
        self.assertEqual(portfolio["positions"][0]["ticker"], "KX-OPEN")
        self.assertFalse(portfolio["live_enabled"])

        status = self.http.get("/api/status").json()
        self.assertEqual(status["account"]["status"], "connected")
        self.assertFalse(status["live_enabled"])
        self.assertFalse(status["live_trading_available"])

        gone = self.http.post("/api/account/disconnect")
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(gone.json()["account"]["status"], "disconnected")
        self.assertFalse(gone.json()["live_enabled"])


if __name__ == "__main__":
    unittest.main()
