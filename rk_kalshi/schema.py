"""Locked paper-fill field names. Tests assert these exact strings."""

# Audit-core — required names, do not rename.
AUDIT_CORE_FIELDS = (
    "timestamp",
    "ticker",
    "side",
    "fill_price",
    "live_mid",
    "edge_thesis",
    "running_pnl",
)

# Additional required identity / edge fields.
REQUIRED_FIELDS = AUDIT_CORE_FIELDS + (
    "event_name",
    "match_id",
    "edge_cents",
)

# Optional but emitted on every paper fill.
OPTIONAL_FIELDS = (
    "edge_bps",
    "contracts",
    "fee",
    "cash_after",
    "mode",
    "latency_ms",
    "can_size_up",
    "realized_delta",
)

# CSV / JSONL column order.
FILL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
