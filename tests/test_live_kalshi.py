"""Optional live check against Kalshi public tennis markets. Skips if offline."""

from __future__ import annotations

import unittest

import httpx

from rk_kalshi.config import load_config
from rk_kalshi.client import KalshiPublicClient


class LiveKalshiTennisTests(unittest.TestCase):
    def test_public_tennis_markets_shape(self):
        cfg = load_config()
        try:
            with KalshiPublicClient(cfg) as client:
                markets, latency_ms = client.list_tennis_markets()
        except httpx.HTTPError as exc:
            self.skipTest(f"Kalshi public API unavailable: {exc}")

        self.assertGreater(latency_ms, 0.0)
        if not markets:
            self.skipTest("no open tennis markets right now")
        sample = markets[0]
        self.assertTrue(sample.ticker)
        self.assertTrue(sample.event_ticker)
        self.assertTrue(sample.event_name)
        self.assertEqual(sample.match_id, sample.event_ticker)
        self.assertGreaterEqual(sample.yes_bid, 0.0)
        self.assertGreaterEqual(sample.yes_ask, 0.0)


if __name__ == "__main__":
    unittest.main()
