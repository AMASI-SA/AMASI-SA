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

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


MONEY = Decimal("0.01")


def _decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("courier_amount_invalid") from None
    if not number.is_finite() or number < 0:
        raise ValueError("courier_amount_invalid")
    return number


def money_decimal(value: Any) -> Decimal:
    """One half-up/halala rounding policy for shipping and commission."""
    return _decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _money(value: Any) -> float:
    # Historical public callers store JSON numbers. Never calculate in float.
    return float(money_decimal(value))


def split_tax_amount(quoted_amount: Any, vat_percent: Any, *, inclusive: bool) -> dict:
    """Split a contractual quote with an exact net + VAT == gross identity.

    Inclusive: round gross, round net = gross / (1 + rate), then take VAT as
    the remainder. Rounding VAT independently could introduce a spare halala.
    No company name/ID or global tax default is consulted here.
    """
    if not isinstance(inclusive, bool):
        raise ValueError("courier_tax_inclusion_must_be_explicit")
    quoted, rate = money_decimal(quoted_amount), _decimal(vat_percent)
    if rate > 100:
        raise ValueError("courier_vat_rate_invalid")
    if inclusive:
        gross = quoted
        net = money_decimal(gross / (Decimal(1) + rate / 100))
        vat = gross - net
    else:
        net = quoted
        vat = money_decimal(net * rate / 100)
        gross = net + vat
    return {"net": net, "vat": vat, "gross": gross}



