"""Category → live event catalog for the paper desk dropdowns."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from rk_kalshi.config import AppConfig
from rk_kalshi.models import MarketSnapshot, select_bitcoin_tradeable


@dataclass(frozen=True)
class MarketCategory:
    id: str
    label: str
    kind: str  # tennis | bitcoin | all
    series: tuple[str, ...]
    blurb: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "series": list(self.series),
            "blurb": self.blurb,
        }


CATEGORIES: tuple[MarketCategory, ...] = (
    MarketCategory("all", "All live books", "all", (), "Every supported live tennis and Bitcoin book"),
    MarketCategory("atp", "Tennis · ATP", "tennis", ("KXATPMATCH",), "Live ATP match markets"),
    MarketCategory("wta", "Tennis · WTA", "tennis", ("KXWTAMATCH",), "Live WTA match markets"),
    MarketCategory(
        "challenger",
        "Tennis · Challenger",
        "tennis",
        ("KXATPCHALLENGERMATCH",),
        "Live Challenger match markets",
    ),
    MarketCategory("itf", "Tennis · ITF", "tennis", ("KXITFWMATCH", "KXITFMMATCH"), "Live ITF match markets"),
    MarketCategory("btc15m", "Bitcoin · 15 min", "bitcoin", ("KXBTC15M",), "Open Bitcoin 15-minute up/down books"),
    MarketCategory("btcd", "Bitcoin · daily", "bitcoin", ("KXBTCD",), "Near-the-money Bitcoin daily strikes"),
)

CATEGORY_BY_ID = {item.id: item for item in CATEGORIES}


def _merge_series(*groups: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for series in group:
            if not series or series in seen:
                continue
            seen.add(series)
            ordered.append(series)
    return tuple(ordered)


def catalog_series_tickers() -> tuple[str, ...]:
    return _merge_series(*(category.series for category in CATEGORIES))


def tennis_series_tickers() -> tuple[str, ...]:
    return _merge_series(*(category.series for category in CATEGORIES if category.kind == "tennis"))


def bitcoin_series_tickers() -> tuple[str, ...]:
    return _merge_series(*(category.series for category in CATEGORIES if category.kind == "bitcoin"))


def trade_flags_for_category(category_id: str) -> tuple[bool, bool]:
    """Return (trade_tennis, trade_bitcoin) for a catalog category."""
    cid = normalize_category_id(category_id) or "all"
    spec = CATEGORY_BY_ID[cid]
    if spec.kind == "tennis":
        return True, False
    if spec.kind == "bitcoin":
        return False, True
    return True, True


def apply_category(cfg: AppConfig, category_id: str) -> AppConfig:
    cid = normalize_category_id(category_id) or "all"
    spec = CATEGORY_BY_ID[cid]
    tennis, bitcoin = trade_flags_for_category(cid)
    if spec.kind == "tennis":
        series = spec.series
        btc_series = _merge_series(cfg.bitcoin_series_tickers, bitcoin_series_tickers())
    elif spec.kind == "bitcoin":
        series = _merge_series(cfg.series_tickers, tennis_series_tickers())
        btc_series = spec.series
    else:
        series = _merge_series(cfg.series_tickers, tennis_series_tickers())
        btc_series = _merge_series(cfg.bitcoin_series_tickers, bitcoin_series_tickers())
    return replace(
        cfg,
        target_category_id=cid,
        trade_tennis=tennis,
        trade_bitcoin=bitcoin,
        series_tickers=series,
        bitcoin_series_tickers=btc_series,
        live_enabled=False,
    )


def category_id_for_series(series: str) -> str:
    prefix = (series or "").upper().split("-", 1)[0]
    if prefix == "KXATPMATCH":
        return "atp"
    if prefix == "KXWTAMATCH":
        return "wta"
    if "CHALLENGER" in prefix:
        return "challenger"
    if prefix.startswith("KXITF"):
        return "itf"
    if "15M" in prefix and (prefix.startswith("KXBTC") or prefix.startswith("KXETH")):
        return "btc15m"
    if prefix.startswith("KXBTC") or prefix.startswith("KXETH"):
        return "btcd"
    return "other"


def category_id_for_market(market: MarketSnapshot) -> str:
    return category_id_for_series(market.series_ticker or market.ticker)


def normalize_category_id(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in CATEGORY_BY_ID:
        return text
    return ""


def filter_markets_by_category(
    markets: Iterable[MarketSnapshot],
    category_id: str,
    *,
    near_money_low: float = 0.15,
    near_money_high: float = 0.85,
) -> list[MarketSnapshot]:
    cid = normalize_category_id(category_id)
    items = list(markets)
    if not cid or cid == "all":
        tennis = [m for m in items if m.asset_class == "tennis"]
        bitcoin = select_bitcoin_tradeable(items, near_money_low, near_money_high)
        others = [m for m in items if m.asset_class not in {"tennis", "bitcoin"}]
        return tennis + bitcoin + others
    spec = CATEGORY_BY_ID[cid]
    kept = [m for m in items if category_id_for_market(m) == cid]
    if spec.kind == "bitcoin":
        return select_bitcoin_tradeable(kept, near_money_low, near_money_high)
    return kept


def market_is_live(
    market: MarketSnapshot,
    now: float,
    *,
    pre_start_s: float = 600.0,
    max_duration_s: float = 12 * 3600.0,
) -> bool:
    if market.asset_class == "tennis":
        return market.is_in_play(now, pre_start_s=pre_start_s, max_duration_s=max_duration_s)
    return market.yes_mid is not None


def catalog_events(
    markets: Iterable[MarketSnapshot],
    now: float,
    *,
    category_id: str = "",
    live_only: bool = True,
    pre_start_s: float = 600.0,
    max_duration_s: float = 12 * 3600.0,
    near_money_low: float = 0.15,
    near_money_high: float = 0.85,
) -> list[dict[str, Any]]:
    filtered = filter_markets_by_category(
        markets,
        category_id,
        near_money_low=near_money_low,
        near_money_high=near_money_high,
    )
    grouped: dict[str, dict[str, Any]] = {}
    for market in filtered:
        key = market.event_ticker or market.match_id or market.ticker
        if not key:
            continue
        live = market_is_live(market, now, pre_start_s=pre_start_s, max_duration_s=max_duration_s)
        row = grouped.get(key)
        if row is None:
            grouped[key] = {
                "event_ticker": key,
                "event_name": market.event_name or key,
                "series_ticker": market.series_ticker,
                "category_id": category_id_for_market(market),
                "asset_class": market.asset_class,
                "live": live,
                "market_count": 1,
                "sample_ticker": market.ticker,
                "yes_mid": market.yes_mid,
            }
            continue
        row["market_count"] += 1
        row["live"] = row["live"] or live
        if row["yes_mid"] is None and market.yes_mid is not None:
            row["yes_mid"] = market.yes_mid
    events = list(grouped.values())
    if live_only:
        events = [row for row in events if row["live"]]
    events.sort(key=lambda row: ((row.get("event_name") or ""), row["event_ticker"]))
    return events


def categories_payload() -> list[dict[str, Any]]:
    return [item.as_dict() for item in CATEGORIES]
