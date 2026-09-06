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


class KalshiUrlError(ValueError):
    """Pasted text is not a usable Kalshi tennis or crypto contract."""


class KalshiTennisUrlError(KalshiUrlError):
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


EXAMPLE_CRYPTO_URLS = (
    "https://kalshi.com/markets/kxbtc15m/bitcoin-price-up-down/kxbtc15m-26sep061845",
)
EXAMPLE_CHALLENGER_URL = (
    "https://kalshi.com/markets/kxatpchallengermatch/challenger-atp-/"
    "kxatpchallengermatch-26sep06kimtam"
)
EXAMPLE_CONTRACT_URLS = (
    EXAMPLE_URLS[0],
    EXAMPLE_CHALLENGER_URL,
    EXAMPLE_CRYPTO_URLS[0],
)
_HELP_MULTI = (
    "Paste any Kalshi market or event URL, for example "
    + EXAMPLE_CHALLENGER_URL
    + " or "
    + EXAMPLE_CRYPTO_URLS[0]
    + "."
)
_CRYPTO_SERIES = ("KXBTC15M", "KXBTCD")


@dataclass(frozen=True)
class ParsedContract:
    series_ticker: str
    event_ticker: str
    market_ticker: str | None
    match_id: str
    source: str
    raw: str
    asset_class: str

    def as_dict(self) -> dict[str, str | None]:
        return {
            "series_ticker": self.series_ticker,
            "event_ticker": self.event_ticker,
            "market_ticker": self.market_ticker,
            "match_id": self.match_id,
            "source": self.source,
            "raw": self.raw,
            "label": self.event_ticker,
            "asset_class": self.asset_class,
        }


def parse_contract(text: str) -> ParsedContract:
    """Parse any Kalshi event/market URL or KX ticker — no series whitelist."""
    raw = (text or "").strip()
    if not raw:
        raise KalshiUrlError("Paste any Kalshi market or event URL. " + _HELP_MULTI)

    if "://" in raw or raw.lower().startswith(("kalshi.com/", "www.kalshi.com/")):
        series, event, market = _generic_from_url(raw)
        source = "url"
    else:
        series, event, market = _generic_from_ticker(raw)
        source = "ticker"

    if not event:
        raise KalshiUrlError("That is not a recognizable Kalshi event or market. " + _HELP_MULTI)
    return ParsedContract(
        series_ticker=series,
        event_ticker=event,
        market_ticker=market,
        match_id=event,
        source=source,
        raw=raw,
        asset_class=_asset_class_for_series(series),
    )


def _asset_class_for_series(series: str) -> str:
    prefix = (series or "").upper().split("-", 1)[0]
    if prefix.startswith("KXBTC") or prefix.startswith("KXETH"):
        return "bitcoin"
    if prefix.startswith(("KXATP", "KXWTA", "KXITF")):
        return "tennis"
    return "other"


def _generic_from_url(raw: str) -> tuple[str, str, str | None]:
    url = raw if "://" in raw else "https://" + raw
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _KALSHI_HOSTS and not host.endswith(".kalshi.com"):
        raise KalshiUrlError("That is not a Kalshi link. " + _HELP_MULTI)

    pieces: list[str] = []
    for part in parsed.path.split("/"):
        if part:
            pieces.append(unquote(part))
    for key, values in parse_qs(parsed.query).items():
        if key.lower() in {"ticker", "event_ticker", "market", "market_ticker"}:
            pieces.extend(values)

    tokens = [tok for piece in pieces if (tok := _normalize_kx_token(piece))]
    market = next((tok for tok in reversed(tokens) if _kx_kind(tok) == "market"), None)
    event = _event_from_kx(market) if market else None
    if event is None:
        event = next((tok for tok in reversed(tokens) if _kx_kind(tok) == "event"), None)
    if event:
        series = event.split("-", 1)[0]
        return series, event, market

    if any(_kx_kind(tok) == "series" for tok in tokens) or any(
        _normalize_kx_token(part) and _kx_kind(_normalize_kx_token(part) or "") == "series"
        for part in pieces
    ):
        raise KalshiUrlError(
            "That Kalshi link is a series page, not a specific event or market. " + _HELP_MULTI
        )
    raise KalshiUrlError("That is not a recognizable Kalshi event or market. " + _HELP_MULTI)


def _generic_from_ticker(raw: str) -> tuple[str, str, str | None]:
    ticker = _normalize_kx_token(raw)
    if not ticker:
        raise KalshiUrlError("That is not a recognizable Kalshi event or market. " + _HELP_MULTI)
    kind = _kx_kind(ticker)
    if kind == "series":
        raise KalshiUrlError(
            "That Kalshi ticker is a series, not a specific event or market. " + _HELP_MULTI
        )
    if kind == "market":
        event = _event_from_kx(ticker)
        if not event:
            raise KalshiUrlError("That is not a recognizable Kalshi event or market. " + _HELP_MULTI)
        return event.split("-", 1)[0], event, ticker
    if kind == "event":
        return ticker.split("-", 1)[0], ticker, None
    raise KalshiUrlError("That is not a recognizable Kalshi event or market. " + _HELP_MULTI)


def _normalize_kx_token(value: str) -> str | None:
    text = (value or "").strip().strip("/")
    if not text:
        return None
    text = text.split("?", 1)[0].split("#", 1)[0].upper()
    if _kx_kind(text):
        return text
    return None


