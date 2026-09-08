import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from rk_kalshi.config import AppConfig, _signal_mode
from rk_kalshi.execution import LiveKalshiExecution
from rk_kalshi.live_caps import LIVE_TIME_IN_FORCE_GTC, create_order_v2_body
from rk_kalshi.models import MarketSnapshot, Position
from rk_kalshi.risk import RiskManager
from rk_kalshi.runner import PaperRunner
from rk_kalshi.signal import SignalEngine, algorithm_label
from rk_kalshi.state import load_state, new_state, save_state
from rk_kalshi.swing import evaluate_swing
from tests.test_account import FakeSignedClient, _creds


def _tennis(**overrides) -> MarketSnapshot:
    data = dict(
        ticker="KXATPMATCH-26SEP08ADA-ADA",
        event_ticker="KXATPMATCH-26SEP08ADA",
        event_name="Ada vs Bea",
        title="Ada wins",
        yes_bid=0.19,
        yes_ask=0.21,
        last_price=0.20,
        volume=200.0,
        updated_ts=1_700_000_000.0,
        status="active",
        series_ticker="KXATPMATCH",
        occurrence_ts=time.time(),
        yes_bid_size=40.0,
        yes_ask_size=40.0,
    )
    data.update(overrides)
    return MarketSnapshot(**data)


def _dump_mids(n: int = 16, high: float = 0.80, low: float = 0.20) -> list[float]:
    return [high] * (n - 4) + [0.55, 0.40, 0.28, low]


def _swing_cfg(**overrides) -> AppConfig:
    data = dict(
        signal_mode="swing",
        trade_bitcoin=False,
        live_matches_only=False,
        max_signals_per_cycle=8,
        base_contracts=2,
        starting_cash=100.0,
        max_dollars_per_ticker=20.0,
    )
    data.update(overrides)
    return AppConfig(**data)


