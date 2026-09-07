import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from rk_kalshi.config import AppConfig
from rk_kalshi.execution import LiveKalshiExecution, LiveOrderRejected, LiveTradingDisabledError, PaperExecution
from rk_kalshi.live_caps import (
    CREATE_ORDER_PATH,
    LIVE_MAX_DOLLARS_HARD_CEILING,
    LiveStartError,
    clamp_live_dollars,
    create_order_v2_body,
    require_live_credentials,
)
from rk_kalshi.models import Signal
from rk_kalshi.risk import RiskManager
from rk_kalshi.runner import PaperRunner
from rk_kalshi.state import new_state
from tests.test_account import FakeSignedClient, _creds, _rsa_key


def _signal(side: str = "buy", **overrides) -> Signal:
    data = dict(
        ticker="T",
        event_name="Match",
        match_id="E",
        side=side,
        live_mid=0.50,
        fill_price=0.50,
        edge_cents=3.5,
        edge_bps=350.0,
        edge_thesis="test",
        fee_per_contract=0.02,
        contracts=1,
        yes_bid=0.49,
        yes_ask=0.51,
        last_price=0.55,
        fair_yes=0.54,
    )
    data.update(overrides)
    return Signal(**data)


class LiveCapsTests(unittest.TestCase):
    def test_live_disabled_by_default(self):
        self.assertFalse(AppConfig().live_enabled)
        self.assertFalse(LiveKalshiExecution(enabled=True).enabled)

    def test_hard_ceiling_clamps_ui_request(self):
        self.assertEqual(clamp_live_dollars(5), 5)
        self.assertEqual(clamp_live_dollars(25), LIVE_MAX_DOLLARS_HARD_CEILING)
        self.assertEqual(clamp_live_dollars(10), 10)

    def test_v2_order_body_uses_bid_ask_and_ioc(self):
        body = create_order_v2_body(ticker="KX-1", side="buy", contracts=1, price=0.56)
        self.assertEqual(body["side"], "bid")
        self.assertEqual(body["count"], "1.00")
        self.assertEqual(body["price"], "0.5600")
        self.assertEqual(body["time_in_force"], "immediate_or_cancel")
        self.assertEqual(CREATE_ORDER_PATH, "/portfolio/events/orders")
        ask = create_order_v2_body(ticker="KX-1", side="sell", contracts=2, price=0.40)
        self.assertEqual(ask["side"], "ask")

    def test_refuse_ambiguous_environment(self):
        creds = _creds()
        with self.assertRaises(LiveStartError):
            require_live_credentials(creds, environ={})
        with self.assertRaises(LiveStartError):
            require_live_credentials(None, environ={"KALSHI_ENVIRONMENT": "prod"})
        with self.assertRaises(LiveStartError):
            require_live_credentials(creds, environ={"KALSHI_ENVIRONMENT": "prod"})
        got = require_live_credentials(creds, environ={"KALSHI_ENVIRONMENT": "demo"})
        self.assertIs(got, creds)


