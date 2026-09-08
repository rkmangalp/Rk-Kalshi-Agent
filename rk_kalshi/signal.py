"""Signal module — isolated from execution.

Paper fair value is an Avellaneda–Stoikov reservation price (inventory,
volatility, time-to-close) plus a short-horizon order-book imbalance tilt.
Half-spread and a Kalshi-style quadratic fee are subtracted before the
edge threshold. Optional EMA / last-print fair is a fallback only.

This is a microstructure heuristic for REST polling, not a match-winner or
BTC price model, and not financial advice. There is no guaranteed edge;
most scans should emit nothing. Live order placement stays disabled.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from rk_kalshi.config import AppConfig
from rk_kalshi.fees import quadratic_fee_cents, quadratic_fee_dollars
from rk_kalshi.models import MarketSnapshot, PaperState, Position, Signal

SIGNAL_ALGORITHM = "Avellaneda–Stoikov + order-book imbalance"
SIGNAL_DISCLAIMER = (
    "Not financial advice and not a match or Bitcoin predictor. "
    "There is no guaranteed profitable model; REST paper fills audit costs and risk."
)
PAIR_LOCK_MIN_CENTS = 1.0


def algorithm_label(mode: str | None) -> str:
    text = str(mode or "as_obi").strip().lower()
    if text == "llm":
        return "ChatGPT research (paper)"
    if text == "hybrid":
        return "Hybrid AS+OBI + ChatGPT"
    return SIGNAL_ALGORITHM


def reservation_price(
    mid: float,
    inventory_q: float,
    gamma: float,
    sigma: float,
    t_frac: float,
) -> float:
    """Avellaneda–Stoikov reservation: r = mid - q * γ * σ² * T."""
    r = float(mid) - float(inventory_q) * float(gamma) * (float(sigma) ** 2) * float(t_frac)
    return min(max(r, 1e-6), 1.0 - 1e-6)


def order_book_imbalance(bid_size: float, ask_size: float) -> float:
    total = float(bid_size) + float(ask_size)
    if total <= 0:
        return 0.0
    return (float(bid_size) - float(ask_size)) / total


def rolling_sigma(mids: Sequence[float], floor: float) -> float:
    """Sample std of mid levels in probability space, floored."""
    if len(mids) < 2:
        return float(floor)
    mean = sum(mids) / len(mids)
    var = sum((x - mean) ** 2 for x in mids) / (len(mids) - 1)
    return max(float(floor), math.sqrt(max(var, 0.0)))


def time_to_close_frac(close_ts: float | None, now: float, horizon_s: float) -> float:
    """Remaining time / horizon, clipped to [0, 1]. Missing close → 1."""
    if close_ts is None:
        return 1.0
    horizon = max(float(horizon_s), 1e-6)
    remaining = float(close_ts) - float(now)
    if remaining <= 0:
        return 0.0
    return min(1.0, remaining / horizon)


class SignalEngine:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._ema: dict[str, float] = {}
        self._mids: dict[str, deque[float]] = {}

    def load_ema(self, ema: Mapping[str, float]) -> None:
        self._ema = dict(ema)

    def dump_ema(self) -> dict[str, float]:
        return dict(self._ema)

    def load_mids(self, history: Mapping[str, Sequence[float]] | None) -> None:
        window = max(int(self.cfg.sigma_window), 2)
        self._mids = {}
        for ticker, values in (history or {}).items():
            buf = deque((float(v) for v in values if v is not None), maxlen=window)
            if buf:
                self._mids[str(ticker)] = buf

    def dump_mids(self) -> dict[str, list[float]]:
        return {ticker: list(buf) for ticker, buf in self._mids.items()}

    def evaluate(
        self,
        markets: list[MarketSnapshot],
        inventory: Mapping[str, Any] | PaperState | None = None,
        now: float | None = None,
    ) -> list[Signal]:
        now = time.time() if now is None else now
        signals: list[Signal] = []
        for market in markets:
            signal = self.evaluate_one(
                market,
                inventory_q=self._inventory_q(market.ticker, inventory),
                now=now,
            )
            if signal is not None:
                signals.append(signal)
        signals.sort(key=lambda s: (not s.pair_lock, -s.edge_cents))
        return signals

    def evaluate_one(
        self,
        market: MarketSnapshot,
        inventory_q: float = 0.0,
        now: float | None = None,
        inventory: Mapping[str, Any] | PaperState | None = None,
    ) -> Signal | None:
        now = time.time() if now is None else now
        pos = self._position(market.ticker, inventory, inventory_q)
        if inventory is not None:
            inventory_q = float(pos.contracts)
        mid = market.yes_mid
        lock_mid = mid if mid is not None else max(float(market.yes_bid), float(market.yes_ask), 0.01)
        lock = self._pair_lock(market, pos, lock_mid)
        if lock is not None:
            return lock
        if self.cfg.live_enabled and pos.contracts != 0:
            # Live holds the open until a profitable other-side flatten (or settlement).
            return None

        half_spread = market.half_spread
        if mid is None or half_spread is None:
            return None
        if market.volume < self.cfg.min_volume:
            return None
        spread_cents = market.spread_cents
        if spread_cents is None or spread_cents > self.cfg.max_spread_cents:
            return None
        if _is_stale(market, now, self.cfg.stale_mid_seconds) and spread_cents > 2.0:
            return None

        self._record_mid(market.ticker, mid)
        prev = self._ema.get(market.ticker, mid)
        ema = self.cfg.ema_alpha * mid + (1.0 - self.cfg.ema_alpha) * prev
        self._ema[market.ticker] = ema

        sigma = rolling_sigma(self._mids.get(market.ticker) or [mid], self.cfg.sigma_floor)
        t_frac = time_to_close_frac(
            market.close_ts or market.occurrence_ts,
            now,
            self.cfg.t_frac_horizon_seconds,
        )
        q = float(inventory_q)
        r = reservation_price(mid, q, self.cfg.gamma, sigma, t_frac)
        obi = market.order_book_imbalance
        spread_dollars = (market.yes_ask - market.yes_bid) if spread_cents is not None else 0.0
        tilt_scale = spread_dollars if spread_dollars > 1e-12 else (self.cfg.obi_tilt_cents / 100.0)
        tilt = self.cfg.kappa * obi * tilt_scale
        fair = min(max(r + tilt, 1e-6), 1.0 - 1e-6)
        used_ema = False
        last_note = f"last={market.last_price * 100:.2f}¢" if market.last_price > 0 else "last=n/a"

        if self.cfg.use_ema_fallback and abs(fair - mid) < 1e-12:
            ema_fair, last_note = self._ema_last_fair(market, mid, ema)
            fair = ema_fair
            used_ema = True

        fee_cents = quadratic_fee_cents(
            mid,
            contracts=1.0,
            coefficient=self.cfg.fee_coefficient,
            multiplier=self.cfg.fee_multiplier,
        )
        cost_cents = half_spread * 100.0 + fee_cents
        buy_edge = (fair - mid) * 100.0 - cost_cents
        sell_edge = (mid - fair) * 100.0 - cost_cents
        threshold = self.cfg.edge_threshold_cents

        if buy_edge >= threshold and buy_edge >= sell_edge:
            side = "buy"
            edge = buy_edge
        elif sell_edge >= threshold:
            side = "sell"
            edge = sell_edge
        else:
            return None

        source = "EMA fallback" if used_ema else SIGNAL_ALGORITHM
        thesis = (
            f"{side.upper()} YES {market.ticker}: {source} fair {fair * 100:.2f}¢ vs mid {mid * 100:.2f}¢ "
            f"(r={r * 100:.2f}¢ q={q:.1f} γ={self.cfg.gamma:.3f} σ={sigma:.4f} T={t_frac:.2f} "
            f"OBI={obi:+.3f} κ={self.cfg.kappa:.2f}; {last_note}, ema={ema * 100:.2f}¢); "
            f"half-spread {half_spread * 100:.2f}¢ + fee {fee_cents:.2f}¢; "
            f"net edge {edge:.2f}¢ after costs (threshold {threshold:.2f}¢). "
            f"{_thesis_caveat(market)}"
        )
        fee = quadratic_fee_dollars(
            mid,
            contracts=1.0,
            coefficient=self.cfg.fee_coefficient,
            multiplier=self.cfg.fee_multiplier,
        )
        return Signal(
            ticker=market.ticker,
            event_name=market.event_name,
            match_id=market.match_id,
            side=side,
            live_mid=mid,
            fill_price=mid,
            edge_cents=edge,
            edge_bps=edge * 100.0,
            edge_thesis=thesis,
            fee_per_contract=fee,
            contracts=self.cfg.base_contracts,
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            last_price=market.last_price,
            fair_yes=fair,
        )

    def _pair_lock(self, market: MarketSnapshot, pos: Position, mid: float) -> Signal | None:
        """Flatten when YES+NO (entry + other side) locks a profit after fees.

        Manual tape this copies: buy YES on a tennis winner, then buy NO / sell YES
        when the complementary price is cheap enough that the pair pays $1.
        V2 quotes the YES book: selling YES at the bid is buying NO at 1 - bid.
        """
        held = int(pos.contracts)
        avg = float(pos.avg_price)
        if held == 0 or avg <= 0:
            return None
        contracts = abs(held)
        if held > 0:
            exit_px = float(market.yes_bid) if market.yes_bid > 0 else 0.0
            side = "sell"
            gross = exit_px - avg
            no_px = 1.0 - exit_px
        else:
            exit_px = float(market.yes_ask) if market.yes_ask > 0 else 0.0
            side = "buy"
            gross = avg - exit_px
            no_px = exit_px
        if exit_px < 0.01 or exit_px > 0.99:
            return None
        fee = quadratic_fee_dollars(
            exit_px,
            contracts,
            self.cfg.fee_coefficient,
            self.cfg.fee_multiplier,
        )
        fee_each = fee / contracts
        net_each = gross - fee_each
        if net_each * 100.0 < PAIR_LOCK_MIN_CENTS:
            return None
        edge = net_each * 100.0
        thesis = (
            f"PAIR LOCK {side.upper()} YES {market.ticker}: flatten {contracts} at "
            f"{exit_px * 100:.2f}¢ vs entry {avg * 100:.2f}¢ "
            f"(other side ~{no_px * 100:.2f}¢; pair {avg * 100:.2f}¢ + {no_px * 100:.2f}¢). "
            f"Lock {gross * 100:.2f}¢/ct before exit fee {fee_each * 100:.2f}¢; "
            f"net {edge:.2f}¢/ct after fees. Not financial advice; not a match pick."
        )
        return Signal(
            ticker=market.ticker,
            event_name=market.event_name,
            match_id=market.match_id,
            side=side,
            live_mid=mid,
            fill_price=exit_px,
            edge_cents=edge,
            edge_bps=edge * 100.0,
            edge_thesis=thesis,
            fee_per_contract=fee_each,
            contracts=contracts,
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            last_price=market.last_price,
            fair_yes=exit_px,
            pair_lock=True,
        )

    def _record_mid(self, ticker: str, mid: float) -> None:
        window = max(int(self.cfg.sigma_window), 2)
        buf = self._mids.get(ticker)
        if buf is None or buf.maxlen != window:
            buf = deque(buf or (), maxlen=window)
            self._mids[ticker] = buf
        buf.append(float(mid))

    def _ema_last_fair(
        self, market: MarketSnapshot, mid: float, ema: float
    ) -> tuple[float, str]:
        last = market.last_price if market.last_price > 0 else None
        if last is not None:
            last_gap_cents = abs(last - mid) * 100.0
            if last_gap_cents > self.cfg.max_last_dislocation_cents:
                last = None
        if last is not None:
            fair = (
                self.cfg.last_trade_weight * last
                + (1.0 - self.cfg.last_trade_weight) * ema
            )
            return fair, f"last={last * 100:.2f}¢"
        return ema, "last=n/a (missing or stale vs mid)"

    @staticmethod
    def _position(
        ticker: str,
        inventory: Mapping[str, Any] | PaperState | None,
        inventory_q: float = 0.0,
    ) -> Position:
        if isinstance(inventory, PaperState):
            return inventory.position(ticker)
        if isinstance(inventory, Mapping):
            pos = inventory.get(ticker, 0)
            if isinstance(pos, Position):
                return pos
            try:
                return Position(contracts=int(float(pos)), avg_price=0.0)
            except (TypeError, ValueError):
                return Position()
        try:
            qty = int(float(inventory_q))
        except (TypeError, ValueError):
            qty = 0
        return Position(contracts=qty, avg_price=0.0)

    @staticmethod
    def _inventory_q(ticker: str, inventory: Mapping[str, Any] | PaperState | None) -> float:
        return float(SignalEngine._position(ticker, inventory).contracts)


# Back-compat alias used by older imports and docs.
TennisSignalEngine = SignalEngine


def _thesis_caveat(market: MarketSnapshot) -> str:
    if market.asset_class == "bitcoin":
        return (
            "Not financial advice; not a bitcoin price forecast. "
            "No guaranteed edge — heuristic only."
        )
    return (
        "Not financial advice; not a match pick. "
        "No guaranteed edge — heuristic only."
    )


def _is_stale(market: MarketSnapshot, now: float, stale_seconds: float) -> bool:
    if market.updated_ts is None:
        return False
    return (now - market.updated_ts) > stale_seconds
