import json
import unittest

import httpx

from rk_kalshi.config import AppConfig
from rk_kalshi.llm import LlmResearchTrader, parse_llm_decisions, signal_from_llm_decision
from rk_kalshi.models import MarketSnapshot, Signal
from rk_kalshi.risk import RiskManager
from rk_kalshi.state import new_state


def _market(**overrides) -> MarketSnapshot:
    data = dict(
        ticker="KXATPMATCH-26SEP06FOO-FOO",
        event_ticker="KXATPMATCH-26SEP06FOO",
        event_name="Foo vs Bar",
        title="Foo wins",
        yes_bid=0.395,
        yes_ask=0.405,
        last_price=0.40,
        volume=100.0,
        updated_ts=1_000_000.0,
        status="active",
        series_ticker="KXATPMATCH",
        yes_bid_size=2000.0,
        yes_ask_size=20.0,
    )
    data.update(overrides)
    return MarketSnapshot(**data)


def _as_buy(market: MarketSnapshot) -> Signal:
    return Signal(
        ticker=market.ticker,
        event_name=market.event_name,
        match_id=market.match_id,
        side="buy",
        live_mid=market.yes_mid or 0.40,
        fill_price=market.yes_mid or 0.40,
        edge_cents=5.0,
        edge_bps=500.0,
        edge_thesis="AS buy",
        fee_per_contract=0.02,
        contracts=1,
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        last_price=market.last_price,
        fair_yes=0.50,
    )


class FakeChat:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def complete(self, *, model: str, messages):
        self.calls += 1
        self.model = model
        self.messages = messages
        return {
            "choices": [
                {"message": {"content": json.dumps(self.payload)}}
            ]
        }


