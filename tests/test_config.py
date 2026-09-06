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
        self.assertFalse(cfg.live_enabled)
        self.assertEqual(cfg.series_tickers, ("KXATPMATCH", "KXWTAMATCH", "KXITFWMATCH"))

    def test_repo_yaml_keeps_locks(self):
        root = Path(__file__).resolve().parents[1]
        cfg = load_config(root / "config.yaml")
        self.assertFalse(cfg.allow_size_up)
        self.assertEqual(cfg.min_fills_before_size_up, 200)
        self.assertFalse(cfg.allow_martingale)
        self.assertFalse(cfg.live_enabled)
        self.assertEqual(cfg.max_dollars_per_ticker, 5.0)
        self.assertEqual(cfg.daily_loss_limit, 15.0)
        self.assertEqual(cfg.starting_cash, 100.0)

    def test_load_yaml_overrides(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(
                "bankroll:\n  starting_cash: 80\n"
                "risk:\n  max_dollars_per_ticker: 4\n"
            )
            path = handle.name
        cfg = load_config(path)
        self.assertEqual(cfg.starting_cash, 80.0)
        self.assertEqual(cfg.max_dollars_per_ticker, 4.0)
        self.assertFalse(cfg.allow_size_up)


if __name__ == "__main__":
    unittest.main()
