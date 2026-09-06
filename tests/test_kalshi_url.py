import unittest

from rk_kalshi.kalshi_url import EXAMPLE_URLS, KalshiTennisUrlError, parse_tennis_contract


class KalshiUrlTests(unittest.TestCase):
    def test_parses_atp_event_page(self):
        parsed = parse_tennis_contract(EXAMPLE_URLS[0])
        self.assertEqual(parsed.series_ticker, "KXATPMATCH")
        self.assertEqual(parsed.event_ticker, "KXATPMATCH-26SEP06CERBLO")
        self.assertEqual(parsed.match_id, "KXATPMATCH-26SEP06CERBLO")
        self.assertIsNone(parsed.market_ticker)
        self.assertEqual(parsed.source, "url")

    def test_parses_wta_event_page(self):
        parsed = parse_tennis_contract(
            "https://www.kalshi.com/markets/kxwtamatch/wta-tennis-match/kxwtamatch-26mar29vekgor"
        )
        self.assertEqual(parsed.event_ticker, "KXWTAMATCH-26MAR29VEKGOR")
        self.assertEqual(parsed.series_ticker, "KXWTAMATCH")

    def test_parses_market_level_path(self):
        parsed = parse_tennis_contract(
            "https://kalshi.com/markets/kxatpmatch/atp-tennis-match/"
            "kxatpmatch-26apr05atmtia/kxatpmatch-26apr05atmtia-atm"
        )
        self.assertEqual(parsed.event_ticker, "KXATPMATCH-26APR05ATMTIA")
        self.assertEqual(parsed.market_ticker, "KXATPMATCH-26APR05ATMTIA-ATM")

    def test_parses_query_ticker_and_scheme_less_host(self):
        parsed = parse_tennis_contract(
            "kalshi.com/markets/kxatpmatch/atp-tennis-match?ticker=KXATPMATCH-26SEP06CERBLO-CER"
        )
        self.assertEqual(parsed.market_ticker, "KXATPMATCH-26SEP06CERBLO-CER")
        self.assertEqual(parsed.match_id, "KXATPMATCH-26SEP06CERBLO")

    def test_parses_bare_event_and_market_tickers(self):
        event = parse_tennis_contract("  kxatpmatch-26sep06cerblo  ")
        self.assertEqual(event.event_ticker, "KXATPMATCH-26SEP06CERBLO")
        self.assertEqual(event.source, "ticker")
        market = parse_tennis_contract("KXITFWMATCH-26SEP06KURSID-KUR")
        self.assertEqual(market.series_ticker, "KXITFWMATCH")
        self.assertEqual(market.market_ticker, "KXITFWMATCH-26SEP06KURSID-KUR")

    def test_rejects_series_only_page(self):
        with self.assertRaises(KalshiTennisUrlError) as ctx:
            parse_tennis_contract("https://kalshi.com/markets/kxatpmatch")
        self.assertIn("series page", str(ctx.exception))

    def test_rejects_bitcoin_and_foreign_urls(self):
        with self.assertRaises(KalshiTennisUrlError) as ctx:
            parse_tennis_contract(
                "https://kalshi.com/markets/kxbtc15m/bitcoin-price-up-down/kxbtc15m-26sep060015"
            )
        self.assertIn("not a tennis match", str(ctx.exception))
        with self.assertRaises(KalshiTennisUrlError):
            parse_tennis_contract("https://example.com/markets/kxatpmatch-26sep06cerblo")

    def test_parses_events_path_api_host_and_trailing_slash(self):
        page = parse_tennis_contract(
            "https://kalshi.com/events/kxatpmatch-26sep06cerblo/"
        )
        self.assertEqual(page.event_ticker, "KXATPMATCH-26SEP06CERBLO")
        api = parse_tennis_contract(
            "https://external-api.kalshi.com/trade-api/v2/markets/"
            "KXATPMATCH-26SEP06CERBLO-CER"
        )
        self.assertEqual(api.market_ticker, "KXATPMATCH-26SEP06CERBLO-CER")
        self.assertEqual(api.match_id, "KXATPMATCH-26SEP06CERBLO")
        home = parse_tennis_contract(
            "https://trading.kalshi.com/?event_ticker=kxatpmatch-26sep06medtia"
        )
        self.assertEqual(home.event_ticker, "KXATPMATCH-26SEP06MEDTIA")

    def test_rejects_empty_and_garbage(self):
        with self.assertRaises(KalshiTennisUrlError):
            parse_tennis_contract("")
        with self.assertRaises(KalshiTennisUrlError):
            parse_tennis_contract("please trade nadal")


if __name__ == "__main__":
    unittest.main()
