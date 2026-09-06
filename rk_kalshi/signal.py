"""Signal module — isolated from execution.

Ingests live tennis snapshots, estimates a fair YES from last print + EMA,
subtracts half-spread and a Kalshi-style quadratic fee, and emits buy/sell
only when net edge clears the threshold.

This is a microstructure heuristic, not a match-winner model. REST polling
cannot outrun the book; most scans should emit nothing.
"""

from __future__ import annotations

import time
from typing import Mapping

from rk_kalshi.config import AppConfig
from rk_kalshi.fees import quadratic_fee_cents, quadratic_fee_dollars
from rk_kalshi.models import MarketSnapshot, Signal


class TennisSignalEngine:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._ema: dict[str, float] = {}

    def load_ema(self, ema: Mapping[str, float]) -> None:
        self._ema = dict(ema)

    def dump_ema(self) -> dict[str, float]:
        return dict(self._ema)

    def evaluate(self, markets: list[MarketSnapshot], now: float | None = None) -> list[Signal]:
        now = time.time() if now is None else now
        signals: list[Signal] = []
        for market in markets:
            signal = self.evaluate_one(market, now=now)
            if signal is not None:
                signals.append(signal)
        signals.sort(key=lambda s: s.edge_cents, reverse=True)
        return signals

    def evaluate_one(self, market: MarketSnapshot, now: float | None = None) -> Signal | None:
        now = time.time() if now is None else now
        mid = market.yes_mid
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

        prev = self._ema.get(market.ticker, mid)
        ema = self.cfg.ema_alpha * mid + (1.0 - self.cfg.ema_alpha) * prev
        self._ema[market.ticker] = ema

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
            last_note = f"last={last * 100:.2f}¢"
        else:
            fair = ema
            last_note = "last=n/a (missing or stale vs mid)"

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

        thesis = (
            f"{side.upper()} YES {market.ticker}: fair {fair * 100:.2f}¢ vs mid {mid * 100:.2f}¢ "
            f"({last_note}, ema={ema * 100:.2f}¢); half-spread {half_spread * 100:.2f}¢ + "
            f"fee {fee_cents:.2f}¢; net edge {edge:.2f}¢ after costs "
            f"(threshold {threshold:.2f}¢). Heuristic only — not a match pick."
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


def _is_stale(market: MarketSnapshot, now: float, stale_seconds: float) -> bool:
    if market.updated_ts is None:
        return False
    return (now - market.updated_ts) > stale_seconds