class SwingSignalTests(unittest.TestCase):
    def setUp(self):
        self.cfg = _swing_cfg()
        self.engine = SignalEngine(self.cfg)

    def test_mode_label_and_alias(self):
        self.assertEqual(_signal_mode("tennis_swing"), "swing")
        self.assertIn("swing", algorithm_label("swing").lower())
        self.assertIn("dump", algorithm_label("swing").lower())

    def test_buy_on_volatile_dump_into_cheap_band(self):
        market = _tennis()
        self.engine.load_mids({market.ticker: _dump_mids()})
        signal = self.engine.evaluate([market], inventory=new_state(self.cfg))[0]
        self.assertEqual(signal.side, "buy")
        self.assertTrue(signal.resting)
        self.assertEqual(signal.time_in_force, LIVE_TIME_IN_FORCE_GTC)
        self.assertFalse(signal.pair_lock)
        self.assertGreaterEqual(signal.fill_price, 0.12)
        self.assertLessEqual(signal.fill_price, 0.42)
        self.assertIn("SWING BUY", signal.edge_thesis)
        self.assertIn("Ada", signal.edge_thesis)
        self.assertIn("serve/score", signal.edge_thesis.lower())
        self.assertIn("not a match predictor", signal.edge_thesis.lower())

    def test_no_buy_when_match_is_calm(self):
        market = _tennis(yes_bid=0.49, yes_ask=0.51, last_price=0.50)
        self.engine.load_mids({market.ticker: [0.50] * 16})
        self.assertEqual(self.engine.evaluate([market], inventory=new_state(self.cfg)), [])

    def test_no_buy_outside_cheap_band(self):
        market = _tennis(yes_bid=0.54, yes_ask=0.56, last_price=0.55)
        self.engine.load_mids({market.ticker: [0.80] * 12 + [0.70, 0.62, 0.58, 0.55]})
        self.assertEqual(self.engine.evaluate([market], inventory=new_state(self.cfg)), [])

    def test_skips_bitcoin(self):
        btc = _tennis(
            ticker="KXBTC15M-26SEP08-15",
            event_ticker="KXBTC15M-26SEP08",
            series_ticker="KXBTC15M",
            event_name="BTC 15m",
            title="Up",
        )
        self.engine.load_mids({btc.ticker: _dump_mids()})
        self.assertEqual(self.engine.evaluate([btc], inventory=new_state(self.cfg)), [])

    def test_one_order_at_a_time(self):
        a = _tennis()
        b = _tennis(
            ticker="KXATPMATCH-26SEP08ADA-BEA",
            title="Bea wins",
            yes_bid=0.18,
            yes_ask=0.20,
            last_price=0.19,
        )
        self.engine.load_mids({a.ticker: _dump_mids(), b.ticker: _dump_mids(low=0.19)})
        signals = self.engine.evaluate([a, b], inventory=new_state(self.cfg))
        self.assertEqual(len(signals), 1)

        state = new_state(self.cfg)
        state.positions[a.ticker] = Position(contracts=10, avg_price=0.20)
        blocked = self.engine.evaluate([a, b], inventory=state)
        self.assertTrue(all(s.ticker == a.ticker for s in blocked))

    def test_sell_after_bounce_and_pullback(self):
        market = _tennis(yes_bid=0.27, yes_ask=0.29, last_price=0.28)
        state = new_state(self.cfg)
        state.positions[market.ticker] = Position(contracts=8, avg_price=0.18)
        state.swing_highs[market.ticker] = 0.33
        self.engine.load_mids({market.ticker: [0.18, 0.22, 0.28, 0.33, 0.30, 0.28]})
        signals = self.engine.evaluate([market], inventory=state)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, "sell")
        self.assertEqual(signals[0].contracts, 8)
        self.assertTrue(signals[0].resting)
        self.assertIn("SWING SELL", signals[0].edge_thesis)
        self.assertNotIn("PAIR LOCK", signals[0].edge_thesis)

    def test_wait_if_still_printing_highs(self):
        market = _tennis(yes_bid=0.31, yes_ask=0.33, last_price=0.32)
        state = new_state(self.cfg)
        state.positions[market.ticker] = Position(contracts=8, avg_price=0.18)
        state.swing_highs[market.ticker] = 0.32
        self.engine.load_mids({market.ticker: [0.18, 0.22, 0.26, 0.29, 0.31, 0.32]})
        self.assertEqual(self.engine.evaluate([market], inventory=state), [])

    def test_as_obi_does_not_emit_swing(self):
        cfg = AppConfig(signal_mode="as_obi", kappa=1.0, gamma=0.25, edge_threshold_cents=3.0)
        engine = SignalEngine(cfg)
        flat = _tennis(yes_bid=0.50, yes_ask=0.50, last_price=0.50)
        self.assertEqual(engine.evaluate([flat]), [])


