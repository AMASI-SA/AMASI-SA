"""P02 immutable dated terms; no database, journal or approval endpoint.

This internal layer uses the existing courier_cod_fee_rules calculator.
payment_mode describes payment TO the carrier, not customer COD/prepayment.
Validity intervals are [effective_from, effective_to).
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from courier_cod_fee_rules import (
    CourierCodFeeTier, calculate_courier_charges, validate_courier_cod_fee_tiers,
)


class ShippingContractError(ValueError):
    pass


def _instant(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        raise ShippingContractError("shipping_contract_date_invalid") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ShippingContractError("shipping_contract_timezone_required")
    return result.astimezone(timezone.utc)


def _halalas(value):
    if value is None:
        return value
    try:
        valid = value.is_finite() and value >= 0 and value == value.quantize(Decimal("0.01"))
    except InvalidOperation:
        valid = False
    if not valid:
        raise ShippingContractError("shipping_contract_exact_halalas_required")
    return value


class ShippingContractTier(CourierCodFeeTier):
    """Keep shared range validation while retaining Decimal through its adapter."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    min_amount: Decimal = Field(ge=0)
    max_amount: Decimal | None = Field(default=None, ge=0)
    min_inclusive: StrictBool
    max_inclusive: StrictBool
    commission_percent: Decimal = Field(ge=0, le=1)
    fixed_fee: Decimal = Field(default=Decimal("0"), ge=0)
    vat_percent: Decimal = Field(ge=0, le=100)
    vat_included: StrictBool

    @field_validator("min_amount", "max_amount", "fixed_fee")
    @classmethod
    def money(cls, value):
        return _halalas(value)


