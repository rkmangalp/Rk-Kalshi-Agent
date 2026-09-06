import unittest

from rk_kalshi.config import AppConfig
from rk_kalshi.models import MarketSnapshot
from rk_kalshi.signal import TennisSignalEngine


def _market(**overrides) -> MarketSnapshot:
    data = dict(
        ticker="KXATPMATCH-26SEP06FOO-FOO",
        event_ticker="KXATPMATCH-26SEP06FOO",
        event_name="Foo vs Bar",
        title="Foo wins",
        yes_bid=0.40,
        yes_ask=0.42,
        last_price=0.41,
        volume=100.0,
        updated_ts=1_000_000.0,
        status="active",
        series_ticker="KXATPMATCH",
    )
    data.update(overrides)
    return MarketSnapshot(**data)


class SignalEngineTests(unittest.TestCase):
    def setUp(self):
        self.cfg = AppConfig(edge_threshold_cents=3.0, max_spread_cents=8.0)
        self.engine = TennisSignalEngine(self.cfg)

    def test_buy_when_ema_is_well_above_mid_after_costs(self):
        # Prior EMA above a tight mid; last stays near mid so it is not stale.
        self.engine.load_ema({"KXATPMATCH-26SEP06FOO-FOO": 0.60})
        market = _market(yes_bid=0.395, yes_ask=0.405, last_price=0.40)
        signal = self.engine.evaluate_one(market, now=1_000_000.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "buy")
        self.assertGreaterEqual(signal.edge_cents, 3.0)
        self.assertAlmostEqual(signal.edge_bps, signal.edge_cents * 100.0)
        self.assertAlmostEqual(signal.fill_price, signal.live_mid)
        self.assertEqual(signal.match_id, "KXATPMATCH-26SEP06FOO")
        self.assertEqual(signal.event_name, "Foo vs Bar")
        self.assertIn("BUY YES", signal.edge_thesis)
        self.assertIn("net edge", signal.edge_thesis)

    def test_sell_when_ema_is_well_below_mid_after_costs(self):
        self.engine.load_ema({"KXATPMATCH-26SEP06FOO-FOO": 0.40})
        market = _market(yes_bid=0.595, yes_ask=0.605, last_price=0.60)
        signal = self.engine.evaluate_one(market, now=1_000_000.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "sell")
        self.assertGreaterEqual(signal.edge_cents, 3.0)
        self.assertIn("SELL YES", signal.edge_thesis)

    def test_bitcoin_buy_and_sell_yes(self):
        buy_mkt = _market(
            ticker="KXBTC15M-26SEP060015-15",
            event_ticker="KXBTC15M-26SEP060015",
            event_name="BTC 15 min",
            title="Up",
            series_ticker="KXBTC15M",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
        )
        self.engine.load_ema({buy_mkt.ticker: 0.60})
        buy = self.engine.evaluate_one(buy_mkt, now=1_000_000.0)
        self.assertIsNotNone(buy)
        self.assertEqual(buy.side, "buy")
        self.assertIn("BUY YES", buy.edge_thesis)
        self.assertIn("bitcoin", buy.edge_thesis.lower())

        sell_mkt = _market(
            ticker="KXBTC15M-26SEP060030-15",
            event_ticker="KXBTC15M-26SEP060030",
            event_name="BTC 15 min",
            title="Up",
            series_ticker="KXBTC15M",
            yes_bid=0.595,
            yes_ask=0.605,
            last_price=0.60,
        )
        self.engine.load_ema({sell_mkt.ticker: 0.40})
        sell = self.engine.evaluate_one(sell_mkt, now=1_000_000.0)
        self.assertIsNotNone(sell)
        self.assertEqual(sell.side, "sell")
        self.assertIn("SELL YES", sell.edge_thesis)

    def test_no_signal_when_last_equals_mid(self):
        market = _market(yes_bid=0.50, yes_ask=0.50, last_price=0.50)
        self.assertIsNone(self.engine.evaluate_one(market, now=1_000_000.0))

    def test_ignores_stale_last_print_far_from_tight_mid(self):
        # 20¢ last-vs-mid on a 1¢ book is tape lag, not a 3¢ edge.
        market = _market(yes_bid=0.690, yes_ask=0.700, last_price=0.490)
        self.assertIsNone(self.engine.evaluate_one(market, now=1_000_000.0))

    def test_skips_wide_spread(self):
        market = _market(yes_bid=0.30, yes_ask=0.50, last_price=0.60)
        self.assertIsNone(self.engine.evaluate_one(market, now=1_000_000.0))

    def test_skips_one_sided_book(self):
        market = _market(yes_bid=0.0, yes_ask=0.40, last_price=0.55)
        self.assertIsNone(self.engine.evaluate_one(market, now=1_000_000.0))

    def test_skips_stale_wideish_mid(self):
        market = _market(yes_bid=0.40, yes_ask=0.44, last_price=0.55, updated_ts=1.0)
        self.assertIsNone(self.engine.evaluate_one(market, now=1_000.0))

    def test_evaluate_sorts_by_edge_desc(self):
        self.engine.load_ema({"STRONG": 0.62, "MILD": 0.50})
        strong = _market(ticker="STRONG", yes_bid=0.395, yes_ask=0.405, last_price=0.40)
        mild = _market(ticker="MILD", yes_bid=0.395, yes_ask=0.405, last_price=0.40)
        signals = self.engine.evaluate([mild, strong], now=1_000_000.0)
        self.assertGreaterEqual(len(signals), 1)
        self.assertEqual(signals[0].ticker, "STRONG")


if __name__ == "__main__":
    unittest.main()
