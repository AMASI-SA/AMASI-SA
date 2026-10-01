"""Explicit Track F contracts. No legacy identities, defaults or netting."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

SETUP = "mz2_shipping_setup_v2"
EVIDENCE = "mz2_courier_delivery_evidence_v1"
EVENTS = "mz2_shipping_native_events_v2"
INBOX = "mz2_shipping_inbox_v2"
SOURCE = "mz2_shipping_native_v1"
MAX_ROWS = 1000


def fail(code, status=409, **detail):
    raise HTTPException(status, detail={"code": code, **detail})


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        fail("shipping_timestamp_with_timezone_required")


def money(value, *, positive=False):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result != result.quantize(Decimal("0.01")):
            raise ValueError()
        if positive and result <= 0:
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        fail("shipping_exact_amount_required")


def amount(value):
    return format(value, ".2f")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class SetupInput(Input):
    request_id: str = Field(min_length=8, max_length=120)
    version: int = Field(ge=0, strict=True)
    confirmed: StrictBool
    reason: str = Field(min_length=3, max_length=500)


class CourierInput(SetupInput):
    courier_key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    name: str = Field(min_length=1, max_length=160)
    # Exact source keys approved by the owner. Never normalized/fuzzy matched.
    salla_carrier_keys: list[str] = Field(min_length=1, max_length=20)

    @field_validator("salla_carrier_keys")
    @classmethod
    def exact_keys(cls, values):
        if any(not x or x != x.strip() for x in values) or len(set(values)) != len(values):
            raise ValueError("shipping_source_identity_invalid")
        return values


class Party(Input):
    party_type: Literal["courier", "store_driver"]
    party_id: str = Field(min_length=1, max_length=120)


class RateInput(SetupInput, Party):
    context: str = Field(min_length=1, max_length=120)
    effective_from: datetime
    effective_to: datetime | None = None
    currency: Literal["SAR"]
    delivery_fee: Decimal = Field(ge=0)
    cod_fixed_fee: Decimal = Field(ge=0)
    cod_percent: Decimal = Field(ge=0, le=100)
    vat_percent: Decimal = Field(ge=0, le=100)
    vat_included: StrictBool
    vat_treatment: Literal["gross_expense_no_input_vat", "net_plus_input_vat"]
    contract_reference: str = Field(min_length=3, max_length=500)

    @field_validator("delivery_fee", "cod_fixed_fee")
    @classmethod
    def exact_money(cls, value):
        return money(value)

    @model_validator(mode="after")
    def dates(self):
        start = instant(self.effective_from)
        if self.effective_to is not None and instant(self.effective_to) <= start:
            raise ValueError("shipping_rate_interval_invalid")
        return self


class BindingInput(SetupInput, Party):
    financial_account_id: str = Field(min_length=1, max_length=160)


class RecognitionInput(Input):
    order_number: str = Field(min_length=1, max_length=160)
    # No amount, status, courier or delivery proof may be supplied by the caller.


class DriverRecognitionInput(Input):
    assignment_id: str = Field(min_length=1, max_length=160)


class SettlementInput(Party):
    request_id: str = Field(min_length=8, max_length=120)
    movement_id: str = Field(min_length=1, max_length=160)
    action: Literal["receive_cod", "pay_fee"]
    reason: str = Field(min_length=3, max_length=500)


def subaccounts(kind):
    return "cod_receivable", "payable" if kind == "courier" else "delivery_fee_payable"


def select_rate(setup, kind, identity, context, at):
    candidates = [r for r in setup.get("contracts", [])
                  if (r["party_type"], r["party_id"], r["context"]) == (kind, identity, context)
                  and instant(r["effective_from"]) <= instant(at)
                  and (r["effective_to"] is None or instant(at) < instant(r["effective_to"]))]
    if not candidates:
        fail("shipping_rate_policy_missing")
    if len(candidates) != 1:
        fail("shipping_rate_policy_overlap")
    row = candidates[0]
    if not row.get("confirmed_by") or not row.get("confirmed_at") or row.get("status") != "approved":
        fail("shipping_rate_policy_not_approved")
    return row


def quote(rate, cod):
    value = money(rate["delivery_fee"]) + (money(rate["cod_fixed_fee"]) + cod * Decimal(rate["cod_percent"]) / 100 if cod > 0 else Decimal(0))
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    percent = Decimal(rate["vat_percent"]) / 100
    net = (value / (1 + percent)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if rate["vat_included"] else value
    vat = value - net if rate["vat_included"] else (net * percent).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    gross = net + vat
    return {"gross": amount(gross), "expense": amount(gross if rate["vat_treatment"] == "gross_expense_no_input_vat" else net),
            "input_vat": amount(Decimal(0) if rate["vat_treatment"] == "gross_expense_no_input_vat" else vat)}
