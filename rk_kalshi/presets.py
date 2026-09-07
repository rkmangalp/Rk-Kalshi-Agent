"""Risk/reward trading-style presets for the paper desk.

These only change paper knobs. Live order placement stays disabled.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from rk_kalshi.config import AppConfig

DEFAULT_TRADE_STYLE = "active"


@dataclass(frozen=True)
class TradeStyle:
    id: str
    label: str
    blurb: str
    edge_threshold_cents: float
    max_dollars_per_ticker: float
    daily_loss_limit: float
    base_contracts: int
    max_spread_cents: float
    cycle_sleep_s: float
    gamma: float
    kappa: float
    max_signals_per_cycle: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "blurb": self.blurb,
            "edge_threshold_cents": self.edge_threshold_cents,
            "max_dollars_per_ticker": self.max_dollars_per_ticker,
            "daily_loss_limit": self.daily_loss_limit,
            "base_contracts": self.base_contracts,
            "max_spread_cents": self.max_spread_cents,
            "cycle_sleep_s": self.cycle_sleep_s,
            "gamma": self.gamma,
            "kappa": self.kappa,
            "max_signals_per_cycle": self.max_signals_per_cycle,
            "paper_only": True,
        }


TRADE_STYLES: dict[str, TradeStyle] = {
    "safe": TradeStyle(
        id="safe",
        label="Safe",
        blurb="Low risk, low reward — 1 contract, 6¢ edge, $3/trade, 4¢ max spread, slower polls.",
        edge_threshold_cents=6.0,
        max_dollars_per_ticker=3.0,
        daily_loss_limit=8.0,
        base_contracts=1,
        max_spread_cents=4.0,
        cycle_sleep_s=30.0,
        gamma=0.40,
        kappa=1.0,
        max_signals_per_cycle=3,
    ),
    "conservative": TradeStyle(
        id="conservative",
        label="Conservative",
        blurb="Balanced mid-low — 1 contract, 4.5¢ edge, $5/trade, 6¢ max spread.",
        edge_threshold_cents=4.5,
        max_dollars_per_ticker=5.0,
        daily_loss_limit=12.0,
        base_contracts=1,
        max_spread_cents=6.0,
        cycle_sleep_s=20.0,
        gamma=0.30,
        kappa=1.25,
        max_signals_per_cycle=5,
    ),
    "active": TradeStyle(
        id="active",
        label="Active",
        blurb="Balanced mid-high — 2 contracts, 3¢ edge, $10/trade, 8¢ max spread, faster polls.",
        edge_threshold_cents=3.0,
        max_dollars_per_ticker=10.0,
        daily_loss_limit=20.0,
        base_contracts=2,
        max_spread_cents=8.0,
        cycle_sleep_s=12.0,
        gamma=0.20,
        kappa=1.75,
        max_signals_per_cycle=8,
    ),
    "aggressive": TradeStyle(
        id="aggressive",
        label="Aggressive",
        blurb="High risk, high reward — 4 contracts, 1.5¢ edge, $25/trade, 12¢ max spread. Still paper-only.",
        edge_threshold_cents=1.5,
        max_dollars_per_ticker=25.0,
        daily_loss_limit=40.0,
        base_contracts=4,
        max_spread_cents=12.0,
        cycle_sleep_s=8.0,
        gamma=0.10,
        kappa=2.5,
        max_signals_per_cycle=12,
    ),
}


def normalize_trade_style(value: object, default: str = DEFAULT_TRADE_STYLE) -> str:
    text = str(value or default).strip().lower().replace("-", "_")
    aliases = {"balanced": "conservative", "medium": "active", "high": "aggressive", "low": "safe"}
    text = aliases.get(text, text)
    if text in TRADE_STYLES:
        return text
    return default


def trade_style(value: object = DEFAULT_TRADE_STYLE) -> TradeStyle:
    return TRADE_STYLES[normalize_trade_style(value)]


def apply_trade_style(cfg: AppConfig, style: object) -> AppConfig:
    preset = trade_style(style)
    return replace(
        cfg,
        trade_style=preset.id,
        edge_threshold_cents=preset.edge_threshold_cents,
        max_dollars_per_ticker=preset.max_dollars_per_ticker,
        daily_loss_limit=preset.daily_loss_limit,
        base_contracts=preset.base_contracts,
        max_spread_cents=preset.max_spread_cents,
        cycle_sleep_s=preset.cycle_sleep_s,
        gamma=preset.gamma,
        kappa=preset.kappa,
        max_signals_per_cycle=preset.max_signals_per_cycle,
        live_enabled=False,
    )


def presets_payload() -> dict[str, Any]:
    return {
        "default": DEFAULT_TRADE_STYLE,
        "styles": [item.as_dict() for item in TRADE_STYLES.values()],
        "paper_only": True,
        "live_enabled": False,
    }
