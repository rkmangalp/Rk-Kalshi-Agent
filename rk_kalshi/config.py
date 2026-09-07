from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SERIES = (
    "KXATPMATCH",
    "KXWTAMATCH",
    "KXITFWMATCH",
    "KXITFMMATCH",
    "KXATPCHALLENGERMATCH",
)
DEFAULT_BITCOIN_SERIES = ("KXBTC15M", "KXBTCD")
DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class AppConfig:
    base_url: str = DEFAULT_BASE_URL
    series_tickers: tuple[str, ...] = DEFAULT_SERIES
    bitcoin_series_tickers: tuple[str, ...] = DEFAULT_BITCOIN_SERIES
    trade_tennis: bool = True
    trade_bitcoin: bool = True
    bitcoin_near_money_low: float = 0.15
    bitcoin_near_money_high: float = 0.85
    request_timeout_s: float = 15.0
    page_limit: int = 200
    starting_cash: float = 100.0
    max_dollars_per_ticker: float = 5.0
    daily_loss_limit: float = 15.0
    allow_martingale: bool = False
    allow_size_up: bool = False
    min_fills_before_size_up: int = 200
    base_contracts: int = 1
    edge_threshold_cents: float = 3.0
    fee_coefficient: float = 0.07
    fee_multiplier: float = 1.0
    ema_alpha: float = 0.30
    last_trade_weight: float = 0.50
    max_last_dislocation_cents: float = 8.0
    max_spread_cents: float = 8.0
    stale_mid_seconds: float = 180.0
    min_volume: float = 0.0
    gamma: float = 0.25
    kappa: float = 1.5
    sigma_floor: float = 0.04
    sigma_window: int = 32
    t_frac_horizon_seconds: float = 14400.0
    obi_tilt_cents: float = 2.0
    use_ema_fallback: bool = False
    signal_mode: str = "as_obi"
    trade_style: str = "active"
    target_category_id: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_min_interval_s: float = 20.0
    llm_max_markets_per_call: int = 6
    live_matches_only: bool = True
    live_pre_start_minutes: float = 10.0
    live_max_hours: float = 12.0
    fill_log_csv: Path = Path("data/fills.csv")
    fill_log_jsonl: Path = Path("data/fills.jsonl")
    state_path: Path = Path("data/paper_state.json")
    cycle_sleep_s: float = 15.0
    max_signals_per_cycle: int = 8
    live_enabled: bool = False
    live_state_path: Path = Path("data/live_state.json")
    live_max_dollars_per_ticker: float = 20.0
    live_daily_loss_limit: float = 20.0
    account_environment: str = "prod"
    target_url: str = ""
    target_event_ticker: str = ""
    target_market_ticker: str = ""
    target_label: str = ""
    target_asset_class: str = ""

    def enabled_series_tickers(self) -> tuple[str, ...]:
        ordered: list[str] = []
        if self.trade_tennis:
            ordered.extend(self.series_tickers)
        if self.trade_bitcoin:
            ordered.extend(self.bitcoin_series_tickers)
        seen: set[str] = set()
        unique: list[str] = []
        for ticker in ordered:
            if ticker in seen:
                continue
            seen.add(ticker)
            unique.append(ticker)
        return tuple(unique)


def _signal_mode(value: object) -> str:
    text = str(value or "as_obi").strip().lower().replace("-", "_")
    if text in {"as_obi", "asobi", "as"}:
        return "as_obi"
    if text in {"llm", "openai", "chatgpt"}:
        return "llm"
    if text in {"hybrid", "as_llm"}:
        return "hybrid"
    return "as_obi"


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        return AppConfig()
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return config_from_mapping(raw)


