import time
import unittest
from dataclasses import replace

from rk_kalshi.catalog import (
    apply_category,
    catalog_events,
    category_id_for_series,
    filter_markets_by_category,
    trade_flags_for_category,
)
from rk_kalshi.config import AppConfig
from rk_kalshi.models import MarketSnapshot


def _snap(
    ticker: str,
    series: str,
    event: str,
    name: str,
    *,
    occurrence_ts=None,
    yes_bid=0.40,
    yes_ask=0.42,
) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=ticker,
        event_ticker=event,
        event_name=name,
        title=name,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        last_price=(yes_bid + yes_ask) / 2.0,
        volume=10.0,
        updated_ts=time.time(),
        status="open",
        series_ticker=series,
        occurrence_ts=occurrence_ts,
    )


class CatalogFilterTests(unittest.TestCase):
    def setUp(self):
        now = 1_800_000_000.0
        self.now = now
        self.live_atp = _snap(
            "KXATPMATCH-LIVE-A",
            "KXATPMATCH",
            "KXATPMATCH-LIVE",
            "Live ATP",
            occurrence_ts=now,
        )
        self.upcoming_atp = _snap(
            "KXATPMATCH-NEXT-A",
            "KXATPMATCH",
            "KXATPMATCH-NEXT",
            "Upcoming ATP",
            occurrence_ts=now + 3 * 3600,
        )
        self.live_wta = _snap(
            "KXWTAMATCH-LIVE-A",
            "KXWTAMATCH",
            "KXWTAMATCH-LIVE",
            "Live WTA",
            occurrence_ts=now,
        )
        self.challenger = _snap(
            "KXATPCHALLENGERMATCH-LIVE-A",
            "KXATPCHALLENGERMATCH",
            "KXATPCHALLENGERMATCH-LIVE",
            "Live Challenger",
            occurrence_ts=now,
        )
        self.btc15 = _snap(
            "KXBTC15M-NOW-00",
            "KXBTC15M",
            "KXBTC15M-NOW",
            "BTC 15m",
        )
        self.btc_lottery = _snap(
            "KXBTCD-FAR-T9",
            "KXBTCD",
            "KXBTCD-FAR",
            "BTC daily far",
            yes_bid=0.99,
            yes_ask=1.00,
        )
        self.btc_near = _snap(
            "KXBTCD-NEAR-T1",
            "KXBTCD",
            "KXBTCD-NEAR",
            "BTC daily near",
            yes_bid=0.48,
            yes_ask=0.52,
        )
        self.markets = [
            self.live_atp,
            self.upcoming_atp,
            self.live_wta,
            self.challenger,
            self.btc15,
            self.btc_lottery,
            self.btc_near,
        ]

    def test_series_maps_to_category(self):
        self.assertEqual(category_id_for_series("KXATPMATCH"), "atp")
        self.assertEqual(category_id_for_series("KXATPCHALLENGERMATCH"), "challenger")
        self.assertEqual(category_id_for_series("KXITFMMATCH"), "itf")
        self.assertEqual(category_id_for_series("KXBTC15M"), "btc15m")
        self.assertEqual(category_id_for_series("KXBTCD"), "btcd")

    def test_atp_filter_keeps_atp_and_drops_wta_and_bitcoin(self):
        kept = filter_markets_by_category(self.markets, "atp")
        tickers = {m.ticker for m in kept}
        self.assertEqual(tickers, {self.live_atp.ticker, self.upcoming_atp.ticker})

    def test_catalog_events_live_only_excludes_upcoming_atp(self):
        events = catalog_events(self.markets, self.now, category_id="atp", live_only=True)
        tickers = {row["event_ticker"] for row in events}
        self.assertEqual(tickers, {"KXATPMATCH-LIVE"})
        self.assertTrue(all(row["live"] for row in events))
        self.assertEqual(events[0]["category_id"], "atp")

    def test_challenger_and_bitcoin_categories(self):
        challenger = catalog_events(self.markets, self.now, category_id="challenger")
        self.assertEqual([row["event_ticker"] for row in challenger], ["KXATPCHALLENGERMATCH-LIVE"])
        btc15 = catalog_events(self.markets, self.now, category_id="btc15m")
        self.assertEqual([row["event_ticker"] for row in btc15], ["KXBTC15M-NOW"])
        daily = catalog_events(self.markets, self.now, category_id="btcd")
        self.assertEqual([row["event_ticker"] for row in daily], ["KXBTCD-NEAR"])

    def test_apply_category_sets_flags_and_narrows_series(self):
        cfg = AppConfig()
        atp = apply_category(cfg, "atp")
        self.assertEqual(atp.target_category_id, "atp")
        self.assertTrue(atp.trade_tennis)
        self.assertFalse(atp.trade_bitcoin)
        self.assertEqual(atp.series_tickers, ("KXATPMATCH",))
        self.assertFalse(atp.live_enabled)
        self.assertEqual(trade_flags_for_category("btc15m"), (False, True))
        btc = apply_category(cfg, "btc15m")
        self.assertFalse(btc.trade_tennis)
        self.assertTrue(btc.trade_bitcoin)
        self.assertEqual(btc.bitcoin_series_tickers, ("KXBTC15M",))

    def test_all_category_keeps_both_books(self):
        tennis, bitcoin = trade_flags_for_category("all")
        self.assertTrue(tennis)
        self.assertTrue(bitcoin)
        cfg = apply_category(AppConfig(), "all")
        self.assertIn("KXATPCHALLENGERMATCH", cfg.series_tickers)
        restored = replace(cfg, live_enabled=False)
        self.assertFalse(restored.live_enabled)


if __name__ == "__main__":
    unittest.main()
