from __future__ import annotations

import time

from rk_kalshi.account import AccountApiError
from rk_kalshi.catalog import filter_markets_by_category
from rk_kalshi.client import KalshiPublicClient
from rk_kalshi.config import AppConfig
from rk_kalshi.execution import (
    LiveKalshiExecution,
    LiveOrderRejected,
    LiveTradingDisabledError,
    PaperExecution,
)
from rk_kalshi.fees import quadratic_fee_dollars
from rk_kalshi.journal import FillJournal
from rk_kalshi.models import (
    Fill,
    MarketSnapshot,
    Signal,
    parse_count,
    parse_dollars,
    select_bitcoin_tradeable,
    select_in_play,
    select_targeted_markets,
)
from rk_kalshi.risk import RiskManager
from rk_kalshi.signal import SignalEngine
from rk_kalshi.llm import LlmResearchTrader
from rk_kalshi.state import apply_fill, load_state, local_now_iso, save_state


class PaperRunner:
    def __init__(
        self,
        cfg: AppConfig,
        client: KalshiPublicClient | None = None,
        llm: LlmResearchTrader | None = None,
        signed_client=None,
    ):
        self.cfg = cfg
        self.client = client or KalshiPublicClient(cfg)
        self.owns_client = client is None
        self.signed_client = signed_client
        self.signal = SignalEngine(cfg)
        self.llm = llm if llm is not None else LlmResearchTrader(cfg)
        self.risk = RiskManager(cfg)
        self.paper = PaperExecution(cfg, self.risk)
        self.live = LiveKalshiExecution(
            cfg, self.risk, client=signed_client, enabled=bool(cfg.live_enabled)
        )
        self.journal = FillJournal(cfg.fill_log_csv, cfg.fill_log_jsonl)
        self.bind_execution()
        self.last_scan: dict = {
            "open": 0,
            "live": 0,
            "bitcoin": 0,
            "next_event_name": "",
            "next_start_iso": "",
            "targeted": 0,
            "target_event_ticker": "",
            "target_market_ticker": "",
            "target_category_id": "",
        }

    def bind_execution(self) -> None:
        self.risk.cfg = self.cfg
        self.paper.cfg = self.cfg
        self.paper.risk = self.risk
        self.live.cfg = self.cfg
        self.live.risk = self.risk
        self.live.client = self.signed_client
        self.live.enabled = bool(self.cfg.live_enabled) and self.signed_client is not None
        self.execution = self.live if self.cfg.live_enabled else self.paper
        self.journal = FillJournal(self.cfg.fill_log_csv, self.cfg.fill_log_jsonl)

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def run_cycles(self, cycles: int, sleep_s: float | None = None) -> list[Fill]:
        fills: list[Fill] = []
        for index in range(cycles):
            fills.extend(self.run_once())
            if index + 1 < cycles:
                time.sleep(self.cfg.cycle_sleep_s if sleep_s is None else sleep_s)
        return fills

    def run_once(self) -> list[Fill]:
        state = load_state(self.cfg)
        self.signal.load_ema(state.ema)
        self.signal.load_mids(state.mid_history)
        now = time.time()
        has_target = bool(self.cfg.target_event_ticker or self.cfg.target_market_ticker)
        targeted: list[MarketSnapshot] = []
        markets: list[MarketSnapshot] = []
        latency_ms = 0.0
        if has_target:
            fetched = getattr(self.client, "list_event_markets", None)
            if callable(fetched):
                try:
                    raw_targeted, latency_ms = fetched(self.cfg.target_event_ticker)
                except (TypeError, ValueError):
                    raw_targeted, latency_ms = [], 0.0
                if isinstance(raw_targeted, list):
                    targeted = raw_targeted
            if not targeted:
                markets, latency_ms = self.client.list_markets()
                targeted = markets
            targeted = select_targeted_markets(
                targeted,
                event_ticker=self.cfg.target_event_ticker,
                market_ticker=self.cfg.target_market_ticker,
            )
            tradeable = targeted
            tennis = [m for m in targeted if m.asset_class == "tennis"]
            bitcoin = [m for m in targeted if m.asset_class == "bitcoin"]
            live, nxt = select_in_play(
                tennis,
                now,
                pre_start_s=max(0.0, self.cfg.live_pre_start_minutes) * 60.0,
                max_duration_s=max(0.1, self.cfg.live_max_hours) * 3600.0,
            )
            self.last_scan = {
                "open": len(targeted),
                "live": len(live),
                "bitcoin": len(bitcoin),
                "next_event_name": nxt.event_name if nxt else "",
                "next_start_iso": (
                    time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(nxt.occurrence_ts))
                    if nxt and nxt.occurrence_ts
                    else ""
                ),
            }
        else:
            markets, latency_ms = self.client.list_markets()
            if (self.cfg.target_category_id or "").strip() and self.cfg.target_category_id.lower() != "all":
                markets = filter_markets_by_category(
                    markets,
                    self.cfg.target_category_id,
                    near_money_low=self.cfg.bitcoin_near_money_low,
                    near_money_high=self.cfg.bitcoin_near_money_high,
                )
            tennis = [m for m in markets if m.asset_class == "tennis"]
            bitcoin = select_bitcoin_tradeable(
                markets,
                near_money_low=self.cfg.bitcoin_near_money_low,
                near_money_high=self.cfg.bitcoin_near_money_high,
            )
            live, nxt = select_in_play(
                tennis,
                now,
                pre_start_s=max(0.0, self.cfg.live_pre_start_minutes) * 60.0,
                max_duration_s=max(0.1, self.cfg.live_max_hours) * 3600.0,
            )
            self.last_scan = {
                "open": len(markets),
                "live": len(live),
                "bitcoin": len(bitcoin),
                "next_event_name": nxt.event_name if nxt else "",
                "next_start_iso": (
                    time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(nxt.occurrence_ts))
                    if nxt and nxt.occurrence_ts
                    else ""
                ),
            }
            pool: list[MarketSnapshot] = []
            if self.cfg.trade_tennis:
                pool.extend(live if self.cfg.live_matches_only else tennis)
            if self.cfg.trade_bitcoin:
                pool.extend(bitcoin)
            tradeable = pool
        if str(self.cfg.signal_mode or "").strip().lower() == "swing":
            tradeable = [m for m in tradeable if m.asset_class == "tennis"]
            self.last_scan["swing_note"] = (
                "Tennis swing: buy dumped cheap YES (GTC), trail bounce, sell same contract. "
                "One order at a time. Kalshi does not publish serve/score."
            )
        self.last_scan["targeted"] = len(targeted)
        self.last_scan["target_event_ticker"] = self.cfg.target_event_ticker
        self.last_scan["target_market_ticker"] = self.cfg.target_market_ticker
        self.last_scan["target_category_id"] = self.cfg.target_category_id
        marks = {m.ticker: m.yes_mid for m in tradeable if m.yes_mid is not None}
        snapshots = {m.ticker: m for m in tradeable}
        taken: list[Fill] = []
        taken.extend(self._reconcile_pending(state, snapshots, marks, latency_ms))
        if self.risk.kill_switch_hit(state, marks):
            state.killed = True
            state.kill_reason = state.kill_reason or "daily loss kill-switch"
            if self.cfg.live_enabled:
                self.live.cancel_open()
            state.ema = self.signal.dump_ema()
            state.mid_history = self.signal.dump_mids()
            save_state(self.cfg, state)
            return taken

        as_signals = self.signal.evaluate(tradeable, inventory=state)
        swing = str(self.cfg.signal_mode or "").strip().lower() == "swing"
        signals = as_signals if swing else self.llm.refine(tradeable, as_signals, inventory=state)
        if not swing and self.llm.last_note:
            self.last_scan["llm_note"] = self.llm.last_note
        cap = 1 if swing else self.cfg.max_signals_per_cycle
        for signal in signals:
            if len(taken) >= cap:
                break
            fill = self._maybe_fill(signal, state, latency_ms, marks)
            if fill is not None:
                taken.append(fill)
                if state.killed:
                    break
        self.last_scan["resting"] = len(state.pending_orders or [])
        state.ema = self.signal.dump_ema()
        state.mid_history = self.signal.dump_mids()
        save_state(self.cfg, state)
        return taken

    def _maybe_fill(
        self,
        signal: Signal,
        state,
        latency_ms: float,
        marks: dict[str, float],
    ) -> Fill | None:
        decision = self.risk.approve(signal, state, marks)
        if not decision.ok:
            return None
        try:
            fill = self.execution.execute(signal, state, decision.contracts, latency_ms, marks)
        except (LiveOrderRejected, LiveTradingDisabledError) as exc:
            self.last_scan["live_error"] = str(exc)
            return None
        if fill is None:
            self.last_scan["resting"] = len(state.pending_orders or [])
            return None
        if fill.mode == "paper":
            self.journal.append(fill)
        if state.killed and self.cfg.live_enabled:
            self.live.cancel_open()
        return fill

    def _reconcile_pending(
        self,
        state,
        snapshots: dict[str, MarketSnapshot],
        marks: dict[str, float],
        latency_ms: float,
    ) -> list[Fill]:
        out: list[Fill] = []
        leftover: list[dict] = []
        for row in list(state.pending_orders or []):
            fill: Fill | None = None
            keep = True
            if row.get("paper"):
                fill = self._fill_paper_pending(
                    row, snapshots.get(str(row.get("ticker") or "")), state, marks, latency_ms
                )
                keep = fill is None
            else:
                fill, keep = self._fill_live_pending(row, state, marks, latency_ms)
            if fill is not None:
                out.append(fill)
                if fill.mode == "paper":
                    self.journal.append(fill)
            if keep:
                leftover.append(row)
        state.pending_orders = leftover
        return out

    def _fill_paper_pending(
        self,
        row: dict,
        snap: MarketSnapshot | None,
        state,
        marks: dict[str, float],
        latency_ms: float,
    ) -> Fill | None:
        if snap is None:
            return None
        side = str(row.get("side") or "")
        limit = float(row.get("price") or 0.0)
        mid = snap.yes_mid
        if side == "buy":
            touch = snap.yes_ask if snap.yes_ask > 0 else mid
            if touch is None or float(touch) > limit + 1e-9:
                return None
            fill_price = min(limit, float(touch))
        else:
            touch = snap.yes_bid if snap.yes_bid > 0 else mid
            if touch is None or float(touch) < limit - 1e-9:
                return None
            fill_price = max(limit, float(touch))
        contracts = int(row.get("contracts") or 0)
        if contracts <= 0:
            return None
        return self._apply_pending_fill(row, state, marks, contracts, fill_price, latency_ms, mode="paper")

    def _fill_live_pending(
        self,
        row: dict,
        state,
        marks: dict[str, float],
        latency_ms: float,
    ) -> tuple[Fill | None, bool]:
        client = self.signed_client or getattr(self.live, "client", None)
        getter = getattr(client, "get_order", None) if client is not None else None
        if not callable(getter):
            return None, True
        try:
            payload = getter(str(row.get("order_id")))
            if isinstance(payload, tuple):
                order = payload[0]
            else:
                order = payload
        except (AccountApiError, TypeError, ValueError, OSError) as exc:
            self.last_scan["live_error"] = str(exc)
            return None, True
        if not isinstance(order, dict):
            return None, True
        inner = order.get("order") if isinstance(order.get("order"), dict) else order
        status = str(inner.get("status") or "").lower()
        filled = parse_count(inner.get("fill_count"), default=0.0)
        if filled <= 0:
            filled = parse_count(inner.get("fill_count_fp"), default=0.0)
        remaining = parse_count(inner.get("remaining_count"), default=0.0)
        if remaining <= 0:
            remaining = parse_count(inner.get("remaining_count_fp"), default=0.0)
        already = int(row.get("filled_so_far") or 0)
        new_fills = max(0, int(round(filled)) - already)
        canceled = status in {"canceled", "cancelled", "expired"}
        done = status in {"executed", "filled"} or remaining <= 0.009
        if new_fills <= 0:
            return None, not (canceled or done)
        avg = parse_dollars(inner.get("average_fill_price"), default=0.0)
        fill_price = avg if avg > 0 else float(row.get("price") or 0.0)
        fill = self._apply_pending_fill(
            row, state, marks, new_fills, fill_price, latency_ms, mode="live"
        )
        row["filled_so_far"] = already + new_fills
        row["contracts"] = max(0, int(round(remaining)))
        return fill, not (canceled or done or remaining <= 0.009)

    def _apply_pending_fill(
        self,
        row: dict,
        state,
        marks: dict[str, float],
        contracts: int,
        fill_price: float,
        latency_ms: float,
        *,
        mode: str,
    ) -> Fill:
        fee = quadratic_fee_dollars(
            fill_price,
            contracts,
            self.cfg.fee_coefficient,
            self.cfg.fee_multiplier,
        )
        ticker = str(row.get("ticker") or "")
        side = str(row.get("side") or "buy")
        realized_delta = apply_fill(
            state,
            ticker=ticker,
            side=side,
            contracts=contracts,
            fill_price=fill_price,
            fee=fee,
        )
        live_mid = float(row.get("live_mid") or fill_price)
        marks = dict(marks or {})
        marks[ticker] = live_mid
        if self.risk.kill_switch_hit(state, marks):
            state.killed = True
            state.kill_reason = state.kill_reason or "daily loss kill-switch"
            if self.cfg.live_enabled:
                self.live.cancel_open()
        return Fill(
            timestamp=local_now_iso(),
            ticker=ticker,
            side=side,
            fill_price=fill_price,
            live_mid=live_mid,
            edge_thesis=str(row.get("thesis") or ""),
            running_pnl=state.running_pnl(marks),
            event_name=str(row.get("event_name") or ""),
            match_id=str(row.get("match_id") or ""),
            edge_cents=float(row.get("edge_cents") or 0.0),
            edge_bps=float(row.get("edge_bps") or 0.0),
            contracts=contracts,
            fee=fee,
            cash_after=state.cash,
            mode=mode,
            latency_ms=float(latency_ms),
            can_size_up=False if mode == "live" else self.risk.can_size_up(state),
            realized_delta=realized_delta,
        )