def config_from_mapping(raw: dict[str, Any]) -> AppConfig:
    kalshi = raw.get("kalshi") or {}
    bankroll = raw.get("bankroll") or {}
    risk = raw.get("risk") or {}
    sizing = raw.get("sizing") or {}
    signal = raw.get("signal") or {}
    paper = raw.get("paper") or {}
    live = raw.get("live") or {}
    account = raw.get("account") or {}

    series = kalshi.get("series_tickers") or list(DEFAULT_SERIES)
    bitcoin_series = kalshi.get("bitcoin_series_tickers") or list(DEFAULT_BITCOIN_SERIES)
    return AppConfig(
        base_url=str(kalshi.get("base_url") or DEFAULT_BASE_URL),
        series_tickers=tuple(str(s) for s in series),
        bitcoin_series_tickers=tuple(str(s) for s in bitcoin_series),
        trade_tennis=bool(kalshi.get("trade_tennis", True)),
        trade_bitcoin=bool(kalshi.get("trade_bitcoin", True)),
        bitcoin_near_money_low=float(kalshi.get("bitcoin_near_money_low", 0.15)),
        bitcoin_near_money_high=float(kalshi.get("bitcoin_near_money_high", 0.85)),
        request_timeout_s=float(kalshi.get("request_timeout_s", 15.0)),
        page_limit=int(kalshi.get("page_limit", 200)),
        starting_cash=float(bankroll.get("starting_cash", 100.0)),
        max_dollars_per_ticker=float(risk.get("max_dollars_per_ticker", 5.0)),
        daily_loss_limit=float(risk.get("daily_loss_limit", 15.0)),
        allow_martingale=bool(risk.get("allow_martingale", False)),
        allow_size_up=bool(sizing.get("allow_size_up", False)),
        min_fills_before_size_up=int(sizing.get("min_fills_before_size_up", 200)),
        base_contracts=max(1, int(sizing.get("base_contracts", 1))),
        edge_threshold_cents=float(signal.get("edge_threshold_cents", 3.0)),
        fee_coefficient=float(signal.get("fee_coefficient", 0.07)),
        fee_multiplier=float(signal.get("fee_multiplier", 1.0)),
        ema_alpha=float(signal.get("ema_alpha", 0.30)),
        last_trade_weight=float(signal.get("last_trade_weight", 0.50)),
        max_last_dislocation_cents=float(signal.get("max_last_dislocation_cents", 8.0)),
        max_spread_cents=float(signal.get("max_spread_cents", 8.0)),
        stale_mid_seconds=float(signal.get("stale_mid_seconds", 180.0)),
        min_volume=float(signal.get("min_volume", 0.0)),
        gamma=float(signal.get("gamma", 0.25)),
        kappa=float(signal.get("kappa", signal.get("obi_weight", 1.5))),
        sigma_floor=float(signal.get("sigma_floor", 0.04)),
        sigma_window=max(2, int(signal.get("sigma_window", 32))),
        t_frac_horizon_seconds=float(signal.get("t_frac_horizon_seconds", 14400.0)),
        obi_tilt_cents=float(signal.get("obi_tilt_cents", 2.0)),
        use_ema_fallback=bool(signal.get("use_ema_fallback", False)),
        signal_mode=_signal_mode(signal.get("mode", signal.get("signal_mode", "as_obi"))),
        trade_style=str(paper.get("trade_style") or signal.get("trade_style") or "active"),
        target_category_id=str(kalshi.get("target_category_id") or ""),
        llm_model=str(signal.get("llm_model") or "gpt-4o-mini"),
        llm_min_interval_s=float(signal.get("llm_min_interval_s", 20.0)),
        llm_max_markets_per_call=max(1, int(signal.get("llm_max_markets_per_call", 6))),
        live_matches_only=bool(signal.get("live_matches_only", True)),
        live_pre_start_minutes=float(signal.get("live_pre_start_minutes", 10.0)),
        live_max_hours=float(signal.get("live_max_hours", 12.0)),
        fill_log_csv=Path(paper.get("fill_log_csv", "data/fills.csv")),
        fill_log_jsonl=Path(paper.get("fill_log_jsonl", "data/fills.jsonl")),
        state_path=Path(paper.get("state_path", "data/paper_state.json")),
        cycle_sleep_s=float(paper.get("cycle_sleep_s", 15.0)),
        max_signals_per_cycle=int(paper.get("max_signals_per_cycle", 8)),
        live_enabled=bool(live.get("enabled", False)),
        live_state_path=Path(live.get("state_path", "data/live_state.json")),
        live_max_dollars_per_ticker=float(live.get("max_dollars_per_ticker", 20.0)),
        live_daily_loss_limit=float(live.get("daily_loss_limit", 20.0)),
        account_environment=str(account.get("environment") or "prod"),
    )
