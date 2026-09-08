import unittest

from rk_kalshi.config import AppConfig
from rk_kalshi.models import MarketSnapshot, Position
from rk_kalshi.signal import (
    SIGNAL_ALGORITHM,
    SIGNAL_DISCLAIMER,
    SignalEngine,
    TennisSignalEngine,
    order_book_imbalance,
    reservation_price,
    rolling_sigma,
    time_to_close_frac,
)


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
        yes_bid_size=0.0,
        yes_ask_size=0.0,
    )
    data.update(overrides)
    return MarketSnapshot(**data)


class ReservationHelpersTests(unittest.TestCase):
    def test_long_inventory_lowers_reservation(self):
        mid = 0.50
        long_r = reservation_price(mid, inventory_q=10, gamma=0.5, sigma=0.1, t_frac=1.0)
        flat_r = reservation_price(mid, inventory_q=0, gamma=0.5, sigma=0.1, t_frac=1.0)
        short_r = reservation_price(mid, inventory_q=-10, gamma=0.5, sigma=0.1, t_frac=1.0)
        self.assertLess(long_r, flat_r)
        self.assertGreater(short_r, flat_r)
        self.assertAlmostEqual(flat_r, mid)

    def test_obi_sign_and_zero_when_sizes_missing(self):
        self.assertGreater(order_book_imbalance(100.0, 10.0), 0.0)
        self.assertLess(order_book_imbalance(10.0, 100.0), 0.0)
        self.assertEqual(order_book_imbalance(0.0, 0.0), 0.0)
        self.assertAlmostEqual(order_book_imbalance(80.0, 20.0), 0.60)

    def test_sigma_floor_and_dispersion(self):
        self.assertEqual(rolling_sigma([0.40], 0.04), 0.04)
        self.assertGreater(rolling_sigma([0.30, 0.50, 0.70], 0.01), 0.04)

    def test_t_frac_missing_close_is_one_expired_is_zero(self):
        self.assertEqual(time_to_close_frac(None, now=100.0, horizon_s=100.0), 1.0)
        self.assertEqual(time_to_close_frac(50.0, now=100.0, horizon_s=100.0), 0.0)
        self.assertAlmostEqual(time_to_close_frac(150.0, now=100.0, horizon_s=100.0), 0.5)


