import unittest
from dataclasses import replace

from rk_kalshi.config import AppConfig
from rk_kalshi.presets import (
    TRADE_STYLES,
    apply_trade_style,
    normalize_trade_style,
    presets_payload,
)


class TradeStylePresetTests(unittest.TestCase):
    def test_four_modes_scale_risk_and_reward(self):
        safe = TRADE_STYLES["safe"]
        conservative = TRADE_STYLES["conservative"]
        active = TRADE_STYLES["active"]
        aggressive = TRADE_STYLES["aggressive"]
        self.assertLess(safe.max_dollars_per_ticker, conservative.max_dollars_per_ticker)
        self.assertLess(conservative.max_dollars_per_ticker, active.max_dollars_per_ticker)
        self.assertLess(active.max_dollars_per_ticker, aggressive.max_dollars_per_ticker)
        self.assertGreater(safe.edge_threshold_cents, conservative.edge_threshold_cents)
        self.assertGreater(conservative.edge_threshold_cents, active.edge_threshold_cents)
        self.assertGreater(active.edge_threshold_cents, aggressive.edge_threshold_cents)
        self.assertLess(safe.base_contracts, aggressive.base_contracts)
        self.assertLess(safe.max_spread_cents, aggressive.max_spread_cents)
        self.assertGreater(safe.gamma, aggressive.gamma)
        self.assertLess(safe.kappa, aggressive.kappa)
        self.assertGreater(safe.cycle_sleep_s, aggressive.cycle_sleep_s)

    def test_apply_trade_style_mutates_paper_knobs_only(self):
        cfg = AppConfig(live_enabled=False, starting_cash=100.0, edge_threshold_cents=3.0)
        aggressive = apply_trade_style(cfg, "aggressive")
        self.assertEqual(aggressive.trade_style, "aggressive")
        self.assertAlmostEqual(aggressive.edge_threshold_cents, 1.5)
        self.assertAlmostEqual(aggressive.max_dollars_per_ticker, 25.0)
        self.assertEqual(aggressive.base_contracts, 4)
        self.assertAlmostEqual(aggressive.max_spread_cents, 12.0)
        self.assertAlmostEqual(aggressive.gamma, 0.10)
        self.assertAlmostEqual(aggressive.kappa, 2.5)
        self.assertFalse(aggressive.live_enabled)
        self.assertAlmostEqual(aggressive.starting_cash, 100.0)

        safe = apply_trade_style(cfg, "safe")
        self.assertGreater(safe.edge_threshold_cents, aggressive.edge_threshold_cents)
        self.assertLess(safe.max_dollars_per_ticker, aggressive.max_dollars_per_ticker)
        self.assertFalse(safe.live_enabled)

    def test_aliases_and_payload_stay_paper_only(self):
        self.assertEqual(normalize_trade_style("HIGH"), "aggressive")
        self.assertEqual(normalize_trade_style("balanced"), "conservative")
        payload = presets_payload()
        self.assertEqual(payload["default"], "active")
        self.assertFalse(payload["live_enabled"])
        self.assertTrue(payload["paper_only"])
        self.assertEqual([row["id"] for row in payload["styles"]], ["safe", "conservative", "active", "aggressive"])

    def test_unknown_style_falls_back_without_enabling_live(self):
        cfg = replace(AppConfig(), live_enabled=False)
        out = apply_trade_style(cfg, "not-a-mode")
        self.assertEqual(out.trade_style, "active")
        self.assertFalse(out.live_enabled)


if __name__ == "__main__":
    unittest.main()