class CourierCodFeeTier(BaseModel):
    """One per-shipment COD commission bracket.

    ``commission_percent`` is stored as a decimal for compatibility with the
    existing courier settings contract (``0.01`` means 1%).  The UI converts
    it to a merchant-facing percentage.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    min_amount: float = Field(ge=0)
    max_amount: Optional[float] = Field(default=None, ge=0)
    min_inclusive: bool = True
    max_inclusive: bool = True
    commission_percent: float = Field(ge=0, le=1)
    fixed_fee: float = Field(default=0, ge=0)
    vat_percent: float = Field(default=15, ge=0, le=100)
    vat_included: bool = False
    # Provenance is optional for historical callers. MZ2 persists it on every
    # tier of an immutable, effective-dated contract version.
    effective_from: Optional[str] = None
    evidence_ref: Optional[str] = None

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
        _decimal(row["min_amount"]),
        Decimal("Infinity") if row.get("max_amount") is None else _decimal(row["max_amount"]),
    ))

    for index, current in enumerate(normalized):
        if index == 0:
            continue
        previous = normalized[index - 1]
        previous_max = previous.get("max_amount")
        if previous_max is None:
            raise ValueError("an unlimited COD fee tier must be the final tier")
        current_min = _decimal(current["min_amount"])
        previous_max = _decimal(previous_max)
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
    amount = _decimal(amount)
    lower = _decimal(rule.get("min_amount") or 0)
    upper = rule.get("max_amount")
    if amount < lower or (amount == lower and not rule.get("min_inclusive", True)):
        return False
    if upper is None:
        return True
    upper = _decimal(upper)
    return amount < upper or (amount == upper and rule.get("max_inclusive", True))


def calculate_courier_cod_fee_decimal(cod_amount: Any, company: dict) -> dict:
    """Canonical Decimal engine; legacy and MZ2 call exactly this function.

    MZ2 supplies commission_vat_inclusive from the immutable contract. Legacy
    callers retain their explicit per-tier/flat inclusion flags. An explicit
    conflicting tier flag fails closed; no implicit SMSA or iMile defaults.
    """
    amount = money_decimal(cod_amount)
    raw_tiers = company.get("cod_fee_tiers") or []
    tiers = validate_courier_cod_fee_tiers(raw_tiers) if raw_tiers else []
    rule = next((row for row in tiers if _matches(amount, row)), None)
    if (tiers or company.get("cod_tiers_required")) and rule is None:
        return {"cod_amount": amount, "fee_net": Decimal(0), "fee_vat": Decimal(0),
                "fee_total": Decimal(0), "commission_percent": None, "fixed_fee": None,
                "vat_percent": None, "source": "tier_unmatched", "needs_review": True,
                "matched_rule": None}
    if rule is not None:
        percent, fixed = _decimal(rule["commission_percent"]), _decimal(rule["fixed_fee"])
        vat_percent = _decimal(rule["vat_percent"])
        inclusive = rule["vat_included"]
        source = "tier"
    else:
        percent = _decimal(company.get("cod_fee_percent") or 0)
        fixed = _decimal(company.get("cod_fee_fixed_per_order") or 0)
        vat_percent = _decimal(company.get("cod_fee_vat_percent")
            if company.get("cod_fee_vat_percent") is not None else company.get("vat_percent") or 0)
        inclusive = company.get("cod_fee_vat_included", False)
        source = "flat"
    contract_inclusive = company.get("commission_vat_inclusive")
    if contract_inclusive is not None:
        if not isinstance(contract_inclusive, bool):
            raise ValueError("courier_tax_inclusion_must_be_explicit")
        for raw in raw_tiers:
            explicit = (raw.model_dump(exclude_unset=True)
                        if isinstance(raw, CourierCodFeeTier) else raw)
            if "vat_included" in explicit and explicit["vat_included"] != contract_inclusive:
                raise ValueError("courier_commission_tax_policy_conflict")
        inclusive = contract_inclusive
    if percent > 1 or vat_percent > 100:
        raise ValueError("courier_commission_rate_invalid")
    amounts = split_tax_amount(amount * percent + fixed, vat_percent, inclusive=inclusive)
    return {"cod_amount": amount, "fee_net": amounts["net"], "fee_vat": amounts["vat"],
            "fee_total": amounts["gross"], "commission_percent": percent * 100,
            "fixed_fee": money_decimal(fixed), "vat_percent": vat_percent,
            "source": source, "vat_included": inclusive, "needs_review": False,
            "matched_rule": rule}


def calculate_courier_cod_fee(cod_amount: Any, company: dict) -> dict:
    """Backward-compatible JSON-numeric facade over the ONE Decimal engine."""
    return {key: float(value) if isinstance(value, Decimal) else value
            for key, value in calculate_courier_cod_fee_decimal(cod_amount, company).items()}


def calculate_courier_charges(cost_per_order: Any, vat_percent: Any, *,
                              cod_amount: Any | None, contract: dict) -> dict:
    """P02 monetary facts, in Decimal, from an explicitly dated contract.

    cost_per_order is the CONTRACT QUOTE; shipping_cost_vat_inclusive records
    whether that quote is gross or net. The returned net/tax/gross always have
    the same meaning. COD and shipping tax policies are independent.
    """
    shipping = split_tax_amount(cost_per_order, vat_percent,
                                 inclusive=contract.get("shipping_cost_vat_inclusive"))
    if cod_amount is not None and not isinstance(contract.get("commission_vat_inclusive"), bool):
        raise ValueError("courier_commission_tax_policy_required")
    cod = (calculate_courier_cod_fee_decimal(cod_amount, {**contract, "cod_tiers_required": True})
           if cod_amount is not None else None)
    commission = cod["fee_net"] if cod else Decimal(0)
    commission_vat = cod["fee_vat"] if cod else Decimal(0)
    commission_gross = cod["fee_total"] if cod else Decimal(0)
    return {"shipping_net": shipping["net"], "shipping_vat": shipping["vat"],
            "shipping_gross": shipping["gross"], "cod_commission": commission,
            "cod_commission_vat": commission_vat, "cod_commission_gross": commission_gross,
            "payable_total": shipping["gross"] + commission_gross,
            "cod_gross": money_decimal(cod_amount) if cod_amount is not None else Decimal(0),
            "needs_review": bool(cod and cod["needs_review"]),
            "matched_rule": cod["matched_rule"] if cod else None,
            "shipping_cost_vat_inclusive": contract["shipping_cost_vat_inclusive"],
            "commission_vat_inclusive": contract.get("commission_vat_inclusive"),
            "calculator_source": "courier_cod_fee_rules"}


__all__ = [
    "CourierCodFeeTier",
    "calculate_courier_charges",
    "calculate_courier_cod_fee_decimal",
    "split_tax_amount",
    "money_decimal",
    "calculate_courier_cod_fee",
    "validate_courier_cod_fee_tiers",
]
