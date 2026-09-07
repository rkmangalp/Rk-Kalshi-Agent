import unittest

from rk_kalshi.kalshi_url import (
    EXAMPLE_CHALLENGER_URL,
    EXAMPLE_CRYPTO_URLS,
    EXAMPLE_URLS,
    KalshiTennisUrlError,
    KalshiUrlError,
    parse_contract,
    parse_crypto_contract,
    parse_tennis_contract,
)


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

    def test_parse_contract_accepts_btc_15m_url(self):
        parsed = parse_contract(EXAMPLE_CRYPTO_URLS[0])
        self.assertEqual(parsed.asset_class, "bitcoin")
        self.assertEqual(parsed.series_ticker, "KXBTC15M")
        self.assertEqual(parsed.event_ticker, "KXBTC15M-26SEP061845")
        self.assertIsNone(parsed.market_ticker)
        market = parse_contract(
            "https://kalshi.com/markets/kxbtc15m/bitcoin-price-up-down/"
            "kxbtc15m-26sep061900/kxbtc15m-26sep061900-00"
        )
        self.assertEqual(market.market_ticker, "KXBTC15M-26SEP061900-00")
        self.assertEqual(market.event_ticker, "KXBTC15M-26SEP061900")
        bare = parse_crypto_contract("KXBTC15M-26SEP061845")
        self.assertEqual(bare.event_ticker, "KXBTC15M-26SEP061845")
        daily = parse_contract("KXBTCD-26SEP0601-T70099.99")
        self.assertEqual(daily.market_ticker, "KXBTCD-26SEP0601-T70099.99")
        eth = parse_contract("KXETH15M-26SEP061845")
        self.assertEqual(eth.asset_class, "bitcoin")
        self.assertEqual(eth.series_ticker, "KXETH15M")

    def test_parse_contract_accepts_challenger_and_other_series(self):
        parsed = parse_contract(EXAMPLE_CHALLENGER_URL)
        self.assertEqual(parsed.series_ticker, "KXATPCHALLENGERMATCH")
        self.assertEqual(parsed.event_ticker, "KXATPCHALLENGERMATCH-26SEP06KIMTAM")
        self.assertIsNone(parsed.market_ticker)
        self.assertEqual(parsed.asset_class, "tennis")
        market = parse_contract("KXATPCHALLENGERMATCH-26SEP06KIMTAM-KIM")
        self.assertEqual(market.market_ticker, "KXATPCHALLENGERMATCH-26SEP06KIMTAM-KIM")
        self.assertEqual(market.event_ticker, "KXATPCHALLENGERMATCH-26SEP06KIMTAM")
        nfl = parse_contract(
            "https://kalshi.com/markets/kxnhlgame/nhl-game/kxnhlgame-26sep06edmtor"
        )
        self.assertEqual(nfl.series_ticker, "KXNHLGAME")
        self.assertEqual(nfl.event_ticker, "KXNHLGAME-26SEP06EDMTOR")
        self.assertEqual(nfl.asset_class, "other")
        weather = parse_contract("KXHIGHNY-24JAN01")
        self.assertEqual(weather.event_ticker, "KXHIGHNY-24JAN01")
        self.assertEqual(weather.asset_class, "other")
        with self.assertRaises(KalshiUrlError) as ctx:
            parse_contract("https://kalshi.com/markets/kxatpchallengermatch")
        self.assertIn("series page", str(ctx.exception))

    def test_parse_contract_keeps_tennis_and_rejects_garbage(self):
        tennis = parse_contract(EXAMPLE_URLS[0])
        self.assertEqual(tennis.asset_class, "tennis")
        self.assertEqual(tennis.event_ticker, "KXATPMATCH-26SEP06CERBLO")
        with self.assertRaises(KalshiUrlError) as ctx:
            parse_contract("https://example.com/foo")
        self.assertIn("not a Kalshi link", str(ctx.exception))
        with self.assertRaises(KalshiUrlError):
            parse_crypto_contract("https://kalshi.com/markets/kxbtc15m")

    def test_rejects_empty_and_garbage(self):
        with self.assertRaises(KalshiTennisUrlError):
            parse_tennis_contract("")
        with self.assertRaises(KalshiTennisUrlError):
            parse_tennis_contract("please trade nadal")


if __name__ == "__main__":
    unittest.main()
