"""Optional ChatGPT / OpenAI paper-trading research path.

Reads OPENAI_API_KEY from the process environment / local .env only.
Never accepts keys from the dashboard. Live orders stay disabled.

LLM research is slow versus the book and is not a guaranteed edge.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import httpx

from rk_kalshi.auth import load_dotenv_file
from rk_kalshi.config import AppConfig
from rk_kalshi.fees import quadratic_fee_cents, quadratic_fee_dollars
from rk_kalshi.models import MarketSnapshot, PaperState, Signal
from rk_kalshi.signal import SIGNAL_DISCLAIMER, _thesis_caveat

OPENAI_ENV = "OPENAI_API_KEY"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MISSING_OPENAI_MESSAGE = (
    "OPENAI_API_KEY is missing. Set it in a local .env (never paste keys in the UI)."
)


class ChatCompletionsClient(Protocol):
    def complete(self, *, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        ...


def openai_api_key(environ: Mapping[str, str] | None = None) -> str:
    if environ is None:
        load_dotenv_file()
        environ = os.environ
    return str(environ.get(OPENAI_ENV) or "").strip()


def openai_configured(environ: Mapping[str, str] | None = None) -> bool:
    return bool(openai_api_key(environ))


@dataclass(frozen=True)
class LlmDecision:
    ticker: str
    action: str
    edge_cents_estimate: float
    confidence: float
    thesis: str
    model: str


class HttpOpenAIClient:
    def __init__(self, api_key: str, timeout_s: float = 20.0, http: httpx.Client | None = None):
        self.api_key = api_key
        self._owns = http is None
        self._http = http or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def complete(self, *, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        response = self._http.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": messages,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("OpenAI returned a non-object JSON body")
        return payload


class LlmResearchTrader:
    def __init__(
        self,
        cfg: AppConfig,
        *,
        api_key: str | None = None,
        client: ChatCompletionsClient | None = None,
        now: float | None = None,
    ):
        self.cfg = cfg
        load_dotenv_file()
        self.api_key = (api_key if api_key is not None else openai_api_key()).strip()
        self._client = client
        self._last_call_ts = 0.0
        self.last_note = ""
        self._clock = now

    def configured(self) -> bool:
        return bool(self.api_key) or self._client is not None

    def refine(
        self,
        markets: list[MarketSnapshot],
        as_signals: list[Signal],
        inventory: Mapping[str, Any] | PaperState | None = None,
        now: float | None = None,
    ) -> list[Signal]:
        mode = (self.cfg.signal_mode or "as_obi").strip().lower()
        if mode not in {"llm", "hybrid"}:
            self.last_note = ""
            return as_signals
        if not self.configured():
            self.last_note = MISSING_OPENAI_MESSAGE
            return [] if mode == "llm" else as_signals
        if mode == "hybrid" and not as_signals:
            self.last_note = "hybrid: no AS+OBI candidates; skipped ChatGPT"
            return []

        stamp = time.time() if now is None else now
        min_interval = max(0.0, float(self.cfg.llm_min_interval_s))
        if self._last_call_ts and (stamp - self._last_call_ts) < min_interval:
            self.last_note = "ChatGPT rate-limited this cycle"
            return [] if mode == "llm" else as_signals

        pool = as_signals if mode == "hybrid" else markets
        chosen_markets = _markets_for_llm(markets, pool, self.cfg.llm_max_markets_per_call)
        if not chosen_markets:
            self.last_note = "no markets for ChatGPT"
            return []

        try:
            decisions = self._decide(chosen_markets, inventory, as_signals, mode)
        except (httpx.HTTPError, OSError, RuntimeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            self.last_note = f"ChatGPT request failed: {exc}"
            return [] if mode == "llm" else as_signals
        self._last_call_ts = stamp
        if mode == "hybrid":
            return _apply_hybrid(as_signals, decisions, self.cfg.llm_model)
        out: list[Signal] = []
        by_ticker = {m.ticker: m for m in chosen_markets}
        for decision in decisions:
            market = by_ticker.get(decision.ticker)
            if market is None:
                continue
            signal = signal_from_llm_decision(market, decision, self.cfg)
            if signal is not None:
                out.append(signal)
        out.sort(key=lambda s: s.edge_cents, reverse=True)
        return out

    def _decide(
        self,
        markets: list[MarketSnapshot],
        inventory: Mapping[str, Any] | PaperState | None,
        as_signals: list[Signal],
        mode: str,
    ) -> list[LlmDecision]:
        client = self._client
        if client is None:
            if not self.api_key:
                return []
            client = HttpOpenAIClient(self.api_key, timeout_s=max(5.0, self.cfg.request_timeout_s))
        payload = _prompt_payload(markets, inventory, as_signals, mode, self.cfg)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a paper-trading research assistant for Kalshi-style binary "
                    "prediction markets (tennis matches and Bitcoin up/down). Return JSON "
                    "only. Never claim guaranteed profit. This is not financial advice. "
                    "Anticipate likely near-term score swings, momentum shifts, and how "
                    "those would move YES vs NO mids — do not only restate the current "
                    "odds. For in-play tennis: hold vs break, swing after a game or set, "
                    "and whether the YES mid should reprice up or down. For upcoming "
                    "matches: the likely opening swing after the first games. For Bitcoin "
                    "15m: short-horizon continuation vs mean-reversion of YES, not a BTC "
                    "price forecast. Prefer skip when the book is tight, the anticipated "
                    "move is unclear, or fees eat the edge. Live order placement is "
                    "disabled. Research is advisory and rate-limited."
                ),
            },
            {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
        ]
        raw = client.complete(model=self.cfg.llm_model, messages=messages)
        text = _message_text(raw)
        return parse_llm_decisions(text, self.cfg.llm_model)


def parse_llm_decisions(text: str, model: str) -> list[LlmDecision]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    rows = payload.get("decisions") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    out: list[LlmDecision] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or row.get("side") or "skip").strip().lower()
        if action not in {"buy", "sell", "skip"}:
            action = "skip"
        ticker = str(row.get("ticker") or "").strip()
        if not ticker:
            continue
        try:
            edge = float(row.get("edge_cents_estimate") or row.get("edge_cents") or 0.0)
        except (TypeError, ValueError):
            edge = 0.0
        try:
            confidence = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        thesis = str(row.get("thesis") or "").strip()
        out.append(
            LlmDecision(
                ticker=ticker,
                action=action,
                edge_cents_estimate=edge,
                confidence=max(0.0, min(1.0, confidence)),
                thesis=thesis[:400],
                model=model,
            )
        )
    return out


def signal_from_llm_decision(
    market: MarketSnapshot,
    decision: LlmDecision,
    cfg: AppConfig,
) -> Signal | None:
    if decision.action not in {"buy", "sell"}:
        return None
    mid = market.yes_mid
    half_spread = market.half_spread
    if mid is None or half_spread is None:
        return None
    fee_cents = quadratic_fee_cents(
        mid,
        contracts=1.0,
        coefficient=cfg.fee_coefficient,
        multiplier=cfg.fee_multiplier,
    )
    cost_cents = half_spread * 100.0 + fee_cents
    raw_edge = float(decision.edge_cents_estimate)
    net = raw_edge - cost_cents
    if net < cfg.edge_threshold_cents:
        return None
    fee = quadratic_fee_dollars(
        mid,
        contracts=1.0,
        coefficient=cfg.fee_coefficient,
        multiplier=cfg.fee_multiplier,
    )
    thesis = (
        f"{decision.action.upper()} YES {market.ticker}: ChatGPT {decision.model} "
        f"conf={decision.confidence:.2f} estimate {raw_edge:.2f}¢; half-spread "
        f"{half_spread * 100:.2f}¢ + fee {fee_cents:.2f}¢; net edge {net:.2f}¢ "
        f"(threshold {cfg.edge_threshold_cents:.2f}¢). {decision.thesis} "
        f"{_thesis_caveat(market)} LLM research is not a guaranteed edge."
    )
    return Signal(
        ticker=market.ticker,
        event_name=market.event_name,
        match_id=market.match_id,
        side=decision.action,
        live_mid=mid,
        fill_price=mid,
        edge_cents=net,
        edge_bps=net * 100.0,
        edge_thesis=thesis,
        fee_per_contract=fee,
        contracts=cfg.base_contracts,
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        last_price=market.last_price,
        fair_yes=mid,
    )


def _apply_hybrid(as_signals: list[Signal], decisions: list[LlmDecision], model: str) -> list[Signal]:
    by_ticker = {item.ticker: item for item in decisions}
    kept: list[Signal] = []
    for signal in as_signals:
        decision = by_ticker.get(signal.ticker)
        if decision is None or decision.action == "skip":
            continue
        if decision.action != signal.side:
            continue
        thesis = (
            f"{signal.edge_thesis} | ChatGPT {model} confirmed "
            f"(conf={decision.confidence:.2f}): {decision.thesis}".strip()
        )
        kept.append(
            Signal(
                ticker=signal.ticker,
                event_name=signal.event_name,
                match_id=signal.match_id,
                side=signal.side,
                live_mid=signal.live_mid,
                fill_price=signal.fill_price,
                edge_cents=signal.edge_cents,
                edge_bps=signal.edge_bps,
                edge_thesis=thesis,
                fee_per_contract=signal.fee_per_contract,
                contracts=signal.contracts,
                yes_bid=signal.yes_bid,
                yes_ask=signal.yes_ask,
                last_price=signal.last_price,
                fair_yes=signal.fair_yes,
            )
        )
    return kept


def _markets_for_llm(
    markets: list[MarketSnapshot],
    pool: list[MarketSnapshot] | list[Signal],
    limit: int,
) -> list[MarketSnapshot]:
    tickers: list[str] = []
    for item in pool:
        ticker = item.ticker if hasattr(item, "ticker") else ""
        if ticker and ticker not in tickers:
            tickers.append(ticker)
        if len(tickers) >= limit:
            break
    by_ticker = {m.ticker: m for m in markets}
    return [by_ticker[t] for t in tickers if t in by_ticker]


def _inventory_q(ticker: str, inventory: Mapping[str, Any] | PaperState | None) -> float:
    if inventory is None:
        return 0.0
    if isinstance(inventory, PaperState):
        return float(inventory.position(ticker).contracts)
    pos = inventory.get(ticker, 0) if isinstance(inventory, Mapping) else 0
    if hasattr(pos, "contracts"):
        return float(pos.contracts)
    try:
        return float(pos)
    except (TypeError, ValueError):
        return 0.0


def _prompt_payload(
    markets: list[MarketSnapshot],
    inventory: Mapping[str, Any] | PaperState | None,
    as_signals: list[Signal],
    mode: str,
    cfg: AppConfig,
) -> dict[str, Any]:
    as_by_ticker = {s.ticker: s for s in as_signals}
    now = time.time()
    rows = []
    for market in markets:
        mid = market.yes_mid
        start_in = None
        if market.occurrence_ts is not None:
            start_in = market.occurrence_ts - now
        close_in = None
        if market.close_ts is not None:
            close_in = market.close_ts - now
        rows.append(
            {
                "ticker": market.ticker,
                "event_name": market.event_name,
                "title": market.title,
                "asset_class": market.asset_class,
                "yes_bid": market.yes_bid,
                "yes_ask": market.yes_ask,
                "yes_mid": mid,
                "last_price": market.last_price,
                "spread_cents": market.spread_cents,
                "obi": market.order_book_imbalance,
                "volume": market.volume,
                "inventory_q": _inventory_q(market.ticker, inventory),
                "in_play": market.is_in_play(now),
                "occurrence_ts": market.occurrence_ts,
                "close_ts": market.close_ts,
                "seconds_to_start": start_in,
                "seconds_to_close": close_in,
                "as_side": as_by_ticker[market.ticker].side if market.ticker in as_by_ticker else None,
                "as_edge_cents": (
                    as_by_ticker[market.ticker].edge_cents if market.ticker in as_by_ticker else None
                ),
            }
        )
    return {
        "mode": mode,
        "edge_threshold_cents": cfg.edge_threshold_cents,
        "paper_only": True,
        "disclaimer": SIGNAL_DISCLAIMER,
        "instruction": (
            "Return JSON {\"decisions\":[{\"ticker\",\"side\":\"buy|sell|skip\","
            "\"action\":\"buy|sell|skip\",\"edge_cents_estimate\",\"confidence\","
            "\"thesis\"}]}. side and action are the same paper YES action "
            "(buy/sell/skip). Use skip often. "
            "Anticipate likely tennis score/momentum swings (or short-horizon Bitcoin "
            "drift) and map that to YES/NO mid moves; do not only summarize current odds. "
            "Thesis must mention the anticipated path. Research is advisory only."
        ),
        "target": {
            "url": cfg.target_url,
            "event_ticker": cfg.target_event_ticker,
            "market_ticker": cfg.target_market_ticker,
            "label": cfg.target_label,
            "active": bool(cfg.target_event_ticker or cfg.target_market_ticker),
        },
        "markets": rows,
    }


def _message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return "{}"
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return "{}"
    return str(message.get("content") or "{}")
