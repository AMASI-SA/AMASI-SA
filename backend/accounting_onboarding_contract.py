"""Non-financial setup schema. Missing facts never acquire zero defaults."""
from datetime import datetime
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from accounting_financial_accounts import OpeningLine, _utc_iso
from accounting_module_contract import EVIDENCE_SECTIONS

SCHEMA_VERSION = 1
TARGET_CUTOVER = "2026-10-01T00:00:00+03:00"
SECTION_IDS = tuple(row["id"] for row in EVIDENCE_SECTIONS)
SECTION_STATES = ("not_started", "incomplete", "complete", "not_applicable")
SETUP_STAGE_SECTIONS = {
    "cutover": None, "banks": "banks_cash", "providers": "providers",
    "employees": "payroll_obligations", "suppliers": "suppliers",
    "external_persons": "suppliers", "courier_contracts": "couriers_cod",
    "courier_balances": "couriers_cod", "drivers": "couriers_cod",
    "inventory": "inventory", "payment_fees": None, "advertising": "providers",
    "prepaid": "equity", "obligations": "equity", "review": None, "approval": None,
}


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


class SetupDraft(StrictModel):
    """Non-authoritative entry data, never an opening payload or approval.

    Partial values must survive navigation. Only the separately compiled typed
    financial sections can reach preview/review. Bound JSON depth/size and keys
    before storing a full snapshot inside the single CAS-protected session.
    """
    schema_version: Literal[1] = 1
    active_stage: str = "cutover"
    sections: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=16)
    couriers: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=1000)
    section_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=8)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def bounded_setup(self):
        if self.active_stage not in SETUP_STAGE_SECTIONS or set(self.sections) - set(SETUP_STAGE_SECTIONS):
            raise ValueError("onboarding_setup_stage_invalid")
        if set(self.section_metadata) - {*SECTION_IDS, "cutover"}:
            raise ValueError("onboarding_setup_stage_invalid")
        def check(value, depth=0):
            if depth > 14:
                raise ValueError("onboarding_setup_limit")
            if isinstance(value, dict):
                if len(value) > 1000:
                    raise ValueError("onboarding_setup_limit")
                for key, item in value.items():
                    if not isinstance(key, str) or len(key) > 160 or any(c in key for c in (".", "$", "\x00")):
                        raise ValueError("onboarding_setup_key_invalid")
                    check(item, depth + 1)
            elif isinstance(value, list):
                if len(value) > 2000:
                    raise ValueError("onboarding_setup_limit")
                for item in value:
                    check(item, depth + 1)
            elif isinstance(value, str):
                if len(value) > 4000:
                    raise ValueError("onboarding_setup_limit")
            elif value is not None and not isinstance(value, (bool, int, float)):
                raise ValueError("onboarding_setup_invalid")
        data = self.model_dump(mode="json")
        check(data)
        if len(json.dumps(data, allow_nan=False).encode("utf-8")) > 2_000_000:
            raise ValueError("onboarding_setup_limit")
        return self


class SetupDraftSave(StrictModel):
    version: int = Field(ge=1, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=160)
    setup_draft: SetupDraft


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
