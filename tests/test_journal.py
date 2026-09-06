import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from rk_kalshi.config import AppConfig
from rk_kalshi.execution import PaperExecution
from rk_kalshi.journal import FillJournal, read_fills, summarize_pnl
from rk_kalshi.models import Signal
from rk_kalshi.risk import RiskManager
from rk_kalshi.schema import AUDIT_CORE_FIELDS, FILL_FIELDS, REQUIRED_FIELDS
from rk_kalshi.state import local_now_iso, new_state


def _signal() -> Signal:
    return Signal(
        ticker="KXWTAMATCH-26SEP06AAA-BBB",
        event_name="Ada vs Bea",
        match_id="KXWTAMATCH-26SEP06AAA",
        side="buy",
        live_mid=0.415,
        fill_price=0.415,
        edge_cents=4.25,
        edge_bps=425.0,
        edge_thesis="BUY YES demo: net edge 4.25¢ after costs",
        fee_per_contract=0.02,
        contracts=1,
        yes_bid=0.41,
        yes_ask=0.42,
        last_price=0.48,
        fair_yes=0.46,
    )


class FillJournalTests(unittest.TestCase):
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
        )
        self.risk = RiskManager(self.cfg)
        self.paper = PaperExecution(self.cfg, self.risk)
        self.journal = FillJournal(self.cfg.fill_log_csv, self.cfg.fill_log_jsonl)
        self.state = new_state(self.cfg, day="2026-09-06")

    def tearDown(self):
        self.tmp.cleanup()

    def test_csv_and_jsonl_emit_locked_schema(self):
        fill = self.paper.execute(_signal(), self.state, contracts=1, latency_ms=12.5)
        self.journal.append(fill)
        self.journal.append(fill)

        with self.cfg.fill_log_csv.open(newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, list(FILL_FIELDS))
            rows = list(reader)
        self.assertEqual(len(rows), 2)
        row = rows[0]
        for name in REQUIRED_FIELDS:
            self.assertIn(name, row)
            self.assertNotEqual(row[name], "")
        for name in AUDIT_CORE_FIELDS:
            self.assertIn(name, row)
        self.assertEqual(row["ticker"], "KXWTAMATCH-26SEP06AAA-BBB")
        self.assertEqual(row["side"], "buy")
        self.assertEqual(row["event_name"], "Ada vs Bea")
        self.assertEqual(row["match_id"], "KXWTAMATCH-26SEP06AAA")
        self.assertEqual(row["mode"], "paper")
        self.assertEqual(row["can_size_up"], "false")
        self.assertAlmostEqual(float(row["fill_price"]), float(row["live_mid"]))
        self.assertAlmostEqual(float(row["edge_cents"]) * 100.0, float(row["edge_bps"]))
        self.assertGreater(float(row["latency_ms"]), 0)

        lines = self.cfg.fill_log_jsonl.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)
        payload = json.loads(lines[0])
        for name in FILL_FIELDS:
            self.assertIn(name, payload)
        self.assertIs(payload["can_size_up"], False)
        self.assertEqual(payload["mode"], "paper")

    def test_running_pnl_and_cash_move_on_buy(self):
        fill = self.paper.execute(_signal(), self.state, contracts=1, latency_ms=8.0)
        self.journal.append(fill)
        self.assertLess(fill.cash_after, 100.0)
        self.assertAlmostEqual(fill.cash_after, 100.0 - fill.fill_price - fill.fee, places=6)
        self.assertAlmostEqual(fill.running_pnl, -fill.fee, places=6)
        self.assertEqual(fill.can_size_up, False)

    def test_show_pnl_summary_reads_log(self):
        fill = self.paper.execute(_signal(), self.state, contracts=1, latency_ms=3.0)
        self.journal.append(fill)
        rows = read_fills(self.cfg.fill_log_csv)
        summary = summarize_pnl(rows)
        self.assertEqual(summary["fills"], 1)
        self.assertFalse(summary["can_size_up"])
        self.assertIn("KXWTAMATCH-26SEP06AAA-BBB", summary["by_ticker"])
        parsed = datetime.fromisoformat(fill.timestamp)
        self.assertIsNotNone(parsed.tzinfo)

    def test_local_now_iso_is_timezone_aware(self):
        parsed = datetime.fromisoformat(local_now_iso())
        self.assertIsNotNone(parsed.tzinfo)


if __name__ == "__main__":
    unittest.main()