def _kx_kind(ticker: str) -> str | None:
    """Return 'series', 'event', or 'market' for a KX… token."""
    if not ticker.startswith("KX") or len(ticker) < 4:
        return None
    parts = ticker.split("-")
    series = parts[0]
    if not series[2:].isalnum():
        return None
    if len(parts) == 1:
        return "series"
    code = parts[1]
    if len(code) < 4 or not all(ch.isalnum() or ch == "." for ch in code):
        return None
    if len(parts) == 2:
        return "event"
    suffix = "-".join(parts[2:])
    if not suffix or not all(ch.isalnum() or ch in {".", "_"} for ch in suffix):
        return None
    return "market"


def _event_from_kx(ticker: str | None) -> str | None:
    if not ticker:
        return None
    parts = ticker.split("-")
    if len(parts) < 2:
        return None
    event = "-".join(parts[:2])
    return event if _kx_kind(event) == "event" else None


def parse_crypto_contract(text: str) -> ParsedContract:
    raw = (text or "").strip()
    if not raw:
        raise KalshiUrlError("Paste a Kalshi Bitcoin URL or ticker. " + _HELP_MULTI)

    if "://" in raw or raw.lower().startswith(("kalshi.com/", "www.kalshi.com/")):
        return _parse_crypto_url(raw)

    ticker = _normalize_crypto_ticker(raw)
    if ticker:
        return _crypto_from_ticker(ticker, raw=raw, source="ticker")

    raise KalshiUrlError("That is not a recognizable Kalshi Bitcoin URL. " + _HELP_MULTI)


def _parse_crypto_url(raw: str) -> ParsedContract:
    url = raw if "://" in raw else "https://" + raw
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _KALSHI_HOSTS and not host.endswith(".kalshi.com"):
        raise KalshiUrlError("That is not a Kalshi link. " + _HELP_MULTI)

    query_values: list[str] = []
    for key, values in parse_qs(parsed.query).items():
        if key.lower() in {"ticker", "event_ticker", "market", "market_ticker"}:
            query_values.extend(values)

    segments = [unquote(part) for part in parsed.path.split("/") if part]
    tokens = [_normalize_crypto_ticker(part) for part in [*segments, *query_values]]
    tokens = [tok for tok in tokens if tok]

    market = next((tok for tok in reversed(tokens) if _is_crypto_market_ticker(tok)), None)
    event = _crypto_event_from_market(market) if market else None
    if event is None:
        event = next((tok for tok in reversed(tokens) if _is_crypto_event_ticker(tok)), None)
    if event:
        return _crypto_from_ticker(market or event, raw=raw, source="url")

    if any(_is_crypto_series(part.upper()) for part in segments):
        raise KalshiUrlError(
            "That Kalshi link is a series page, not a specific Bitcoin or ETH contract. "
            + _HELP_MULTI
        )
    raise KalshiUrlError("That is not a recognizable Kalshi Bitcoin URL. " + _HELP_MULTI)


def _crypto_from_ticker(ticker: str, raw: str, source: str) -> ParsedContract:
    market: str | None = None
    if _is_crypto_market_ticker(ticker):
        market = ticker
        event = _crypto_event_from_market(ticker)
    elif _is_crypto_event_ticker(ticker):
        event = ticker
    else:
        raise KalshiUrlError("That is not a recognizable Kalshi Bitcoin ticker. " + _HELP_MULTI)
    if event is None:
        raise KalshiUrlError("That is not a recognizable Kalshi Bitcoin ticker. " + _HELP_MULTI)
    series = event.split("-", 1)[0]
    if not _is_crypto_series(series):
        raise KalshiUrlError("That Kalshi ticker is not a Bitcoin or ETH contract. " + _HELP_MULTI)
    return ParsedContract(
        series_ticker=series,
        event_ticker=event,
        market_ticker=market,
        match_id=event,
        source=source,
        raw=raw,
        asset_class="bitcoin",
    )


def _normalize_crypto_ticker(value: str) -> str | None:
    text = (value or "").strip().strip("/")
    if not text:
        return None
    text = text.split("?", 1)[0].split("#", 1)[0].upper()
    if _is_crypto_market_ticker(text) or _is_crypto_event_ticker(text) or _is_crypto_series(text):
        return text
    return None


def _is_crypto_series(name: str) -> bool:
    series = (name or "").upper()
    return series.startswith("KXBTC") or series.startswith("KXETH") or series in _CRYPTO_SERIES


def _is_crypto_event_ticker(ticker: str) -> bool:
    parts = ticker.split("-")
    if len(parts) != 2:
        return False
    return _is_crypto_series(parts[0]) and parts[1].isalnum() and len(parts[1]) >= 6


def _is_crypto_market_ticker(ticker: str) -> bool:
    parts = ticker.split("-")
    if len(parts) != 3:
        return False
    suffix = parts[2].replace(".", "")
    return (
        _is_crypto_series(parts[0])
        and parts[1].isalnum()
        and len(parts[1]) >= 6
        and suffix.isalnum()
        and 1 <= len(parts[2]) <= 16
    )


def _crypto_event_from_market(ticker: str | None) -> str | None:
    if not ticker:
        return None
    parts = ticker.split("-")
    if len(parts) < 2:
        return None
    event = "-".join(parts[:2])
    return event if _is_crypto_event_ticker(event) else None


def _looks_like_crypto_text(text: str) -> bool:
    upper = (text or "").upper()
    return "KXBTC" in upper or "KXETH" in upper or "BITCOIN" in upper
