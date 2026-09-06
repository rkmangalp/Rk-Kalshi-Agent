from __future__ import annotations

import time

from rk_kalshi.client import KalshiPublicClient
from rk_kalshi.config import AppConfig
from rk_kalshi.execution import PaperExecution
from rk_kalshi.journal import FillJournal
from rk_kalshi.models import Fill, Signal
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
        markets, latency_ms = self.client.list_tennis_markets()
        marks = {m.ticker: m.yes_mid for m in markets if m.yes_mid is not None}
        if self.risk.kill_switch_hit(state, marks):
            state.killed = True
            state.kill_reason = state.kill_reason or "daily loss kill-switch"
            state.ema = self.signal.dump_ema()
            save_state(self.cfg, state)
            return []

        signals = self.signal.evaluate(markets)
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
