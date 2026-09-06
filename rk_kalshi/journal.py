from __future__ import annotations

import csv
import json
from pathlib import Path

from rk_kalshi.models import Fill
from rk_kalshi.schema import FILL_FIELDS


class FillJournal:
    def __init__(self, csv_path: Path, jsonl_path: Path):
        self.csv_path = csv_path
        self.jsonl_path = jsonl_path

    def append(self, fill: Fill) -> None:
        row = fill.as_row()
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with self.csv_path.open("a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(FILL_FIELDS), extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(_csv_row(row))
        with self.jsonl_path.open("a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def clear_fill_logs(csv_path: Path, jsonl_path: Path) -> None:
    """Delete paper fill files so the next append starts a new log."""
    for path in (csv_path, jsonl_path):
        try:
            path.unlink()
        except FileNotFoundError:
            continue


def read_fills(csv_path: Path) -> list[dict]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with csv_path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def summarize_pnl(rows: list[dict]) -> dict:
    if not rows:
        return {
            "fills": 0,
            "running_pnl": 0.0,
            "realized_delta_sum": 0.0,
            "cash_after": None,
            "can_size_up": False,
            "by_ticker": {},
        }
    last = rows[-1]
    by_ticker: dict[str, dict] = {}
    realized = 0.0
    for row in rows:
        ticker = row.get("ticker") or "?"
        bucket = by_ticker.setdefault(ticker, {"fills": 0, "realized_delta": 0.0})
        bucket["fills"] += 1
        delta = _f(row.get("realized_delta"))
        bucket["realized_delta"] += delta
        realized += delta
    return {
        "fills": len(rows),
        "running_pnl": _f(last.get("running_pnl")),
        "realized_delta_sum": realized,
        "cash_after": _f(last.get("cash_after")),
        "can_size_up": _truthy(last.get("can_size_up")),
        "by_ticker": by_ticker,
    }


def _csv_row(row: dict) -> dict:
    out = dict(row)
    out["can_size_up"] = "true" if row.get("can_size_up") else "false"
    return out


def _f(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}
