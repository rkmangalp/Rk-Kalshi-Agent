import unittest

from rk_kalshi.execution import LiveKalshiExecution, LiveTradingDisabledError, PaperExecution
from rk_kalshi.config import AppConfig
from rk_kalshi.models import Signal
from rk_kalshi.risk import RiskManager
from rk_kalshi.state import new_state


def _signal(side: str = "buy") -> Signal:
    return Signal(
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


class ExecutionTests(unittest.TestCase):
    def test_live_stub_raises_even_if_enabled_flag_passed(self):
        live = LiveKalshiExecution(enabled=True)
        with self.assertRaises(LiveTradingDisabledError) as ctx:
            live.submit(_signal())
        self.assertIn("Live Kalshi execution is off", str(ctx.exception))
        with self.assertRaises(LiveTradingDisabledError):
            live.execute(_signal())

    def test_paper_fill_at_live_yes_mid(self):
        cfg = AppConfig(starting_cash=100.0, allow_size_up=False)
        risk = RiskManager(cfg)
        paper = PaperExecution(cfg, risk)
        state = new_state(cfg, day="2026-09-06")
        fill = paper.execute(_signal(), state, contracts=1, latency_ms=11.0)
        self.assertEqual(fill.mode, "paper")
        self.assertEqual(fill.fill_price, 0.50)
        self.assertEqual(fill.live_mid, 0.50)
        self.assertFalse(fill.can_size_up)
        self.assertEqual(fill.latency_ms, 11.0)
        self.assertEqual(state.position("T").contracts, 1)

    def test_paper_sell_reduces_long(self):
        cfg = AppConfig(starting_cash=100.0)
        risk = RiskManager(cfg)
        paper = PaperExecution(cfg, risk)
        state = new_state(cfg, day="2026-09-06")
        paper.execute(_signal("buy"), state, 1, 1.0)
        paper.execute(_signal("sell"), state, 1, 1.0)
        self.assertEqual(state.position("T").contracts, 0)


if __name__ == "__main__":
    unittest.main()
