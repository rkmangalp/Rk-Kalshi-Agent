"""Local FastAPI dashboard wrapping the paper-trade stack.

Binds to localhost by default. Live order submission is never exposed.
"""

from __future__ import annotations

import json
import re
import threading
import time
import webbrowser
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from rk_kalshi.client import KalshiPublicClient
from rk_kalshi.config import AppConfig, load_config
from rk_kalshi.journal import read_fills, summarize_pnl
from rk_kalshi.models import MarketSnapshot
from rk_kalshi.risk import RiskManager
from rk_kalshi.runner import PaperRunner
from rk_kalshi.schema import FILL_FIELDS
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
        raise ValueError("live trading is disabled; paper mode only")
    if mode is not None and str(mode).strip().lower() == "live":
        raise ValueError("live trading is disabled; paper mode only")


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
    live: bool | None = None
    mode: str | None = None

    @model_validator(mode="after")
    def reject_live(self) -> "StartRequest":
        _reject_live(self.live, self.mode)
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
                "mode": "paper",
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
        self._log("stop requested — finishing current cycle (paper mode)")
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
                self._log(
                    "PAPER MODE ONLY — Start: polling tennis markets until Stop; "
                    "live orders disabled; can_size_up stays locked"
                )
            else:
                self._log(
                    f"PAPER MODE ONLY — starting {cycles} cycle(s); "
                    "live orders disabled; can_size_up stays locked"
                )
            index = 0
            while not self._stop.is_set():
                if not continuous and index >= cycles:
                    break
                label = f"{index + 1}" if continuous else f"{index + 1}/{cycles}"
                self._log(f"cycle {label}: scanning tennis markets")
                fills = self.runner.run_once()
                scan = getattr(self.runner, "last_scan", None) or {}
                if scan:
                    self._log(
                        f"  live {scan.get('live', 0)} / open {scan.get('open', 0)} "
                        f"(live-matches-only={self.cfg.live_matches_only})"
                    )
                    if self.cfg.live_matches_only and not scan.get("live"):
                        nxt = scan.get("next_event_name") or "none scheduled"
                        when = scan.get("next_start_iso") or "n/a"
                        self._log(f"  no in-play matches — next: {nxt} at {when}")
                with self._lock:
                    self.cycles_done = index + 1
                    self.fills_this_run += len(fills)
                if fills:
                    for fill in fills:
                        self._log(
                            f"FILL {fill.side:4} {fill.ticker} mid={fill.live_mid:.4f} "
                            f"edge={fill.edge_cents:.2f}¢ pnl={fill.running_pnl:.4f} "
                            f"can_size_up={fill.can_size_up}"
                        )
                        self._log(f"  thesis: {fill.edge_thesis}")
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
    ):
        self.cfg = cfg
        self.config_path = Path(config_path) if config_path else None
        self.client = client or KalshiPublicClient(cfg)
        self.owns_client = client is None
        self.runner = runner or PaperRunner(cfg, client=self.client)
        self.controller = RunController(self.runner, cfg)

    def close(self) -> None:
        self.runner.close()
        if self.owns_client:
            self.client.close()

    def bind_config(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.controller.cfg = cfg
        runner = self.runner
        runner.cfg = cfg
        if getattr(runner, "signal", None) is not None:
            runner.signal.cfg = cfg
        if getattr(runner, "risk", None) is not None:
            runner.risk.cfg = cfg
        if getattr(runner, "paper", None) is not None:
            runner.paper.cfg = cfg

    def apply_session(
        self,
        starting_cash: float,
        max_dollars_per_ticker: float,
        daily_loss_limit: float,
        cycle_sleep_s: float | None = None,
        live_matches_only: bool = True,
    ) -> dict[str, Any]:
        sleep_s = self.cfg.cycle_sleep_s if cycle_sleep_s is None else cycle_sleep_s
        cfg = replace(
            self.cfg,
            starting_cash=float(starting_cash),
            max_dollars_per_ticker=float(max_dollars_per_ticker),
            daily_loss_limit=float(daily_loss_limit),
            cycle_sleep_s=float(sleep_s),
            live_matches_only=bool(live_matches_only),
            live_enabled=False,
        )
        self.bind_config(cfg)
        applied = _apply_bankroll_state(cfg)
        persisted = _persist_session(cfg, self.config_path)
        return {
            "paper_mode": True,
            "live_enabled": False,
            "starting_cash": cfg.starting_cash,
            "max_dollars_per_ticker": cfg.max_dollars_per_ticker,
            "daily_loss_limit": cfg.daily_loss_limit,
            "cycle_sleep_s": cfg.cycle_sleep_s,
            "live_matches_only": cfg.live_matches_only,
            "can_size_up": False,
            "allow_size_up": cfg.allow_size_up,
            "state": applied,
            "persisted": persisted,
        }


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
    return replace(
        cfg,
        starting_cash=float(raw.get("starting_cash", cfg.starting_cash)),
        max_dollars_per_ticker=float(raw.get("max_dollars_per_ticker", cfg.max_dollars_per_ticker)),
        daily_loss_limit=float(raw.get("daily_loss_limit", cfg.daily_loss_limit)),
        cycle_sleep_s=float(raw.get("cycle_sleep_s", cfg.cycle_sleep_s)),
        live_matches_only=bool(raw.get("live_matches_only", cfg.live_matches_only)),
        live_enabled=False,
    )


def _persist_session(cfg: AppConfig, config_path: Path | None) -> dict[str, bool]:
    payload = {
        "starting_cash": cfg.starting_cash,
        "max_dollars_per_ticker": cfg.max_dollars_per_ticker,
        "daily_loss_limit": cfg.daily_loss_limit,
        "cycle_sleep_s": cfg.cycle_sleep_s,
        "live_matches_only": cfg.live_matches_only,
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
        "yes_mid": market.yes_mid,
        "last_price": market.last_price,
        "spread_cents": market.spread_cents,
        "volume": market.volume,
        "status": market.status,
        "series_ticker": market.series_ticker,
        "occurrence_ts": market.occurrence_ts,
        "in_play": market.is_in_play(time.time()),
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
    summary["paper_mode"] = True
    summary["live_enabled"] = False
    return summary


def _status_payload(service: DashboardService) -> dict[str, Any]:
    state = load_state(service.cfg)
    risk = RiskManager(service.cfg)
    return {
        "paper_mode": True,
        "mode": "paper",
        "banner": "PAPER MODE ONLY — no live orders",
        "live_enabled": False,
        "live_trading_available": False,
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
        "series_tickers": list(service.cfg.series_tickers),
        "edge_threshold_cents": service.cfg.edge_threshold_cents,
        "live_matches_only": service.cfg.live_matches_only,
        "run": service.controller.snapshot(),
    }


def create_app(
    cfg: AppConfig | None = None,
    client: KalshiPublicClient | None = None,
    runner: PaperRunner | None = None,
    config_path: Path | None = None,
) -> FastAPI:
    if cfg is None:
        default_path = Path("config.yaml")
        if config_path is None and default_path.exists():
            config_path = default_path
        cfg = load_config(config_path) if config_path and Path(config_path).exists() else load_config()
    elif config_path is not None:
        config_path = Path(config_path)
    cfg = _cfg_from_session(cfg)
    service = DashboardService(cfg, client=client, runner=runner, config_path=config_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        service.close()

    app = FastAPI(
        title="Rk Kalshi Paper Desk",
        description="Local paper-trading dashboard. Live orders are disabled.",
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
        return FileResponse(page)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "paper_mode": True, "live_enabled": False}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return _status_payload(service)

    @app.get("/api/markets")
    def markets() -> dict[str, Any]:
        try:
            snapshots, latency_ms = service.client.list_tennis_markets()
        except Exception as exc:  # noqa: BLE001 — HTTP client / parse errors
            raise HTTPException(status_code=502, detail=f"Kalshi public API error: {exc}") from exc
        payload = [_market_payload(m) for m in snapshots]
        payload.sort(key=lambda row: (row["event_name"], row["ticker"]))
        return {
            "count": len(payload),
            "latency_ms": round(float(latency_ms), 3),
            "series": list(service.cfg.series_tickers),
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
        session = service.apply_session(
            starting_cash=body.starting_cash,
            max_dollars_per_ticker=body.max_dollars_per_ticker,
            daily_loss_limit=body.daily_loss_limit,
            cycle_sleep_s=body.sleep_s,
            live_matches_only=body.live_matches_only,
        )
        continuous = bool(body.continuous) or body.cycles is None
        cycles = 1 if continuous else int(body.cycles or 1)
        try:
            run = service.controller.start(cycles, body.sleep_s, continuous=continuous)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"session": session, "run": run, "paper_mode": True, "live_enabled": False}

    @app.post("/api/stop")
    def stop_session() -> dict[str, Any]:
        return service.controller.stop()

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
            "warning: binding beyond localhost; the UI still cannot place live orders",
            flush=True,
        )
    url = f"http://{host}:{port}/"
    print("PAPER MODE ONLY — no live orders", flush=True)
    print(f"dashboard: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")
