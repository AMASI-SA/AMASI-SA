"""Non-financial setup schema. Missing facts never acquire zero defaults."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from accounting_currency import validate_currency_code
from accounting_financial_accounts import OpeningLine, _utc_iso
from accounting_module_contract import EVIDENCE_SECTIONS

SCHEMA_VERSION = 1
TARGET_CUTOVER = "2026-10-01T00:00:00+03:00"
SECTION_IDS = tuple(row["id"] for row in EVIDENCE_SECTIONS)
SECTION_STATES = ("not_started", "incomplete", "complete", "not_applicable")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SessionCreate(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=160)
    cutover_at: datetime
    cutover_timezone: Literal["Asia/Riyadh"]

    @model_validator(mode="after")
    def aware_cutover(self):
        _utc_iso(self.cutover_at, field="opening_cutover", riyadh=True)
        return self


class SessionAction(StrictModel):
    version: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=160)
    note: str = Field(min_length=3, max_length=1000)


class CutoverSave(SessionCreate):
    version: int = Field(ge=1, strict=True)
    cutover_evidence_file_id: str | None = Field(default=None, min_length=1, max_length=160)


class ProviderBinding(StrictModel):
    provider: Literal["salla", "tabby", "tamara", "emkan"]
    bank_account_id: str = Field(min_length=1, max_length=160, strict=True)
    evidence_file_id: str = Field(min_length=1, max_length=160, strict=True)


class InventoryValuation(StrictModel):
    total_sar: str = Field(min_length=1, max_length=80, strict=True)
    evidence_file_id: str = Field(min_length=1, max_length=160, strict=True)
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$", strict=True)
    account_totals: dict[StrictStr, StrictStr] = Field(min_length=1, max_length=1000)


class SectionData(StrictModel):
    # Partial typed line facts are allowed only during data entry. The complete
    # line/FX/identity/evidence contract is enforced by preview and review.
    lines: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    provider_bindings: list[ProviderBinding] = Field(default_factory=list, max_length=4)
    inventory_valuation: InventoryValuation | None = None

    @model_validator(mode="after")
    def known_fields_only(self):
        fields = set(OpeningLine.model_fields)
        for line in self.lines:
            if "original_currency" in line:
                validate_currency_code(line["original_currency"])
            if set(line) - fields:
                raise ValueError("onboarding_payload_invalid")
            if any(isinstance(value, (dict, list)) or isinstance(value, str) and len(value) > 1000
                   for value in line.values()):
                raise ValueError("onboarding_payload_invalid")
        return self


class SectionSave(StrictModel):
    version: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=160)
    status: Literal["not_started", "incomplete", "complete", "not_applicable"]
    reason: str = Field(default="", max_length=1000)
    evidence_file_id: str | None = Field(default=None, min_length=1, max_length=160)
    data: SectionData = Field(default_factory=SectionData)

    @model_validator(mode="after")
    def explicit_completion(self):
        if self.status in {"complete", "not_applicable"} and not self.evidence_file_id:
            raise ValueError("opening_evidence_section_file_required")
        if self.status == "not_applicable" and (
            not self.reason or self.data.lines or self.data.provider_bindings
            or self.data.inventory_valuation is not None
        ):
            raise ValueError("onboarding_not_applicable_conflict")
        if self.status == "complete":
            if not self.data.lines:
                raise ValueError("onboarding_explicit_balance_required")
            for line in self.data.lines:
                OpeningLine.model_validate(line)
        return self


class InventoryDraftAllocation(StrictModel):
    location_id: str = Field(default="", max_length=160, strict=True)
    quantity: str = Field(default="", max_length=80, strict=True)
    scanned_location_barcode: str = Field(default="", max_length=160, strict=True)


class InventoryDraftRow(StrictModel):
    # Entry text deliberately permits incomplete/invalid numeric drafts. This
    # metadata is never evidence of valuation or approved physical inventory.
    item_type: Literal["PRODUCT", "STOCK_COMPONENT"] = "PRODUCT"
    product_v2_id: str = Field(default="", max_length=160, strict=True)
    product_id: str = Field(default="", max_length=160, strict=True)
    variant_id: str = Field(default="", max_length=160, strict=True)
    resource_id: str = Field(default="", max_length=160, strict=True)
    category_id: str = Field(default="", max_length=160, strict=True)
    inventory_account_id: str = Field(default="", max_length=160, strict=True)
    opening_quantity: str = Field(default="", max_length=80, strict=True)
    opening_unit_cost: str = Field(default="", max_length=80, strict=True)
    opening_total_cost: str = Field(default="", max_length=80, strict=True)
    allocations: list[InventoryDraftAllocation] = Field(default_factory=list, max_length=100)


class InventoryDraft(StrictModel):
    rows: list[InventoryDraftRow] = Field(default_factory=list, max_length=1000)
    financial_lines: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def financial_fields_only(self):
        SectionData(lines=self.financial_lines)
        return self


class InventoryDraftSave(StrictModel):
    version: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=160)
    draft: InventoryDraft
