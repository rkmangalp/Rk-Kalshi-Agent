"""Local FastAPI dashboard wrapping the paper-trade stack.

Binds to localhost by default. Live order submission is opt-in with hard caps.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import webbrowser
from datetime import datetime
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from rk_kalshi.account import (
    AccountApiError,
    AccountNotConnectedError,
    AccountService,
    default_store_path,
)
from rk_kalshi.auth import AccountAuthError
from rk_kalshi.catalog import (
    apply_category,
    catalog_events,
    catalog_series_tickers,
    categories_payload,
    normalize_category_id,
    trade_flags_for_category,
)
from rk_kalshi.client import KalshiPublicClient
from rk_kalshi.config import AppConfig, load_config
from rk_kalshi.journal import clear_fill_logs, read_fills, summarize_pnl
from rk_kalshi.kalshi_url import EXAMPLE_CONTRACT_URLS, KalshiUrlError, parse_contract
from rk_kalshi.live_caps import (
    LIVE_CONFIRM_MESSAGE,
    LIVE_CONNECT_REQUIRED,
    LiveStartError,
    clamp_live_daily_loss,
    clamp_live_dollars,
    live_caps_payload,
    require_live_credentials,
)
from rk_kalshi.models import MarketSnapshot, select_bitcoin_tradeable
from rk_kalshi.presets import (
    apply_trade_style,
    presets_payload,
    trade_style as preset_for,
)
from rk_kalshi.risk import RiskManager
from rk_kalshi.runner import PaperRunner
from rk_kalshi.schema import FILL_FIELDS
from rk_kalshi.signal import SIGNAL_DISCLAIMER, algorithm_label
from rk_kalshi.llm import openai_configured
from rk_kalshi.state import load_state, local_now_iso, new_state, save_state

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_CYCLES = 500
MAX_SLEEP_S = 3600.0
_NUMERIC_FILL_FIELDS = {
    "fill_price",
    "live_mid",
    "running_pnl",
    "edge_cents",
    "edge_bps",
    "fee",
    "cash_after",
    "latency_ms",
    "realized_delta",
    "contracts",
}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _reject_live(live: bool | None, mode: str | None) -> None:
    if live is True:
        raise ValueError("this endpoint is paper-only; use Start with Live confirmation to place real orders")
    if mode is not None and str(mode).strip().lower() == "live":
        raise ValueError("this endpoint is paper-only; use Start with Live confirmation to place real orders")


def _wants_live(live: bool | None, mode: str | None) -> bool:
    if live is True:
        return True
    return mode is not None and str(mode).strip().lower() == "live"


class RunRequest(BaseModel):
    cycles: int = Field(default=1, ge=1, le=MAX_CYCLES)
    sleep_s: float | None = Field(default=None, ge=0.0, le=MAX_SLEEP_S)
    live: bool | None = None
    mode: str | None = None

    @model_validator(mode="after")
    def reject_live(self) -> "RunRequest":
        _reject_live(self.live, self.mode)
        return self


class StartRequest(BaseModel):
    starting_cash: float = Field(default=100.0, gt=0, le=100_000)
    max_dollars_per_ticker: float = Field(default=5.0, gt=0, le=10_000)
    daily_loss_limit: float = Field(default=15.0, gt=0, le=100_000)
    sleep_s: float | None = Field(default=None, ge=0.0, le=MAX_SLEEP_S)
    continuous: bool = True
    cycles: int | None = Field(default=None, ge=1, le=MAX_CYCLES)
    live_matches_only: bool = True
    trade_bitcoin: bool = True
    trade_tennis: bool = True
    signal_mode: str | None = None
    target_url: str | None = None
    target_event_ticker: str | None = None
    target_market_ticker: str | None = None
    trade_style: str | None = None
    category_id: str | None = None
    live: bool | None = None
    mode: str | None = None
    confirm_live: bool = False
    understand_real_money: bool = False

    @model_validator(mode="after")
    def live_requires_confirmation(self) -> "StartRequest":
        if not _wants_live(self.live, self.mode):
            return self
        if not (self.confirm_live and self.understand_real_money):
            raise ValueError(LIVE_CONFIRM_MESSAGE)
        return self


class ConnectRequest(BaseModel):
    enable_live_trading: bool | None = None
    live: bool | None = None
    mode: str | None = None
    api_key_id: str | None = None
    private_key_path: str | None = None
    private_key_pem: str | None = None
    openai_api_key: str | None = None
    environment: str | None = None

    @model_validator(mode="after")
    def reject_live_and_pasted_secrets(self) -> "ConnectRequest":
        _reject_live(self.live, self.mode)
        if self.enable_live_trading:
            raise ValueError(
                "Connecting does not enable live orders. Use the Live toggle after Connect."
            )
        pasted = any(
            str(value or "").strip()
            for value in (self.api_key_id, self.private_key_path, self.private_key_pem, self.openai_api_key)
        )
        if pasted:
            raise ValueError(
                "Do not paste API keys in the UI. Set KALSHI_API_KEY_ID, "
                "KALSHI_PRIVATE_KEY_PATH, and OPENAI_API_KEY in a local .env."
            )
        return self


class ContractRequest(BaseModel):
    url: str | None = None
    event_ticker: str | None = None
    market_ticker: str | None = None
    live: bool | None = None
    mode: str | None = None


class LiveArmRequest(BaseModel):
    enabled: bool = False
    confirm_live: bool = False
    understand_real_money: bool = False

    @model_validator(mode="after")
    def confirm_when_enabling(self) -> "LiveArmRequest":
        if self.enabled and not (self.confirm_live and self.understand_real_money):
            raise ValueError(LIVE_CONFIRM_MESSAGE)
        return self


class RunController:
    """Background paper-run loop with a pollable log buffer."""

    def __init__(self, runner: PaperRunner, cfg: AppConfig):
        self.runner = runner
        self.cfg = cfg
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.running = False
        self.stopping = False
        self.continuous = False
        self.logs: deque[str] = deque(maxlen=300)
        self.cycles_done = 0
        self.cycles_total = 0
        self.fills_this_run = 0
        self.last_error: str | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "stopping": self.stopping,
                "continuous": self.continuous,
                "cycles_done": self.cycles_done,
                "cycles_total": self.cycles_total,
                "fills_this_run": self.fills_this_run,
                "last_error": self.last_error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "logs": list(self.logs),
                "mode": "live" if self.cfg.live_enabled else "paper",
            }

    def start(
        self,
        cycles: int,
        sleep_s: float | None,
        continuous: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if self.running:
                raise RuntimeError("paper-run already in progress")
            self.running = True
            self.stopping = False
            self.continuous = continuous
            self.cycles_done = 0
            self.cycles_total = 0 if continuous else cycles
            self.fills_this_run = 0
            self.last_error = None
            self.started_at = local_now_iso()
            self.finished_at = None
            self.logs.clear()
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                args=(cycles, sleep_s, continuous),
                name="paper-run",
                daemon=True,
            )
            thread.start()
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self.running:
                return self.snapshot()
            self.stopping = True
        self._stop.set()
        self._log("stop requested — finishing current cycle")
        return self.snapshot()

    def clear_logs(self) -> dict[str, Any]:
        with self._lock:
            self.logs.clear()
            self.last_error = None
        self._log("logs cleared")
        return self.snapshot()

    def reset_ui_state(self) -> dict[str, Any]:
        with self._lock:
            self.logs.clear()
            self.last_error = None
            self.fills_this_run = 0
            self.cycles_done = 0
            self.started_at = None
            self.finished_at = None
        self._log("paper session cleared — local paper data only; live Kalshi account is unchanged")
        return self.snapshot()

    def reset_view_only(self) -> dict[str, Any]:
        with self._lock:
            self.logs.clear()
            self.last_error = None
            self.fills_this_run = 0
        self._log(
            "local session view cleared — Kalshi orders were not cancelled; "
            "paper journal is unchanged"
        )
        return self.snapshot()

    def _log(self, message: str) -> None:
        line = f"{local_now_iso()}  {message}"
        with self._lock:
            self.logs.append(line)

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._stop.wait(seconds)

    def _run(self, cycles: int, sleep_s: float | None, continuous: bool) -> None:
        interval = self.cfg.cycle_sleep_s if sleep_s is None else sleep_s
        try:
            if continuous:
                target = self.cfg.target_event_ticker or self.cfg.target_label
                desk = "LIVE (real money)" if self.cfg.live_enabled else "PAPER MODE"
                caps = (
                    f"max ${self.cfg.max_dollars_per_ticker:g}/trade · "
                    f"daily kill ${self.cfg.daily_loss_limit:g} · can_size_up locked"
                )
                if target:
                    kind = self.cfg.target_asset_class or "contract"
                    self._log(
                        f"{desk} — Start: {kind} {target} only; {caps}"
                    )
                else:
                    self._log(
                        f"{desk} — Start: polling selected books until Stop; {caps}"
                    )
            else:
                desk = "LIVE (real money)" if self.cfg.live_enabled else "PAPER MODE ONLY"
                self._log(
                    f"{desk} — starting {cycles} cycle(s); can_size_up stays locked"
                )
            index = 0
            while not self._stop.is_set():
                if not continuous and index >= cycles:
                    break
                label = f"{index + 1}" if continuous else f"{index + 1}/{cycles}"
                if self.cfg.target_event_ticker:
                    kind = self.cfg.target_asset_class or "contract"
                    self._log(
                        f"cycle {label}: scanning selected {kind} "
                        f"{self.cfg.target_event_ticker}"
                    )
                else:
                    self._log(f"cycle {label}: scanning Bitcoin and tennis markets")
                fills = self.runner.run_once()
                scan = getattr(self.runner, "last_scan", None) or {}
                if scan:
                    self._log(
                        f"  btc {scan.get('bitcoin', 0)} / live tennis {scan.get('live', 0)} "
                        f"/ open {scan.get('open', 0)} "
                        f"(live-matches-only={self.cfg.live_matches_only} "
                        f"btc={self.cfg.trade_bitcoin} tennis={self.cfg.trade_tennis})"
                    )
                    if scan.get("target_event_ticker"):
                        self._log(
                            f"  selected match {scan.get('target_event_ticker')} "
                            f"contracts={scan.get('targeted', 0)}"
                        )
                    if scan.get("llm_note"):
                        self._log(f"  ChatGPT: {scan.get('llm_note')}")
                    if (
                        self.cfg.trade_tennis
                        and self.cfg.live_matches_only
                        and not self.cfg.target_event_ticker
                        and not scan.get("live")
                    ):
                        nxt = scan.get("next_event_name") or "none scheduled"
                        when = scan.get("next_start_iso") or "n/a"
                        self._log(f"  no in-play tennis — next: {nxt} at {when}")
                with self._lock:
                    self.cycles_done = index + 1
                    self.fills_this_run += len(fills)
                if fills:
                    for fill in fills:
                        tag = "LIVE FILL" if fill.mode == "live" else "FILL"
                        self._log(
                            f"{tag} {fill.side:4} {fill.ticker} mid={fill.live_mid:.4f} "
                            f"edge={fill.edge_cents:.2f}¢ pnl={fill.running_pnl:.4f} "
                            f"can_size_up={fill.can_size_up}"
                        )
                        self._log(f"  thesis: {fill.edge_thesis}")
                else:
                    err = (getattr(self.runner, "last_scan", None) or {}).get("live_error")
                    if self.cfg.live_enabled:
                        extra = f" ({err})" if err else ""
                        self._log(
                            "no live fills: net edge after spread+fee did not clear "
                            f"threshold, risk blocked, or the order did not fill{extra}"
                        )
                    else:
                        self._log(
                            "no paper fills: net edge after spread+fee did not clear "
                            "threshold, or risk blocked"
                        )
                index += 1
                if not continuous and index >= cycles:
                    break
                if self._stop.is_set():
                    break
                self._log(f"sleep {interval:g}s before next cycle")
                self._sleep(interval)
            if self._stop.is_set():
                self._log(
                    f"stopped cycles={self.cycles_done} fills_this_run={self.fills_this_run} "
                    "can_size_up=false (locked)"
                )
            else:
                self._log(
                    f"done cycles={self.cycles_done} fills_this_run={self.fills_this_run} "
                    f"can_size_up=false (locked allow_size_up={self.cfg.allow_size_up})"
                )
        except Exception as exc:  # noqa: BLE001 — surface in the UI log
            self.last_error = str(exc)
            self._log(f"error: {exc}")
        finally:
            with self._lock:
                self.running = False
                self.stopping = False
                self.finished_at = local_now_iso()


class DashboardService:
    def __init__(
        self,
        cfg: AppConfig,
        client: KalshiPublicClient | None = None,
        runner: PaperRunner | None = None,
        config_path: Path | None = None,
        account: AccountService | None = None,
    ):
        self.cfg = cfg
        self.config_path = Path(config_path) if config_path else None
        self.client = client or KalshiPublicClient(cfg)
        self.owns_client = client is None
        self.runner = runner or PaperRunner(cfg, client=self.client)
        self.controller = RunController(self.runner, cfg)
        self.account = account or AccountService(
            store_path=default_store_path(cfg.state_path.parent),
            timeout_s=cfg.request_timeout_s,
        )
        self.owns_account = account is None
        self.last_contract_error = ""
        self._paper_state_path = cfg.state_path
        self._paper_fill_csv = cfg.fill_log_csv
        self._paper_fill_jsonl = cfg.fill_log_jsonl
        self.runner.signed_client = self.account.signed_client()
        self.runner.bind_execution()

    def target_payload(self, cfg: AppConfig | None = None) -> dict[str, Any]:
        return _target_payload(cfg or self.cfg, self.last_contract_error)

    def close(self) -> None:
        self.runner.close()
        if self.owns_client:
            self.client.close()
        if self.owns_account:
            self.account.close()

    def bind_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.controller.cfg = cfg
        runner = self.runner
        runner.cfg = cfg
        if getattr(runner, "signal", None) is not None:
            runner.signal.cfg = cfg
        if getattr(runner, "llm", None) is not None:
            runner.llm.cfg = cfg
        if getattr(runner, "risk", None) is not None:
            runner.risk.cfg = cfg
        if getattr(runner, "paper", None) is not None:
            runner.paper.cfg = cfg
        runner.signed_client = self.account.signed_client()
        if hasattr(runner, "bind_execution"):
            runner.bind_execution()

    def apply_session(
        self,
        starting_cash: float,
        max_dollars_per_ticker: float,
        daily_loss_limit: float,
        cycle_sleep_s: float | None = None,
        live_matches_only: bool = True,
        trade_bitcoin: bool = True,
        trade_tennis: bool = True,
        signal_mode: str | None = None,
        target_url: str | None = None,
        target_event_ticker: str | None = None,
        target_market_ticker: str | None = None,
        target_label: str | None = None,
        trade_style: str | None = None,
        category_id: str | None = None,
        live: bool = False,
    ) -> dict[str, Any]:
        sleep_s = self.cfg.cycle_sleep_s if cycle_sleep_s is None else cycle_sleep_s
        cfg = replace(
            self.cfg,
            live_enabled=False,
            state_path=self._paper_state_path,
            fill_log_csv=self._paper_fill_csv,
            fill_log_jsonl=self._paper_fill_jsonl,
        )
        if (trade_style or "").strip():
            cfg = apply_trade_style(cfg, trade_style)
        if (category_id or "").strip():
            cfg = apply_category(cfg, category_id)
            trade_tennis, trade_bitcoin = trade_flags_for_category(cfg.target_category_id)
        cfg = replace(
            cfg,
            starting_cash=float(starting_cash),
            max_dollars_per_ticker=float(max_dollars_per_ticker),
            daily_loss_limit=float(daily_loss_limit),
            cycle_sleep_s=float(sleep_s),
            live_matches_only=bool(live_matches_only),
            trade_bitcoin=bool(trade_bitcoin),
            trade_tennis=bool(trade_tennis),
            signal_mode=self.cfg.signal_mode if signal_mode is None else str(signal_mode),
            target_url=self.cfg.target_url if target_url is None else target_url,
            target_event_ticker=(
                self.cfg.target_event_ticker if target_event_ticker is None else target_event_ticker
            ),
            target_market_ticker=(
                self.cfg.target_market_ticker if target_market_ticker is None else target_market_ticker
            ),
            target_label=self.cfg.target_label if target_label is None else target_label,
            target_asset_class=self.cfg.target_asset_class,
            live_enabled=False,
            allow_size_up=False,
        )
        if live:
            cfg = self._live_cfg(cfg, max_dollars_per_ticker, daily_loss_limit)
        self.bind_config(cfg)
        applied = _apply_bankroll_state(cfg)
        persist_cfg = replace(
            cfg,
            live_enabled=False,
            state_path=self._paper_state_path,
            fill_log_csv=self._paper_fill_csv,
            fill_log_jsonl=self._paper_fill_jsonl,
            max_dollars_per_ticker=float(max_dollars_per_ticker),
            daily_loss_limit=float(daily_loss_limit),
        )
        persisted = _persist_session(persist_cfg, self.config_path)
        preset = preset_for(cfg.trade_style)
        return {
            "paper_mode": not cfg.live_enabled,
            "live_enabled": bool(cfg.live_enabled),
            "live_armed": self.account.orders_enabled,
            "live_caps": live_caps_payload(),
            "starting_cash": cfg.starting_cash,
            "max_dollars_per_ticker": cfg.max_dollars_per_ticker,
            "daily_loss_limit": cfg.daily_loss_limit,
            "cycle_sleep_s": cfg.cycle_sleep_s,
            "live_matches_only": cfg.live_matches_only,
            "trade_bitcoin": cfg.trade_bitcoin,
            "trade_tennis": cfg.trade_tennis,
            "signal_mode": cfg.signal_mode,
            "trade_style": cfg.trade_style,
            "trade_style_blurb": preset.blurb,
            "target_category_id": cfg.target_category_id,
            "edge_threshold_cents": cfg.edge_threshold_cents,
            "gamma": cfg.gamma,
            "kappa": cfg.kappa,
            "base_contracts": cfg.base_contracts,
            "max_spread_cents": cfg.max_spread_cents,
            "target": self.target_payload(cfg),
            "can_size_up": False,
            "allow_size_up": cfg.allow_size_up,
            "state": applied,
            "persisted": persisted,
        }

    def set_contract(self, body: ContractRequest) -> dict[str, Any]:
        raw = (body.url or body.market_ticker or body.event_ticker or "").strip()
        if not raw:
            self.last_contract_error = ""
            cfg = replace(
                self.cfg,
                target_url="",
                target_event_ticker="",
                target_market_ticker="",
                target_label="",
                target_asset_class="",
            )
            self.bind_config(cfg)
            _persist_session(cfg, self.config_path)
            return {
                "paper_mode": not cfg.live_enabled,
                "live_enabled": bool(cfg.live_enabled),
                "contract": None,
                "target": self.target_payload(cfg),
            }
        try:
            parsed = parse_contract(raw)
        except KalshiUrlError as exc:
            self.last_contract_error = str(exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        self.last_contract_error = ""
        label = parsed.event_ticker
        cfg = replace(
            self.cfg,
            target_url=parsed.raw if parsed.source == "url" else "",
            target_event_ticker=parsed.event_ticker,
            target_market_ticker=parsed.market_ticker or "",
            target_label=label,
            target_asset_class=parsed.asset_class,
            trade_tennis=True if parsed.asset_class == "tennis" else self.cfg.trade_tennis,
            trade_bitcoin=True if parsed.asset_class == "bitcoin" else self.cfg.trade_bitcoin,
        )
        self.bind_config(cfg)
        _persist_session(cfg, self.config_path)
        return {
            "paper_mode": not cfg.live_enabled,
            "live_enabled": bool(cfg.live_enabled),
            "contract": parsed.as_dict() | {"label": label},
            "target": self.target_payload(cfg),
        }

    def arm_live(self) -> dict[str, Any]:
        status = self.account.snapshot()
        if status.get("status") != "connected":
            raise LiveStartError(LIVE_CONNECT_REQUIRED)
        creds = self.account.credentials()
        require_live_credentials(creds)
        return self.account.set_orders_enabled(True)

    def disarm_live(self, *, cancel_open: bool = False) -> dict[str, Any]:
        if cancel_open:
            live_exec = getattr(self.runner, "live", None)
            if live_exec is not None:
                try:
                    live_exec.cancel_open(force=True)
                except Exception:
                    pass
        snapshot = self.account.set_orders_enabled(False)
        if self.cfg.live_enabled:
            cfg = replace(
                self.cfg,
                live_enabled=False,
                state_path=self._paper_state_path,
                fill_log_csv=self._paper_fill_csv,
                fill_log_jsonl=self._paper_fill_jsonl,
            )
            self.bind_config(cfg)
        return snapshot

    def _live_cfg(self, cfg: AppConfig, max_dollars: float, daily_loss: float) -> AppConfig:
        live_cfg = replace(
            cfg,
            live_enabled=True,
            allow_size_up=False,
            allow_martingale=False,
            max_dollars_per_ticker=clamp_live_dollars(max_dollars),
            daily_loss_limit=clamp_live_daily_loss(daily_loss),
            state_path=cfg.live_state_path,
        )
        return self._seed_live_state(live_cfg)

    def _seed_live_state(self, cfg: AppConfig) -> AppConfig:
        path = cfg.state_path
        if path.exists() and path.stat().st_size > 0:
            return cfg
        cash = cfg.starting_cash
        try:
            book = self.account.portfolio()
            if book.get("balance") is not None:
                cash = float(book["balance"])
        except Exception:
            pass
        seeded = replace(cfg, starting_cash=cash)
        save_state(seeded, new_state(seeded))
        return seeded

    def clear_session(self) -> dict[str, Any]:
        if self.controller.snapshot()["running"]:
            raise HTTPException(status_code=409, detail="Stop the paper-run before Clear")
        paper_cfg = replace(
            self.cfg,
            state_path=self._paper_state_path,
            fill_log_csv=self._paper_fill_csv,
            fill_log_jsonl=self._paper_fill_jsonl,
            live_enabled=False,
        )
        archived = _archive_paper_session(paper_cfg)
        clear_fill_logs(paper_cfg.fill_log_csv, paper_cfg.fill_log_jsonl)
        save_state(paper_cfg, new_state(paper_cfg))
        cfg = replace(
            paper_cfg,
            target_url="",
            target_event_ticker="",
            target_market_ticker="",
            target_label="",
            target_asset_class="",
            target_category_id="",
            live_enabled=False,
        )
        self.last_contract_error = ""
        self.bind_config(cfg)
        _persist_session(cfg, self.config_path)
        run = self.controller.reset_ui_state()
        return {
            "paper_mode": True,
            "live_enabled": False,
            "archived_to": archived,
            "target": self.target_payload(cfg),
            "run": run,
            "cleared": True,
            "note": "Cleared local paper session only — not a live Kalshi account.",
        }

    def clear_view(self) -> dict[str, Any]:
        if self.controller.snapshot()["running"]:
            raise HTTPException(
                status_code=409,
                detail="Stop before clearing the local session view",
            )
        run = self.controller.reset_view_only()
        return {
            "paper_mode": not self.cfg.live_enabled,
            "live_enabled": bool(self.cfg.live_enabled),
            "target": self.target_payload(),
            "run": run,
            "cleared": True,
            "cleared_view_only": True,
            "note": (
                "Cleared local session view only — Kalshi orders were not cancelled "
                "and the paper journal is unchanged. Use Cancel open Kalshi orders "
                "if you intend to cancel-all resting orders from this desk."
            ),
        }

    def cancel_open_live_orders(self) -> dict[str, Any]:
        live_exec = getattr(self.runner, "live", None)
        cancelled = 0
        if live_exec is not None:
            try:
                cancelled = int(live_exec.cancel_open(force=True) or 0)
            except Exception as exc:  # noqa: BLE001 — surface to the Live desk
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "cancelled": cancelled,
            "live_enabled": bool(self.cfg.live_enabled),
            "note": (
                f"Cancel open Kalshi orders: {cancelled} request(s) sent. "
                "This control is explicit cancel — Clear view does not cancel-all."
            ),
        }


def _target_payload(cfg: AppConfig, error: str = "") -> dict[str, Any]:
    return {
        "url": cfg.target_url,
        "event_ticker": cfg.target_event_ticker,
        "market_ticker": cfg.target_market_ticker,
        "match_id": cfg.target_event_ticker,
        "label": cfg.target_label or cfg.target_event_ticker,
        "asset_class": cfg.target_asset_class,
        "active": bool(cfg.target_event_ticker or cfg.target_market_ticker),
        "error": error,
        "examples": list(EXAMPLE_CONTRACT_URLS),
    }


def _archive_paper_session(cfg: AppConfig) -> str | None:
    files = [cfg.fill_log_csv, cfg.fill_log_jsonl, cfg.state_path]
    existing = [path for path in files if path.exists() and path.stat().st_size > 0]
    if not existing:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = cfg.state_path.parent / "archive" / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for path in existing:
        shutil.copy2(path, dest / path.name)
    return str(dest)


def _session_path(cfg: AppConfig) -> Path:
    return cfg.state_path.parent / "dashboard_session.json"


def _read_session(cfg: AppConfig) -> dict[str, Any]:
    path = _session_path(cfg)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _cfg_from_session(cfg: AppConfig) -> AppConfig:
    raw = _read_session(cfg)
    if not raw:
        return cfg
    restored = replace(
        cfg,
        starting_cash=float(raw.get("starting_cash", cfg.starting_cash)),
        max_dollars_per_ticker=float(raw.get("max_dollars_per_ticker", cfg.max_dollars_per_ticker)),
        daily_loss_limit=float(raw.get("daily_loss_limit", cfg.daily_loss_limit)),
        cycle_sleep_s=float(raw.get("cycle_sleep_s", cfg.cycle_sleep_s)),
        live_matches_only=bool(raw.get("live_matches_only", cfg.live_matches_only)),
        trade_bitcoin=bool(raw.get("trade_bitcoin", cfg.trade_bitcoin)),
        trade_tennis=bool(raw.get("trade_tennis", cfg.trade_tennis)),
        signal_mode=str(raw.get("signal_mode") or cfg.signal_mode),
        target_url=str(raw.get("target_url") or cfg.target_url),
        target_event_ticker=str(raw.get("target_event_ticker") or cfg.target_event_ticker),
        target_market_ticker=str(raw.get("target_market_ticker") or cfg.target_market_ticker),
        target_label=str(raw.get("target_label") or cfg.target_label),
        target_asset_class=str(raw.get("target_asset_class") or cfg.target_asset_class),
        live_enabled=False,
    )
    style = str(raw.get("trade_style") or "")
    if style:
        restored = apply_trade_style(restored, style)
    category = str(raw.get("target_category_id") or "")
    if category:
        restored = apply_category(restored, category)
    return replace(
        restored,
        starting_cash=float(raw.get("starting_cash", restored.starting_cash)),
        max_dollars_per_ticker=float(raw.get("max_dollars_per_ticker", restored.max_dollars_per_ticker)),
        daily_loss_limit=float(raw.get("daily_loss_limit", restored.daily_loss_limit)),
        cycle_sleep_s=float(raw.get("cycle_sleep_s", restored.cycle_sleep_s)),
        live_matches_only=bool(raw.get("live_matches_only", restored.live_matches_only)),
        signal_mode=str(raw.get("signal_mode") or restored.signal_mode),
        target_url=str(raw.get("target_url") or restored.target_url),
        target_event_ticker=str(raw.get("target_event_ticker") or restored.target_event_ticker),
        target_market_ticker=str(raw.get("target_market_ticker") or restored.target_market_ticker),
        target_label=str(raw.get("target_label") or restored.target_label),
        target_asset_class=str(raw.get("target_asset_class") or restored.target_asset_class),
        live_enabled=False,
    )


def _persist_session(cfg: AppConfig, config_path: Path | None) -> dict[str, bool]:
    payload = {
        "starting_cash": cfg.starting_cash,
        "max_dollars_per_ticker": cfg.max_dollars_per_ticker,
        "daily_loss_limit": cfg.daily_loss_limit,
        "cycle_sleep_s": cfg.cycle_sleep_s,
        "live_matches_only": cfg.live_matches_only,
        "trade_bitcoin": cfg.trade_bitcoin,
        "trade_tennis": cfg.trade_tennis,
        "signal_mode": cfg.signal_mode,
        "trade_style": cfg.trade_style,
        "target_category_id": cfg.target_category_id,
        "target_url": cfg.target_url,
        "target_event_ticker": cfg.target_event_ticker,
        "target_market_ticker": cfg.target_market_ticker,
        "target_label": cfg.target_label,
        "target_asset_class": cfg.target_asset_class,
        "paper_mode": True,
        "live_enabled": False,
    }
    path = _session_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    wrote_yaml = False
    if config_path is not None and config_path.exists():
        wrote_yaml = _patch_config_yaml(config_path, cfg)
    return {"session_json": True, "config_yaml": wrote_yaml}


def _yaml_num(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}.0"
    return repr(float(value))


def _patch_config_yaml(path: Path, cfg: AppConfig) -> bool:
    text = path.read_text()
    updates = {
        "starting_cash": cfg.starting_cash,
        "max_dollars_per_ticker": cfg.max_dollars_per_ticker,
        "daily_loss_limit": cfg.daily_loss_limit,
        "cycle_sleep_s": cfg.cycle_sleep_s,
    }
    patched = text
    for key, value in updates.items():
        pattern = re.compile(rf"^(\s*{re.escape(key)}:\s*)[-+0-9.eE]+", re.M)
        patched, count = pattern.subn(rf"\g<1>{_yaml_num(value)}", patched, count=1)
        if count != 1:
            return False
    if patched != text:
        path.write_text(patched)
    return True


def _apply_bankroll_state(cfg: AppConfig) -> str:
    state = load_state(cfg)
    has_book = bool(state.positions) or state.fill_count > 0
    if not has_book:
        save_state(cfg, new_state(cfg))
        return "initialized"
    if abs(state.starting_cash - cfg.starting_cash) <= 1e-9:
        return "unchanged"
    delta = cfg.starting_cash - state.starting_cash
    state.starting_cash = cfg.starting_cash
    state.cash += delta
    state.start_of_day_equity += delta
    save_state(cfg, state)
    return "rebased"


def _coerce_fill(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in FILL_FIELDS:
        value = row.get(field, "")
        if field in _NUMERIC_FILL_FIELDS:
            try:
                parsed = float(value) if value not in (None, "") else 0.0
            except (TypeError, ValueError):
                parsed = 0.0
            if field == "contracts":
                out[field] = int(parsed)
            else:
                out[field] = parsed
        elif field == "can_size_up":
            out[field] = str(value).strip().lower() in {"1", "true", "yes"}
        else:
            out[field] = value
    return out


def _market_payload(market: MarketSnapshot) -> dict[str, Any]:
    return {
        "ticker": market.ticker,
        "event_name": market.event_name,
        "match_id": market.match_id,
        "event_ticker": market.event_ticker,
        "title": market.title,
        "yes_bid": market.yes_bid,
        "yes_ask": market.yes_ask,
        "yes_bid_size": market.yes_bid_size,
        "yes_ask_size": market.yes_ask_size,
        "order_book_imbalance": market.order_book_imbalance,
        "yes_mid": market.yes_mid,
        "last_price": market.last_price,
        "spread_cents": market.spread_cents,
        "volume": market.volume,
        "status": market.status,
        "series_ticker": market.series_ticker,
        "occurrence_ts": market.occurrence_ts,
        "in_play": market.is_in_play(time.time()),
        "asset_class": market.asset_class,
    }


def _pnl_payload(service: DashboardService) -> dict[str, Any]:
    rows = read_fills(service.cfg.fill_log_csv)
    summary = summarize_pnl(rows)
    state = load_state(service.cfg)
    risk = RiskManager(service.cfg)
    summary["can_size_up"] = risk.can_size_up(state)
    summary["killed"] = state.killed
    summary["kill_reason"] = state.kill_reason
    summary["cash"] = state.cash
    summary["starting_cash"] = state.starting_cash
    if state.fill_count:
        summary["running_pnl"] = state.running_pnl()
    summary["fill_count_state"] = state.fill_count
    summary["allow_size_up"] = service.cfg.allow_size_up
    summary["min_fills_before_size_up"] = service.cfg.min_fills_before_size_up
    summary["max_dollars_per_ticker"] = service.cfg.max_dollars_per_ticker
    summary["daily_loss_limit"] = service.cfg.daily_loss_limit
    summary["paper_mode"] = not service.cfg.live_enabled
    summary["live_enabled"] = bool(service.cfg.live_enabled)
    return summary


def _mode_flags(service: DashboardService) -> dict[str, Any]:
    live_on = bool(service.cfg.live_enabled)
    account = service.account.snapshot()
    connected = account.get("status") == "connected"
    armed = bool(service.account.orders_enabled) and connected
    if live_on:
        banner = (
            f"LIVE DESK — REAL MONEY · max ${service.cfg.max_dollars_per_ticker:g}/trade · "
            f"daily kill ${service.cfg.daily_loss_limit:g}"
        )
    elif armed:
        banner = "LIVE ARMED — next Start can spend REAL MONEY; paper remains selectable"
    else:
        banner = "PAPER MODE ONLY — no live orders"
    return {
        "paper_mode": not live_on,
        "mode": "live" if live_on else "paper",
        "banner": banner,
        "live_enabled": live_on,
        "live_armed": armed,
        "live_trading_available": connected,
        "live_caps": live_caps_payload(),
    }


def _status_payload(service: DashboardService) -> dict[str, Any]:
    state = load_state(service.cfg)
    risk = RiskManager(service.cfg)
    flags = _mode_flags(service)
    return {
        **flags,
        "can_size_up": risk.can_size_up(state),
        "allow_size_up": service.cfg.allow_size_up,
        "min_fills_before_size_up": service.cfg.min_fills_before_size_up,
        "fill_count": state.fill_count,
        "killed": state.killed,
        "kill_reason": state.kill_reason,
        "starting_cash": state.starting_cash,
        "cash": state.cash,
        "max_dollars_per_ticker": service.cfg.max_dollars_per_ticker,
        "daily_loss_limit": service.cfg.daily_loss_limit,
        "cycle_sleep_s": service.cfg.cycle_sleep_s,
        "series_tickers": list(service.cfg.enabled_series_tickers()),
        "edge_threshold_cents": service.cfg.edge_threshold_cents,
        "signal_algorithm": algorithm_label(service.cfg.signal_mode),
        "signal_disclaimer": SIGNAL_DISCLAIMER,
        "signal_mode": service.cfg.signal_mode,
        "llm_model": service.cfg.llm_model,
        "openai_configured": openai_configured(),
        "gamma": service.cfg.gamma,
        "kappa": service.cfg.kappa,
        "base_contracts": service.cfg.base_contracts,
        "max_spread_cents": service.cfg.max_spread_cents,
        "use_ema_fallback": service.cfg.use_ema_fallback,
        "live_matches_only": service.cfg.live_matches_only,
        "trade_bitcoin": service.cfg.trade_bitcoin,
        "trade_tennis": service.cfg.trade_tennis,
        "trade_style": service.cfg.trade_style,
        "trade_style_blurb": preset_for(service.cfg.trade_style).blurb,
        "target_category_id": service.cfg.target_category_id,
        "categories": categories_payload(),
        "bitcoin_series_tickers": list(service.cfg.bitcoin_series_tickers),
        "target": service.target_payload(),
        "run": service.controller.snapshot(),
        "account": service.account.snapshot(),
        "account_environment_default": service.cfg.account_environment,
    }


def create_app(
    cfg: AppConfig | None = None,
    client: KalshiPublicClient | None = None,
    runner: PaperRunner | None = None,
    config_path: Path | None = None,
    account: AccountService | None = None,
) -> FastAPI:
    if cfg is None:
        default_path = Path("config.yaml")
        if config_path is None and default_path.exists():
            config_path = default_path
        cfg = load_config(config_path) if config_path and Path(config_path).exists() else load_config()
    elif config_path is not None:
        config_path = Path(config_path)
    cfg = _cfg_from_session(cfg)
    cfg = replace(cfg, live_enabled=False)
    service = DashboardService(
        cfg, client=client, runner=runner, config_path=config_path, account=account
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        service.close()

    app = FastAPI(
        title="Rk Kalshi Paper Desk",
        description=(
            "Local paper-trading dashboard. Signal is Avellaneda–Stoikov + "
            "order-book imbalance. Live orders are opt-in with hard caps. "
            "Not financial advice."
        ),
        lifespan=lifespan,
    )
    app.state.service = service

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            raise HTTPException(500, "dashboard static files are missing")
        return FileResponse(
            page,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        flags = _mode_flags(service)
        return {
            "ok": True,
            "account": service.account.snapshot(),
            **flags,
        }

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return _status_payload(service)

    @app.get("/api/markets")
    def markets() -> dict[str, Any]:
        try:
            snapshots, latency_ms = service.client.list_markets()
            target_event = service.cfg.target_event_ticker
            fetch_event = getattr(service.client, "list_event_markets", None)
            if target_event and callable(fetch_event):
                fetched = fetch_event(target_event)
                if (
                    isinstance(fetched, tuple)
                    and len(fetched) == 2
                    and isinstance(fetched[0], list)
                ):
                    extra, extra_ms = fetched
                    try:
                        latency_ms = max(float(latency_ms), float(extra_ms or 0.0))
                    except (TypeError, ValueError):
                        pass
                    if extra:
                        seen = {m.ticker for m in snapshots}
                        snapshots = list(snapshots) + [m for m in extra if m.ticker not in seen]
        except Exception as exc:  # noqa: BLE001 — HTTP client / parse errors
            raise HTTPException(status_code=502, detail=f"Kalshi public API error: {exc}") from exc
        tennis = [m for m in snapshots if m.asset_class != "bitcoin"]
        bitcoin = select_bitcoin_tradeable(
            snapshots,
            near_money_low=service.cfg.bitcoin_near_money_low,
            near_money_high=service.cfg.bitcoin_near_money_high,
        )
        others = [m for m in snapshots if m.asset_class not in {"tennis", "bitcoin"}]
        payload = [_market_payload(m) for m in tennis + bitcoin + others]
        payload.sort(key=lambda row: (0 if row["asset_class"] == "bitcoin" else 1, row["event_name"], row["ticker"]))
        return {
            "count": len(payload),
            "latency_ms": round(float(latency_ms), 3),
            "series": list(service.cfg.enabled_series_tickers()),
            "paper_mode": True,
            "markets": payload,
        }

    @app.get("/api/fills")
    def fills() -> dict[str, Any]:
        rows = [_coerce_fill(row) for row in read_fills(service.cfg.fill_log_csv)]
        rows.reverse()
        return {
            "count": len(rows),
            "fields": list(FILL_FIELDS),
            "fills": rows,
            "paper_mode": True,
        }

    @app.get("/api/pnl")
    def pnl() -> dict[str, Any]:
        return _pnl_payload(service)

    @app.get("/api/run")
    def run_status() -> dict[str, Any]:
        return service.controller.snapshot()

    @app.post("/api/run")
    def start_run(body: RunRequest) -> dict[str, Any]:
        try:
            snapshot = service.controller.start(body.cycles, body.sleep_s, continuous=False)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return snapshot

    @app.post("/api/start")
    def start_session(body: StartRequest) -> dict[str, Any]:
        if service.controller.snapshot()["running"]:
            raise HTTPException(status_code=409, detail="paper-run already in progress")
        category = normalize_category_id(body.category_id)
        trade_bitcoin = body.trade_bitcoin
        trade_tennis = body.trade_tennis
        if category:
            trade_tennis, trade_bitcoin = trade_flags_for_category(category)
        if not trade_bitcoin and not trade_tennis:
            raise HTTPException(status_code=400, detail="select Bitcoin and/or tennis")
        has_target = bool(body.target_url or body.target_event_ticker or body.target_market_ticker)
        if has_target:
            try:
                service.set_contract(
                    ContractRequest(
                        url=body.target_url,
                        event_ticker=body.target_event_ticker,
                        market_ticker=body.target_market_ticker,
                    )
                )
            except HTTPException:
                raise
        elif category:
            service.set_contract(ContractRequest())
        wants_live = _wants_live(body.live, body.mode)
        if wants_live:
            try:
                service.arm_live()
            except LiveStartError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        session = service.apply_session(
            starting_cash=body.starting_cash,
            max_dollars_per_ticker=body.max_dollars_per_ticker,
            daily_loss_limit=body.daily_loss_limit,
            cycle_sleep_s=body.sleep_s,
            live_matches_only=body.live_matches_only,
            trade_bitcoin=trade_bitcoin,
            trade_tennis=trade_tennis,
            signal_mode=body.signal_mode,
            trade_style=body.trade_style,
            category_id=body.category_id,
            live=wants_live,
        )
        continuous = bool(body.continuous) or body.cycles is None
        cycles = 1 if continuous else int(body.cycles or 1)
        try:
            run = service.controller.start(cycles, body.sleep_s, continuous=continuous)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        flags = _mode_flags(service)
        return {"session": session, "run": run, **flags}

    @app.post("/api/stop")
    def stop_session() -> dict[str, Any]:
        live_exec = getattr(service.runner, "live", None)
        if live_exec is not None:
            try:
                live_exec.cancel_open(force=True)
            except Exception:
                pass
        return service.controller.stop()

    @app.post("/api/live")
    def arm_or_disarm_live(body: LiveArmRequest) -> dict[str, Any]:
        try:
            if body.enabled:
                account = service.arm_live()
            else:
                account = service.disarm_live()
        except LiveStartError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        flags = _mode_flags(service)
        return {"account": account, **flags}

    @app.post("/api/logs/clear")
    def clear_logs() -> dict[str, Any]:
        return service.controller.clear_logs()

    @app.get("/api/presets")
    def presets() -> dict[str, Any]:
        return presets_payload()

    @app.get("/api/catalog")
    def catalog(category: str = "") -> dict[str, Any]:
        try:
            snapshots, latency_ms = service.client.list_markets(catalog_series_tickers())
        except Exception as exc:  # noqa: BLE001 — HTTP client / parse errors
            raise HTTPException(status_code=502, detail=f"Kalshi public API error: {exc}") from exc
        now = time.time()
        events = catalog_events(
            snapshots,
            now,
            category_id=category,
            live_only=True,
            pre_start_s=max(0.0, service.cfg.live_pre_start_minutes) * 60.0,
            max_duration_s=max(0.1, service.cfg.live_max_hours) * 3600.0,
            near_money_low=service.cfg.bitcoin_near_money_low,
            near_money_high=service.cfg.bitcoin_near_money_high,
        )
        cid = normalize_category_id(category) or "all"
        return {
            "paper_mode": True,
            "live_enabled": False,
            "category_id": cid,
            "categories": categories_payload(),
            "events": events,
            "count": len(events),
            "latency_ms": round(float(latency_ms), 3),
            "series": list(catalog_series_tickers()),
        }

    @app.get("/api/contract")
    def get_contract() -> dict[str, Any]:
        return {
            "paper_mode": True,
            "live_enabled": False,
            "target": service.target_payload(),
            "examples": list(EXAMPLE_CONTRACT_URLS),
        }

    @app.post("/api/contract")
    def set_contract(body: ContractRequest) -> dict[str, Any]:
        return service.set_contract(body)

    @app.post("/api/clear")
    def clear_session() -> dict[str, Any]:
        return service.clear_session()

    @app.post("/api/clear-view")
    def clear_view() -> dict[str, Any]:
        return service.clear_view()

    @app.post("/api/live/cancel-open")
    def cancel_open_live() -> dict[str, Any]:
        return service.cancel_open_live_orders()

    @app.get("/api/account")
    def account_status() -> dict[str, Any]:
        return service.account.snapshot()

    @app.post("/api/account/connect")
    def account_connect(body: ConnectRequest) -> dict[str, Any]:
        try:
            snapshot = service.account.connect_from_local()
        except AccountAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AccountApiError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Kalshi account request failed: {exc}") from exc
        return {
            "account": snapshot,
            "paper_mode": True,
            "live_enabled": False,
            "read_only": True,
            "note": "Connected for portfolio view. Enable Live separately to place real orders.",
        }

    @app.post("/api/account/disconnect")
    def account_disconnect() -> dict[str, Any]:
        service.disarm_live(cancel_open=True)
        return {
            "account": service.account.disconnect(),
            "paper_mode": True,
            "live_enabled": False,
            "live_armed": False,
        }

    @app.get("/api/account/portfolio")
    def account_portfolio() -> dict[str, Any]:
        try:
            return service.account.portfolio()
        except AccountNotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AccountAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AccountApiError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Kalshi account request failed: {exc}") from exc

    return app


def serve(
    cfg: AppConfig | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
    config_path: Path | None = None,
) -> None:
    import uvicorn

    app = create_app(cfg, config_path=config_path)
    if host not in _LOOPBACK:
        print(
            "warning: binding beyond localhost; live orders still require the dashboard Live toggle",
            flush=True,
        )
    url = f"http://{host}:{port}/"
    print("PAPER MODE default — live orders opt-in with hard caps", flush=True)
    print(f"dashboard: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")
