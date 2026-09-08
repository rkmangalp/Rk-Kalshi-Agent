from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from rk_kalshi.config import AppConfig
from rk_kalshi.models import LastTickerTrade, PaperState, Position


def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def local_now_iso() -> str:
    """Machine-local wall clock with offset, e.g. 2026-09-06T20:31:45.123456-07:00."""
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def new_state(cfg: AppConfig, day: str | None = None) -> PaperState:
    day = day or utc_today()
    return PaperState(
        starting_cash=cfg.starting_cash,
        cash=cfg.starting_cash,
        day=day,
        start_of_day_equity=cfg.starting_cash,
    )


def load_state(cfg: AppConfig) -> PaperState:
    path = cfg.state_path
    if not path.exists():
        return new_state(cfg)
    raw = json.loads(path.read_text())
    state = PaperState(
        starting_cash=float(raw.get("starting_cash", cfg.starting_cash)),
        cash=float(raw.get("cash", cfg.starting_cash)),
        day=str(raw.get("day") or utc_today()),
        start_of_day_equity=float(raw.get("start_of_day_equity", raw.get("cash", cfg.starting_cash))),
        fill_count=int(raw.get("fill_count", 0)),
        killed=bool(raw.get("killed", False)),
        kill_reason=str(raw.get("kill_reason") or ""),
        ema={k: float(v) for k, v in (raw.get("ema") or {}).items()},
        mid_history={
            str(ticker): [float(v) for v in (values or [])]
            for ticker, values in (raw.get("mid_history") or {}).items()
        },
        daily_realized=float(raw.get("daily_realized", 0.0)),
        pending_orders=[dict(row) for row in (raw.get("pending_orders") or []) if isinstance(row, dict)],
        swing_highs={str(k): float(v) for k, v in (raw.get("swing_highs") or {}).items()},
    )
    for ticker, pos in (raw.get("positions") or {}).items():
        state.positions[ticker] = Position(
            contracts=int(pos.get("contracts", 0)),
            avg_price=float(pos.get("avg_price", 0.0)),
        )
    for ticker, last in (raw.get("last_trade") or {}).items():
        state.last_trade[ticker] = LastTickerTrade(
            contracts=int(last.get("contracts", 0)),
            lost=bool(last.get("lost", False)),
        )
    rollover_day(state)
    return state


def rollover_day(state: PaperState, today: str | None = None) -> None:
    today = today or utc_today()
    if state.day == today:
        return
    state.day = today
    state.start_of_day_equity = state.mtm_equity()
    state.daily_realized = 0.0
    state.killed = False
    state.kill_reason = ""


def save_state(cfg: AppConfig, state: PaperState) -> None:
    path = cfg.state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "starting_cash": state.starting_cash,
        "cash": state.cash,
        "day": state.day,
        "start_of_day_equity": state.start_of_day_equity,
        "fill_count": state.fill_count,
        "killed": state.killed,
        "kill_reason": state.kill_reason,
        "daily_realized": state.daily_realized,
        "ema": state.ema,
        "mid_history": {
            ticker: [float(v) for v in values[-64:]]
            for ticker, values in (state.mid_history or {}).items()
            if values
        },
        "positions": {
            ticker: {"contracts": pos.contracts, "avg_price": pos.avg_price}
            for ticker, pos in state.positions.items()
            if pos.contracts != 0
        },
        "last_trade": {
            ticker: {"contracts": last.contracts, "lost": last.lost}
            for ticker, last in state.last_trade.items()
        },
        "pending_orders": [dict(row) for row in (state.pending_orders or [])],
        "swing_highs": {
            ticker: float(px)
            for ticker, px in (state.swing_highs or {}).items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def apply_fill(
    state: PaperState,
    ticker: str,
    side: str,
    contracts: int,
    fill_price: float,
    fee: float,
) -> float:
    """Apply a paper fill. Returns realized_delta (closed PnL minus this order's fee)."""
    pos = state.positions.get(ticker, Position())
    signed = contracts if side == "buy" else -contracts
    realized = 0.0

    if pos.contracts == 0 or (pos.contracts > 0 and signed > 0) or (pos.contracts < 0 and signed < 0):
        new_qty = pos.contracts + signed
        if new_qty == 0:
            pos.avg_price = 0.0
        else:
            total = pos.avg_price * pos.contracts + fill_price * signed
            pos.avg_price = total / new_qty
        pos.contracts = new_qty
    else:
        closing = min(abs(pos.contracts), abs(signed))
        direction = 1 if pos.contracts > 0 else -1
        realized = direction * (fill_price - pos.avg_price) * closing
        leftover_pos = pos.contracts + signed
        if leftover_pos == 0:
            pos.contracts = 0
            pos.avg_price = 0.0
        elif (pos.contracts > 0 and leftover_pos > 0) or (pos.contracts < 0 and leftover_pos < 0):
            pos.contracts = leftover_pos
        else:
            pos.contracts = leftover_pos
            pos.avg_price = fill_price

    if side == "buy":
        state.cash -= contracts * fill_price + fee
    else:
        state.cash += contracts * fill_price - fee

    realized_delta = realized - fee
    state.daily_realized += realized_delta
    state.fill_count += 1
    if pos.contracts == 0:
        state.positions.pop(ticker, None)
    else:
        state.positions[ticker] = pos
    # Martingale guard keys off a closed losing lot, not the opening fee.
    state.last_trade[ticker] = LastTickerTrade(contracts=contracts, lost=realized < 0)
    if pos.contracts > 0:
        peak = state.swing_highs.get(ticker, fill_price)
        state.swing_highs[ticker] = max(float(peak), float(fill_price))
    else:
        state.swing_highs.pop(ticker, None)
    return realized_delta


def make_pending(
    *,
    order_id: str,
    ticker: str,
    side: str,
    price: float,
    contracts: int,
    paper: bool,
    event_name: str = "",
    match_id: str = "",
    thesis: str = "",
    live_mid: float = 0.0,
    yes_bid: float = 0.0,
    yes_ask: float = 0.0,
    edge_cents: float = 0.0,
    edge_bps: float = 0.0,
    filled_so_far: int = 0,
) -> dict:
    return {
        "order_id": str(order_id),
        "ticker": str(ticker),
        "side": str(side),
        "price": float(price),
        "contracts": int(contracts),
        "filled_so_far": int(filled_so_far),
        "paper": bool(paper),
        "event_name": event_name,
        "match_id": match_id,
        "thesis": thesis,
        "live_mid": float(live_mid),
        "yes_bid": float(yes_bid),
        "yes_ask": float(yes_ask),
        "edge_cents": float(edge_cents),
        "edge_bps": float(edge_bps),
        "time_in_force": "good_till_canceled",
    }


def park_pending(state: PaperState, row: dict) -> dict:
    pending = list(state.pending_orders or [])
    pending.append(dict(row))
    state.pending_orders = pending
    return row


def drop_pending(state: PaperState, order_id: str) -> None:
    oid = str(order_id)
    state.pending_orders = [
        row for row in (state.pending_orders or []) if str(row.get("order_id")) != oid
    ]
