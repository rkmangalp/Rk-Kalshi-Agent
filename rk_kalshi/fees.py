"""Kalshi-style quadratic taker fee.

General schedule (illustrative; series may apply a multiplier M):

    fee = round_up(M * 0.07 * C * P * (1 - P))

P is the contract price in dollars, C is contract count, M defaults to 1.
Rounding is up to the next cent on the whole order.
"""

from __future__ import annotations

import math


def quadratic_fee_dollars(
    price_dollars: float,
    contracts: float = 1.0,
    coefficient: float = 0.07,
    multiplier: float = 1.0,
) -> float:
    if contracts <= 0:
        return 0.0
    price = min(max(float(price_dollars), 0.0), 1.0)
    raw = multiplier * coefficient * float(contracts) * price * (1.0 - price)
    if raw <= 0:
        return 0.0
    return math.ceil(raw * 100.0 - 1e-12) / 100.0


def quadratic_fee_cents(
    price_dollars: float,
    contracts: float = 1.0,
    coefficient: float = 0.07,
    multiplier: float = 1.0,
) -> float:
    return quadratic_fee_dollars(price_dollars, contracts, coefficient, multiplier) * 100.0
