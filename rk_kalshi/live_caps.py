"""Hard live-trading caps and Kalshi Create Order V2 helpers.

These ceilings are code-level and cannot be raised from YAML, the UI, or
style presets. Paper mode does not use this module for execution.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from rk_kalshi.auth import (
    AccountAuthError,
    KalshiCredentials,
    ENV_ENVIRONMENT,
    normalize_environment,
)
from rk_kalshi.fees import quadratic_fee_dollars

# Documented Kalshi Trade API v2 event-order paths (relative to /trade-api/v2).
CREATE_ORDER_PATH = "/portfolio/events/orders"
CANCEL_ORDER_PATH = "/portfolio/events/orders/{order_id}"

LIVE_MAX_DOLLARS_DEFAULT = 5.0
LIVE_MAX_DOLLARS_HARD_CEILING = 10.0
LIVE_DAILY_LOSS_DEFAULT = 10.0
LIVE_DAILY_LOSS_HARD_CEILING = 25.0
LIVE_TIME_IN_FORCE = "immediate_or_cancel"
LIVE_SELF_TRADE_PREVENTION = "taker_at_cross"

LIVE_DISABLED_MESSAGE = (
    "Live Kalshi execution is off. Paper mode is the default. Enable Live from "
    "the dashboard after Connect (valid .env credentials) and the real-money "
    "confirmation checkbox."
)
LIVE_CONFIRM_MESSAGE = (
    "Live trading spends real money. Check both Enable live trading and "
    "I understand this spends real money, then Start. Losses are real."
)
LIVE_CONNECT_REQUIRED = (
    "Refuse live start: Connect a Kalshi account first using KALSHI_API_KEY_ID "
    "and KALSHI_PRIVATE_KEY_PATH from a local .env (never paste keys in the UI)."
)
LIVE_ENV_REQUIRED = (
    "Refuse live start: set KALSHI_ENVIRONMENT to demo or prod in a local .env. "
    "Blank or unknown values are treated as ambiguous."
)
LIVE_ENV_MISMATCH = (
    "Refuse live start: KALSHI_ENVIRONMENT does not match the connected host "
    "(demo vs prod)."
)


class LiveStartError(RuntimeError):
    """Live path refused before any order is signed or posted."""


def clamp_live_dollars(requested: float, default: float = LIVE_MAX_DOLLARS_DEFAULT) -> float:
    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return min(value, LIVE_MAX_DOLLARS_HARD_CEILING)


def clamp_live_daily_loss(requested: float, default: float = LIVE_DAILY_LOSS_DEFAULT) -> float:
    try:
        value = float(requested)
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return min(value, LIVE_DAILY_LOSS_HARD_CEILING)


def max_contracts_for_cap(
    price: float,
    max_dollars: float,
    *,
    fee_coefficient: float = 0.07,
    fee_multiplier: float = 1.0,
    base_contracts: int = 1,
) -> int:
    """Largest whole-contract size that stays under the live dollar cap."""
    cap = clamp_live_dollars(max_dollars)
    px = float(price)
    if px <= 0:
        return 0
    limit = max(1, int(base_contracts))
    for contracts in range(limit, 0, -1):
        fee = quadratic_fee_dollars(px, contracts, fee_coefficient, fee_multiplier)
        if contracts * px + fee <= cap + 1e-9:
            return contracts
    return 0


def live_limit_price(side: str, yes_bid: float, yes_ask: float, fallback: float) -> float:
    """Aggressive take: buy YES at ask, sell YES at bid (V2 book is YES-only)."""
    if str(side).lower() == "buy":
        px = float(yes_ask) if yes_ask > 0 else float(fallback)
    else:
        px = float(yes_bid) if yes_bid > 0 else float(fallback)
    return min(max(px, 0.01), 0.99)


def book_side_for_signal(side: str) -> str:
    """Map paper buy/sell YES onto Create Order V2 bid/ask."""
    return "bid" if str(side).lower() == "buy" else "ask"


def format_fixed_count(contracts: int | float) -> str:
    return f"{float(contracts):.2f}"


def format_fixed_price(price: float) -> str:
    return f"{float(price):.4f}"


def create_order_v2_body(
    *,
    ticker: str,
    side: str,
    contracts: int,
    price: float,
    client_order_id: str | None = None,
    time_in_force: str = LIVE_TIME_IN_FORCE,
) -> dict[str, Any]:
    """Body for POST /portfolio/events/orders (Create Order V2)."""
    return {
        "ticker": str(ticker),
        "side": book_side_for_signal(side),
        "count": format_fixed_count(contracts),
        "price": format_fixed_price(price),
        "time_in_force": time_in_force,
        "self_trade_prevention_type": LIVE_SELF_TRADE_PREVENTION,
        "client_order_id": client_order_id or str(uuid.uuid4()),
        "post_only": False,
    }


def cancel_order_path(order_id: str) -> str:
    return CANCEL_ORDER_PATH.format(order_id=str(order_id))


def require_live_credentials(credentials: KalshiCredentials | None, environ: dict[str, str] | None = None) -> KalshiCredentials:
    """Refuse live start when keys are missing or demo/prod is ambiguous."""
    if credentials is None:
        raise LiveStartError(LIVE_CONNECT_REQUIRED)
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_ENVIRONMENT) or "").strip()
    if not raw:
        raise LiveStartError(LIVE_ENV_REQUIRED)
    try:
        wanted = normalize_environment(raw)
    except AccountAuthError as exc:
        raise LiveStartError(LIVE_ENV_REQUIRED) from exc
    if credentials.environment != wanted:
        raise LiveStartError(LIVE_ENV_MISMATCH)
    host = (credentials.base_url or "").lower()
    if wanted == "demo" and "demo" not in host:
        raise LiveStartError(LIVE_ENV_MISMATCH)
    if wanted == "prod" and "demo" in host:
        raise LiveStartError(LIVE_ENV_MISMATCH)
    if not credentials.api_key_id or credentials.private_key is None:
        raise LiveStartError(LIVE_CONNECT_REQUIRED)
    return credentials


def live_caps_payload() -> dict[str, Any]:
    return {
        "max_dollars_default": LIVE_MAX_DOLLARS_DEFAULT,
        "max_dollars_hard_ceiling": LIVE_MAX_DOLLARS_HARD_CEILING,
        "daily_loss_default": LIVE_DAILY_LOSS_DEFAULT,
        "daily_loss_hard_ceiling": LIVE_DAILY_LOSS_HARD_CEILING,
        "allow_size_up": False,
        "allow_martingale": False,
        "time_in_force": LIVE_TIME_IN_FORCE,
        "create_order_path": CREATE_ORDER_PATH,
        "warning": (
            "Live losses are real. Caps cannot be raised past the hard ceiling. "
            "Never paste API keys in chat or the UI."
        ),
    }
