from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


def parse_dollars(value: object, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_count(value: object, default: float = 0.0) -> float:
    return parse_dollars(value, default)


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    event_ticker: str
    event_name: str
    title: str
    yes_bid: float
    yes_ask: float
    last_price: float
    volume: float
    updated_ts: Optional[float]
    status: str = "open"
    series_ticker: str = ""
    occurrence_ts: Optional[float] = None

    def is_in_play(
        self,
        now: float,
        pre_start_s: float = 600.0,
        max_duration_s: float = 12 * 3600.0,
    ) -> bool:
        """True when the scheduled start is near/now and the match window has not expired."""
        if self.occurrence_ts is None:
            return False
        return (self.occurrence_ts - pre_start_s) <= now <= (self.occurrence_ts + max_duration_s)

    @property
    def match_id(self) -> str:
        return self.event_ticker

    @property
    def asset_class(self) -> str:
        return asset_class_for_series(self.series_ticker or self.ticker)

    @property
    def yes_mid(self) -> Optional[float]:
        if self.yes_bid <= 0 or self.yes_ask <= 0:
            return None
        if self.yes_ask < self.yes_bid:
            return None
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def half_spread(self) -> Optional[float]:
        mid = self.yes_mid
        if mid is None:
            return None
        return (self.yes_ask - self.yes_bid) / 2.0

    @property
    def spread_cents(self) -> Optional[float]:
        if self.yes_bid <= 0 or self.yes_ask <= 0 or self.yes_ask < self.yes_bid:
            return None
        return (self.yes_ask - self.yes_bid) * 100.0


@dataclass(frozen=True)
class Signal:
    ticker: str
    event_name: str
    match_id: str
    side: str
    live_mid: float
    fill_price: float
    edge_cents: float
    edge_bps: float
    edge_thesis: str
    fee_per_contract: float
    contracts: int
    yes_bid: float
    yes_ask: float
    last_price: float
    fair_yes: float


@dataclass
class RiskDecision:
    ok: bool
    reason: str
    contracts: int = 0


@dataclass
class Fill:
    timestamp: str
    ticker: str
    side: str
    fill_price: float
    live_mid: float
    edge_thesis: str
    running_pnl: float
    event_name: str
    match_id: str
    edge_cents: float
    edge_bps: float
    contracts: int
    fee: float
    cash_after: float
    mode: str
    latency_ms: float
    can_size_up: bool
    realized_delta: float

    def as_row(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "ticker": self.ticker,
            "side": self.side,
            "fill_price": _num(self.fill_price),
            "live_mid": _num(self.live_mid),
            "edge_thesis": self.edge_thesis,
            "running_pnl": _num(self.running_pnl),
            "event_name": self.event_name,
            "match_id": self.match_id,
            "edge_cents": _num(self.edge_cents),
            "edge_bps": _num(self.edge_bps),
            "contracts": self.contracts,
            "fee": _num(self.fee),
            "cash_after": _num(self.cash_after),
            "mode": self.mode,
            "latency_ms": _num(self.latency_ms, 3),
            "can_size_up": self.can_size_up,
            "realized_delta": _num(self.realized_delta),
        }


@dataclass
class Position:
    contracts: int = 0
    avg_price: float = 0.0

    def notional(self, price: float) -> float:
        return abs(self.contracts) * price


@dataclass
class LastTickerTrade:
    contracts: int
    lost: bool


@dataclass
class PaperState:
    starting_cash: float
    cash: float
    day: str
    start_of_day_equity: float
    fill_count: int = 0
    killed: bool = False
    kill_reason: str = ""
    positions: dict[str, Position] = field(default_factory=dict)
    ema: dict[str, float] = field(default_factory=dict)
    last_trade: dict[str, LastTickerTrade] = field(default_factory=dict)
    daily_realized: float = 0.0

    def position(self, ticker: str) -> Position:
        return self.positions.get(ticker, Position())

    def ticker_notional(self, ticker: str, price: float) -> float:
        return self.position(ticker).notional(price)

    def mtm_equity(self, marks: dict[str, float] | None = None) -> float:
        equity = self.cash
        for ticker, pos in self.positions.items():
            px = (marks or {}).get(ticker, pos.avg_price)
            equity += pos.contracts * px
        return equity

    def running_pnl(self, marks: dict[str, float] | None = None) -> float:
        return self.mtm_equity(marks) - self.starting_cash

    def daily_pnl(self, marks: dict[str, float] | None = None) -> float:
        return self.mtm_equity(marks) - self.start_of_day_equity


def asset_class_for_series(series: str) -> str:
    text = (series or "").upper()
    prefix = text.split("-", 1)[0]
    if prefix.startswith("KXBTC") or prefix.startswith("KXETH"):
        return "bitcoin"
    if prefix.startswith(("KXATP", "KXWTA", "KXITF")):
        return "tennis"
    return "other"


def select_bitcoin_tradeable(
    markets: list[MarketSnapshot],
    near_money_low: float = 0.15,
    near_money_high: float = 0.85,
) -> list[MarketSnapshot]:
    """Keep 15-minute BTC books plus near-the-money above/below strikes."""
    kept: list[MarketSnapshot] = []
    for market in markets:
        if market.asset_class != "bitcoin" or market.yes_mid is None:
            continue
        series = (market.series_ticker or market.ticker).upper()
        if "15M" in series or near_money_low <= market.yes_mid <= near_money_high:
            kept.append(market)
    return kept


def select_targeted_markets(
    markets: list[MarketSnapshot],
    event_ticker: str = "",
    market_ticker: str = "",
) -> list[MarketSnapshot]:
    """Keep one pasted/selected event (or a single market on that event)."""
    market_key = (market_ticker or "").strip().upper()
    event_key = (event_ticker or "").strip().upper()
    if market_key:
        exact = [m for m in markets if m.ticker.upper() == market_key]
        if exact:
            return exact
        parent = market_key.rsplit("-", 1)[0]
        event_key = event_key or parent
    if not event_key:
        return list(markets)
    return [
        m
        for m in markets
        if m.event_ticker.upper() == event_key or m.match_id.upper() == event_key
    ]


select_targeted_tennis = select_targeted_markets


def select_in_play(
    markets: list[MarketSnapshot],
    now: float,
    pre_start_s: float = 600.0,
    max_duration_s: float = 12 * 3600.0,
) -> tuple[list[MarketSnapshot], MarketSnapshot | None]:
    """Split to in-play markets and the next upcoming match (if any)."""
    live: list[MarketSnapshot] = []
    upcoming: list[MarketSnapshot] = []
    seen_upcoming: set[str] = set()
    for market in markets:
        if market.occurrence_ts is None:
            continue
        if market.is_in_play(now, pre_start_s, max_duration_s):
            live.append(market)
        elif market.occurrence_ts > now and market.match_id not in seen_upcoming:
            upcoming.append(market)
            seen_upcoming.add(market.match_id)
    upcoming.sort(key=lambda item: item.occurrence_ts or 0.0)
    return live, (upcoming[0] if upcoming else None)


def _num(value: float, digits: int = 6) -> float:
    return round(float(value), digits)
