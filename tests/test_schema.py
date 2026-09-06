import unittest

from rk_kalshi.schema import (
    AUDIT_CORE_FIELDS,
    FILL_FIELDS,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
)


class LockedSchemaTests(unittest.TestCase):
    def test_audit_core_names_are_locked(self):
        self.assertEqual(
            AUDIT_CORE_FIELDS,
            (
                "timestamp",
                "ticker",
                "side",
                "fill_price",
                "live_mid",
                "edge_thesis",
                "running_pnl",
            ),
        )

    def test_required_identity_and_edge_fields(self):
        self.assertIn("event_name", REQUIRED_FIELDS)
        self.assertIn("match_id", REQUIRED_FIELDS)
        self.assertIn("edge_cents", REQUIRED_FIELDS)
        for name in AUDIT_CORE_FIELDS:
            self.assertIn(name, REQUIRED_FIELDS)

    def test_optional_fields_include_edge_bps_and_ops(self):
        self.assertIn("edge_bps", OPTIONAL_FIELDS)
        self.assertIn("contracts", OPTIONAL_FIELDS)
        self.assertIn("fee", OPTIONAL_FIELDS)
        self.assertIn("cash_after", OPTIONAL_FIELDS)
        self.assertIn("mode", OPTIONAL_FIELDS)
        self.assertIn("latency_ms", OPTIONAL_FIELDS)
        self.assertIn("can_size_up", OPTIONAL_FIELDS)
        self.assertIn("realized_delta", OPTIONAL_FIELDS)

    def test_fill_fields_are_required_plus_optional_without_dupes(self):
        self.assertEqual(FILL_FIELDS, REQUIRED_FIELDS + OPTIONAL_FIELDS)
        self.assertEqual(len(FILL_FIELDS), len(set(FILL_FIELDS)))


if __name__ == "__main__":
    unittest.main()
