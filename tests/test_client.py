import unittest

from rk_kalshi.client import _snapshots_from_event


class ClientSnapshotTests(unittest.TestCase):
    def test_parses_kalshi_bid_ask_size_fp_and_close_time(self):
        event = {
            "event_ticker": "KXBTC15M-26SEP062245",
            "series_ticker": "KXBTC15M",
            "title": "BTC 15 min",
            "markets": [
                {
                    "ticker": "KXBTC15M-26SEP062245-45",
                    "event_ticker": "KXBTC15M-26SEP062245",
                    "title": "BTC price up in next 15 mins?",
                    "status": "active",
                    "yes_bid_dollars": "0.4000",
                    "yes_ask_dollars": "0.4100",
                    "last_price_dollars": "0.4050",
                    "volume_fp": "100.00",
                    "yes_bid_size_fp": "1245.01",
                    "yes_ask_size_fp": "371.84",
                    "close_time": "2026-09-07T02:45:00Z",
                    "occurrence_datetime": "2026-09-07T02:50:00Z",
                    "updated_time": "2026-09-07T02:30:00Z",
                }
            ],
        }
        snaps = _snapshots_from_event(event, "KXBTC15M")
        self.assertEqual(len(snaps), 1)
        market = snaps[0]
        self.assertAlmostEqual(market.yes_bid_size, 1245.01)
        self.assertAlmostEqual(market.yes_ask_size, 371.84)
        self.assertGreater(market.order_book_imbalance, 0.0)
        self.assertIsNotNone(market.close_ts)
        self.assertIsNotNone(market.occurrence_ts)

    def test_missing_sizes_are_zero_imbalance(self):
        event = {
            "event_ticker": "KXATPMATCH-X",
            "series_ticker": "KXATPMATCH",
            "title": "A vs B",
            "markets": [
                {
                    "ticker": "KXATPMATCH-X-A",
                    "status": "open",
                    "yes_bid_dollars": "0.40",
                    "yes_ask_dollars": "0.42",
                    "last_price_dollars": "0.41",
                    "volume_fp": "1",
                }
            ],
        }
        market = _snapshots_from_event(event, "KXATPMATCH")[0]
        self.assertEqual(market.yes_bid_size, 0.0)
        self.assertEqual(market.yes_ask_size, 0.0)
        self.assertEqual(market.order_book_imbalance, 0.0)


if __name__ == "__main__":
    unittest.main()
