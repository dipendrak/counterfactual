"""Opted-in synthetic USD cents storage; models and simulations use USD dollars.

Account markers are an application convention, not a Nessie currency feature.
Never infer units from amount magnitude or rewrite raw server records.
"""
from decimal import Decimal, InvalidOperation
import math

USD = "usd"
USD_CENTS = "usd_cents"
CENTS_ACCOUNT_PREFIX = "Synthetic USD cents "
RETIRED_ACCOUNT_PREFIX = "Superseded dollar seed "


def to_cents(value) -> int:
    if isinstance(value, bool):
        raise ValueError("Money cannot be boolean")
    try:
        cents = Decimal(str(value)) * 100
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Money must be a finite decimal amount") from None
    if not cents.is_finite():
        raise ValueError("Money must have at most two decimal places; rounding is forbidden")
    integral = cents.to_integral_value()
    if cents != integral:
        # Earlier fixture generation used binary multiplication (175 * 1.4 =
        # 244.99999999999997). Accept only one floating-point step of residue;
        # strings and genuine sub-cent amounts are never rounded.
        if not isinstance(value, float) or abs(value - int(integral) / 100) > math.ulp(value):
            raise ValueError("Money must have at most two decimal places; rounding is forbidden")
        cents = integral
    return int(cents)


def monetary_unit(snapshot: dict, bundle: dict) -> str:
    unit = bundle.get("monetary_unit", snapshot.get("monetary_unit", USD))
    if unit not in (USD, USD_CENTS):
        raise ValueError("Unknown monetary unit")
    nickname = str(bundle["account"].get("nickname", ""))
    if nickname.startswith(CENTS_ACCOUNT_PREFIX) and unit != USD_CENTS:
        raise ValueError("Cents seed account is missing its unit metadata; re-ingest it")
    return unit


def to_dollars(value, unit: str) -> float:
    if unit == USD:
        return float(value)
    if unit != USD_CENTS or isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("USD cents must be stored as integers")
    return float(Decimal(value) / 100)
