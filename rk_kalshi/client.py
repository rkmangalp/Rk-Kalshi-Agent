"""Public Kalshi REST client. Market-data reads typically need no auth."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from rk_kalshi.config import AppConfig
from rk_kalshi.models import MarketSnapshot, parse_count, parse_dollars


class KalshiPublicClient:
    def __init__(self, cfg: AppConfig, client: httpx.Client | None = None):
        self.cfg = cfg
        self._owns_client = client is None
        self._http = client or httpx.Client(
            base_url=cfg.base_url.rstrip("/"),
            timeout=cfg.request_timeout_s,
            headers={"User-Agent": "rk-kalshi-agent/0.1 (+paper-trading)"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "KalshiPublicClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], float]:
        started = time.perf_counter()
        response = self._http.get(path, params=params)
        latency_ms = (time.perf_counter() - started) * 1000.0
        response.raise_for_status()
        return response.json(), latency_ms

    def list_tennis_series(self) -> list[dict[str, Any]]:
        payload, _ = self.get_json("/series", params={"tags": "Tennis", "category": "Sports"})
        series = payload.get("series") or []
        return [s for s in series if "Tennis" in (s.get("tags") or [])]

    def iter_events(self, series_ticker: str, status: str = "open") -> Iterable[dict[str, Any]]:
        cursor = None
        while True:
            params: dict[str, Any] = {
                "series_ticker": series_ticker,
                "status": status,
                "with_nested_markets": True,
                "limit": self.cfg.page_limit,
            }
            if cursor:
                params["cursor"] = cursor
            payload, _ = self.get_json("/events", params=params)
            for event in payload.get("events") or []:
                yield event
            cursor = payload.get("cursor") or None
            if not cursor:
                break

    def list_tennis_markets(self) -> tuple[list[MarketSnapshot], float]:
        return self.list_markets(self.cfg.series_tickers)

    def list_event_markets(self, event_ticker: str) -> tuple[list[MarketSnapshot], float]:
        """Fetch one event’s markets by ticker, any series."""
        ticker = (event_ticker or "").strip()
        if not ticker:
            return [], 0.0
        try:
            payload, latency_ms = self.get_json(
                f"/events/{ticker}",
                params={"with_nested_markets": True},
            )
        except httpx.HTTPStatusError:
            payload, latency_ms = self.get_json(
                "/markets",
                params={
                    "event_ticker": ticker,
                    "status": "open",
                    "limit": self.cfg.page_limit,
                },
            )
            markets = payload.get("markets") or []
            event = {
                "event_ticker": ticker,
                "series_ticker": ticker.split("-", 1)[0],
                "title": ticker,
                "markets": markets,
            }
            return _snapshots_from_event(event, event["series_ticker"]), latency_ms

        event = payload.get("event")
        if not isinstance(event, dict):
            events = payload.get("events") or []
            event = events[0] if events else payload
        if not isinstance(event, dict):
            return [], latency_ms
        series = str(event.get("series_ticker") or ticker.split("-", 1)[0])
        return _snapshots_from_event(event, series), latency_ms

    def list_markets(
        self,
        series_tickers: tuple[str, ...] | None = None,
    ) -> tuple[list[MarketSnapshot], float]:
        snapshots: list[MarketSnapshot] = []
        max_latency = 0.0
        for series in series_tickers or self.cfg.enabled_series_tickers():
            cursor = None
            while True:
                params: dict[str, Any] = {
                    "series_ticker": series,
                    "status": "open",
                    "with_nested_markets": True,
                    "limit": self.cfg.page_limit,
                }
                if cursor:
                    params["cursor"] = cursor
                payload, latency_ms = self.get_json("/events", params=params)
                max_latency = max(max_latency, latency_ms)
                for event in payload.get("events") or []:
                    snapshots.extend(_snapshots_from_event(event, series))
                cursor = payload.get("cursor") or None
                if not cursor:
                    break
        return snapshots, max_latency


def _snapshots_from_event(event: dict[str, Any], series_ticker: str) -> list[MarketSnapshot]:
    event_ticker = str(event.get("event_ticker") or "")
    event_name = str(event.get("title") or event.get("sub_title") or event_ticker)
    out: list[MarketSnapshot] = []
    for market in event.get("markets") or []:
        status = str(market.get("status") or "")
        if status not in {"open", "active", ""}:
            continue
        out.append(
            MarketSnapshot(
                ticker=str(market.get("ticker") or ""),
                event_ticker=str(market.get("event_ticker") or event_ticker),
                event_name=event_name,
                title=str(market.get("title") or market.get("yes_sub_title") or ""),
                yes_bid=parse_dollars(market.get("yes_bid_dollars")),
                yes_ask=parse_dollars(market.get("yes_ask_dollars")),
                last_price=parse_dollars(market.get("last_price_dollars")),
                volume=parse_count(market.get("volume_fp")),
                updated_ts=_parse_ts(market.get("updated_time") or event.get("last_updated_ts")),
                status=status or "open",
                series_ticker=str(event.get("series_ticker") or series_ticker),
                occurrence_ts=_parse_ts(
                    market.get("occurrence_datetime") or market.get("expected_expiration_time")
                ),
            )
        )
    return [s for s in out if s.ticker]


def _parse_ts(value: object) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None