class LlmTraderTests(unittest.TestCase):
    def test_hybrid_skip_drops_as_candidate(self):
        market = _market()
        cfg = AppConfig(signal_mode="hybrid", llm_min_interval_s=0, kappa=12.0, gamma=0.25)
        fake = FakeChat(
            {
                "decisions": [
                    {
                        "ticker": market.ticker,
                        "action": "skip",
                        "edge_cents_estimate": 9.0,
                        "confidence": 0.9,
                        "thesis": "no clear edge",
                    }
                ]
            }
        )
        trader = LlmResearchTrader(cfg, api_key="test", client=fake)
        kept = trader.refine([market], [_as_buy(market)])
        self.assertEqual(kept, [])
        self.assertEqual(fake.calls, 1)

    def test_hybrid_confirm_keeps_as_signal_and_appends_thesis(self):
        market = _market()
        cfg = AppConfig(signal_mode="hybrid", llm_min_interval_s=0, llm_model="gpt-4o-mini")
        fake = FakeChat(
            {
                "decisions": [
                    {
                        "ticker": market.ticker,
                        "action": "buy",
                        "edge_cents_estimate": 6.0,
                        "confidence": 0.7,
                        "thesis": "imbalance supports bid",
                    }
                ]
            }
        )
        trader = LlmResearchTrader(cfg, api_key="test", client=fake)
        kept = trader.refine([market], [_as_buy(market)])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].side, "buy")
        self.assertIn("ChatGPT gpt-4o-mini confirmed", kept[0].edge_thesis)
        self.assertIn("imbalance supports bid", kept[0].edge_thesis)
        user = json.loads(fake.messages[1]["content"])
        self.assertIn("anticipat", user["instruction"].lower())
        self.assertIn("in_play", user["markets"][0])
        self.assertIn("momentum", fake.messages[0]["content"].lower())
        self.assertIn("hold vs break", fake.messages[0]["content"].lower())

    def test_llm_mode_fee_gate_blocks_tiny_estimate(self):
        market = _market()
        cfg = AppConfig(signal_mode="llm", edge_threshold_cents=3.0, llm_min_interval_s=0)
        fake = FakeChat(
            {
                "decisions": [
                    {
                        "ticker": market.ticker,
                        "action": "buy",
                        "edge_cents_estimate": 2.0,
                        "confidence": 0.8,
                        "thesis": "tiny",
                    }
                ]
            }
        )
        trader = LlmResearchTrader(cfg, api_key="test", client=fake)
        self.assertEqual(trader.refine([market], []), [])

    def test_llm_buy_still_blocked_by_risk_cap(self):
        market = _market()
        cfg = AppConfig(
            signal_mode="llm",
            edge_threshold_cents=3.0,
            max_dollars_per_ticker=0.10,
            llm_min_interval_s=0,
        )
        fake = FakeChat(
            {
                "decisions": [
                    {
                        "ticker": market.ticker,
                        "action": "buy",
                        "edge_cents_estimate": 20.0,
                        "confidence": 0.9,
                        "thesis": "research buy",
                    }
                ]
            }
        )
        trader = LlmResearchTrader(cfg, api_key="test", client=fake)
        signals = trader.refine([market], [])
        self.assertEqual(len(signals), 1)
        decision = RiskManager(cfg).approve(signals[0], new_state(cfg), {market.ticker: 0.40})
        self.assertFalse(decision.ok)

    def test_as_obi_mode_does_not_call_model(self):
        market = _market()
        cfg = AppConfig(signal_mode="as_obi")
        fake = FakeChat({"decisions": []})
        trader = LlmResearchTrader(cfg, api_key="test", client=fake)
        candidate = _as_buy(market)
        out = trader.refine([market], [candidate])
        self.assertEqual(out, [candidate])
        self.assertEqual(fake.calls, 0)

    def test_parse_decisions_and_signal_from_llm(self):
        parsed = parse_llm_decisions(
            json.dumps(
                {
                    "decisions": [
                        {
                            "ticker": "T",
                            "action": "sell",
                            "edge_cents_estimate": 8,
                            "confidence": 0.4,
                            "thesis": "fade",
                        }
                    ]
                }
            ),
            "gpt-4o-mini",
        )
        self.assertEqual(parsed[0].action, "sell")
        market = _market(ticker="T")
        signal = signal_from_llm_decision(market, parsed[0], AppConfig(edge_threshold_cents=3.0))
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "sell")
        self.assertIn("gpt-4o-mini", signal.edge_thesis)
        self.assertGreaterEqual(signal.edge_cents, 3.0)

    def test_parse_accepts_side_alias(self):
        parsed = parse_llm_decisions(
            json.dumps(
                {
                    "decisions": [
                        {
                            "ticker": "T",
                            "side": "buy",
                            "edge_cents_estimate": 9,
                            "confidence": 0.5,
                            "thesis": "anticipate break",
                        }
                    ]
                }
            ),
            "gpt-4o-mini",
        )
        self.assertEqual(parsed[0].action, "buy")

    def test_hybrid_rate_limit_keeps_as_and_skips_second_call(self):
        market = _market()
        cfg = AppConfig(signal_mode="hybrid", llm_min_interval_s=30)
        fake = FakeChat(
            {
                "decisions": [
                    {
                        "ticker": market.ticker,
                        "action": "buy",
                        "edge_cents_estimate": 6.0,
                        "confidence": 0.7,
                        "thesis": "ok",
                    }
                ]
            }
        )
        trader = LlmResearchTrader(cfg, api_key="test", client=fake)
        candidate = _as_buy(market)
        first = trader.refine([market], [candidate], now=1_000.0)
        self.assertEqual(len(first), 1)
        second = trader.refine([market], [candidate], now=1_010.0)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(second, [candidate])
        self.assertIn("rate-limited", trader.last_note.lower())

    def test_missing_key_hybrid_falls_back_to_as(self):
        market = _market()
        cfg = AppConfig(signal_mode="hybrid")
        trader = LlmResearchTrader(cfg, api_key="")
        candidate = _as_buy(market)
        out = trader.refine([market], [candidate])
        self.assertEqual(out, [candidate])
        self.assertIn("OPENAI_API_KEY", trader.last_note)

    def test_missing_key_llm_mode_emits_nothing(self):
        market = _market()
        cfg = AppConfig(signal_mode="llm")
        trader = LlmResearchTrader(cfg, api_key="")
        self.assertEqual(trader.refine([market], [_as_buy(market)]), [])

    def test_prompt_includes_selected_contract_target(self):
        from dataclasses import replace

        market = _market()
        cfg = replace(
            AppConfig(signal_mode="hybrid", llm_min_interval_s=0),
            target_event_ticker="KXATPMATCH-26SEP06FOO",
            target_market_ticker=market.ticker,
            target_label="Foo vs Bar",
            live_enabled=False,
        )
        fake = FakeChat(
            {
                "decisions": [
                    {
                        "ticker": market.ticker,
                        "action": "buy",
                        "edge_cents_estimate": 6.0,
                        "confidence": 0.6,
                        "thesis": "hold serve then break",
                    }
                ]
            }
        )
        trader = LlmResearchTrader(cfg, api_key="test", client=fake)
        trader.refine([market], [_as_buy(market)])
        user = json.loads(fake.messages[1]["content"])
        self.assertEqual(user["target"]["event_ticker"], "KXATPMATCH-26SEP06FOO")
        self.assertEqual(user["target"]["market_ticker"], market.ticker)
        self.assertTrue(user["paper_only"])
        self.assertIn("side", user["instruction"])

    def test_openai_error_does_not_crash_hybrid(self):
        market = _market()
        cfg = AppConfig(signal_mode="hybrid", llm_min_interval_s=0)

        class Boom:
            def complete(self, *, model, messages):
                raise httpx.ConnectError("openai down")

        trader = LlmResearchTrader(cfg, api_key="test", client=Boom())
        candidate = _as_buy(market)
        out = trader.refine([market], [candidate])
        self.assertEqual(out, [candidate])
        self.assertIn("failed", trader.last_note.lower())


if __name__ == "__main__":
    unittest.main()
