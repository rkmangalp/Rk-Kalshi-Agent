import tempfile
import unittest
from pathlib import Path

from rk_kalshi.config import AppConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_lock_sizing_and_risk(self):
        cfg = AppConfig()
        self.assertEqual(cfg.starting_cash, 100.0)
        self.assertEqual(cfg.max_dollars_per_ticker, 5.0)
        self.assertEqual(cfg.daily_loss_limit, 15.0)
        self.assertFalse(cfg.allow_martingale)
        self.assertFalse(cfg.allow_size_up)
        self.assertEqual(cfg.min_fills_before_size_up, 200)
        self.assertEqual(cfg.edge_threshold_cents, 3.0)
        self.assertAlmostEqual(cfg.gamma, 0.25)
        self.assertAlmostEqual(cfg.kappa, 1.5)
        self.assertAlmostEqual(cfg.sigma_floor, 0.04)
        self.assertFalse(cfg.use_ema_fallback)
        self.assertEqual(cfg.signal_mode, "as_obi")
        self.assertEqual(cfg.llm_model, "gpt-4o-mini")
        self.assertFalse(cfg.live_enabled)
        self.assertTrue(cfg.live_matches_only)
        self.assertEqual(
            cfg.series_tickers,
            ("KXATPMATCH", "KXWTAMATCH", "KXITFWMATCH", "KXITFMMATCH", "KXATPCHALLENGERMATCH"),
        )
        self.assertEqual(cfg.bitcoin_series_tickers, ("KXBTC15M", "KXBTCD"))
        self.assertEqual(cfg.trade_style, "active")
        self.assertTrue(cfg.trade_bitcoin)
        self.assertTrue(cfg.trade_tennis)
        self.assertIn("KXBTC15M", cfg.enabled_series_tickers())

    def test_repo_yaml_keeps_locks(self):
        root = Path(__file__).resolve().parents[1]
        cfg = load_config(root / "config.yaml")
        self.assertFalse(cfg.allow_size_up)
        self.assertEqual(cfg.min_fills_before_size_up, 200)
        self.assertFalse(cfg.allow_martingale)
        self.assertFalse(cfg.live_enabled)
        self.assertEqual(cfg.account_environment, "prod")
        self.assertTrue(cfg.live_matches_only)
        self.assertGreater(cfg.max_dollars_per_ticker, 0)
        self.assertGreater(cfg.daily_loss_limit, 0)
        self.assertGreater(cfg.starting_cash, 0)
        self.assertTrue(cfg.trade_bitcoin)
        self.assertEqual(cfg.bitcoin_series_tickers, ("KXBTC15M", "KXBTCD"))
        self.assertAlmostEqual(cfg.gamma, 0.25)
        self.assertAlmostEqual(cfg.kappa, 1.5)
        self.assertFalse(cfg.use_ema_fallback)
        self.assertEqual(cfg.signal_mode, "hybrid")

    def test_load_yaml_overrides(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(
                "bankroll:\n  starting_cash: 80\n"
                "risk:\n  max_dollars_per_ticker: 4\n"
                "signal:\n  gamma: 0.4\n  obi_weight: 2.5\n  mode: hybrid\n"
            )
            path = handle.name
        cfg = load_config(path)
        self.assertEqual(cfg.starting_cash, 80.0)
        self.assertEqual(cfg.max_dollars_per_ticker, 4.0)
        self.assertFalse(cfg.allow_size_up)
        self.assertAlmostEqual(cfg.gamma, 0.4)
        self.assertAlmostEqual(cfg.kappa, 2.5)
        self.assertEqual(cfg.signal_mode, "hybrid")


if __name__ == "__main__":
    unittest.main()
