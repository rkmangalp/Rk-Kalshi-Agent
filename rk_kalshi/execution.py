"""Execution layer. Paper fills at live YES mid. Live posts Create Order V2."""

from __future__ import annotations

import uuid

from rk_kalshi.account import AccountApiError, KalshiSignedClient
from rk_kalshi.config import AppConfig
from rk_kalshi.fees import quadratic_fee_dollars
from rk_kalshi.live_caps import (
    LIVE_DISABLED_MESSAGE,
    LIVE_TIME_IN_FORCE,
    LIVE_TIME_IN_FORCE_GTC,
    clamp_live_dollars,
    create_order_v2_body,
    is_gtc_signal,
    live_limit_price,
    max_contracts_for_cap,
    require_live_credentials,
    resting_limit_price,
)
from rk_kalshi.models import Fill, PaperState, Signal, parse_count, parse_dollars
from rk_kalshi.risk import RiskManager
from rk_kalshi.state import apply_fill, local_now_iso, make_pending, park_pending


class LiveTradingDisabledError(RuntimeError):
    """Raised when the live Kalshi order path is invoked while disarmed."""


class LiveOrderRejected(RuntimeError):
    """Kalshi rejected a live order or the local live gate blocked it."""


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
    ) -> Fill | None:
        if is_gtc_signal(signal):
            row = make_pending(
                order_id=f"paper-{uuid.uuid4().hex[:12]}",
                ticker=signal.ticker,
                side=signal.side,
                price=resting_limit_price(signal.fill_price),
                contracts=int(contracts),
                paper=True,
                event_name=signal.event_name,
                match_id=signal.match_id,
                thesis=signal.edge_thesis,
                live_mid=signal.live_mid,
                yes_bid=signal.yes_bid,
                yes_ask=signal.yes_ask,
                edge_cents=signal.edge_cents,
                edge_bps=signal.edge_bps,
            )
            park_pending(state, row)
            return None
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
    """Place Kalshi Create Order V2 (POST /portfolio/events/orders) when armed.

    Paper stays the default. This path requires:
    - cfg.live_enabled and this instance enabled
    - connected credentials with an explicit demo/prod environment
    - hard dollar / daily-loss caps (code ceiling, not bypassable)
    - RiskManager approval (research signals cannot skip gates)
    """

    mode = "live"

    def __init__(
        self,
        cfg: AppConfig | None = None,
        risk: RiskManager | None = None,
        client: KalshiSignedClient | None = None,
        enabled: bool = False,
    ):
        self.cfg = cfg or AppConfig()
        self.risk = risk or RiskManager(self.cfg)
        self.client = client
        self.enabled = bool(enabled) and bool(self.cfg.live_enabled) and client is not None
        self.open_order_ids: list[str] = []

    def execute(
        self,
        signal: Signal | object = None,
        state: PaperState | None = None,
        contracts: int = 0,
        latency_ms: float = 0.0,
        marks: dict[str, float] | None = None,
        *args: object,
        **kwargs: object,
    ) -> Fill | None:
        if not self.enabled or not isinstance(signal, Signal) or state is None:
            raise LiveTradingDisabledError(LIVE_DISABLED_MESSAGE)
        payload, order_latency = self._submit_order(signal, state, int(contracts))
        body = _unwrap_order(payload)
        filled, fill_price, fee = _parse_live_fill(body, signal, contracts)
        remaining = parse_count(body.get("remaining_count"))
        if remaining <= 0:
            remaining = parse_count(body.get("remaining_count_fp"))
        order_id = str(body.get("order_id") or payload.get("order_id") or "")
        gtc = is_gtc_signal(signal)
        if filled <= 0:
            if gtc and remaining > 0.009 and order_id:
                park_pending(
                    state,
                    make_pending(
                        order_id=order_id,
                        ticker=signal.ticker,
                        side=signal.side,
                        price=resting_limit_price(signal.fill_price),
                        contracts=max(1, int(round(remaining))),
                        paper=False,
                        event_name=signal.event_name,
                        match_id=signal.match_id,
                        thesis=signal.edge_thesis,
                        live_mid=signal.live_mid,
                        yes_bid=signal.yes_bid,
                        yes_ask=signal.yes_ask,
                        edge_cents=signal.edge_cents,
                        edge_bps=signal.edge_bps,
                    ),
                )
                self.open_order_ids.append(order_id)
                return None
            raise LiveOrderRejected("live order did not fill (IOC canceled remaining)")
        realized_delta = apply_fill(
            state,
            ticker=signal.ticker,
            side=signal.side,
            contracts=filled,
            fill_price=fill_price,
            fee=fee,
        )
        marks = dict(marks or {})
        marks[signal.ticker] = signal.live_mid
        running = state.running_pnl(marks)
        if self.risk.kill_switch_hit(state, marks):
            state.killed = True
            state.kill_reason = state.kill_reason or "daily loss kill-switch"
            self.cancel_open()
        if remaining > 0.009 and order_id:
            self.open_order_ids.append(order_id)
            if gtc:
                park_pending(
                    state,
                    make_pending(
                        order_id=order_id,
                        ticker=signal.ticker,
                        side=signal.side,
                        price=resting_limit_price(signal.fill_price),
                        contracts=max(1, int(round(remaining))),
                        paper=False,
                        event_name=signal.event_name,
                        match_id=signal.match_id,
                        thesis=signal.edge_thesis,
                        live_mid=signal.live_mid,
                        yes_bid=signal.yes_bid,
                        yes_ask=signal.yes_ask,
                        edge_cents=signal.edge_cents,
                        edge_bps=signal.edge_bps,
                        filled_so_far=filled,
                    ),
                )
        return Fill(
            timestamp=local_now_iso(),
            ticker=signal.ticker,
            side=signal.side,
            fill_price=fill_price,
            live_mid=signal.live_mid,
            edge_thesis=signal.edge_thesis,
            running_pnl=running,
            event_name=signal.event_name,
            match_id=signal.match_id,
            edge_cents=signal.edge_cents,
            edge_bps=signal.edge_bps,
            contracts=filled,
            fee=fee,
            cash_after=state.cash,
            mode=self.mode,
            latency_ms=float(latency_ms) + float(order_latency),
            can_size_up=False,
            realized_delta=realized_delta,
        )

    def submit(self, *args: object, **kwargs: object) -> dict:
        if not self.enabled:
            raise LiveTradingDisabledError(LIVE_DISABLED_MESSAGE)
        signal = args[0] if args else kwargs.get("signal")
        state = kwargs.get("state")
        if not isinstance(signal, Signal) or not isinstance(state, PaperState):
            raise LiveTradingDisabledError(LIVE_DISABLED_MESSAGE)
        contracts = int(kwargs.get("contracts") or signal.contracts or 1)
        payload, _latency = self._submit_order(signal, state, contracts)
        return payload

    def cancel_open(self, *, force: bool = False) -> int:
        client = self.client
        if not force:
            client = self._require_client()
        elif client is None:
            return 0
        leftover = []
        cancelled = 0
        for order_id in list(self.open_order_ids):
            try:
                client.cancel_order(order_id)
                cancelled += 1
            except AccountApiError:
                leftover.append(order_id)
        self.open_order_ids = leftover
        return cancelled

    def _submit_order(
        self,
        signal: Signal,
        state: PaperState,
        contracts: int,
    ) -> tuple[dict, float]:
        client = self._require_client()
        if self.risk.kill_switch_hit(state):
            state.killed = True
            state.kill_reason = state.kill_reason or "daily loss kill-switch"
            raise LiveOrderRejected("daily loss kill-switch")
        if int(contracts) <= 0:
            raise LiveOrderRejected("non-positive live size")
        if is_gtc_signal(signal):
            price = resting_limit_price(signal.fill_price)
        else:
            price = live_limit_price(signal.side, signal.yes_bid, signal.yes_ask, signal.fill_price)
        held = int(state.position(signal.ticker).contracts)
        reducing = (held > 0 and signal.side == "sell") or (held < 0 and signal.side == "buy")
        if reducing:
            capped = min(int(contracts), abs(held))
        else:
            cap = clamp_live_dollars(self.cfg.max_dollars_per_ticker)
            capped = max_contracts_for_cap(
                price,
                cap,
                fee_coefficient=self.cfg.fee_coefficient,
                fee_multiplier=self.cfg.fee_multiplier,
                max_contracts=int(contracts),
            )
        if capped <= 0:
            raise LiveOrderRejected("live dollar cap: cannot size an order under the hard ceiling")
        if not reducing:
            exposure = abs(state.position(signal.ticker).contracts + (capped if signal.side == "buy" else -capped)) * price
            if exposure > clamp_live_dollars(self.cfg.max_dollars_per_ticker) + 1e-9:
                raise LiveOrderRejected("live ticker cap would be exceeded")
        tif = LIVE_TIME_IN_FORCE_GTC if is_gtc_signal(signal) else LIVE_TIME_IN_FORCE
        body = create_order_v2_body(
            ticker=signal.ticker,
            side=signal.side,
            contracts=capped,
            price=price,
            time_in_force=tif,
        )
        try:
            payload, latency_ms = client.create_order(
                ticker=body["ticker"],
                side=signal.side,
                contracts=capped,
                price=price,
                client_order_id=str(body["client_order_id"]),
                time_in_force=str(body["time_in_force"]),
            )
        except AccountApiError as exc:
            raise LiveOrderRejected(str(exc)) from exc
        return payload, latency_ms

    def _require_client(self) -> KalshiSignedClient:
        if not self.enabled or self.client is None or not self.cfg.live_enabled:
            raise LiveTradingDisabledError(LIVE_DISABLED_MESSAGE)
        if not getattr(self.client, "orders_enabled", False):
            raise LiveTradingDisabledError(LIVE_DISABLED_MESSAGE)
        require_live_credentials(self.client.credentials)
        return self.client


def _unwrap_order(payload: dict) -> dict:
    inner = payload.get("order") if isinstance(payload, dict) else None
    if isinstance(inner, dict):
        return inner
    return payload if isinstance(payload, dict) else {}


def _parse_live_fill(payload: dict, signal: Signal, requested: int) -> tuple[int, float, float]:
    body = _unwrap_order(payload)
    filled = parse_count(body.get("fill_count"), default=0.0)
    if filled <= 0:
        filled = parse_count(body.get("fill_count_fp"), default=0.0)
    if filled <= 0:
        return 0, 0.0, 0.0
    contracts = max(1, int(round(filled)))
    contracts = min(contracts, int(requested))
    avg = parse_dollars(body.get("average_fill_price"), default=0.0)
    fill_price = avg if avg > 0 else live_limit_price(
        signal.side, signal.yes_bid, signal.yes_ask, signal.fill_price
    )
    fee_each = parse_dollars(body.get("average_fee_paid"), default=0.0)
    if fee_each > 0:
        fee = fee_each * contracts
    else:
        fee = quadratic_fee_dollars(fill_price, contracts)
    return contracts, fill_price, fee