class SwingExecutionTests(unittest.TestCase):
    def test_gtc_order_body(self):
        body = create_order_v2_body(
            ticker="KX-1",
            side="buy",
            contracts=12,
            price=0.18,
            time_in_force=LIVE_TIME_IN_FORCE_GTC,
        )
        self.assertEqual(body["time_in_force"], "good_till_canceled")
        self.assertEqual(body["side"], "bid")
        self.assertEqual(body["price"], "0.1800")

    def test_paper_parks_gtc_then_fills_when_ask_crosses(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cfg = _swing_cfg(
            fill_log_csv=root / "fills.csv",
            fill_log_jsonl=root / "fills.jsonl",
            state_path=root / "state.json",
        )
        dump = _tennis()
        seeded = new_state(cfg, day="2026-09-08")
        seeded.mid_history[dump.ticker] = _dump_mids()
        save_state(cfg, seeded)
        client = MagicMock()
        fillable = _tennis(yes_bid=0.17, yes_ask=0.18, last_price=0.18)
        client.list_markets.side_effect = [([dump], 4.0), ([fillable], 4.0)]
        runner = PaperRunner(cfg, client=client)
        first = runner.run_once()
        self.assertEqual(first, [])
        parked = load_state(cfg)
        self.assertEqual(len(parked.pending_orders), 1)
        self.assertEqual(parked.pending_orders[0]["side"], "buy")
        self.assertEqual(parked.position(dump.ticker).contracts, 0)
        second = runner.run_once()
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].side, "buy")
        self.assertEqual(second[0].mode, "paper")
        saved = load_state(cfg)
        self.assertEqual(saved.pending_orders, [])
        self.assertGreater(saved.position(dump.ticker).contracts, 0)

    def test_live_gtc_rests_then_reconciles_fill(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cfg = _swing_cfg(
            live_enabled=True,
            fill_log_csv=root / "fills.csv",
            fill_log_jsonl=root / "fills.jsonl",
            state_path=root / "live_state.json",
            live_state_path=root / "live_state.json",
        )
        dump = _tennis()
        seeded = new_state(cfg, day="2026-09-08")
        seeded.mid_history[dump.ticker] = _dump_mids()
        save_state(cfg, seeded)
        signed = FakeSignedClient(_creds())
        signed.orders_enabled = True

        def create_order(**kwargs):
            signed.created_orders.append(kwargs)
            return {
                "order_id": "gtc-1",
                "status": "resting",
                "fill_count": "0.00",
                "remaining_count": f"{float(kwargs.get('contracts', 1)):.2f}",
                "average_fill_price": "0.0000",
            }, 3.0

        signed.create_order = create_order
        signed._gtc_filled = False

        def get_order(order_id):
            if not signed._gtc_filled:
                return {
                    "order_id": order_id,
                    "status": "resting",
                    "fill_count": "0.00",
                    "remaining_count": "10.00",
                }, 1.0
            return {
                "order_id": order_id,
                "status": "executed",
                "fill_count": "10.00",
                "remaining_count": "0.00",
                "average_fill_price": "0.1800",
            }, 1.0

        signed.get_order = get_order
        client = MagicMock()
        client.list_markets.return_value = ([dump], 5.0)
        runner = PaperRunner(cfg, client=client, signed_client=signed)
        with patch.dict("os.environ", {"KALSHI_ENVIRONMENT": "demo"}):
            first = runner.run_once()
            self.assertEqual(first, [])
            self.assertEqual(signed.created_orders[0]["time_in_force"], LIVE_TIME_IN_FORCE_GTC)
            parked = load_state(cfg)
            self.assertEqual(len(parked.pending_orders), 1)
            self.assertEqual(parked.position(dump.ticker).contracts, 0)
            signed._gtc_filled = True
            second = runner.run_once()
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].mode, "live")
        self.assertGreater(load_state(cfg).position(dump.ticker).contracts, 0)

    def test_armed_live_as_obi_still_ioc(self):
        cfg = AppConfig(
            live_enabled=True,
            signal_mode="as_obi",
            max_dollars_per_ticker=5.0,
            starting_cash=100.0,
        )
        signed = FakeSignedClient(_creds())
        signed.orders_enabled = True
        live = LiveKalshiExecution(cfg, RiskManager(cfg), client=signed, enabled=True)
        from tests.test_live_orders import _signal

        with patch.dict("os.environ", {"KALSHI_ENVIRONMENT": "demo"}):
            fill = live.execute(_signal(), new_state(cfg, day="2026-09-08"), 1, 8.0)
        self.assertEqual(fill.mode, "live")
        self.assertEqual(signed.created_orders[0].get("time_in_force"), "immediate_or_cancel")


class DirectEvaluateSwingTests(unittest.TestCase):
    def test_blocked_skips_other_ticker(self):
        cfg = _swing_cfg()
        market = _tennis()
        self.assertIsNone(
            evaluate_swing(
                market,
                mids=_dump_mids(),
                position=Position(),
                pending=None,
                cfg=cfg,
                blocked=True,
                swing_highs={},
            )
        )


if __name__ == "__main__":
    unittest.main()
