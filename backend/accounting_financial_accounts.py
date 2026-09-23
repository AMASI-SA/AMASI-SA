"""Financial accounts and the reviewed, evidence-locked V2 opening workflow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from accounting_atomic import atomic_owner
from accounting_ledger_v2 import (
    AccountingLedgerV2Error,
    assert_no_mz2_rows_in_legacy_ledger,
    post_opening_journal_v2,
    reverse_journal_v2,
)
from accounting_module_contract import (
    EVIDENCE_SECTIONS,
    OPERATION_ID,
    accounting_owner_id,
    require_accounting_permission,
)
from accounting_periods import assert_open_journal_periods
from accounting_source_files import preserve_original
from accounting_write_control import fresh_actor
from accounting_writer_transition import (
    advance_transition,
    assert_writer_allowed,
    transition_state,
)


PERMISSIONS = {
    "accounts_view": "accounting.financial_accounts.view",
    "accounts_manage": "accounting.financial_accounts.manage",
    "opening_view": "accounting.opening_balances.view",
    "drafts_manage": "accounting.opening_balances.drafts.manage",
    "review": "accounting.opening_balances.review",
    "post": "accounting.opening_balances.post",
    "reverse": "accounting.journals.reverse",
    "transition": "accounting.ledger_transition.manage",
}
ACCOUNT_TYPES = ("bank", "cash", "ad_prepaid_wallet", "ad_payable", "overdraft")
OPENING_CATEGORY_CATALOG: dict[str, dict[str, str]] = {
    "provider_receivable": {
        "label": "ذمة مزود دفع لنا", "entity_type": "payment_gateway",
        "sub_account": "receivable", "side": "debit", "section": "providers",
    },
    "courier_cod_receivable": {
        "label": "COD لدى شركة شحن", "entity_type": "courier",
        "sub_account": "cod_receivable", "side": "debit", "section": "couriers_cod",
    },
    "courier_payable": {
        "label": "مستحق لشركة شحن", "entity_type": "courier",
        "sub_account": "payable", "side": "credit", "section": "couriers_cod",
    },
    "store_driver_cod_receivable": {
        "label": "COD لدى موصل المتجر", "entity_type": "store_driver",
        "sub_account": "cod_receivable", "side": "debit", "section": "couriers_cod",
    },
    "store_driver_fee_payable": {
        "label": "أجرة مستحقة لموصل المتجر", "entity_type": "store_driver",
        "sub_account": "delivery_fee_payable", "side": "credit", "section": "couriers_cod",
    },
    "employee_advance": {
        "label": "سلفة موظف", "entity_type": "employee", "sub_account": "advance",
        "side": "debit", "section": "payroll_obligations",
    },
    "employee_custody": {
        "label": "عهدة موظف", "entity_type": "employee", "sub_account": "custody",
        "side": "debit", "section": "payroll_obligations",
    },
    "employee_salary_payable": {
        "label": "راتب مستحق", "entity_type": "employee", "sub_account": "salary_payable",
        "side": "credit", "section": "payroll_obligations",
    },
    "supplier_payable": {
        "label": "مستحق لمورد", "entity_type": "supplier", "sub_account": "payable",
        "side": "credit", "section": "suppliers",
    },
    "customer_receivable": {
        "label": "مستحق لنا على عميل", "entity_type": "external_person",
        "sub_account": "receivable", "side": "debit", "section": "suppliers",
    },
    "inventory_asset": {
        "label": "المخزون بالتكلفة", "entity_type": "asset", "sub_account": "inventory",
        "side": "debit", "section": "inventory",
    },
    "sales_vat_payable": {
        "label": "ضريبة مبيعات مستحقة", "entity_type": "tax",
        "sub_account": "sales_vat_payable", "side": "credit", "section": "equity",
    },
    "input_vat": {
        "label": "ضريبة مدخلات", "entity_type": "tax", "sub_account": "input_vat",
        "side": "debit", "section": "equity",
    },
    "other_receivable": {
        "label": "أصل أو ذمة مدينة أخرى", "entity_type": "asset",
        "sub_account": "other_receivable", "side": "debit", "section": "equity",
    },
    "other_payable": {
        "label": "التزام أو ذمة دائنة أخرى", "entity_type": "liability",
        "sub_account": "other_payable", "side": "credit", "section": "equity",
    },
}
FINANCIAL_ACCOUNT_RULES: dict[str, dict[str, str]] = {
    "bank": {"entity_type": "bank", "sub_account": "main", "side": "debit", "section": "banks_cash"},
    "cash": {"entity_type": "bank", "sub_account": "main", "side": "debit", "section": "banks_cash"},
    "ad_prepaid_wallet": {"entity_type": "ad_account", "sub_account": "balance", "side": "debit", "section": "providers"},
    "ad_payable": {"entity_type": "ad_account", "sub_account": "debt", "side": "credit", "section": "providers"},
    "overdraft": {"entity_type": "liability", "sub_account": "bank_overdraft", "side": "credit", "section": "banks_cash"},
}
OPENING_CATEGORIES = ("financial_account", *OPENING_CATEGORY_CATALOG)
EVIDENCE_SECTION_IDS = frozenset(row["id"] for row in EVIDENCE_SECTIONS)
OPENING_PURPOSES = frozenset({"opening_balance", "cutover", "fx_rate", "opening_reversal_reason"})
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
MONEY = Decimal("0.01")
SAR_PARITY = Decimal("1")
RIYADH_OFFSET = timedelta(hours=3)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner(actor: dict[str, Any]) -> str:
    value = accounting_owner_id(actor)
    if not value:
        raise HTTPException(403, "accounting_owner_required")
    return value


def _require(actor: dict[str, Any], key: str) -> None:
    require_accounting_permission(actor, PERMISSIONS[key])


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_id"}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _http_from_ledger(error: AccountingLedgerV2Error) -> HTTPException:
    status = 503 if error.code == "accounting_v2_atomic_transaction_required" else 409
    return HTTPException(status, detail={"code": error.code, "message": error.message, **error.details})


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    account_type: Literal["bank", "cash", "ad_prepaid_wallet", "ad_payable", "overdraft"]
    currency: str = Field(default="SAR", pattern=r"^[A-Za-z]{3}$")
    external_ref: str | None = Field(default=None, max_length=160)
    idempotency_key: str = Field(min_length=8, max_length=160)


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=2, max_length=120)
    external_ref: str | None = Field(default=None, max_length=160)


class AccountArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


def _decimal_string(value: Any, *, field: str, allow_zero: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field}_decimal_string_required")
    try:
        parsed = Decimal(value.strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field}_invalid") from error
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        raise ValueError(f"{field}_invalid")
    return parsed


def _utc_iso(value: datetime, *, field: str, riyadh: bool = False) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(422, detail={"code": f"{field}_timezone_required"})
    if riyadh and value.utcoffset() != RIYADH_OFFSET:
        raise HTTPException(422, detail={"code": "opening_cutover_must_use_asia_riyadh"})
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class OpeningLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal[
        "financial_account", "provider_receivable", "courier_cod_receivable",
        "courier_payable", "store_driver_cod_receivable", "store_driver_fee_payable",
        "employee_advance", "employee_custody", "employee_salary_payable",
        "supplier_payable", "customer_receivable", "inventory_asset",
        "sales_vat_payable", "input_vat", "other_receivable", "other_payable",
    ]
    financial_account_id: str | None = Field(default=None, max_length=160)
    entity_id: str | None = Field(default=None, max_length=160)
    label: str = Field(default="", max_length=200)
    meaning: Literal["available_to_us", "owed_by_us", "zero"]
    original_amount: Decimal
    original_currency: str = Field(default="SAR", pattern=r"^[A-Za-z]{3}$")
    fx_rate_to_sar: Decimal = Decimal("1")
    fx_at: datetime | None = None
    fx_source: str | None = Field(default=None, max_length=300)
    fx_evidence_file_id: str | None = Field(default=None, max_length=160)
    evidence_file_id: str = Field(min_length=1, max_length=160)

    @field_validator("original_amount", mode="before")
    @classmethod
    def amount_is_decimal_string(cls, value: Any) -> Decimal:
        return _decimal_string(value, field="original_amount", allow_zero=True)

    @field_validator("fx_rate_to_sar", mode="before")
    @classmethod
    def fx_is_decimal_string(cls, value: Any) -> Decimal:
        return _decimal_string(value, field="fx_rate_to_sar")

    @model_validator(mode="after")
    def validate_contract(self):
        self.original_currency = self.original_currency.upper()
        if self.category == "financial_account":
            if not str(self.financial_account_id or "").strip() or self.entity_id is not None:
                raise ValueError("financial_account_identity_required")
        elif not str(self.entity_id or "").strip() or self.financial_account_id is not None:
            raise ValueError("opening_entity_identity_required")
        if self.meaning == "zero" and self.original_amount != 0:
            raise ValueError("opening_zero_amount_must_be_zero")
        if self.meaning != "zero" and self.original_amount <= 0:
            raise ValueError("opening_nonzero_amount_required")
        if self.original_currency == "SAR":
            if self.fx_rate_to_sar != SAR_PARITY:
                raise ValueError("opening_sar_fx_rate_must_equal_one")
        else:
            if self.fx_at is None or self.fx_at.tzinfo is None or self.fx_at.utcoffset() is None:
                raise ValueError("opening_fx_timestamp_required")
            if not str(self.fx_source or "").strip() and not str(self.fx_evidence_file_id or "").strip():
                raise ValueError("opening_fx_source_or_evidence_required")
        return self


class OpeningDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=160)
    cutover_at: datetime
    cutover_timezone: Literal["Asia/Riyadh"] = "Asia/Riyadh"
    cutover_evidence_file_id: str = Field(min_length=1, max_length=160)
    section_evidence_file_ids: dict[str, str]
    lines: list[OpeningLine] = Field(min_length=1, max_length=1_000)
    replaces_draft_id: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def all_evidence_sections_are_explicit(self):
        if set(self.section_evidence_file_ids) != EVIDENCE_SECTION_IDS:
            raise ValueError("opening_evidence_sections_incomplete")
        if any(not str(value or "").strip() for value in self.section_evidence_file_ids.values()):
            raise ValueError("opening_evidence_section_file_required")
        return self


class OpeningAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=160)
    note: str = Field(min_length=3, max_length=1_000)
    effective_at: datetime | None = None


class OpeningReverseAction(OpeningAction):
    evidence_file_id: str = Field(min_length=1, max_length=160)


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["transition_blocked", "v2_active"]
    expected_revision: int = Field(ge=0)
    activation_ref: str = Field(default="", max_length=160)


def _draft_content(payload: OpeningDraftCreate) -> dict[str, Any]:
    return {
        "cutover_at": _utc_iso(payload.cutover_at, field="opening_cutover", riyadh=True),
        "cutover_timezone": payload.cutover_timezone,
        "cutover_evidence_file_id": payload.cutover_evidence_file_id,
        "section_evidence_file_ids": payload.section_evidence_file_ids,
        "lines": [line.model_dump(mode="json") for line in payload.lines],
        "replaces_draft_id": payload.replaces_draft_id,
    }


def _money(value: Decimal) -> str:
    return format(value.quantize(MONEY, rounding=ROUND_HALF_UP), "f")


def _meaning_for_side(side: str) -> str:
    return "available_to_us" if side == "debit" else "owed_by_us"


def _account_rule(account: dict[str, Any]) -> dict[str, str]:
    rule = FINANCIAL_ACCOUNT_RULES.get(str(account.get("account_type") or ""))
    if not rule:
        raise HTTPException(409, detail={"code": "opening_financial_account_type_invalid"})
    return rule


async def _compile_opening(
    db: Any,
    *,
    owner: str,
    payload: OpeningDraftCreate,
) -> dict[str, Any]:
    content = _draft_content(payload)
    account_ids = sorted({
        str(line.financial_account_id)
        for line in payload.lines
        if line.category == "financial_account"
    })
    account_rows = await db.mz2_financial_accounts.find({
        "user_id": owner, "id": {"$in": account_ids}, "status": "active",
    }).to_list(len(account_ids) + 1) if account_ids else []
    accounts = {str(row.get("id")): row for row in account_rows}
    if set(accounts) != set(account_ids):
        raise HTTPException(409, detail={"code": "opening_financial_account_missing_or_inactive"})

    compiled: list[dict[str, Any]] = []
    all_lines: list[dict[str, Any]] = []
    zero_accounts: list[dict[str, Any]] = []
    evidence_requirements: list[dict[str, Any]] = [{
        "source_file_id": payload.cutover_evidence_file_id,
        "purpose": "cutover",
        "section_id": None,
    }, *[{
        "source_file_id": payload.section_evidence_file_ids[section_id],
        "purpose": "opening_balance", "section_id": section_id,
    } for section_id in sorted(EVIDENCE_SECTION_IDS)]]
    covered: set[tuple[str, str, str]] = set()
    for index, line in enumerate(payload.lines, start=1):
        account_snapshot = None
        if line.category == "financial_account":
            account = accounts[str(line.financial_account_id)]
            rule = _account_rule(account)
            currency = str(account.get("currency") or "").upper()
            if currency != line.original_currency:
                raise HTTPException(409, detail={
                    "code": "opening_account_currency_mismatch",
                    "financial_account_id": account.get("id"),
                })
            account_snapshot = {
                "id": str(account["id"]), "name": str(account.get("name") or ""),
                "account_type": str(account["account_type"]), "currency": currency,
                "status": "active", "version": int(account.get("version") or 1),
                "external_ref": account.get("external_ref"),
            }
            entity_id = str(account["id"])
            label = line.label.strip() or str(account.get("name") or rule["entity_type"])
        else:
            rule = OPENING_CATEGORY_CATALOG[line.category]
            entity_id = str(line.entity_id).strip()
            label = line.label.strip() or rule["label"]
        key = (rule["entity_type"], entity_id, rule["sub_account"])
        if key in covered:
            raise HTTPException(409, detail={"code": "opening_duplicate_account", "account": "/".join(key)})
        covered.add(key)
        expected_meaning = _meaning_for_side(rule["side"])
        if line.evidence_file_id != payload.section_evidence_file_ids[rule["section"]]:
            raise HTTPException(409, detail={"code": "opening_line_evidence_section_mismatch"})
        if line.meaning != "zero" and line.meaning != expected_meaning:
            raise HTTPException(409, detail={
                "code": "opening_accounting_meaning_mismatch",
                "expected": expected_meaning,
            })
        if account_snapshot and account_snapshot["account_type"] == "cash" and line.meaning == "owed_by_us":
            raise HTTPException(409, detail={"code": "opening_cash_negative_forbidden"})

        fx_at = (
            content["cutover_at"]
            if line.original_currency == "SAR"
            else _utc_iso(line.fx_at, field="opening_fx_at")
        )
        fx_snapshot = {
            "rate_to_sar": format(line.fx_rate_to_sar, "f"),
            "fx_at": fx_at,
            "source": "sar_parity" if line.original_currency == "SAR" else (line.fx_source or None),
            "evidence_file_id": line.fx_evidence_file_id,
        }
        sar_decimal = (line.original_amount * line.fx_rate_to_sar).quantize(MONEY, rounding=ROUND_HALF_UP)
        if line.meaning != "zero" and sar_decimal <= 0:
            raise HTTPException(409, detail={"code": "opening_amount_rounds_to_zero"})
        canonical = {
            "line_no": index,
            "category": line.category,
            "label": label,
            "financial_account_id": line.financial_account_id,
            "entity_type": rule["entity_type"], "entity_id": entity_id,
            "sub_account": rule["sub_account"], "side": rule["side"],
            "meaning": line.meaning,
            "original_amount": format(line.original_amount, "f"),
            "original_currency": line.original_currency,
            "sar_amount": _money(sar_decimal),
            "ledger_currency": "SAR",
            "fx_snapshot": fx_snapshot,
            "account_snapshot": account_snapshot,
            "evidence_file_id": line.evidence_file_id,
            "evidence_section_id": rule["section"],
        }
        all_lines.append(canonical)
        evidence_requirements.append({
            "source_file_id": line.evidence_file_id,
            "purpose": "opening_balance",
            "section_id": rule["section"],
        })
        if line.fx_evidence_file_id:
            evidence_requirements.append({
                "source_file_id": line.fx_evidence_file_id,
                "purpose": "fx_rate",
                "section_id": rule["section"],
            })
        if line.meaning == "zero":
            zero_accounts.append({
                "entity_type": rule["entity_type"], "entity_id": entity_id,
                "sub_account": rule["sub_account"], "account_snapshot": account_snapshot,
                "evidence_file_id": line.evidence_file_id,
            })
        else:
            compiled.append(canonical)

    debit = sum((Decimal(row["sar_amount"]) for row in compiled if row["side"] == "debit"), Decimal())
    credit = sum((Decimal(row["sar_amount"]) for row in compiled if row["side"] == "credit"), Decimal())
    difference = (debit - credit).quantize(MONEY)
    entries = list(compiled)
    if difference:
        equity_evidence_file_id = payload.section_evidence_file_ids["equity"]
        entries.append({
            "line_no": len(entries) + 1, "category": "opening_equity_counterpart",
            "label": "حقوق الملكية / موازنة الافتتاح", "financial_account_id": None,
            "entity_type": "equity", "entity_id": "opening_balance_equity",
            "sub_account": "main", "side": "credit" if difference > 0 else "debit",
            "meaning": "owed_by_us" if difference > 0 else "available_to_us",
            "original_amount": _money(abs(difference)), "original_currency": "SAR",
            "sar_amount": _money(abs(difference)), "ledger_currency": "SAR",
            "fx_snapshot": {"rate_to_sar": "1", "fx_at": content["cutover_at"], "source": "sar_parity", "evidence_file_id": None},
            "account_snapshot": {"id": "opening_balance_equity", "account_type": "equity", "currency": "SAR", "status": "system"},
            "evidence_file_id": equity_evidence_file_id,
            "evidence_section_id": "equity",
        })
    debit = sum((Decimal(row["sar_amount"]) for row in entries if row["side"] == "debit"), Decimal())
    credit = sum((Decimal(row["sar_amount"]) for row in entries if row["side"] == "credit"), Decimal())
    if debit != credit or (entries and debit <= 0):
        raise HTTPException(409, detail={"code": "opening_preview_not_balanced"})
    return {
        **content,
        "lines": all_lines,
        "preview_entries": entries,
        "zero_accounts": zero_accounts,
        "evidence_requirements": evidence_requirements,
        "debit_total": _money(debit), "credit_total": _money(credit),
        "zero_only": not entries,
    }


async def _verified_evidence(
    db: Any,
    *,
    owner: str,
    requirements: list[dict[str, Any]],
    approval_version: int | None = None,
    approved_by: str | None = None,
    approved_at: str | None = None,
) -> list[dict[str, Any]]:
    unique = {(row["source_file_id"], row["purpose"], row.get("section_id")) for row in requirements}
    if len(unique) != len(requirements):
        requirements = [
            {"source_file_id": file_id, "purpose": purpose, "section_id": section_id}
            for file_id, purpose, section_id in sorted(unique, key=lambda value: tuple(str(item or "") for item in value))
        ]
    file_ids = {row["source_file_id"] for row in requirements}
    rows = await db.mz2_opening_evidence.find(
        {"user_id": owner, "source_file_id": {"$in": sorted(file_ids)}}
    ).to_list(len(file_ids) + 1)
    by_id = {str(row.get("source_file_id")): row for row in rows}
    if set(by_id) != file_ids:
        raise HTTPException(409, detail={"code": "opening_evidence_missing_or_foreign"})
    snapshots: list[dict[str, Any]] = []
    for requirement in sorted(requirements, key=lambda row: (row["source_file_id"], row["purpose"], row.get("section_id") or "")):
        file_id = requirement["source_file_id"]
        evidence = by_id[file_id]
        source = await db.accounting_source_files.find_one({"user_id": owner, "file_id": file_id})
        if not source:
            raise HTTPException(409, detail={"code": "opening_evidence_source_missing"})
        content = bytes(source.get("content") or b"")
        digest = hashlib.sha256(content).hexdigest()
        size = len(content)
        if (
            evidence.get("user_id") != owner
            or source.get("user_id") != owner
            or evidence.get("purpose") != requirement["purpose"]
            or evidence.get("section_id") != requirement.get("section_id")
            or evidence.get("sha256") != digest
            or source.get("sha256") != digest
            or evidence.get("size") != size
            or source.get("size") != size
        ):
            raise HTTPException(409, detail={"code": "opening_evidence_contract_mismatch"})
        snapshot = {
            "owner_id": owner,
            "source_file_id": file_id,
            "purpose": evidence["purpose"],
            "section_id": evidence.get("section_id"),
            "sha256": digest,
            "size": size,
        }
        if approval_version is not None:
            snapshot.update({
                "approval_version": approval_version,
                "approved_by": approved_by,
                "approved_at": approved_at,
            })
        snapshots.append(snapshot)
    return snapshots


def _approval_hash(draft: dict[str, Any], snapshots: list[dict[str, Any]]) -> str:
    return _canonical_hash({
        "draft_id": draft["id"],
        "approval_version": draft["version"] + 1,
        "preview_id": draft["preview_id"],
        "approved_preview_hash": draft["preview_hash"],
        "evidence": snapshots,
    })


async def _assert_snapshot(db: Any, owner: str, draft: dict[str, Any]) -> None:
    snapshots = draft.get("evidence_snapshot")
    if not isinstance(snapshots, list) or not snapshots:
        raise HTTPException(409, detail={"code": "opening_evidence_approval_missing"})
    for snapshot in snapshots:
        if (
            snapshot.get("owner_id") != owner
            or snapshot.get("approval_version") != draft.get("version")
            or snapshot.get("approved_by") != draft.get("reviewed_by")
            or snapshot.get("approved_at") != draft.get("reviewed_at")
        ):
            raise HTTPException(409, detail={"code": "opening_evidence_approval_mismatch"})
    current = await _verified_evidence(
        db,
        owner=owner,
        requirements=[{
            "source_file_id": snapshot["source_file_id"],
            "purpose": snapshot["purpose"],
            "section_id": snapshot.get("section_id"),
        } for snapshot in snapshots],
        approval_version=draft["version"],
        approved_by=draft["reviewed_by"],
        approved_at=draft["reviewed_at"],
    )
    if current != snapshots:
        raise HTTPException(409, detail={"code": "opening_evidence_snapshot_changed"})
    expected_hash = _canonical_hash({
        "draft_id": draft["id"],
        "approval_version": draft["version"],
        "preview_id": draft["preview_id"],
        "approved_preview_hash": draft["preview_hash"],
        "evidence": snapshots,
    })
    if expected_hash != draft.get("approval_hash"):
        raise HTTPException(409, detail={"code": "opening_evidence_snapshot_hash_mismatch"})


def _action_hash(action: str, payload: BaseModel) -> str:
    return _canonical_hash({"action": action, **payload.model_dump(mode="json")})


def _action_replay(draft: dict[str, Any], action: str, payload: BaseModel) -> bool:
    stored = (draft.get("actions") or {}).get(action) or {}
    if stored.get("idempotency_key") != payload.idempotency_key:
        return False
    if stored.get("request_hash") != _action_hash(action, payload):
        raise HTTPException(409, detail={"code": "opening_action_idempotency_conflict"})
    return True


def _v2_entries(draft: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, line in enumerate(draft["preview_entries"], start=1):
        result.append({
            "leg_key": f"opening:{index}:{line['category']}:{line['entity_type']}:{line['entity_id']}",
            "entity_type": line["entity_type"],
            "entity_id": line["entity_id"],
            "sub_account": line.get("sub_account"),
            "entry_type": "opening_balance",
            "amount": str(line["sar_amount"]),
            "side": line["side"],
            "metadata": {
                "opening_category": line["category"],
                "source_currency": line["original_currency"],
                "source_amount": line["original_amount"],
                "fx_snapshot": line["fx_snapshot"],
                "account_snapshot": line.get("account_snapshot"),
                "evidence_file_id": line["evidence_file_id"],
            },
        })
    return result


async def ensure_financial_account_indexes(db: Any) -> None:
    await db.mz2_financial_accounts.create_index(
        [("user_id", 1), ("id", 1)], unique=True, name="uniq_mz2_financial_account"
    )
    await db.mz2_financial_accounts.create_index(
        [("user_id", 1), ("idempotency_key", 1)], unique=True,
        name="uniq_mz2_financial_account_request",
    )
    await db.mz2_opening_balance_drafts.create_index(
        [("user_id", 1), ("idempotency_key", 1)], unique=True,
        name="uniq_mz2_opening_draft_request",
    )
    await db.mz2_opening_balance_drafts.create_index(
        [("user_id", 1), ("active_slot", 1)], unique=True,
        partialFilterExpression={"active_slot": "opening"},
        name="uniq_mz2_active_opening_draft",
    )
    await db.mz2_opening_evidence.create_index(
        [("user_id", 1), ("source_file_id", 1)], unique=True,
        name="uniq_mz2_opening_evidence_source",
    )
    await db.mz2_opening_balance_audit.create_index(
        [("user_id", 1), ("draft_id", 1), ("event_no", 1)], unique=True,
        name="uniq_mz2_opening_audit_event",
    )


async def _append_opening_audit(
    db: Any,
    *,
    owner: str,
    draft_id: str,
    event_no: str,
    event_type: str,
    actor_id: str,
    manifest: dict[str, Any],
) -> None:
    recorded_at = _now()
    document = {
        "_id": f"{owner}:{draft_id}:{event_no}",
        "id": f"{draft_id}:{event_no}",
        "user_id": owner,
        "operation_id": OPERATION_ID,
        "draft_id": draft_id,
        "event_no": event_no,
        "event_type": event_type,
        "actor_id": actor_id,
        "recorded_at": recorded_at,
        "manifest": manifest,
        "manifest_hash": _canonical_hash(manifest),
    }
    await db.mz2_opening_balance_audit.insert_one(document)


async def _assert_account_snapshots(db: Any, owner: str, draft: dict[str, Any]) -> None:
    snapshots = {
        str(line["account_snapshot"]["id"]): line["account_snapshot"]
        for line in draft.get("lines") or []
        if isinstance(line.get("account_snapshot"), dict)
        and line["account_snapshot"].get("status") != "system"
    }
    if not snapshots:
        return
    rows = await db.mz2_financial_accounts.find({
        "user_id": owner,
        "id": {"$in": sorted(snapshots)},
    }).to_list(len(snapshots) + 1)
    current = {
        str(row.get("id")): {
            "id": str(row["id"]), "name": str(row.get("name") or ""),
            "account_type": str(row.get("account_type") or ""),
            "currency": str(row.get("currency") or "").upper(),
            "status": str(row.get("status") or ""),
            "version": int(row.get("version") or 1),
            "external_ref": row.get("external_ref"),
        }
        for row in rows
    }
    if current != snapshots:
        raise HTTPException(409, detail={"code": "opening_account_snapshot_changed"})


async def _assert_financial_account_coverage(db: Any, owner: str, draft: dict[str, Any]) -> None:
    accounts = await db.mz2_financial_accounts.find({
        "user_id": owner, "status": "active",
    }, {"_id": 0, "id": 1}).to_list(1_001)
    if len(accounts) > 1_000:
        raise HTTPException(409, detail={"code": "opening_financial_account_scope_too_large"})
    required = {str(row.get("id") or "") for row in accounts}
    covered = {
        str(line.get("financial_account_id") or "")
        for line in draft.get("lines") or []
        if line.get("financial_account_id")
    }
    missing = sorted(required - covered)
    if missing:
        raise HTTPException(409, detail={
            "code": "opening_financial_accounts_require_balance_or_zero",
            "missing_count": len(missing),
        })


def _cutover_evidence_sections(draft: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, list[dict[str, Any]]] = {section["id"]: [] for section in EVIDENCE_SECTIONS}
    for snapshot in draft.get("evidence_snapshot") or []:
        section = snapshot.get("section_id")
        if section in sections:
            sections[section].append({
                "source_file_id": snapshot["source_file_id"],
                "sha256": snapshot["sha256"],
                "size": snapshot["size"],
                "approval_version": snapshot["approval_version"],
            })
    return sections


def _zero_account_settings(draft: dict[str, Any], group_id: str | None) -> list[dict[str, Any]]:
    return [{
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "sub_account": row["sub_account"],
        "evidence_ref": row["evidence_file_id"],
        "opening_balance_txn_group_id": group_id or f"zero:{draft['id']}",
        "accounting_at": draft["cutover_at"],
    } for row in draft.get("zero_accounts") or []]


def install_financial_account_routes(router: Any, db: Any, current_user: Any) -> None:
    """Install the authoritative #1131 financial-account and opening routes."""
    base = "/accounting-module/financial-accounts"
    opening = base + "/opening-balances"

    async def actor_for(user: dict[str, Any], *permissions: str) -> tuple[dict[str, Any], str]:
        actor = await fresh_actor(db, user)
        for permission in permissions:
            _require(actor, permission)
        return actor, _owner(actor)

    @router.get(base + "/definitions")
    async def definitions(user: dict = Depends(current_user)):
        await actor_for(user, "accounts_view")
        return {
            "account_types": list(ACCOUNT_TYPES),
            "opening_categories": [
                {"id": "financial_account", "label": "حساب مالي معرف", "dynamic_rule": True},
                *[{
                    "id": key,
                    "label": value["label"],
                    "section_id": value["section"],
                    "meaning": _meaning_for_side(value["side"]),
                } for key, value in OPENING_CATEGORY_CATALOG.items()],
            ],
            "financial_account_rules": {
                key: {
                    "section_id": value["section"],
                    "meaning": _meaning_for_side(value["side"]),
                }
                for key, value in FINANCIAL_ACCOUNT_RULES.items()
            },
            "evidence_sections": list(EVIDENCE_SECTIONS),
            "evidence_purposes": sorted(OPENING_PURPOSES),
            "cutover_timezone": "Asia/Riyadh",
        }

    @router.get(base)
    async def list_accounts(user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "accounts_view")
        rows = await db.mz2_financial_accounts.find({"user_id": owner}).sort("created_at", 1).to_list(500)
        return {"items": [_public(row) for row in rows]}

    @router.get(base + "/accounts/{account_id}")
    async def get_account(account_id: str, user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "accounts_view")
        row = await db.mz2_financial_accounts.find_one({"user_id": owner, "id": account_id})
        if not row:
            raise HTTPException(404, "financial_account_not_found")
        return _public(row)

    @router.post(base)
    async def create_account(payload: AccountCreate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounts_view", "accounts_manage")
        content = payload.model_dump(mode="json")
        content["currency"] = payload.currency.upper()
        request_hash = _canonical_hash(content)

        async def create(scoped):
            existing = await scoped.mz2_financial_accounts.find_one(
                {"user_id": owner, "idempotency_key": payload.idempotency_key}
            )
            if existing:
                if existing.get("request_hash") != request_hash:
                    raise HTTPException(409, detail={"code": "financial_account_idempotency_conflict"})
                return {**_public(existing), "existing": True}
            account_id = str(uuid.uuid4())
            document = {
                "_id": f"{owner}:{account_id}", "id": account_id, "user_id": owner,
                **content, "request_hash": request_hash, "status": "active", "version": 1,
                "created_at": _now(), "created_by": str(actor["id"]),
            }
            try:
                await scoped.mz2_financial_accounts.insert_one(document)
            except DuplicateKeyError:
                prior = await scoped.mz2_financial_accounts.find_one(
                    {"user_id": owner, "idempotency_key": payload.idempotency_key}
                )
                if not prior or prior.get("request_hash") != request_hash:
                    raise HTTPException(409, detail={"code": "financial_account_idempotency_conflict"}) from None
                return {**_public(prior), "existing": True}
            return {**_public(document), "existing": False}

        return await atomic_owner(db, owner, create)

    @router.patch(base + "/accounts/{account_id}")
    async def update_account(account_id: str, payload: AccountUpdate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounts_view", "accounts_manage")
        changes = payload.model_dump(exclude={"version"}, exclude_unset=True)
        if not changes:
            raise HTTPException(422, "financial_account_changes_required")
        changes.update({"updated_at": _now(), "updated_by": str(actor["id"])})

        async def update(scoped):
            row = await scoped.mz2_financial_accounts.find_one_and_update(
                {"user_id": owner, "id": account_id, "version": payload.version, "status": "active"},
                {"$set": changes, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "financial_account_state_or_version_conflict")
            return _public(row)

        return await atomic_owner(db, owner, update)

    @router.delete(base + "/accounts/{account_id}")
    async def archive_account(account_id: str, payload: AccountArchive, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounts_view", "accounts_manage")

        async def archive(scoped):
            row = await scoped.mz2_financial_accounts.find_one_and_update(
                {"user_id": owner, "id": account_id, "version": payload.version, "status": "active"},
                {"$set": {
                    "status": "archived", "archive_reason": payload.reason,
                    "archived_at": _now(), "archived_by": str(actor["id"]),
                }, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "financial_account_state_or_version_conflict")
            return _public(row)

        return await atomic_owner(db, owner, archive)

    @router.post(opening + "/evidence")
    async def upload_evidence(
        purpose: str = Form(...), section_id: str = Form(""),
        file: UploadFile = File(...), user: dict = Depends(current_user),
    ):
        actor, owner = await actor_for(user, "opening_view", "drafts_manage")
        normalized_section = str(section_id or "").strip() or None
        if purpose not in OPENING_PURPOSES:
            raise HTTPException(422, detail={"code": "opening_evidence_purpose_invalid"})
        if purpose in {"opening_balance", "fx_rate"}:
            if normalized_section not in EVIDENCE_SECTION_IDS:
                raise HTTPException(422, detail={"code": "opening_evidence_section_invalid"})
        elif normalized_section is not None:
            raise HTTPException(422, detail={"code": "opening_evidence_section_not_allowed"})
        content = await file.read()
        if not content or len(content) > MAX_EVIDENCE_BYTES:
            raise HTTPException(422, detail={"code": "opening_evidence_size_invalid"})
        file_id = str(uuid.uuid4())
        try:
            digest = await preserve_original(db, owner, file_id, content)
        except ValueError as error:
            raise HTTPException(409, detail={"code": "opening_evidence_preserve_failed"}) from error
        document = {
            "_id": f"{owner}:{file_id}", "id": file_id, "user_id": owner,
            "source_file_id": file_id, "filename": file.filename, "purpose": purpose,
            "section_id": normalized_section, "sha256": digest, "size": len(content),
            "status": "immutable", "created_at": _now(), "created_by": str(actor["id"]),
        }
        await db.mz2_opening_evidence.insert_one(document)
        return _public(document)

    @router.get(opening + "/drafts")
    async def list_drafts(user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "opening_view")
        rows = await db.mz2_opening_balance_drafts.find({"user_id": owner}).sort("created_at", -1).to_list(200)
        return {"items": [_public(row) for row in rows]}

    @router.get(opening + "/drafts/{draft_id}")
    async def get_draft(draft_id: str, user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "opening_view")
        row = await db.mz2_opening_balance_drafts.find_one({"user_id": owner, "id": draft_id})
        if not row:
            raise HTTPException(404, "opening_draft_not_found")
        return _public(row)

    @router.post(opening + "/drafts")
    async def create_draft(payload: OpeningDraftCreate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "opening_view", "drafts_manage")
        raw_content = _draft_content(payload)
        request_hash = _canonical_hash(raw_content)

        async def create(scoped):
            existing_request = await scoped.mz2_opening_balance_drafts.find_one(
                {"user_id": owner, "idempotency_key": payload.idempotency_key}
            )
            if existing_request:
                if existing_request.get("request_hash") != request_hash:
                    raise HTTPException(409, detail={"code": "opening_draft_idempotency_conflict"})
                return {**_public(existing_request), "existing": True}
            compiled = await _compile_opening(scoped, owner=owner, payload=payload)
            evidence = await _verified_evidence(
                scoped, owner=owner, requirements=compiled["evidence_requirements"]
            )
            active = await scoped.mz2_opening_balance_drafts.find_one(
                {"user_id": owner, "active_slot": "opening"}
            )
            prior = None
            if active:
                if active.get("id") != payload.replaces_draft_id or active.get("status") not in {"draft", "previewed", "reviewed"}:
                    raise HTTPException(409, detail={
                        "code": "opening_draft_replacement_required", "active_draft_id": active.get("id"),
                    })
                prior = active
            elif payload.replaces_draft_id:
                prior = await scoped.mz2_opening_balance_drafts.find_one({
                    "user_id": owner, "id": payload.replaces_draft_id, "status": "reversed",
                })
                if not prior:
                    raise HTTPException(409, detail={"code": "opening_replacement_requires_reversed_draft"})
                if compiled["cutover_at"] != prior.get("cutover_at"):
                    raise HTTPException(409, detail={"code": "opening_replacement_cutover_must_match_root"})
                settings = await scoped.settings.find_one({"user_id": owner}) or {}
                cutover = settings.get("mezan2_financial_cutover") or {}
                if (
                    cutover.get("opening_replacement_required") is not True
                    or cutover.get("opening_last_reversed_txn_group_id") != prior.get("txn_group_id")
                ):
                    raise HTTPException(409, detail={"code": "opening_replacement_state_conflict"})
            else:
                already_posted = await scoped.mz2_opening_balance_drafts.find_one({
                    "user_id": owner, "status": {"$in": ["posted", "reversed"]},
                })
                if already_posted:
                    raise HTTPException(409, detail={"code": "opening_replacement_link_required"})

            draft_id = str(uuid.uuid4())
            if active:
                await scoped.mz2_opening_balance_drafts.update_one(
                    {"_id": active["_id"], "version": active["version"]},
                    {"$set": {"status": "replaced", "replaced_by": draft_id, "updated_at": _now()}, "$unset": {"active_slot": ""}},
                )
            reversed_prior = prior if prior and prior.get("status") == "reversed" else None
            if reversed_prior:
                opening_revision = int(reversed_prior.get("opening_revision") or 0) + 1
                replacement_for_draft_id = reversed_prior.get("id")
                prior_active_id = str(reversed_prior.get("txn_group_id") or "").strip() or None
                if prior_active_id and not prior_active_id.startswith("zero:"):
                    ledger_anchor_id = prior_active_id
                    ledger_anchor_reversal_id = str(
                        reversed_prior.get("reversal_txn_group_id") or ""
                    ).strip() or None
                else:
                    ledger_anchor_id = str(
                        reversed_prior.get("ledger_replacement_anchor_txn_group_id") or ""
                    ).strip() or None
                    ledger_anchor_reversal_id = str(
                        reversed_prior.get("ledger_replacement_anchor_reversal_txn_group_id") or ""
                    ).strip() or None
                ledger_root_id = (
                    str(reversed_prior.get("opening_root_txn_group_id") or "").strip()
                    if ledger_anchor_id
                    else ""
                ) or None
                ledger_opening_revision = opening_revision if ledger_anchor_id else 1
            elif active:
                opening_revision = int(active.get("opening_revision") or 1)
                replacement_for_draft_id = active.get("replacement_for_draft_id")
                prior_active_id = active.get("replaces_opening_active_id")
                ledger_anchor_id = active.get("replaces_txn_group_id")
                ledger_anchor_reversal_id = active.get("required_reversal_txn_group_id")
                ledger_root_id = active.get("opening_root_txn_group_id")
                ledger_opening_revision = int(active.get("ledger_opening_revision") or 1)
            else:
                opening_revision = 1
                replacement_for_draft_id = None
                prior_active_id = None
                ledger_anchor_id = None
                ledger_anchor_reversal_id = None
                ledger_root_id = None
                ledger_opening_revision = 1
            document = {
                "_id": f"{owner}:{draft_id}", "id": draft_id, "user_id": owner,
                "operation_id": OPERATION_ID,
                "idempotency_key": payload.idempotency_key, "request_hash": request_hash,
                "cutover_at": compiled["cutover_at"], "cutover_timezone": "Asia/Riyadh",
                "cutover_evidence_file_id": compiled["cutover_evidence_file_id"],
                "section_evidence_file_ids": compiled["section_evidence_file_ids"],
                "lines": compiled["lines"], "candidate_entries": compiled["preview_entries"],
                "zero_accounts": compiled["zero_accounts"], "zero_only": compiled["zero_only"],
                "evidence_requirements": compiled["evidence_requirements"],
                "evidence_manifest": evidence,
                "debit_total": compiled["debit_total"], "credit_total": compiled["credit_total"],
                "replacement_for_draft_id": replacement_for_draft_id,
                "replaces_opening_active_id": prior_active_id,
                "opening_root_txn_group_id": ledger_root_id,
                "replaces_txn_group_id": ledger_anchor_id,
                "required_reversal_txn_group_id": ledger_anchor_reversal_id,
                "ledger_replacement_anchor_txn_group_id": ledger_anchor_id,
                "ledger_replacement_anchor_reversal_txn_group_id": ledger_anchor_reversal_id,
                "opening_revision": opening_revision,
                "ledger_opening_revision": ledger_opening_revision,
                "status": "draft", "active_slot": "opening", "version": 1,
                "created_at": _now(), "created_by": str(actor["id"]), "actions": {},
            }
            await scoped.mz2_opening_balance_drafts.insert_one(document)
            await _append_opening_audit(
                scoped, owner=owner, draft_id=draft_id, event_no="1:draft_created",
                event_type="draft_created", actor_id=str(actor["id"]),
                manifest={
                    "request_hash": request_hash, "cutover_at": document["cutover_at"],
                    "opening_revision": opening_revision, "evidence": evidence,
                },
            )
            return {**_public(document), "existing": False}

        return await atomic_owner(db, owner, create)

    @router.post(opening + "/drafts/{draft_id}/preview")
    async def preview_draft(draft_id: str, payload: OpeningAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "opening_view", "drafts_manage")

        async def preview(scoped):
            draft = await scoped.mz2_opening_balance_drafts.find_one({"user_id": owner, "id": draft_id})
            if not draft:
                raise HTTPException(404, "opening_draft_not_found")
            if _action_replay(draft, "preview", payload):
                return {**_public(draft), "existing": True}
            if draft.get("status") != "draft" or draft.get("version") != payload.version:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            await _assert_account_snapshots(scoped, owner, draft)
            evidence = await _verified_evidence(
                scoped, owner=owner, requirements=draft["evidence_requirements"]
            )
            preview_id = str(uuid.uuid4())
            preview_manifest = {
                "operation_id": OPERATION_ID, "draft_id": draft_id,
                "draft_revision": payload.version, "preview_id": preview_id,
                "cutover_at": draft["cutover_at"], "cutover_timezone": draft["cutover_timezone"],
                "lines": draft["lines"], "entries": draft["candidate_entries"],
                "zero_accounts": draft["zero_accounts"], "evidence": evidence,
            }
            preview_hash = _canonical_hash(preview_manifest)
            now = _now()
            row = await scoped.mz2_opening_balance_drafts.find_one_and_update(
                {"_id": draft["_id"], "status": "draft", "version": payload.version},
                {"$set": {
                    "status": "previewed", "preview_id": preview_id,
                    "preview_hash": preview_hash, "preview_manifest": preview_manifest,
                    "preview_entries": draft["candidate_entries"], "preview_note": payload.note,
                    "previewed_by": str(actor["id"]), "previewed_at": now,
                    "actions.preview": {"idempotency_key": payload.idempotency_key, "request_hash": _action_hash("preview", payload)},
                    "updated_at": now,
                }, "$inc": {"version": 1}}, return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            await _append_opening_audit(
                scoped, owner=owner, draft_id=draft_id,
                event_no=f"{row['version']}:preview_created", event_type="preview_created",
                actor_id=str(actor["id"]), manifest=preview_manifest,
            )
            return {**_public(row), "existing": False}

        return await atomic_owner(db, owner, preview)

    @router.post(opening + "/drafts/{draft_id}/review")
    async def review_draft(draft_id: str, payload: OpeningAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "opening_view", "review")

        async def review(scoped):
            draft = await scoped.mz2_opening_balance_drafts.find_one({"user_id": owner, "id": draft_id})
            if not draft:
                raise HTTPException(404, "opening_draft_not_found")
            if _action_replay(draft, "review", payload):
                return {**_public(draft), "existing": True}
            if draft.get("status") != "previewed" or draft.get("version") != payload.version:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            if _canonical_hash(draft.get("preview_manifest") or {}) != draft.get("preview_hash"):
                raise HTTPException(409, detail={"code": "opening_preview_hash_mismatch"})
            await _assert_account_snapshots(scoped, owner, draft)
            reviewed_at = _now()
            snapshots = await _verified_evidence(
                scoped, owner=owner, requirements=draft["evidence_requirements"],
                approval_version=payload.version + 1, approved_by=str(actor["id"]),
                approved_at=reviewed_at,
            )
            approval_hash = _approval_hash(draft, snapshots)
            row = await scoped.mz2_opening_balance_drafts.find_one_and_update(
                {"_id": draft["_id"], "status": "previewed", "version": payload.version},
                {"$set": {
                    "status": "reviewed", "review_note": payload.note,
                    "reviewed_by": str(actor["id"]), "reviewed_at": reviewed_at,
                    "evidence_snapshot": snapshots, "approval_hash": approval_hash,
                    "actions.review": {"idempotency_key": payload.idempotency_key, "request_hash": _action_hash("review", payload)},
                    "updated_at": reviewed_at,
                }, "$inc": {"version": 1}}, return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            await _append_opening_audit(
                scoped, owner=owner, draft_id=draft_id,
                event_no=f"{row['version']}:review_approved", event_type="review_approved",
                actor_id=str(actor["id"]), manifest={
                    "preview_id": row["preview_id"], "preview_hash": row["preview_hash"],
                    "approval_hash": approval_hash, "approval_version": row["version"],
                    "approved_by": row["reviewed_by"], "approved_at": row["reviewed_at"],
                    "evidence": snapshots,
                },
            )
            return {**_public(row), "existing": False}

        return await atomic_owner(db, owner, review)

    @router.post(opening + "/drafts/{draft_id}/post")
    async def post_draft(draft_id: str, payload: OpeningAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "opening_view", "post")

        async def post(scoped):
            draft = await scoped.mz2_opening_balance_drafts.find_one({"user_id": owner, "id": draft_id})
            if not draft:
                raise HTTPException(404, "opening_draft_not_found")
            if _action_replay(draft, "post", payload):
                return {**_public(draft), "existing": True}
            if draft.get("status") != "reviewed" or draft.get("version") != payload.version:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            await _assert_snapshot(scoped, owner, draft)
            await _assert_account_snapshots(scoped, owner, draft)
            await _assert_financial_account_coverage(scoped, owner, draft)
            await assert_open_journal_periods(scoped, owner, [{"metadata": {"accounting_at": draft["cutover_at"]}}])
            await assert_writer_allowed(scoped, owner, "v2")
            result = None
            if not draft.get("zero_only"):
                try:
                    result = await post_opening_journal_v2(
                        scoped._db, user_id=owner, actor_id=str(actor["id"]),
                        actor_name=actor.get("name") or actor.get("email") or str(actor["id"]),
                        opening_operation_id=draft["id"], approved_preview_hash=draft["preview_hash"],
                        effective_at=draft["cutover_at"], entries=_v2_entries(draft),
                        opening_root_txn_group_id=draft.get("opening_root_txn_group_id"),
                        replaces_txn_group_id=draft.get("replaces_txn_group_id"),
                        reversal_txn_group_id=draft.get("required_reversal_txn_group_id"),
                        opening_revision=int(draft.get("ledger_opening_revision") or 1),
                        mongo_session=scoped._session,
                    )
                except AccountingLedgerV2Error as error:
                    raise _http_from_ledger(error) from error
            group_id = result["group"]["txn_group_id"] if result else None
            active_id = group_id or f"zero:{draft['id']}"
            root_id = draft.get("opening_root_txn_group_id") or group_id or active_id
            settings = await scoped.settings.find_one({"user_id": owner}) or {}
            cutover = (settings.get("mezan2_financial_cutover") or {})
            expected_active = draft.get("replaces_opening_active_id")
            if draft.get("opening_revision", 1) > 1:
                if cutover.get("opening_replacement_required") is not True or cutover.get("opening_last_reversed_txn_group_id") != expected_active:
                    raise HTTPException(409, detail={"code": "opening_replacement_state_conflict"})
            elif cutover.get("opening_root_txn_group_id"):
                raise HTTPException(409, detail={"code": "opening_root_already_committed"})
            posted_at = _now()
            zero_accounts = _zero_account_settings(draft, group_id)
            await scoped.settings.update_one({"user_id": owner}, {"$set": {
                "mezan2_financial_cutover.operation_id": OPERATION_ID,
                "mezan2_financial_cutover.ledger_source": "accounting_v2_operation_scoped",
                "mezan2_financial_cutover.status": "active",
                "mezan2_financial_cutover.cutover_at": draft["cutover_at"],
                "mezan2_financial_cutover.cutover_timezone": "Asia/Riyadh",
                "mezan2_financial_cutover.opening_balance_preview_id": draft["preview_id"],
                "mezan2_financial_cutover.opening_balance_preview_balanced": True,
                "mezan2_financial_cutover.opening_balance_approved_at": draft["reviewed_at"],
                "mezan2_financial_cutover.opening_balance_approved_by": draft["reviewed_by"],
                "mezan2_financial_cutover.opening_balance_txn_group_id": active_id,
                "mezan2_financial_cutover.opening_root_txn_group_id": root_id,
                "mezan2_financial_cutover.opening_active_txn_group_id": active_id,
                "mezan2_financial_cutover.opening_revision": draft["opening_revision"],
                "mezan2_financial_cutover.opening_zero_manifest_id": draft["id"] if draft.get("zero_only") else None,
                "mezan2_financial_cutover.opening_balance_zero_accounts": zero_accounts,
                "mezan2_financial_cutover.evidence_sheet_ref": draft["cutover_evidence_file_id"],
                "mezan2_financial_cutover.evidence_sections": _cutover_evidence_sections(draft),
                "mezan2_financial_cutover.opening_posted_at": posted_at,
                "mezan2_financial_cutover.opening_posted_by": str(actor["id"]),
                "mezan2_financial_cutover.opening_replacement_required": False,
            }, "$unset": {
                "mezan2_financial_cutover.opening_last_reversed_txn_group_id": "",
                "mezan2_financial_cutover.opening_last_reversal_txn_group_id": "",
            }}, upsert=True)
            row = await scoped.mz2_opening_balance_drafts.find_one_and_update(
                {"_id": draft["_id"], "status": "reviewed", "version": payload.version},
                {"$set": {
                    "status": "posted", "txn_group_id": active_id,
                    "opening_root_txn_group_id": root_id,
                    "post_note": payload.note, "posted_by": str(actor["id"]), "posted_at": posted_at,
                    "actions.post": {"idempotency_key": payload.idempotency_key, "request_hash": _action_hash("post", payload)},
                    "updated_at": posted_at,
                }, "$inc": {"version": 1}, "$unset": {"active_slot": ""}},
                return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            await _append_opening_audit(
                scoped, owner=owner, draft_id=draft_id,
                event_no=f"{row['version']}:opening_posted", event_type="opening_posted",
                actor_id=str(actor["id"]), manifest={
                    "preview_id": row["preview_id"], "preview_hash": row["preview_hash"],
                    "approval_hash": row["approval_hash"], "txn_group_id": active_id,
                    "opening_root_txn_group_id": root_id, "opening_revision": row["opening_revision"],
                    "ledger_opening_revision": row.get("ledger_opening_revision"),
                    "zero_accounts": zero_accounts,
                },
            )
            return {**_public(row), "existing": bool(result and result.get("existing"))}

        return await atomic_owner(db, owner, post)

    @router.post(opening + "/drafts/{draft_id}/reverse")
    async def reverse_draft(draft_id: str, payload: OpeningReverseAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "opening_view", "reverse")
        if payload.effective_at is None:
            raise HTTPException(422, detail={"code": "reversal_effective_at_required"})

        async def reverse(scoped):
            draft = await scoped.mz2_opening_balance_drafts.find_one({"user_id": owner, "id": draft_id})
            if not draft:
                raise HTTPException(404, "opening_draft_not_found")
            if _action_replay(draft, "reverse", payload):
                return {**_public(draft), "existing": True}
            if draft.get("status") != "posted" or draft.get("version") != payload.version:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            effective_at = _utc_iso(payload.effective_at, field="opening_reversal_effective_at")
            if effective_at != draft.get("cutover_at"):
                raise HTTPException(409, detail={"code": "opening_reversal_must_use_cutover_instant"})
            reversal_evidence = await _verified_evidence(
                scoped, owner=owner, requirements=[{
                    "source_file_id": payload.evidence_file_id,
                    "purpose": "opening_reversal_reason", "section_id": None,
                }], approval_version=payload.version + 1,
                approved_by=str(actor["id"]), approved_at=_now(),
            )
            await assert_open_journal_periods(scoped, owner, [{"metadata": {"accounting_at": effective_at}}])
            await assert_writer_allowed(scoped, owner, "v2")
            result = None
            if not str(draft["txn_group_id"]).startswith("zero:"):
                try:
                    result = await reverse_journal_v2(
                        scoped._db, user_id=owner, actor_id=str(actor["id"]),
                        actor_name=actor.get("name") or actor.get("email") or str(actor["id"]),
                        original_txn_group_id=draft["txn_group_id"], effective_at=effective_at,
                        reason=payload.note, evidence_snapshot=reversal_evidence,
                        mongo_session=scoped._session,
                    )
                except AccountingLedgerV2Error as error:
                    raise _http_from_ledger(error) from error
            reversal_id = result["group"]["txn_group_id"] if result else f"zero-reversal:{draft['id']}"
            if result:
                ledger_anchor_id = draft["txn_group_id"]
                ledger_anchor_reversal_id = reversal_id
            else:
                ledger_anchor_id = draft.get("ledger_replacement_anchor_txn_group_id")
                ledger_anchor_reversal_id = draft.get(
                    "ledger_replacement_anchor_reversal_txn_group_id"
                )
            reversed_at = _now()
            settings = await scoped.settings.find_one({"user_id": owner}) or {}
            state = settings.get("mezan2_financial_cutover") or {}
            if state.get("opening_active_txn_group_id") != draft["txn_group_id"]:
                raise HTTPException(409, detail={"code": "opening_active_reference_changed"})
            await scoped.settings.update_one({"user_id": owner}, {"$set": {
                "mezan2_financial_cutover.status": "prepared",
                "mezan2_financial_cutover.opening_replacement_required": True,
                "mezan2_financial_cutover.opening_last_reversed_txn_group_id": draft["txn_group_id"],
                "mezan2_financial_cutover.opening_last_reversal_txn_group_id": reversal_id,
            }, "$unset": {
                "mezan2_financial_cutover.opening_active_txn_group_id": "",
                "mezan2_financial_cutover.opening_balance_txn_group_id": "",
            }})
            row = await scoped.mz2_opening_balance_drafts.find_one_and_update(
                {"_id": draft["_id"], "status": "posted", "version": payload.version},
                {"$set": {
                    "status": "reversed", "reversal_txn_group_id": reversal_id,
                    "ledger_replacement_anchor_txn_group_id": ledger_anchor_id,
                    "ledger_replacement_anchor_reversal_txn_group_id": ledger_anchor_reversal_id,
                    "reversal_evidence_snapshot": reversal_evidence,
                    "reverse_note": payload.note, "reversed_by": str(actor["id"]),
                    "reversed_at": reversed_at,
                    "actions.reverse": {"idempotency_key": payload.idempotency_key, "request_hash": _action_hash("reverse", payload)},
                    "updated_at": reversed_at,
                }, "$inc": {"version": 1}}, return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            await _append_opening_audit(
                scoped, owner=owner, draft_id=draft_id,
                event_no=f"{row['version']}:opening_reversed", event_type="opening_reversed",
                actor_id=str(actor["id"]), manifest={
                    "txn_group_id": draft["txn_group_id"],
                    "reversal_txn_group_id": reversal_id,
                    "reason_evidence": reversal_evidence,
                },
            )
            return {**_public(row), "existing": bool(result and result.get("existing"))}

        return await atomic_owner(db, owner, reverse)

    @router.get(base + "/transition")
    async def get_transition(user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "accounts_view")
        return await transition_state(db, owner)

    @router.post(base + "/transition")
    async def set_transition(payload: TransitionRequest, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounts_view", "transition")

        async def advance(scoped):
            state = await advance_transition(
                scoped, owner=owner, actor_id=str(actor["id"]), target=payload.target,
                expected_revision=payload.expected_revision, activation_ref=payload.activation_ref,
            )
            reviewed = None
            if payload.target == "v2_active":
                reviewed = await scoped.mz2_opening_balance_drafts.find_one({
                    "user_id": owner, "status": "reviewed", "active_slot": "opening",
                })
                if not reviewed:
                    raise HTTPException(409, detail={"code": "opening_balance_review_required"})
                await _assert_snapshot(scoped, owner, reviewed)
                await _assert_account_snapshots(scoped, owner, reviewed)
                await _assert_financial_account_coverage(scoped, owner, reviewed)
                settings = await scoped.settings.find_one({"user_id": owner}) or {}
                cutover = settings.get("mezan2_financial_cutover") or {}
                if cutover.get("p02_shipping_cod_enabled") is True or cutover.get("p03_inventory_enabled") is True:
                    raise HTTPException(409, detail={"code": "later_phases_must_remain_locked"})
                try:
                    await assert_no_mz2_rows_in_legacy_ledger(
                        scoped._db, user_id=owner, mongo_session=scoped._session,
                    )
                except AccountingLedgerV2Error as error:
                    raise _http_from_ledger(error) from error
            if reviewed:
                await scoped.settings.update_one({"user_id": owner}, {"$set": {
                    "mezan2_financial_cutover.operation_id": OPERATION_ID,
                    "mezan2_financial_cutover.ledger_source": "accounting_v2_operation_scoped",
                    "mezan2_financial_cutover.status": "prepared",
                    "mezan2_financial_cutover.cutover_at": reviewed["cutover_at"],
                    "mezan2_financial_cutover.cutover_timezone": "Asia/Riyadh",
                    "mezan2_financial_cutover.opening_balance_preview_id": reviewed["preview_id"],
                    "mezan2_financial_cutover.opening_balance_preview_balanced": True,
                    "mezan2_financial_cutover.opening_balance_approved_at": reviewed["reviewed_at"],
                    "mezan2_financial_cutover.opening_balance_approved_by": reviewed["reviewed_by"],
                    "mezan2_financial_cutover.evidence_sheet_ref": reviewed["cutover_evidence_file_id"],
                    "mezan2_financial_cutover.evidence_sections": _cutover_evidence_sections(reviewed),
                    "mezan2_financial_cutover.activation_ref": payload.activation_ref.strip(),
                    "mezan2_financial_cutover.transitioned_at": _now(),
                    "mezan2_financial_cutover.transitioned_by": str(actor["id"]),
                    "mezan2_financial_cutover.p02_shipping_cod_enabled": False,
                    "mezan2_financial_cutover.p03_inventory_enabled": False,
                }}, upsert=True)
            return state

        return await atomic_owner(db, owner, advance)
