import unittest

from rk_kalshi.config import AppConfig
from rk_kalshi.models import LastTickerTrade, PaperState, Position, Signal
from rk_kalshi.risk import RiskManager
from rk_kalshi.state import apply_fill, new_state


def _signal(**overrides) -> Signal:
    data = dict(
        ticker="KXATPMATCH-T",
        event_name="A vs B",
        match_id="KXATPMATCH-T",
        side="buy",
        live_mid=0.40,
        fill_price=0.40,
        edge_cents=5.0,
        edge_bps=500.0,
        edge_thesis="test thesis",
        fee_per_contract=0.02,
        contracts=1,
        yes_bid=0.39,
        yes_ask=0.41,
        last_price=0.45,
        fair_yes=0.45,
    )
    data.update(overrides)
    return Signal(**data)


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.cfg = AppConfig(
            max_dollars_per_ticker=5.0,
            daily_loss_limit=15.0,
            allow_martingale=False,
            allow_size_up=False,
            min_fills_before_size_up=200,
            base_contracts=1,
            starting_cash=100.0,
        )
        self.risk = RiskManager(self.cfg)
        self.state = new_state(self.cfg, day="2026-09-06")

    def test_default_can_size_up_is_locked_off(self):
        self.assertFalse(self.cfg.allow_size_up)
        self.assertEqual(self.cfg.min_fills_before_size_up, 200)
        self.assertFalse(self.risk.can_size_up(self.state))
        self.state.fill_count = 500
        self.state.cash = 200.0
        self.assertFalse(self.risk.can_size_up(self.state))

    def test_can_size_up_stays_false_even_with_many_losing_fills(self):
        unlocked = AppConfig(allow_size_up=True, min_fills_before_size_up=200, starting_cash=100.0)
        risk = RiskManager(unlocked)
        state = new_state(unlocked, day="2026-09-06")
        state.fill_count = 250
        state.cash = 80.0  # negative paper P&L
        self.assertFalse(risk.can_size_up(state))

    def test_ticker_dollar_cap_rejects_oversized_buy(self):
        # 20 * $0.40 = $8 > $5
        decision = self.risk.approve(_signal(contracts=20, fill_price=0.40), self.state)
        self.assertTrue(decision.ok)
        self.assertLessEqual(decision.contracts * 0.40, 5.0 + 1e-9)

    def test_ticker_cap_rejects_when_even_one_contract_exceeds(self):
        tight = AppConfig(max_dollars_per_ticker=0.10, base_contracts=1, starting_cash=100.0)
        risk = RiskManager(tight)
        state = new_state(tight, day="2026-09-06")
        decision = risk.approve(_signal(contracts=1, fill_price=0.40), state)
        self.assertFalse(decision.ok)
        self.assertIn("cap", decision.reason)

    def test_existing_position_counts_toward_ticker_cap(self):
        self.state.positions["KXATPMATCH-T"] = Position(contracts=12, avg_price=0.40)
        # 12 * 0.40 = 4.80, one more 0.40 contract → 5.20 > 5.00
        decision = self.risk.approve(_signal(contracts=1, fill_price=0.40), self.state)
        self.assertFalse(decision.ok)

    def test_no_martingale_after_loss(self):
        self.state.last_trade["KXATPMATCH-T"] = LastTickerTrade(contracts=1, lost=True)
        decision = self.risk.approve(_signal(contracts=2), self.state)
        self.assertFalse(decision.ok)
        self.assertIn("martingale", decision.reason)

    def test_martingale_still_forbidden_if_config_flag_is_true(self):
        cfg = AppConfig(allow_martingale=True, allow_size_up=True, min_fills_before_size_up=0, starting_cash=100.0)
        # force positive pnl + fill count so can_size_up could otherwise pass
        state = new_state(cfg, day="2026-09-06")
        state.fill_count = 200
        state.cash = 150.0
        state.last_trade["KXATPMATCH-T"] = LastTickerTrade(contracts=1, lost=True)
        risk = RiskManager(cfg)
        decision = risk.approve(_signal(contracts=2), state)
        self.assertFalse(decision.ok)
        self.assertIn("martingale", decision.reason)

    def test_same_size_after_loss_is_allowed(self):
        self.state.last_trade["KXATPMATCH-T"] = LastTickerTrade(contracts=1, lost=True)
        decision = self.risk.approve(_signal(contracts=1), self.state)
        self.assertTrue(decision.ok)
        self.assertEqual(decision.contracts, 1)

    def test_daily_loss_kill_switch(self):
        self.state.start_of_day_equity = 100.0
        self.state.cash = 80.0  # -20 vs $15 limit
        self.assertTrue(self.risk.kill_switch_hit(self.state))
        decision = self.risk.approve(_signal(), self.state)
        self.assertFalse(decision.ok)
        self.assertIn("kill-switch", decision.reason)
        self.assertTrue(self.state.killed)

    def test_kill_switch_uses_mark_to_market(self):
        self.state.cash = 90.0
        self.state.positions["X"] = Position(contracts=10, avg_price=1.0)
        # equity = 90 + 10*0.40 = 94 → daily -6, under $15 limit
        self.assertFalse(self.risk.kill_switch_hit(self.state, {"X": 0.40}))
        # mark crash: 90 + 10*0.10 = 91? wait that's only -9
        # make it worse: cash 80 + 10*0.10 = 81 → -19
        self.state.cash = 80.0
        self.assertTrue(self.risk.kill_switch_hit(self.state, {"X": 0.10}))

    def test_paper_still_clips_to_base_contracts(self):
        decision = self.risk.approve(_signal(contracts=20, fill_price=0.40), self.state)
        self.assertTrue(decision.ok)
        self.assertEqual(decision.contracts, 1)

    def test_live_sizes_new_entry_to_dollar_cap(self):
        cfg = AppConfig(
            live_enabled=True,
            max_dollars_per_ticker=20.0,
            base_contracts=2,
            starting_cash=100.0,
            allow_size_up=False,
        )
        risk = RiskManager(cfg)
        state = new_state(cfg, day="2026-09-06")
        decision = risk.approve(_signal(contracts=2, fill_price=0.53, live_mid=0.53), state)
        self.assertTrue(decision.ok)
        self.assertGreater(decision.contracts, 2)
        self.assertLessEqual(decision.contracts * 0.53, 20.0 + 1e-9)

    def test_pair_lock_cover_is_not_clipped_to_base_or_cap(self):
        cfg = AppConfig(
            live_enabled=True,
            max_dollars_per_ticker=20.0,
            base_contracts=2,
            starting_cash=100.0,
            allow_size_up=False,
        )
        risk = RiskManager(cfg)
        state = new_state(cfg, day="2026-09-06")
        state.positions["KXATPMATCH-T"] = Position(contracts=62, avg_price=0.53)
        decision = risk.approve(
            _signal(side="sell", contracts=62, fill_price=0.81, live_mid=0.82, yes_bid=0.81, yes_ask=0.83),
            state,
        )
        self.assertTrue(decision.ok)
        self.assertEqual(decision.contracts, 62)

    def test_apply_fill_marks_loss_for_martingale_guard(self):
        apply_fill(self.state, "KXATPMATCH-T", "buy", 1, 0.60, fee=0.02)
        apply_fill(self.state, "KXATPMATCH-T", "sell", 1, 0.40, fee=0.02)
        last = self.state.last_trade["KXATPMATCH-T"]
        self.assertTrue(last.lost)
        self.assertEqual(last.contracts, 1)


if __name__ == "__main__":
    unittest.main()
