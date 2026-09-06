"""Parse Kalshi tennis match / market URLs and bare tickers.

Public tennis pages (2026) look like:

  https://kalshi.com/markets/kxatpmatch/atp-tennis-match/kxatpmatch-26sep06cerblo
  https://kalshi.com/markets/kxwtamatch/wta-tennis-match/kxwtamatch-26mar29vekgor
  https://kalshi.com/markets/kxitfwmatch/itf-womens-match/kxitfwmatch-26sep06kursid
  https://kalshi.com/markets/kxatpmatch/atp-tennis-match/kxatpmatch-26apr05atmtia/kxatpmatch-26apr05atmtia-atm

A bare event ticker (``KXATPMATCH-26SEP06CERBLO``) or market ticker
(``KXATPMATCH-26SEP06CERBLO-CER``) is also accepted. Series-only pages
and non-tennis Kalshi links are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

TENNIS_SERIES = ("KXATPMATCH", "KXWTAMATCH", "KXITFWMATCH", "KXITFMMATCH")
EXAMPLE_URLS = (
    "https://kalshi.com/markets/kxatpmatch/atp-tennis-match/kxatpmatch-26sep06cerblo",
    "https://kalshi.com/markets/kxwtamatch/wta-tennis-match/kxwtamatch-26mar29vekgor",
    "https://kalshi.com/markets/kxitfwmatch/itf-womens-match/kxitfwmatch-26sep06kursid",
)
_HELP = (
    "Paste a Kalshi tennis match page, for example "
    + EXAMPLE_URLS[0]
    + " — or an event ticker like KXATPMATCH-26SEP06CERBLO."
)

_KALSHI_HOSTS = {
    "kalshi.com",
    "www.kalshi.com",
    "kalshi.co",
    "www.kalshi.co",
    "trading.kalshi.com",
    "election.kalshi.com",
}


class KalshiTennisUrlError(ValueError):
    """Pasted text is not a usable Kalshi tennis match/market."""


@dataclass(frozen=True)
class ParsedTennisContract:
    series_ticker: str
    event_ticker: str
    market_ticker: str | None
    match_id: str
    source: str
    raw: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "series_ticker": self.series_ticker,
            "event_ticker": self.event_ticker,
            "market_ticker": self.market_ticker,
            "match_id": self.match_id,
            "source": self.source,
            "raw": self.raw,
            "label": self.event_ticker,
        }


def parse_tennis_contract(text: str) -> ParsedTennisContract:
    raw = (text or "").strip()
    if not raw:
        raise KalshiTennisUrlError("Paste a Kalshi tennis match URL or ticker. " + _HELP)

    if "://" in raw or raw.lower().startswith(("kalshi.com/", "www.kalshi.com/")):
        return _parse_url(raw)

    ticker = _normalize_ticker(raw)
    if ticker:
        return _from_ticker(ticker, raw=raw, source="ticker")

    raise KalshiTennisUrlError("That is not a recognizable Kalshi tennis URL. " + _HELP)


def _parse_url(raw: str) -> ParsedTennisContract:
    url = raw if "://" in raw else "https://" + raw
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _KALSHI_HOSTS and not host.endswith(".kalshi.com"):
        raise KalshiTennisUrlError("That is not a Kalshi link. " + _HELP)

    query_values: list[str] = []
    for key, values in parse_qs(parsed.query).items():
        if key.lower() in {"ticker", "event_ticker", "market", "market_ticker"}:
            query_values.extend(values)

    segments = [unquote(part) for part in parsed.path.split("/") if part]
    tokens = [_normalize_ticker(part) for part in [*segments, *query_values]]
    tokens = [tok for tok in tokens if tok]

    market = next((tok for tok in reversed(tokens) if _is_market_ticker(tok)), None)
    event = None
    if market:
        event = _event_from_market(market)
    if event is None:
        event = next((tok for tok in reversed(tokens) if _is_event_ticker(tok)), None)
    if event:
        return _from_ticker(market or event, raw=raw, source="url")

    series_in_path = {part.upper() for part in segments}
    if series_in_path & set(TENNIS_SERIES) or any(
        part.upper().startswith("KXBTC") for part in segments
    ):
        if any(part.upper().startswith("KXBTC") or part.upper().startswith("KXETH") for part in segments):
            raise KalshiTennisUrlError(
                "That Kalshi link is not a tennis match (ATP / WTA / ITF). " + _HELP
            )
        raise KalshiTennisUrlError(
            "That Kalshi link is a series page, not a specific tennis match. " + _HELP
        )
    raise KalshiTennisUrlError("That is not a recognizable Kalshi tennis URL. " + _HELP)


def _from_ticker(ticker: str, raw: str, source: str) -> ParsedTennisContract:
    market: str | None = None
    if _is_market_ticker(ticker):
        market = ticker
        event = _event_from_market(ticker)
    elif _is_event_ticker(ticker):
        event = ticker
    else:
        raise KalshiTennisUrlError("That is not a recognizable Kalshi tennis ticker. " + _HELP)
    if event is None:
        raise KalshiTennisUrlError("That is not a recognizable Kalshi tennis ticker. " + _HELP)
    series = event.split("-", 1)[0]
    if series not in TENNIS_SERIES:
        raise KalshiTennisUrlError(
            "That Kalshi ticker is not a tennis match (ATP / WTA / ITF). " + _HELP
        )
    return ParsedTennisContract(
        series_ticker=series,
        event_ticker=event,
        market_ticker=market,
        match_id=event,
        source=source,
        raw=raw,
    )


def _normalize_ticker(value: str) -> str | None:
    text = (value or "").strip().strip("/")
    if not text:
        return None
    text = text.split("?", 1)[0].split("#", 1)[0].upper()
    if _is_market_ticker(text) or _is_event_ticker(text) or text in TENNIS_SERIES:
        return text
    return None


def _is_event_ticker(ticker: str) -> bool:
    parts = ticker.split("-")
    if len(parts) != 2:
        return False
    return parts[0] in TENNIS_SERIES and _looks_like_match_code(parts[1])


def _is_market_ticker(ticker: str) -> bool:
    parts = ticker.split("-")
    if len(parts) != 3:
        return False
    return (
        parts[0] in TENNIS_SERIES
        and _looks_like_match_code(parts[1])
        and parts[2].isalnum()
        and 2 <= len(parts[2]) <= 8
    )


def _looks_like_match_code(code: str) -> bool:
    # 26SEP06CERBLO — YY + MON + DD + player codes
    if len(code) < 9:
        return False
    return code[:2].isdigit() and code[2:5].isalpha() and code[5:7].isdigit() and code[7:].isalnum()


def _event_from_market(ticker: str) -> str | None:
    parts = ticker.split("-")
    if len(parts) < 2:
        return None
    event = "-".join(parts[:2])
    return event if _is_event_ticker(event) else None
