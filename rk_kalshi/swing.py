"""In-play tennis swing: buy a dumped cheap YES, trail the bounce, sell it.

Kalshi's public and trade APIs do not publish serve, score, or point-by-point
events. Player names come from the market title / event name. The only live
proxy for "who just won a point" is the YES mid path. This is a volatility
heuristic, not a match-winner model, and not financial advice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from rk_kalshi.config import AppConfig
from rk_kalshi.fees import quadratic_fee_dollars
from rk_kalshi.live_caps import LIVE_TIME_IN_FORCE_GTC
from rk_kalshi.models import MarketSnapshot, Position, Signal

SWING_ALGORITHM = "Tennis swing (buy dump, trail bounce)"
SWING_DISCLAIMER = (
    "Not financial advice and not a match predictor. Kalshi does not publish "
    "serve or live score; this uses the YES price path as a point-by-point proxy. "
    "There is no guaranteed edge. Losses are real on the live path."
)


def round_cent(price: float) -> float:
    return round(min(max(float(price), 0.01), 0.99), 2)


def describe_side(market: MarketSnapshot) -> str:
    title = (market.title or "").strip()
    event = (market.event_name or "").strip()
    if title and event and title.lower() not in event.lower():
        return f"{title} · {event}"
    return title or event or market.ticker


def path_note(mids: Sequence[float]) -> str:
    if len(mids) < 2:
        return "short price path"
    window = (float(mids[-1]) - float(mids[0])) * 100.0
    tick = (float(mids[-1]) - float(mids[-2])) * 100.0
    if tick < -0.5:
        direction = "dumping"
    elif tick > 0.5:
        direction = "bouncing"
    else:
        direction = "chop"
    return f"path {window:+.1f}¢, last tick {tick:+.1f}¢ ({direction})"


def evaluate_swing(
    market: MarketSnapshot,
    *,
    mids: Sequence[float],
    position: Position,
    pending: Mapping[str, Any] | None,
    cfg: AppConfig,
    blocked: bool,
    swing_highs: dict[str, float],
) -> Signal | None:
    """Return at most one GTC buy or sell for this tennis contract."""
    if market.asset_class != "tennis":
        return None
    status = str(market.status or "").lower()
    if status in {"closed", "settled", "finalized", "inactive"}:
        return None
    if pending:
        return None

    held = int(position.contracts)
    if held < 0:
        return None
    if blocked and held == 0:
        return None

    mid = market.yes_mid
    if mid is None:
        if held > 0 and market.yes_bid > 0:
            mid = float(market.yes_bid)
        else:
            return None

    history = [float(x) for x in mids if x is not None]
    if not history or abs(history[-1] - float(mid)) > 1e-12:
        history = history + [float(mid)]

    if held > 0:
        peak = max(float(swing_highs.get(market.ticker, mid)), float(mid), float(position.avg_price))
        swing_highs[market.ticker] = peak
        return _maybe_sell(market, history, position, cfg, mid, peak)

    swing_highs.pop(market.ticker, None)
    return _maybe_buy(market, history, cfg, mid)


def _maybe_buy(
    market: MarketSnapshot,
    history: list[float],
    cfg: AppConfig,
    mid: float,
) -> Signal | None:
    lookback = max(4, int(cfg.swing_lookback))
    if len(history) < 4:
        return None
    window = history[-lookback:]
    hi = max(window)
    lo = min(window)
    range_cents = (hi - lo) * 100.0
    dump_cents = (hi - mid) * 100.0
    if range_cents < float(cfg.swing_min_range_cents):
        return None
    if dump_cents < float(cfg.swing_min_dump_cents):
        return None
    if not (float(cfg.swing_buy_low) - 1e-9 <= mid <= float(cfg.swing_buy_high) + 1e-9):
        return None
    spread = market.spread_cents
    if spread is not None and spread > float(cfg.swing_max_spread_cents):
        return None

    raw = mid - 0.01
    if market.yes_bid > 0:
        raw = min(raw, float(market.yes_bid))
    limit = round_cent(max(float(cfg.swing_buy_low), raw))
    if not (float(cfg.swing_buy_low) - 1e-9 <= limit <= float(cfg.swing_buy_high) + 1e-9):
        return None

    fee = quadratic_fee_dollars(
        limit,
        contracts=1.0,
        coefficient=cfg.fee_coefficient,
        multiplier=cfg.fee_multiplier,
    )
    players = describe_side(market)
    thesis = (
        f"SWING BUY YES {market.ticker}: {players}. Dumped {dump_cents:.1f}¢ from "
        f"{hi * 100:.1f}¢ to {mid * 100:.1f}¢ (range {range_cents:.1f}¢). "
        f"Resting GTC bid {limit * 100:.1f}¢ — one order at a time, same-contract "
        f"buy then sell. {path_note(window)}. "
        f"Serve/score are not on Kalshi's API; price path is the point proxy. "
        f"{SWING_DISCLAIMER}"
    )
    return _signal(
        market,
        side="buy",
        mid=mid,
        fill_price=limit,
        edge_cents=dump_cents,
        thesis=thesis,
        fee=fee,
        contracts=cfg.base_contracts,
        fair=limit,
    )


def _maybe_sell(
    market: MarketSnapshot,
    history: list[float],
    position: Position,
    cfg: AppConfig,
    mid: float,
    peak: float,
) -> Signal | None:
    entry = float(position.avg_price)
    if entry <= 0:
        return None
    bounce_cents = (mid - entry) * 100.0
    pullback_cents = (peak - mid) * 100.0
    still_rising = _still_rising(history, mid, peak)
    target = float(cfg.swing_target_cents)
    trail = float(cfg.swing_trail_cents)
    max_hold = float(cfg.swing_max_hold_cents)

    if bounce_cents < target:
        return None
    if still_rising and bounce_cents < max_hold:
        return None

    target_px = entry + target / 100.0
    trail_px = peak - trail / 100.0
    bid = float(market.yes_bid) if market.yes_bid > 0 else mid
    if still_rising and bounce_cents >= max_hold:
        limit = round_cent(max(target_px, mid, bid))
    else:
        limit = round_cent(max(target_px, min(max(trail_px, bid), peak), bid))
    if limit <= entry + 0.004:
        return None

    contracts = abs(int(position.contracts))
    fee = quadratic_fee_dollars(
        limit,
        contracts=1.0,
        coefficient=cfg.fee_coefficient,
        multiplier=cfg.fee_multiplier,
    )
    players = describe_side(market)
    why = (
        f"max-hold {bounce_cents:.1f}¢"
        if still_rising
        else f"bounce {bounce_cents:.1f}¢ then {pullback_cents:.1f}¢ pullback from {peak * 100:.1f}¢"
    )
    thesis = (
        f"SWING SELL YES {market.ticker}: {players}. Flatten {contracts} bought at "
        f"{entry * 100:.1f}¢; {why}. Resting GTC ask {limit * 100:.1f}¢ "
        f"(one live order at a time). {path_note(history[-max(4, int(cfg.swing_lookback)):])}. "
        f"{SWING_DISCLAIMER}"
    )
    return _signal(
        market,
        side="sell",
        mid=mid,
        fill_price=limit,
        edge_cents=max(bounce_cents, 0.0),
        thesis=thesis,
        fee=fee,
        contracts=contracts,
        fair=limit,
    )


def _still_rising(history: Sequence[float], mid: float, peak: float) -> bool:
    if float(mid) >= float(peak) - 0.004:
        if len(history) >= 2 and float(history[-1]) + 1e-12 >= float(history[-2]):
            return True
        return True
    if len(history) >= 3:
        return float(history[-1]) >= float(history[-2]) >= float(history[-3]) - 1e-12
    return False


def _signal(
    market: MarketSnapshot,
    *,
    side: str,
    mid: float,
    fill_price: float,
    edge_cents: float,
    thesis: str,
    fee: float,
    contracts: int,
    fair: float,
) -> Signal:
    return Signal(
        ticker=market.ticker,
        event_name=market.event_name,
        match_id=market.match_id,
        side=side,
        live_mid=mid,
        fill_price=fill_price,
        edge_cents=edge_cents,
        edge_bps=edge_cents * 100.0,
        edge_thesis=thesis,
        fee_per_contract=fee,
        contracts=max(1, int(contracts)),
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        last_price=market.last_price,
        fair_yes=fair,
        pair_lock=False,
        time_in_force=LIVE_TIME_IN_FORCE_GTC,
        resting=True,
    )
