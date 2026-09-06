"""Execution layer. Paper fills at live YES mid. Live path is a disabled stub."""

from __future__ import annotations

from rk_kalshi.config import AppConfig
from rk_kalshi.fees import quadratic_fee_dollars
from rk_kalshi.models import Fill, PaperState, Signal
from rk_kalshi.risk import RiskManager
from rk_kalshi.state import apply_fill, local_now_iso


class LiveTradingDisabledError(RuntimeError):
    """Raised whenever the live Kalshi order path is invoked."""


class PaperExecution:
    """Simulate an immediate fill at the live YES mid. No Kalshi order is sent."""

    mode = "paper"

    def __init__(self, cfg: AppConfig, risk: RiskManager):
        self.cfg = cfg
        self.risk = risk

    def execute(
        self,
        signal: Signal,
        state: PaperState,
        contracts: int,
        latency_ms: float,
        marks: dict[str, float] | None = None,
    ) -> Fill:
        fee = quadratic_fee_dollars(
            signal.fill_price,
            contracts,
            self.cfg.fee_coefficient,
            self.cfg.fee_multiplier,
        )
        realized_delta = apply_fill(
            state,
            ticker=signal.ticker,
            side=signal.side,
            contracts=contracts,
            fill_price=signal.fill_price,
            fee=fee,
        )
        marks = dict(marks or {})
        marks[signal.ticker] = signal.live_mid
        running = state.running_pnl(marks)
        if self.risk.kill_switch_hit(state, marks):
            state.killed = True
            state.kill_reason = state.kill_reason or "daily loss kill-switch"
        return Fill(
            timestamp=local_now_iso(),
            ticker=signal.ticker,
            side=signal.side,
            fill_price=signal.fill_price,
            live_mid=signal.live_mid,
            edge_thesis=signal.edge_thesis,
            running_pnl=running,
            event_name=signal.event_name,
            match_id=signal.match_id,
            edge_cents=signal.edge_cents,
            edge_bps=signal.edge_bps,
            contracts=contracts,
            fee=fee,
            cash_after=state.cash,
            mode=self.mode,
            latency_ms=latency_ms,
            can_size_up=self.risk.can_size_up(state),
            realized_delta=realized_delta,
        )


class LiveKalshiExecution:
    """Disabled stub for a future authenticated order path.

    Live trading is intentionally not implemented. When (and only when) paper
    P&L is positive on a large sample and a human enables it, orders would:

    1. Load a Kalshi API key id + RSA private key from env / files.
    2. Sign ``timestamp_ms + METHOD + path`` with RSA-PSS (SHA-256, MGF1-SHA256,
       salt length = digest length). Path is the URL path only — no query string.
    3. Send headers ``KALSHI-ACCESS-KEY``, ``KALSHI-ACCESS-TIMESTAMP``,
       ``KALSHI-ACCESS-SIGNATURE`` (base64).
    4. POST ``/trade-api/v2/portfolio/events/orders`` (Create Order V2) with
       ``ticker``, ``side`` (``bid`` / ``ask``), fixed-point ``count`` and
       ``price``, ``time_in_force``, and ``self_trade_prevention_type``.
       The legacy ``POST /portfolio/orders`` (yes/no + buy/sell) was removed.

    This class always raises. ``config live.enabled`` cannot turn it on.
    """

    mode = "live"

    def __init__(self, enabled: bool = False):
        self.enabled = False if enabled is False else False

    def execute(self, *args: object, **kwargs: object) -> None:
        raise LiveTradingDisabledError(_LIVE_DISABLED_MESSAGE)

    def submit(self, *args: object, **kwargs: object) -> None:
        raise LiveTradingDisabledError(_LIVE_DISABLED_MESSAGE)


_LIVE_DISABLED_MESSAGE = (
    "Live Kalshi execution is disabled. This repo paper-trades only. "
    "Future live orders would RSA-PSS-sign timestamp+METHOD+path and POST "
    "/portfolio/events/orders — do not enable until paper P&L is positive "
    "on a large sample and can_size_up is intentionally unlocked."
)
