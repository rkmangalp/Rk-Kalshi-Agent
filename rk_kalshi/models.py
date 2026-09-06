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

    @property
    def match_id(self) -> str:
        return self.event_ticker

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


def _num(value: float, digits: int = 6) -> float:
    return round(float(value), digits)
