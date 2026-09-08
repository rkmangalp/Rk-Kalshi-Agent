"""Risk: per-ticker dollar cap, daily loss kill-switch, no martingale.

Sizing up stays locked unless allow_size_up is on, fill count is large,
and paper P&L is already positive. Defaults keep that gate closed.
Live path clamps dollar/daily-loss caps to hard ceilings in live_caps.
Live new entries size to that dollar cap (not paper base_contracts).
Flattening the other side to lock a profit is allowed at full position size.
"""

from __future__ import annotations

from rk_kalshi.config import AppConfig
from rk_kalshi.fees import quadratic_fee_dollars
from rk_kalshi.live_caps import clamp_live_daily_loss, clamp_live_dollars, max_contracts_for_cap
from rk_kalshi.models import PaperState, RiskDecision, Signal


class RiskManager:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def max_dollars_per_ticker(self) -> float:
        cap = float(self.cfg.max_dollars_per_ticker)
        if self.cfg.live_enabled:
            return clamp_live_dollars(cap)
        return cap

    def daily_loss_limit(self) -> float:
        limit = float(self.cfg.daily_loss_limit)
        if self.cfg.live_enabled:
            return clamp_live_daily_loss(limit)
        return limit

    def can_size_up(self, state: PaperState) -> bool:
        if self.cfg.live_enabled:
            return False
        if not self.cfg.allow_size_up:
            return False
        if state.fill_count < self.cfg.min_fills_before_size_up:
            return False
        if state.running_pnl() <= 0:
            return False
        return True

    def kill_switch_hit(self, state: PaperState, marks: dict[str, float] | None = None) -> bool:
        if state.killed:
            return True
        if state.daily_pnl(marks) <= -abs(self.daily_loss_limit()):
            return True
        return False

    def approve(self, signal: Signal, state: PaperState, marks: dict[str, float] | None = None) -> RiskDecision:
        if self.kill_switch_hit(state, marks):
            state.killed = True
            state.kill_reason = state.kill_reason or "daily loss kill-switch"
            return RiskDecision(False, "daily loss kill-switch")

        requested = max(int(signal.contracts), 0)
        if requested <= 0:
            return RiskDecision(False, "non-positive size")

        pos = state.position(signal.ticker)
        reducing = _is_reducing(signal.side, pos.contracts)
        last = state.last_trade.get(signal.ticker)
        if last and last.lost and requested > last.contracts and not reducing:
            return RiskDecision(False, "martingale forbidden: will not increase size after a loss")

        price = signal.fill_price
        if reducing:
            held = abs(int(pos.contracts))
            requested = min(max(requested, 1), held)
        elif self.cfg.live_enabled:
            # Live entries use the dollar cap, not paper base_contracts (1–4).
            requested = max_contracts_for_cap(
                price,
                self.max_dollars_per_ticker(),
                fee_coefficient=self.cfg.fee_coefficient,
                fee_multiplier=self.cfg.fee_multiplier,
            )
        elif not self.can_size_up(state):
            requested = min(requested, self.cfg.base_contracts)

        ticker_cap = self.max_dollars_per_ticker()
        contracts = requested
        while contracts > 0:
            fee = quadratic_fee_dollars(
                price,
                contracts,
                self.cfg.fee_coefficient,
                self.cfg.fee_multiplier,
            )
            order_notional = contracts * price + fee
            existing = _signed_exposure(state, signal.ticker, price, signal.side, contracts)
            if not reducing and existing > ticker_cap + 1e-9:
                contracts -= 1
                continue
            if signal.side == "buy" and state.cash + 1e-9 < order_notional:
                contracts -= 1
                continue
            return RiskDecision(True, "ok", contracts)

        return RiskDecision(False, "ticker or cash cap: cannot size a fill under max_dollars_per_ticker")


def _is_reducing(side: str, held: int) -> bool:
    if held > 0 and str(side).lower() == "sell":
        return True
    if held < 0 and str(side).lower() == "buy":
        return True
    return False


def _signed_exposure(
    state: PaperState,
    ticker: str,
    price: float,
    side: str,
    contracts: int,
) -> float:
    pos = state.position(ticker).contracts
    delta = contracts if side == "buy" else -contracts
    return abs(pos + delta) * price
