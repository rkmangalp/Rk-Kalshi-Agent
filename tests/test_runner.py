import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from rk_kalshi.config import AppConfig
from rk_kalshi.models import MarketSnapshot
from rk_kalshi.runner import PaperRunner


def _dislocated() -> MarketSnapshot:
    return MarketSnapshot(
        ticker="KXATPMATCH-EDGE-AAA",
        event_ticker="KXATPMATCH-EDGE",
        event_name="Edge vs Flat",
        title="Edge wins",
        yes_bid=0.395,
        yes_ask=0.405,
        last_price=0.55,
        volume=50.0,
        updated_ts=1_700_000_000.0,
        status="active",
        series_ticker="KXATPMATCH",
    )


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = AppConfig(
            fill_log_csv=root / "fills.csv",
            fill_log_jsonl=root / "fills.jsonl",
            state_path=root / "state.json",
            allow_size_up=False,
            min_fills_before_size_up=200,
            starting_cash=100.0,
            max_signals_per_cycle=3,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_once_fills_and_persists_when_edge_exists(self):
        client = MagicMock()
        client.list_tennis_markets.return_value = ([_dislocated()], 17.0)
        runner = PaperRunner(self.cfg, client=client)
        fills = runner.run_once()
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].mode, "paper")
        self.assertFalse(fills[0].can_size_up)
        self.assertEqual(fills[0].match_id, "KXATPMATCH-EDGE")
        self.assertTrue(self.cfg.fill_log_csv.exists())
        self.assertTrue(self.cfg.fill_log_jsonl.exists())
        self.assertTrue(self.cfg.state_path.exists())

    def test_run_once_no_fill_on_flat_book(self):
        flat = MarketSnapshot(
            ticker="FLAT",
            event_ticker="FLAT-E",
            event_name="Flat",
            title="Flat",
            yes_bid=0.50,
            yes_ask=0.50,
            last_price=0.50,
            volume=10.0,
            updated_ts=1_700_000_000.0,
        )
        client = MagicMock()
        client.list_tennis_markets.return_value = ([flat], 9.0)
        runner = PaperRunner(self.cfg, client=client)
        self.assertEqual(runner.run_once(), [])


if __name__ == "__main__":
    unittest.main()