class ShippingContractInput(BaseModel):
    """Terms only: tenant/approval identities MUST come from the future service."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    courier_id: str = Field(min_length=1, max_length=120)
    payment_mode: Literal["prepaid", "postpaid"]
    shipping_cost: Decimal = Field(ge=0)
    shipping_cost_vat_inclusive: StrictBool
    shipping_vat_percent: Decimal = Field(ge=0, le=100)
    cod_fee_tiers: tuple[ShippingContractTier, ...]
    commission_vat_inclusive: StrictBool
    commission_vat_percent: Decimal = Field(ge=0, le=100)
    effective_from: datetime
    effective_to: datetime | None = None
    evidence_ref: str = Field(min_length=3, max_length=500)
    source_kind: Literal["contract", "invoice", "statement", "owner_confirmation", "legacy_copy"]

    @model_validator(mode="before")
    @classmethod
    def explicit_contract_tax(cls, raw):
        if not isinstance(raw, Mapping):
            return raw
        data = dict(raw)
        if isinstance(data.get("cod_fee_tiers"), (list, tuple)):
            rows = []
            for tier in data["cod_fee_tiers"]:
                if isinstance(tier, CourierCodFeeTier):
                    row = tier.model_dump(exclude_unset=True)
                elif isinstance(tier, Mapping):
                    row = dict(tier)
                else:
                    rows.append(tier)
                    continue
                for tier_key, contract_key in (
                    ("vat_percent", "commission_vat_percent"),
                    ("vat_included", "commission_vat_inclusive"),
                ):
                    if tier_key not in row and contract_key in data:
                        row[tier_key] = data[contract_key]
                rows.append(row)
            data["cod_fee_tiers"] = rows
        return data

    @field_validator("courier_id", "evidence_ref")
    @classmethod
    def text(cls, value, info):
        result = value.strip()
        if len(result) < (3 if info.field_name == "evidence_ref" else 1):
            raise ShippingContractError("shipping_contract_text_required")
        return result

    @field_validator("shipping_cost")
    @classmethod
    def money(cls, value):
        return _halalas(value)

    @field_validator("effective_from", "effective_to")
    @classmethod
    def date(cls, value):
        return _instant(value) if value is not None else None

    @model_validator(mode="after")
    def consistency(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ShippingContractError("shipping_contract_interval_invalid")
        for tier in self.cod_fee_tiers:
            if tier.vat_percent != self.commission_vat_percent:
                raise ShippingContractError("shipping_contract_tier_vat_conflict")
            if tier.vat_included is not self.commission_vat_inclusive:
                raise ShippingContractError("shipping_contract_tier_inclusion_conflict")
            if tier.evidence_ref is not None and tier.evidence_ref != self.evidence_ref:
                raise ShippingContractError("shipping_contract_tier_evidence_conflict")
            if tier.effective_from is not None and _instant(tier.effective_from) != self.effective_from:
                raise ShippingContractError("shipping_contract_tier_date_conflict")
        validate_courier_cod_fee_tiers(list(self.cod_fee_tiers))
        return self


class ShippingContractVersion(ShippingContractInput):
    """Internal persisted shape, NOT a public approval request model."""
    id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1, strict=True)
    verification_status: Literal["unverified", "approved"]
    approved_by: str | None = Field(default=None, min_length=1, max_length=200)
    approved_at: datetime | None = None

    @field_validator("id", "user_id", "approved_by")
    @classmethod
    def identity(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ShippingContractError("shipping_contract_identity_required")
        return value.strip()

    @field_validator("approved_at")
    @classmethod
    def approval_date(cls, value):
        return _instant(value) if value is not None else None

    @model_validator(mode="after")
    def approval(self):
        if self.verification_status == "approved":
            if self.source_kind == "legacy_copy":
                raise ShippingContractError("legacy_copy_requires_confirmed_new_version")
            if self.approved_by is None or self.approved_at is None:
                raise ShippingContractError("shipping_contract_approval_required")
        elif self.approved_by is not None or self.approved_at is not None:
            raise ShippingContractError("unverified_contract_cannot_claim_approval")
        return self


def _version(value):
    # Revalidate models as well: model_copy(update=...) is not a validation step.
    raw = value.model_dump() if isinstance(value, ShippingContractVersion) else value
    return ShippingContractVersion.model_validate(raw)


def _effective(version, when):
    return version.effective_from <= when and (
        version.effective_to is None or when < version.effective_to
    )


def require_postable_contract(value, *, owner, courier_id, accounting_at):
    version = _version(value)
    if version.user_id != owner or version.courier_id != courier_id:
        raise ShippingContractError("shipping_contract_scope_mismatch")
    if version.verification_status != "approved" or version.source_kind == "legacy_copy":
        raise ShippingContractError("shipping_contract_not_approved")
    if not _effective(version, _instant(accounting_at)):
        raise ShippingContractError("shipping_contract_not_effective")
    return version


def select_shipping_contract(versions, *, owner, courier_id, accounting_at):
    when = _instant(accounting_at)
    selected = []
    for raw in versions:
        scope = raw.model_dump() if isinstance(raw, ShippingContractVersion) else raw
        if not isinstance(scope, Mapping):
            raise ShippingContractError("shipping_contract_record_invalid")
        if scope.get("user_id") != owner or scope.get("courier_id") != courier_id:
            continue
        version = _version(raw)
        if _effective(version, when) and version.verification_status == "approved":
            selected.append(require_postable_contract(
                version, owner=owner, courier_id=courier_id, accounting_at=when
            ))
    if not selected:
        raise ShippingContractError("shipping_contract_approved_version_missing")
    if len(selected) != 1:
        raise ShippingContractError("shipping_contract_effective_interval_ambiguous")
    return selected[0]


def quote_shipping_contract(value, *, owner, courier_id, accounting_at, cod_amount):
    version = require_postable_contract(
        value, owner=owner, courier_id=courier_id, accounting_at=accounting_at
    )
    calculation = calculate_courier_charges(
        version.shipping_cost, version.shipping_vat_percent, cod_amount=cod_amount,
        contract={
            "shipping_cost_vat_inclusive": version.shipping_cost_vat_inclusive,
            "commission_vat_inclusive": version.commission_vat_inclusive,
            "cod_fee_tiers": list(version.cod_fee_tiers),
        },
    )
    return {
        "state": "needs_review" if calculation["needs_review"] else "eligible",
        "reasons": ["cod_amount_not_covered_by_contract"] if calculation["needs_review"] else [],
        "contract_version_id": version.id,
        "contract_snapshot": version.model_dump(mode="json"),
        "calculation": calculation,
    }


def require_shipping_contract_charges(*args, **kwargs):
    """Future writer boundary: an uncovered range must never be posted as zero."""
    proposal = quote_shipping_contract(*args, **kwargs)
    if proposal["state"] != "eligible":
        raise ShippingContractError("cod_amount_not_covered_by_contract_needs_review")
    return proposal
