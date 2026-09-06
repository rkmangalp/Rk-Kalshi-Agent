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
        self.client.list_tennis_markets.return_value = ([_market()], 18.5)
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
        self.assertEqual(body["banner"], "PAPER MODE ONLY — no live orders")

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
        self.client.list_tennis_markets.assert_called()

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
        self.client.list_tennis_markets.return_value = (
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


if __name__ == "__main__":
    unittest.main()
