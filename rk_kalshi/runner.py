from __future__ import annotations

import time

from rk_kalshi.client import KalshiPublicClient
from rk_kalshi.config import AppConfig
from rk_kalshi.execution import PaperExecution
from rk_kalshi.journal import FillJournal
from rk_kalshi.models import (
    Fill,
    MarketSnapshot,
    Signal,
    select_bitcoin_tradeable,
    select_in_play,
    select_targeted_markets,
)
from rk_kalshi.risk import RiskManager
from rk_kalshi.signal import TennisSignalEngine
from rk_kalshi.state import load_state, save_state


class PaperRunner:
    def __init__(self, cfg: AppConfig, client: KalshiPublicClient | None = None):
        self.cfg = cfg
        self.client = client or KalshiPublicClient(cfg)
        self.owns_client = client is None
        self.signal = TennisSignalEngine(cfg)
        self.risk = RiskManager(cfg)
        self.paper = PaperExecution(cfg, self.risk)
        self.journal = FillJournal(cfg.fill_log_csv, cfg.fill_log_jsonl)
        self.last_scan: dict = {
            "open": 0,
            "live": 0,
            "bitcoin": 0,
            "next_event_name": "",
            "next_start_iso": "",
            "targeted": 0,
            "target_event_ticker": "",
            "target_market_ticker": "",
        }

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
        markets, latency_ms = self.client.list_markets()
        now = time.time()
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
        targeted: list[MarketSnapshot] = []
        has_target = bool(self.cfg.target_event_ticker or self.cfg.target_market_ticker)
        pool: list[MarketSnapshot] = []
        if self.cfg.trade_tennis:
            pool.extend(tennis if has_target else (live if self.cfg.live_matches_only else tennis))
        if self.cfg.trade_bitcoin:
            pool.extend(bitcoin)
        if has_target:
            targeted = select_targeted_markets(
                pool,
                event_ticker=self.cfg.target_event_ticker,
                market_ticker=self.cfg.target_market_ticker,
            )
            tradeable = targeted
        else:
            tradeable = pool
        self.last_scan["targeted"] = len(targeted)
        self.last_scan["target_event_ticker"] = self.cfg.target_event_ticker
        self.last_scan["target_market_ticker"] = self.cfg.target_market_ticker
        marks = {m.ticker: m.yes_mid for m in tradeable if m.yes_mid is not None}
        if self.risk.kill_switch_hit(state, marks):
            state.killed = True
            state.kill_reason = state.kill_reason or "daily loss kill-switch"
            state.ema = self.signal.dump_ema()
            save_state(self.cfg, state)
            return []

        signals = self.signal.evaluate(tradeable)
        taken: list[Fill] = []
        for signal in signals:
            if len(taken) >= self.cfg.max_signals_per_cycle:
                break
            fill = self._maybe_fill(signal, state, latency_ms, marks)
            if fill is not None:
                taken.append(fill)
                if state.killed:
                    break
        state.ema = self.signal.dump_ema()
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
        fill = self.paper.execute(signal, state, decision.contracts, latency_ms, marks)
        self.journal.append(fill)
        return fill
