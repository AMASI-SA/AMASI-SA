"""Tiered courier cash-on-delivery commission rules.

The merchant contract can price COD collection by the amount collected for
each delivered shipment.  A rule therefore owns an explicit lower/upper
boundary, percentage, fixed fee, and VAT rate.  Bounds carry their own
inclusive flags so contracts such as ``50..1000`` followed by
``>1000..3000`` are unambiguous at exactly SAR 1,000.

This module is deliberately pure.  It performs no database or ledger writes;
the shipping ledger and settlement workflow consume the same calculation so
estimates cannot drift between screens.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


MONEY = Decimal("0.01")


def _money(value: Any) -> float:
    return float(Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP))


class CourierCodFeeTier(BaseModel):
    """One per-shipment COD commission bracket.

    ``commission_percent`` is stored as a decimal for compatibility with the
    existing courier settings contract (``0.01`` means 1%).  The UI converts
    it to a merchant-facing percentage.
    """

    min_amount: float = Field(ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    min_inclusive: bool = True
    max_inclusive: bool = True
    commission_percent: float = Field(ge=0, le=1)
    fixed_fee: float = Field(default=0, ge=0)
    vat_percent: float = Field(default=15, ge=0, le=100)

    @model_validator(mode="after")
    def _validate_bounds(self):
        if self.max_amount is not None and self.max_amount < self.min_amount:
            raise ValueError("max_amount must be greater than or equal to min_amount")
        if (
            self.max_amount is not None
            and self.max_amount == self.min_amount
            and not (self.min_inclusive and self.max_inclusive)
        ):
            raise ValueError("an empty COD fee tier is not allowed")
        return self


def validate_courier_cod_fee_tiers(tiers: list[Any]) -> list[dict]:
    """Validate ordering/overlap and return normalized dictionaries."""
    normalized = [
        row.model_dump() if isinstance(row, CourierCodFeeTier)
        else CourierCodFeeTier.model_validate(row).model_dump()
        for row in (tiers or [])
    ]
    normalized.sort(key=lambda row: (
        float(row["min_amount"]),
        float("inf") if row.get("max_amount") is None else float(row["max_amount"]),
    ))

    for index, current in enumerate(normalized):
        if index == 0:
            continue
        previous = normalized[index - 1]
        previous_max = previous.get("max_amount")
        if previous_max is None:
            raise ValueError("an unlimited COD fee tier must be the final tier")
        current_min = float(current["min_amount"])
        previous_max = float(previous_max)
        if current_min < previous_max:
            raise ValueError("COD fee tiers must not overlap")
        if (
            current_min == previous_max
            and previous.get("max_inclusive", True)
            and current.get("min_inclusive", True)
        ):
            raise ValueError("adjacent COD fee tiers overlap at their shared boundary")
    return normalized


def _matches(amount: float, rule: dict) -> bool:
    lower = float(rule.get("min_amount") or 0)
    upper = rule.get("max_amount")
    if amount < lower or (amount == lower and not rule.get("min_inclusive", True)):
        return False
    if upper is None:
        return True
    upper = float(upper)
    return amount < upper or (amount == upper and rule.get("max_inclusive", True))


def calculate_courier_cod_fee(cod_amount: float, company: dict) -> dict:
    """Calculate net fee, fee VAT, and total for one delivered COD order.

    Tier rules take priority.  The legacy flat percentage/fixed fields remain
    a fallback for companies that have not configured tiers.  A tiered company
    with no matching bracket is *not* silently charged zero: the result is
    marked ``needs_review`` so the caller can surface the uncovered range.
    """
    amount = _money(cod_amount)
    raw_tiers = company.get("cod_fee_tiers") or []
    tiers = validate_courier_cod_fee_tiers(raw_tiers) if raw_tiers else []
    rule = next((row for row in tiers if _matches(amount, row)), None)

    if tiers and rule is None:
        return {
            "cod_amount": amount,
            "fee_net": 0.0,
            "fee_vat": 0.0,
            "fee_total": 0.0,
            "commission_percent": None,
            "fixed_fee": None,
            "vat_percent": None,
            "source": "tier_unmatched",
            "needs_review": True,
            "matched_rule": None,
        }

    if rule is not None:
        percent = float(rule.get("commission_percent") or 0)
        fixed = float(rule.get("fixed_fee") or 0)
        vat_percent = float(rule.get("vat_percent") or 0)
        source = "tier"
    else:
        percent = float(company.get("cod_fee_percent") or 0)
        fixed = float(company.get("cod_fee_fixed_per_order") or 0)
        vat_percent = float(
            company.get("cod_fee_vat_percent")
            if company.get("cod_fee_vat_percent") is not None
            else (company.get("vat_percent") or 0)
        )
        source = "flat"

    fee_net = _money(Decimal(str(amount)) * Decimal(str(percent)) + Decimal(str(fixed)))
    fee_vat = _money(Decimal(str(fee_net)) * Decimal(str(vat_percent)) / Decimal("100"))
    return {
        "cod_amount": amount,
        "fee_net": fee_net,
        "fee_vat": fee_vat,
        "fee_total": _money(Decimal(str(fee_net)) + Decimal(str(fee_vat))),
        "commission_percent": round(percent * 100, 4),
        "fixed_fee": _money(fixed),
        "vat_percent": round(vat_percent, 4),
        "source": source,
        "needs_review": False,
        "matched_rule": rule,
    }


__all__ = [
    "CourierCodFeeTier",
    "calculate_courier_cod_fee",
    "validate_courier_cod_fee_tiers",
]
