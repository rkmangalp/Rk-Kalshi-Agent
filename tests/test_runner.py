import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from rk_kalshi.config import AppConfig
from rk_kalshi.models import MarketSnapshot
from rk_kalshi.runner import PaperRunner
from rk_kalshi.state import new_state, save_state


def _dislocated() -> MarketSnapshot:
    return MarketSnapshot(
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
        seeded = new_state(self.cfg, day="2026-09-06")
        seeded.ema["KXATPMATCH-EDGE-AAA"] = 0.60
        save_state(self.cfg, seeded)
        client = MagicMock()
        client.list_markets.return_value = ([_dislocated()], 17.0)
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
            occurrence_ts=time.time(),
        )
        client = MagicMock()
        client.list_markets.return_value = ([flat], 9.0)
        runner = PaperRunner(self.cfg, client=client)
        self.assertEqual(runner.run_once(), [])

    def test_run_once_skips_upcoming_when_live_matches_only(self):
        upcoming = MarketSnapshot(
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
            occurrence_ts=time.time() + 6 * 3600,
        )
        seeded = new_state(self.cfg, day="2026-09-06")
        seeded.ema[upcoming.ticker] = 0.60
        save_state(self.cfg, seeded)
        client = MagicMock()
        client.list_markets.return_value = ([upcoming], 11.0)
        runner = PaperRunner(self.cfg, client=client)
        self.assertEqual(runner.run_once(), [])
        self.assertEqual(runner.last_scan["live"], 0)
        self.assertEqual(runner.last_scan["open"], 1)

    def test_run_once_paper_fills_bitcoin_buy_when_live_tennis_empty(self):
        btc = MarketSnapshot(
            ticker="KXBTC15M-26SEP060015-15",
            event_ticker="KXBTC15M-26SEP060015",
            event_name="BTC 15 min",
            title="Target Price: $80000",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            volume=200.0,
            updated_ts=time.time(),
            status="active",
            series_ticker="KXBTC15M",
            occurrence_ts=time.time() + 6 * 3600,
        )
        seeded = new_state(self.cfg, day="2026-09-06")
        seeded.ema[btc.ticker] = 0.60
        save_state(self.cfg, seeded)
        client = MagicMock()
        client.list_markets.return_value = ([btc], 8.0)
        runner = PaperRunner(self.cfg, client=client)
        fills = runner.run_once()
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].side, "buy")
        self.assertEqual(fills[0].mode, "paper")
        self.assertIn("KXBTC15M", fills[0].ticker)
        self.assertEqual(runner.last_scan["bitcoin"], 1)
        self.assertEqual(runner.last_scan["live"], 0)

    def test_run_once_skips_far_otm_bitcoin_strikes(self):
        far = MarketSnapshot(
            ticker="KXBTCD-26SEP0601-T70099.99",
            event_ticker="KXBTCD-26SEP0601",
            event_name="BTC price",
            title="Above 70099",
            yes_bid=0.990,
            yes_ask=1.000,
            last_price=0.995,
            volume=10.0,
            updated_ts=time.time(),
            status="active",
            series_ticker="KXBTCD",
        )
        seeded = new_state(self.cfg, day="2026-09-06")
        seeded.ema[far.ticker] = 0.50
        save_state(self.cfg, seeded)
        client = MagicMock()
        client.list_markets.return_value = ([far], 8.0)
        runner = PaperRunner(self.cfg, client=client)
        self.assertEqual(runner.run_once(), [])
        self.assertEqual(runner.last_scan["bitcoin"], 0)

    def test_run_once_targets_selected_tennis_match_only(self):
        from dataclasses import replace

        wanted = MarketSnapshot(
            ticker="KXATPMATCH-26SEP06CERBLO-CER",
            event_ticker="KXATPMATCH-26SEP06CERBLO",
            event_name="Cerundolo vs Blockx",
            title="Cerundolo wins",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            volume=50.0,
            updated_ts=time.time(),
            status="active",
            series_ticker="KXATPMATCH",
            occurrence_ts=time.time() + 6 * 3600,
        )
        other = MarketSnapshot(
            ticker="KXATPMATCH-26SEP06FOOBAR-FOO",
            event_ticker="KXATPMATCH-26SEP06FOOBAR",
            event_name="Foo vs Bar",
            title="Foo wins",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            volume=50.0,
            updated_ts=time.time(),
            status="active",
            series_ticker="KXATPMATCH",
            occurrence_ts=time.time(),
        )
        cfg = replace(
            self.cfg,
            target_event_ticker="KXATPMATCH-26SEP06CERBLO",
            live_matches_only=True,
            trade_bitcoin=True,
        )
        seeded = new_state(cfg, day="2026-09-06")
        seeded.ema[wanted.ticker] = 0.60
        seeded.ema[other.ticker] = 0.60
        save_state(cfg, seeded)
        client = MagicMock()
        client.list_markets.return_value = ([wanted, other], 9.0)
        runner = PaperRunner(cfg, client=client)
        fills = runner.run_once()
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].ticker, wanted.ticker)
        self.assertEqual(runner.last_scan["targeted"], 1)

    def test_run_once_targets_selected_bitcoin_event_only(self):
        from dataclasses import replace

        wanted = MarketSnapshot(
            ticker="KXBTC15M-26SEP061845-00",
            event_ticker="KXBTC15M-26SEP061845",
            event_name="BTC 15 min",
            title="BTC price up",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            volume=50.0,
            updated_ts=time.time(),
            status="active",
            series_ticker="KXBTC15M",
        )
        other = MarketSnapshot(
            ticker="KXBTC15M-26SEP061900-00",
            event_ticker="KXBTC15M-26SEP061900",
            event_name="BTC later",
            title="BTC price up",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            volume=50.0,
            updated_ts=time.time(),
            status="active",
            series_ticker="KXBTC15M",
        )
        cfg = replace(
            self.cfg,
            target_event_ticker="KXBTC15M-26SEP061845",
            target_asset_class="bitcoin",
            trade_bitcoin=True,
            trade_tennis=True,
        )
        seeded = new_state(cfg, day="2026-09-06")
        seeded.ema[wanted.ticker] = 0.60
        seeded.ema[other.ticker] = 0.60
        save_state(cfg, seeded)
        client = MagicMock()
        client.list_markets.return_value = ([wanted, other], 9.0)
        runner = PaperRunner(cfg, client=client)
        fills = runner.run_once()
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].ticker, wanted.ticker)
        self.assertEqual(runner.last_scan["targeted"], 1)

    def test_select_targeted_tennis_filters_event_and_market(self):
        from rk_kalshi.models import select_targeted_tennis

        match = MarketSnapshot(
            ticker="KXATPMATCH-26SEP06CERBLO-CER",
            event_ticker="KXATPMATCH-26SEP06CERBLO",
            event_name="Cerundolo vs Blockx",
            title="Cerundolo wins",
            yes_bid=0.40,
            yes_ask=0.41,
            last_price=0.40,
            volume=10.0,
            updated_ts=time.time(),
            status="active",
            series_ticker="KXATPMATCH",
        )
        other = MarketSnapshot(
            ticker="KXATPMATCH-26SEP06FOOBAR-FOO",
            event_ticker="KXATPMATCH-26SEP06FOOBAR",
            event_name="Foo vs Bar",
            title="Foo wins",
            yes_bid=0.40,
            yes_ask=0.41,
            last_price=0.40,
            volume=10.0,
            updated_ts=time.time(),
            status="active",
            series_ticker="KXATPMATCH",
        )
        event_only = select_targeted_tennis(
            [match, other], event_ticker="KXATPMATCH-26SEP06CERBLO"
        )
        self.assertEqual([m.ticker for m in event_only], [match.ticker])
        market_only = select_targeted_tennis(
            [match, other], market_ticker="KXATPMATCH-26SEP06CERBLO-CER"
        )
        self.assertEqual([m.ticker for m in market_only], [match.ticker])


if __name__ == "__main__":
    unittest.main()