class SignalEngineTests(unittest.TestCase):
    def setUp(self):
        self.cfg = AppConfig(
            edge_threshold_cents=3.0,
            max_spread_cents=8.0,
            gamma=0.5,
            kappa=10.0,
            sigma_floor=0.10,
            use_ema_fallback=False,
        )
        self.engine = SignalEngine(self.cfg)

    def test_alias_still_exports_tennis_name(self):
        self.assertIs(TennisSignalEngine, SignalEngine)
        self.assertIn("Avellaneda", SIGNAL_ALGORITHM)
        self.assertIn("no guaranteed", SIGNAL_DISCLAIMER.lower())

    def test_long_inventory_prefers_sell(self):
        market = _market(yes_bid=0.495, yes_ask=0.505, last_price=0.50)
        signal = self.engine.evaluate_one(market, inventory_q=20, now=1_000_000.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "sell")
        self.assertLess(signal.fair_yes, signal.live_mid)
        self.assertGreaterEqual(signal.edge_cents, 3.0)
        self.assertIn("SELL YES", signal.edge_thesis)
        self.assertIn("Avellaneda", signal.edge_thesis)
        self.assertIn("not a match pick", signal.edge_thesis.lower())

    def test_short_inventory_prefers_buy(self):
        market = _market(yes_bid=0.495, yes_ask=0.505, last_price=0.50)
        signal = self.engine.evaluate_one(market, inventory_q=-20, now=1_000_000.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "buy")
        self.assertGreater(signal.fair_yes, signal.live_mid)
        self.assertIn("BUY YES", signal.edge_thesis)

    def test_inventory_mapping_and_position_objects(self):
        market = _market(yes_bid=0.495, yes_ask=0.505, last_price=0.50)
        from_map = self.engine.evaluate_one(
            market, inventory={market.ticker: 20}, now=1_000_000.0
        )
        from_pos = self.engine.evaluate_one(
            market,
            inventory={market.ticker: Position(contracts=20, avg_price=0.50)},
            now=1_000_000.0,
        )
        self.assertIsNotNone(from_map)
        self.assertEqual(from_map.side, "sell")
        self.assertEqual(from_pos.side, "sell")

    def test_positive_obi_tilts_fair_up_and_buys(self):
        market = _market(
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            yes_bid_size=2000.0,
            yes_ask_size=20.0,
        )
        signal = self.engine.evaluate_one(market, inventory_q=0, now=1_000_000.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "buy")
        self.assertGreater(signal.fair_yes, signal.live_mid)
        self.assertGreaterEqual(signal.edge_cents, 3.0)
        self.assertIn("OBI=", signal.edge_thesis)

    def test_negative_obi_tilts_fair_down_and_sells(self):
        market = _market(
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            yes_bid_size=20.0,
            yes_ask_size=2000.0,
        )
        signal = self.engine.evaluate_one(market, inventory_q=0, now=1_000_000.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "sell")
        self.assertLess(signal.fair_yes, signal.live_mid)

    def test_fee_gate_blocks_tiny_edges(self):
        # Flat book, no inventory, no size → fair = mid; costs kill the trade.
        flat = _market(yes_bid=0.50, yes_ask=0.50, last_price=0.50)
        self.assertIsNone(self.engine.evaluate_one(flat, inventory_q=0, now=1_000_000.0))
        # Tiny OBI on a 1¢ book cannot clear half-spread + quadratic fee + 3¢.
        tiny = _market(
            yes_bid=0.495,
            yes_ask=0.505,
            last_price=0.50,
            yes_bid_size=51.0,
            yes_ask_size=49.0,
        )
        quiet = SignalEngine(
            AppConfig(edge_threshold_cents=3.0, gamma=0.25, kappa=1.0, sigma_floor=0.04)
        )
        self.assertIsNone(quiet.evaluate_one(tiny, inventory_q=0, now=1_000_000.0))

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
            yes_bid_size=2000.0,
            yes_ask_size=20.0,
        )
        buy = self.engine.evaluate_one(buy_mkt, now=1_000_000.0)
        self.assertIsNotNone(buy)
        self.assertEqual(buy.side, "buy")
        self.assertIn("BUY YES", buy.edge_thesis)
        self.assertIn("bitcoin", buy.edge_thesis.lower())
        self.assertIn("not financial advice", buy.edge_thesis.lower())

        sell_mkt = _market(
            ticker="KXBTC15M-26SEP060030-15",
            event_ticker="KXBTC15M-26SEP060030",
            event_name="BTC 15 min",
            title="Up",
            series_ticker="KXBTC15M",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            yes_bid_size=20.0,
            yes_ask_size=2000.0,
        )
        sell = self.engine.evaluate_one(sell_mkt, now=1_000_000.0)
        self.assertIsNotNone(sell)
        self.assertEqual(sell.side, "sell")
        self.assertIn("SELL YES", sell.edge_thesis)

    def test_no_signal_when_fair_equals_mid(self):
        market = _market(yes_bid=0.50, yes_ask=0.50, last_price=0.50)
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
        strong = _market(
            ticker="STRONG",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            yes_bid_size=5000.0,
            yes_ask_size=10.0,
        )
        mild = _market(
            ticker="MILD",
            yes_bid=0.395,
            yes_ask=0.405,
            last_price=0.40,
            yes_bid_size=200.0,
            yes_ask_size=100.0,
        )
        signals = self.engine.evaluate([mild, strong], now=1_000_000.0)
        self.assertGreaterEqual(len(signals), 1)
        self.assertEqual(signals[0].ticker, "STRONG")

    def test_ema_fallback_used_only_when_inventory_and_obi_idle(self):
        cfg = AppConfig(
            edge_threshold_cents=3.0,
            use_ema_fallback=True,
            gamma=0.25,
            kappa=1.0,
            sigma_floor=0.04,
        )
        engine = SignalEngine(cfg)
        ticker = "KXATPMATCH-26SEP06FOO-FOO"
        engine.load_ema({ticker: 0.60})
        market = _market(yes_bid=0.395, yes_ask=0.405, last_price=0.40)
        signal = engine.evaluate_one(market, inventory_q=0, now=1_000_000.0)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "buy")
        self.assertIn("EMA fallback", signal.edge_thesis)

    def test_expired_t_frac_kills_inventory_skew(self):
        market = _market(
            yes_bid=0.495,
            yes_ask=0.505,
            last_price=0.50,
            close_ts=1.0,
        )
        signal = self.engine.evaluate_one(market, inventory_q=20, now=1_000_000.0)
        self.assertIsNone(signal)

    def test_pair_lock_sells_yes_when_no_is_cheap(self):
        # Botic-style: long YES at 53¢, NO offered at 19¢ → YES bid ~81¢.
        market = _market(yes_bid=0.81, yes_ask=0.83, last_price=0.82)
        pos = Position(contracts=62, avg_price=0.53)
        signal = self.engine.evaluate_one(
            market, inventory={market.ticker: pos}, now=1_000_000.0
        )
        self.assertIsNotNone(signal)
        self.assertTrue(signal.pair_lock)
        self.assertEqual(signal.side, "sell")
        self.assertEqual(signal.contracts, 62)
        self.assertAlmostEqual(signal.fill_price, 0.81)
        self.assertGreater(signal.edge_cents, 20.0)
        self.assertIn("PAIR LOCK", signal.edge_thesis)

    def test_pair_lock_skips_when_other_side_is_not_cheap(self):
        market = _market(yes_bid=0.495, yes_ask=0.505, last_price=0.50)
        pos = Position(contracts=20, avg_price=0.50)
        signal = self.engine.evaluate_one(
            market, inventory={market.ticker: pos}, now=1_000_000.0
        )
        self.assertIsNotNone(signal)
        self.assertFalse(signal.pair_lock)
        self.assertEqual(signal.side, "sell")

    def test_live_holds_open_until_pair_lock(self):
        live = SignalEngine(AppConfig(live_enabled=True, edge_threshold_cents=3.0, kappa=10.0, gamma=0.5))
        market = _market(yes_bid=0.54, yes_ask=0.56, last_price=0.55)
        pos = Position(contracts=10, avg_price=0.53)
        self.assertIsNone(
            live.evaluate_one(market, inventory={market.ticker: pos}, now=1_000_000.0)
        )
        locked = live.evaluate_one(
            _market(yes_bid=0.81, yes_ask=0.83, last_price=0.82, ticker=market.ticker),
            inventory={market.ticker: pos},
            now=1_000_000.0,
        )
        self.assertIsNotNone(locked)
        self.assertTrue(locked.pair_lock)

    def test_evaluate_passes_inventory_avg_price_for_pair_lock(self):
        from rk_kalshi.state import new_state

        market = _market(yes_bid=0.81, yes_ask=0.83, last_price=0.82)
        state = new_state(self.cfg, day="2026-09-08")
        state.positions[market.ticker] = Position(contracts=62, avg_price=0.53)
        signals = self.engine.evaluate([market], inventory=state, now=1_000_000.0)
        self.assertEqual(len(signals), 1)
        self.assertTrue(signals[0].pair_lock)
        self.assertEqual(signals[0].contracts, 62)


if __name__ == "__main__":
    unittest.main()