class LiveExecutionTests(unittest.TestCase):
    def test_disabled_stub_still_raises(self):
        live = LiveKalshiExecution(enabled=True)
        with self.assertRaises(LiveTradingDisabledError) as ctx:
            live.submit(_signal())
        self.assertIn("Live Kalshi execution is off", str(ctx.exception))
        with self.assertRaises(LiveTradingDisabledError):
            live.execute(_signal())

    def test_create_order_not_called_in_paper(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cfg = AppConfig(
            starting_cash=100.0,
            allow_size_up=False,
            live_enabled=False,
            fill_log_csv=root / "fills.csv",
            fill_log_jsonl=root / "fills.jsonl",
            state_path=root / "state.json",
            kappa=12.0,
        )
        signed = FakeSignedClient(_creds())
        signed.orders_enabled = True
        signed.create_order = MagicMock(side_effect=AssertionError("create_order in paper"))
        client = MagicMock()
        client.list_markets.return_value = ([], 1.0)
        runner = PaperRunner(cfg, client=client, signed_client=signed)
        fills = runner.run_once()
        self.assertEqual(fills, [])
        signed.create_order.assert_not_called()
        paper = PaperExecution(cfg, RiskManager(cfg))
        fill = paper.execute(_signal(), new_state(cfg, day="2026-09-06"), 1, 1.0)
        self.assertEqual(fill.mode, "paper")

    def test_live_caps_and_kill_switch_block_create_order(self):
        cfg = AppConfig(
            live_enabled=True,
            max_dollars_per_ticker=25.0,
            daily_loss_limit=40.0,
            allow_size_up=True,
            min_fills_before_size_up=0,
            base_contracts=40,
            starting_cash=100.0,
        )
        risk = RiskManager(cfg)
        self.assertFalse(risk.can_size_up(new_state(cfg, day="2026-09-06")))
        self.assertEqual(risk.max_dollars_per_ticker(), 10.0)
        self.assertEqual(risk.daily_loss_limit(), 25.0)
        state = new_state(cfg, day="2026-09-06")
        decision = risk.approve(_signal(contracts=40, fill_price=0.40, live_mid=0.40), state)
        self.assertTrue(decision.ok)
        self.assertLessEqual(decision.contracts * 0.40, 10.0 + 1e-9)

        state.cash = 70.0
        state.start_of_day_equity = 100.0
        self.assertTrue(risk.kill_switch_hit(state))

        signed = FakeSignedClient(_creds())
        signed.orders_enabled = True
        live = LiveKalshiExecution(cfg, risk, client=signed, enabled=True)
        with patch.dict("os.environ", {"KALSHI_ENVIRONMENT": "demo"}):
            with self.assertRaises(LiveOrderRejected) as ctx:
                live.execute(_signal(), state, 1, 1.0)
        self.assertIn("kill-switch", str(ctx.exception))
        self.assertEqual(signed.created_orders, [])

    def test_armed_live_posts_create_order(self):
        cfg = AppConfig(
            live_enabled=True,
            max_dollars_per_ticker=5.0,
            daily_loss_limit=10.0,
            allow_size_up=False,
            base_contracts=1,
            starting_cash=100.0,
        )
        signed = FakeSignedClient(_creds())
        signed.orders_enabled = True
        risk = RiskManager(cfg)
        live = LiveKalshiExecution(cfg, risk, client=signed, enabled=True)
        state = new_state(cfg, day="2026-09-06")
        with patch.dict("os.environ", {"KALSHI_ENVIRONMENT": "demo"}):
            fill = live.execute(_signal(), state, 1, 8.0)
        self.assertEqual(fill.mode, "live")
        self.assertEqual(len(signed.created_orders), 1)
        order = signed.created_orders[0]
        self.assertEqual(order["ticker"], "T")
        self.assertEqual(order["contracts"], 1)
        self.assertFalse(fill.can_size_up)


class LiveRunnerIsolationTests(unittest.TestCase):
    def test_live_fill_does_not_write_paper_journal(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cfg = AppConfig(
            live_enabled=True,
            starting_cash=100.0,
            allow_size_up=False,
            fill_log_csv=root / "fills.csv",
            fill_log_jsonl=root / "fills.jsonl",
            state_path=root / "live_state.json",
            live_state_path=root / "live_state.json",
            kappa=12.0,
            max_signals_per_cycle=3,
        )
        from rk_kalshi.models import MarketSnapshot
        import time

        market = MarketSnapshot(
            ticker="KXATPMATCH-EDGE-AAA",
            event_ticker="KXATPMATCH-EDGE",
            event_name="Edge vs Flat",
            title="Edge wins",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            volume=50.0,
            updated_ts=1_700_000_000.0,
            status="active",
            series_ticker="KXATPMATCH",
            occurrence_ts=time.time(),
            yes_bid_size=2000.0,
            yes_ask_size=20.0,
        )
        signed = FakeSignedClient(_creds())
        signed.orders_enabled = True
        client = MagicMock()
        client.list_markets.return_value = ([market], 12.0)
        runner = PaperRunner(cfg, client=client, signed_client=signed)
        from rk_kalshi.state import save_state

        seeded = new_state(cfg, day="2026-09-06")
        seeded.ema["KXATPMATCH-EDGE-AAA"] = 0.60
        save_state(cfg, seeded)
        with patch.dict("os.environ", {"KALSHI_ENVIRONMENT": "demo"}):
            fills = runner.run_once()
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].mode, "live")
        self.assertTrue(signed.created_orders)
        self.assertFalse(cfg.fill_log_csv.exists())


if __name__ == "__main__":
    unittest.main()
