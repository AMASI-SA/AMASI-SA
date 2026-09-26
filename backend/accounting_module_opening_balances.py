"""MZ2-native opening-balance workflow for MZ2-FIN-CUTOVER-001.

This module never reads legacy balances and never guesses a zero.  The operator
provides the approved opening facts and evidence, receives a balanced preview,
then the owner explicitly approves a single immutable opening journal.  A
separate activation step turns P01 on only after the existing readiness
contract verifies the posted group.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounting_module_contract import (
    EVIDENCE_SECTIONS,
    OPERATION_ID,
    accounting_owner_id,
    require_accounting_permission,
    require_owner,
)
from accounting_module_ledger import opening_posted_is_verified
from accounting_module_readiness import build_accounting_module_status
from accounting_write_control import fresh_actor
from ledger_core import post_txn_group


PROVIDERS = ("salla", "tamara", "tabby", "emkan")
MAX_OPENING_LINES = 500
MONEY = Decimal("0.01")


CATEGORY_CATALOG: dict[str, dict[str, str]] = {
    "bank": {
        "label": "بنك",
        "entity_type": "bank",
        "sub_account": "main",
        "natural_side": "debit",
        "evidence_section": "banks_cash",
    },
    "cash": {
        "label": "صندوق / نقدية",
        "entity_type": "bank",
        "sub_account": "main",
        "natural_side": "debit",
        "evidence_section": "banks_cash",
    },
    "provider_receivable": {
        "label": "ذمة مزود دفع لنا",
        "entity_type": "payment_gateway",
        "sub_account": "receivable",
        "natural_side": "debit",
        "evidence_section": "providers",
    },
    "courier_cod_receivable": {
        "label": "COD لدى شركة شحن",
        "entity_type": "courier",
        "sub_account": "cod_receivable",
        "natural_side": "debit",
        "evidence_section": "couriers_cod",
    },
    "courier_payable": {
        "label": "مستحق لشركة شحن",
        "entity_type": "courier",
        "sub_account": "payable",
        "natural_side": "credit",
        "evidence_section": "couriers_cod",
    },
    "store_driver_cod_receivable": {
        "label": "COD لدى موصل المتجر",
        "entity_type": "store_driver",
        "sub_account": "cod_receivable",
        "natural_side": "debit",
        "evidence_section": "couriers_cod",
    },
    "store_driver_fee_payable": {
        "label": "أجرة مستحقة لموصل المتجر",
        "entity_type": "store_driver",
        "sub_account": "delivery_fee_payable",
        "natural_side": "credit",
        "evidence_section": "couriers_cod",
    },
    "employee_advance": {
        "label": "سلفة موظف",
        "entity_type": "employee",
        "sub_account": "advance",
        "natural_side": "debit",
        "evidence_section": "payroll_obligations",
    },
    "employee_custody": {
        "label": "عهدة موظف",
        "entity_type": "employee",
        "sub_account": "custody",
        "natural_side": "debit",
        "evidence_section": "payroll_obligations",
    },
    "employee_salary_payable": {
        "label": "راتب مستحق لموظف",
        "entity_type": "employee",
        "sub_account": "salary_payable",
        "natural_side": "credit",
        "evidence_section": "payroll_obligations",
    },
    "supplier_payable": {
        "label": "مستحق لمورد",
        "entity_type": "supplier",
        "sub_account": "payable",
        "natural_side": "credit",
        "evidence_section": "suppliers",
    },
    "customer_receivable": {
        "label": "مستحق لنا على عميل / طرف",
        "entity_type": "external_person",
        "sub_account": "receivable",
        "natural_side": "debit",
        "evidence_section": "suppliers",
    },
    "inventory_asset": {
        "label": "مخزون بالتكلفة",
        "entity_type": "asset",
        "sub_account": "inventory",
        "natural_side": "debit",
        "evidence_section": "inventory",
    },
    "sales_vat_payable": {
        "label": "ضريبة مبيعات مستحقة",
        "entity_type": "tax",
        "sub_account": "sales_vat_payable",
        "natural_side": "credit",
        "evidence_section": "equity",
    },
    "input_vat": {
        "label": "ضريبة مدخلات قابلة للاسترداد",
        "entity_type": "tax",
        "sub_account": "input_vat",
        "natural_side": "debit",
        "evidence_section": "equity",
    },
    "other_receivable": {
        "label": "أصل / ذمة مدينة أخرى",
        "entity_type": "asset",
        "sub_account": "other_receivable",
        "natural_side": "debit",
        "evidence_section": "equity",
    },
    "other_payable": {
        "label": "التزام / ذمة دائنة أخرى",
        "entity_type": "liability",
        "sub_account": "other_payable",
        "natural_side": "credit",
        "evidence_section": "equity",
    },
}


def _money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "opening_balance_amount_invalid") from exc
    if result <= 0:
        raise HTTPException(400, "opening_balance_amount_must_be_positive")
    return result


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(400, "opening_cutover_timezone_required")
    return value.astimezone(timezone.utc).isoformat()


def _opposite(side: str) -> str:
    return "credit" if side == "debit" else "debit"


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _section_refs(payload: dict[str, str]) -> dict[str, str]:
    expected = {row["id"] for row in EVIDENCE_SECTIONS}
    actual = {str(key) for key in payload}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise HTTPException(400, detail={
            "code": "opening_evidence_sections_incomplete",
            "missing": missing,
            "extra": extra,
        })
    cleaned = {key: str(payload[key] or "").strip() for key in expected}
    if any(not value for value in cleaned.values()):
        raise HTTPException(400, "opening_evidence_reference_required")
    return cleaned


class OpeningLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal[
        "bank", "cash", "provider_receivable",
        "courier_cod_receivable", "courier_payable",
        "store_driver_cod_receivable", "store_driver_fee_payable",
        "employee_advance", "employee_custody", "employee_salary_payable",
        "supplier_payable", "customer_receivable", "inventory_asset",
        "sales_vat_payable", "input_vat", "other_receivable", "other_payable",
    ]
    entity_id: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=200)
    amount: Decimal
    direction: Literal["normal", "opposite"] = "normal"

    @field_validator("entity_id", "label")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value: Decimal) -> Decimal:
        return _money(value)


class OpeningPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cutover_at: datetime
    evidence_sheet_ref: str = Field(min_length=1, max_length=500)
    evidence_sections: dict[str, str]
    lines: list[OpeningLineIn] = Field(min_length=1, max_length=MAX_OPENING_LINES)
    notes: str = Field(default="", max_length=1000)

    @field_validator("evidence_sheet_ref", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class OpeningApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=1, max_length=100)
    confirmation: Literal["APPROVE_OPENING_BALANCE"]


class OpeningActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activation_ref: str = Field(min_length=3, max_length=300)
    confirmation: Literal["ACTIVATE_MZ2_P01"]

    @field_validator("activation_ref")
    @classmethod
    def strip_ref(cls, value: str) -> str:
        return value.strip()


def compile_opening_lines(lines: list[OpeningLineIn], evidence_refs: dict[str, str]) -> list[dict[str, Any]]:
    compiled: list[dict[str, Any]] = []
    covered: set[tuple[str, str, str]] = set()
    for item in lines:
        rule = CATEGORY_CATALOG[item.category]
        entity_id = item.entity_id
        if item.category == "provider_receivable" and entity_id not in PROVIDERS:
            raise HTTPException(400, detail={
                "code": "opening_provider_unsupported",
                "provider": entity_id,
            })
        key = (rule["entity_type"], entity_id, rule["sub_account"])
        if key in covered:
            raise HTTPException(409, detail={
                "code": "opening_duplicate_account",
                "account": "/".join(key),
            })
        covered.add(key)
        side = rule["natural_side"] if item.direction == "normal" else _opposite(rule["natural_side"])
        amount = _money(item.amount)
        compiled.append({
            "category": item.category,
            "label": item.label or rule["label"],
            "entity_type": rule["entity_type"],
            "entity_id": entity_id,
            "sub_account": rule["sub_account"],
            "side": side,
            "amount": str(amount),
            "evidence_ref": evidence_refs[rule["evidence_section"]],
        })

    debit = sum((Decimal(row["amount"]) for row in compiled if row["side"] == "debit"), Decimal(0))
    credit = sum((Decimal(row["amount"]) for row in compiled if row["side"] == "credit"), Decimal(0))
    difference = (debit - credit).quantize(MONEY)
    if difference:
        compiled.append({
            "category": "equity_plug",
            "label": "حقوق الملكية / موازنة الافتتاح",
            "entity_type": "equity",
            "entity_id": "opening_balance_equity",
            "sub_account": "main",
            "side": "credit" if difference > 0 else "debit",
            "amount": str(abs(difference)),
            "evidence_ref": evidence_refs["equity"],
        })
    return compiled


def opening_totals(lines: list[dict[str, Any]]) -> dict[str, Any]:
    debit = sum((Decimal(str(row["amount"])) for row in lines if row["side"] == "debit"), Decimal(0))
    credit = sum((Decimal(str(row["amount"])) for row in lines if row["side"] == "credit"), Decimal(0))
    return {
        "debit": float(debit.quantize(MONEY)),
        "credit": float(credit.quantize(MONEY)),
        "difference": float((debit - credit).quantize(MONEY)),
        "balanced": debit == credit and debit > 0,
        "line_count": len(lines),
    }


async def _cutover(db, owner: str) -> dict[str, Any]:
    row = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_financial_cutover": 1},
    )
    return dict((row or {}).get("mezan2_financial_cutover") or {})


async def _required_zero_scope(db, owner: str, compiled: list[dict[str, Any]], evidence_refs: dict[str, str]) -> list[dict[str, str]]:
    covered = {
        (row["entity_type"], str(row["entity_id"]), row.get("sub_account") or "")
        for row in compiled
    }
    zero: list[dict[str, str]] = []
    accounts = await db.accounts.find(
        {
            "user_id": owner,
            "status": {"$ne": "hidden"},
            "account_type": {"$in": ["bank", "cash", "payment_platform"]},
        },
        {"_id": 0, "id": 1, "account_type": 1},
    ).to_list(MAX_OPENING_LINES + 1)
    if len(accounts) > MAX_OPENING_LINES:
        raise HTTPException(409, "opening_account_scope_too_large")

    for account in accounts:
        account_id = str(account.get("id") or "").strip()
        if not account_id:
            raise HTTPException(409, "opening_account_identity_missing")
        key = ("bank", account_id, "main")
        if key not in covered:
            zero.append({
                "entity_type": "bank",
                "entity_id": account_id,
                "sub_account": "main",
                "evidence_ref": evidence_refs[
                    "providers" if account.get("account_type") == "payment_platform" else "banks_cash"
                ],
            })

    for provider in PROVIDERS:
        key = ("payment_gateway", provider, "receivable")
        if key not in covered:
            zero.append({
                "entity_type": "payment_gateway",
                "entity_id": provider,
                "sub_account": "receivable",
                "evidence_ref": evidence_refs["providers"],
            })

    employees = await db.operating_salaries.find(
        {
            "user_id": owner,
            "category": "employee",
            "status": {"$ne": "inactive"},
            "archived": {"$ne": True},
            "is_archived": {"$ne": True},
            "deleted": {"$ne": True},
            "is_deleted": {"$ne": True},
        },
        {"_id": 0, "id": 1, "employee_id": 1},
    ).to_list(MAX_OPENING_LINES + 1)
    if len(employees) > MAX_OPENING_LINES:
        raise HTTPException(409, "opening_employee_scope_too_large")
    for employee in employees:
        employee_id = str(employee.get("id") or employee.get("employee_id") or "").strip()
        if not employee_id:
            raise HTTPException(409, "opening_employee_identity_missing")
        for sub_account in ("advance", "custody", "salary_payable"):
            key = ("employee", employee_id, sub_account)
            if key not in covered:
                zero.append({
                    "entity_type": "employee",
                    "entity_id": employee_id,
                    "sub_account": sub_account,
                    "evidence_ref": evidence_refs["payroll_obligations"],
                })

    # P02 UAT can only use shipping counterparties whose opening state was
    # explicitly approved during P01 cutover.  Active store drivers and
    # approved MZ2 courier-rate identities are therefore part of the opening
    # zero-evidence scope even when their balance is genuinely zero.
    drivers = await db.store_drivers.find(
        {
            "user_id": owner,
            "status": {"$ne": "inactive"},
            "archived": {"$ne": True},
            "deleted": {"$ne": True},
        },
        {"_id": 0, "id": 1},
    ).to_list(MAX_OPENING_LINES + 1)
    if len(drivers) > MAX_OPENING_LINES:
        raise HTTPException(409, "opening_store_driver_scope_too_large")
    for driver in drivers:
        driver_id = str(driver.get("id") or "").strip()
        if not driver_id:
            raise HTTPException(409, "opening_store_driver_identity_missing")
        for sub_account in ("cod_receivable", "delivery_fee_payable"):
            key = ("store_driver", driver_id, sub_account)
            if key not in covered:
                zero.append({
                    "entity_type": "store_driver",
                    "entity_id": driver_id,
                    "sub_account": sub_account,
                    "evidence_ref": evidence_refs["couriers_cod"],
                })

    shipping_policy = await db.mz2_shipping_rate_policies.find_one(
        {"_id": owner, "user_id": owner},
        {"_id": 0, "versions": 1},
    ) or {}
    versions = shipping_policy.get("versions") or []
    if not isinstance(versions, list) or len(versions) > MAX_OPENING_LINES:
        raise HTTPException(409, "opening_courier_policy_scope_invalid")
    courier_ids = sorted({
        str(version.get("courier_id") or "").strip()
        for version in versions
        if isinstance(version, dict)
        and version.get("verification_status") == "approved"
        and str(version.get("courier_id") or "").strip()
    })
    for courier_id in courier_ids:
        for sub_account in ("cod_receivable", "payable"):
            key = ("courier", courier_id, sub_account)
            if key not in covered:
                zero.append({
                    "entity_type": "courier",
                    "entity_id": courier_id,
                    "sub_account": sub_account,
                    "evidence_ref": evidence_refs["couriers_cod"],
                })
    return sorted(zero, key=lambda row: (row["entity_type"], row["entity_id"], row["sub_account"]))


async def _validate_preview_entities(db, owner: str, compiled: list[dict[str, Any]]) -> None:
    account_ids = {
        row["entity_id"] for row in compiled
        if row["category"] in {"bank", "cash"}
    }
    if account_ids:
        accounts = await db.accounts.find(
            {"user_id": owner, "id": {"$in": sorted(account_ids)}, "status": {"$ne": "hidden"}},
            {"_id": 0, "id": 1, "account_type": 1},
        ).to_list(len(account_ids) + 1)
        by_id = {str(row.get("id")): str(row.get("account_type") or "") for row in accounts}
        for row in compiled:
            if row["category"] == "bank" and by_id.get(row["entity_id"]) != "bank":
                raise HTTPException(409, detail={"code": "opening_bank_account_invalid", "entity_id": row["entity_id"]})
            if row["category"] == "cash" and by_id.get(row["entity_id"]) != "cash":
                raise HTTPException(409, detail={"code": "opening_cash_account_invalid", "entity_id": row["entity_id"]})

    employees = {
        row["entity_id"] for row in compiled
        if row["entity_type"] == "employee"
    }
    for employee_id in sorted(employees):
        query = {
            "user_id": owner,
            "$or": [
                {"id": employee_id},
                {"employee_id": employee_id},
                {"external_id": employee_id},
                {"legacy_id": employee_id},
            ],
            "archived": {"$ne": True},
            "is_archived": {"$ne": True},
            "deleted": {"$ne": True},
            "is_deleted": {"$ne": True},
        }
        exists = (
            await db.operating_salaries.find_one(query, {"_id": 1})
            or await db.employees.find_one(query, {"_id": 1})
        )
        if not exists:
            raise HTTPException(409, detail={
                "code": "opening_employee_missing",
                "entity_id": employee_id,
            })


async def create_opening_preview(db, *, owner: str, actor: dict[str, Any], payload: OpeningPreviewIn) -> dict[str, Any]:
    require_accounting_permission(actor, "accounting.opening_balances.approve")
    state = await _cutover(db, owner)
    if state.get("status") == "active" or state.get("opening_balance_txn_group_id"):
        raise HTTPException(409, "opening_balance_already_committed")
    if state.get("p02_shipping_cod_enabled") is True:
        raise HTTPException(409, "p02_must_remain_locked_during_p01_opening")

    cutover_at = _iso_utc(payload.cutover_at)
    evidence_refs = _section_refs(payload.evidence_sections)
    compiled = compile_opening_lines(payload.lines, evidence_refs)
    totals = opening_totals(compiled)
    if not totals["balanced"]:
        raise HTTPException(409, "opening_preview_unbalanced")
    await _validate_preview_entities(db, owner, compiled)
    zero_scope = await _required_zero_scope(db, owner, compiled, evidence_refs)

    economic = {
        "operation_id": OPERATION_ID,
        "cutover_at": cutover_at,
        "evidence_sheet_ref": payload.evidence_sheet_ref,
        "evidence_sections": evidence_refs,
        "lines": compiled,
        "zero_scope": zero_scope,
    }
    preview_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    preview = {
        "_id": preview_id,
        "id": preview_id,
        "user_id": owner,
        "operation_id": OPERATION_ID,
        "status": "previewed",
        "cutover_at": cutover_at,
        "evidence_sheet_ref": payload.evidence_sheet_ref,
        "evidence_sections": evidence_refs,
        "lines": compiled,
        "zero_scope": zero_scope,
        "totals": totals,
        "economic_hash": _digest(economic),
        "notes": payload.notes,
        "created_by": actor["id"],
        "created_at": now,
    }
    await db.mz2_opening_balance_previews.update_many(
        {"user_id": owner, "status": "previewed"},
        {"$set": {"status": "superseded", "superseded_at": now}},
    )
    await db.mz2_opening_balance_previews.insert_one(preview)
    await db.settings.update_one(
        {"user_id": owner},
        {
            "$set": {
                "mezan2_financial_cutover.operation_id": OPERATION_ID,
                "mezan2_financial_cutover.status": "prepared",
                "mezan2_financial_cutover.cutover_at": cutover_at,
                "mezan2_financial_cutover.evidence_sheet_ref": payload.evidence_sheet_ref,
                "mezan2_financial_cutover.evidence_sections": {
                    key: {"ref": value} for key, value in evidence_refs.items()
                },
                "mezan2_financial_cutover.opening_balance_preview_id": preview_id,
                "mezan2_financial_cutover.opening_balance_preview_balanced": True,
            },
            "$unset": {
                "mezan2_financial_cutover.opening_balance_approved_at": "",
                "mezan2_financial_cutover.opening_balance_approved_by": "",
                "mezan2_financial_cutover.opening_balance_txn_group_id": "",
                "mezan2_financial_cutover.opening_balance_zero_accounts": "",
                "mezan2_financial_cutover.activation_ref": "",
                "mezan2_financial_cutover.activated_at": "",
                "mezan2_financial_cutover.activated_by": "",
            },
        },
        upsert=True,
    )
    await db.mz2_opening_balance_audit.insert_one({
        "user_id": owner,
        "action": "preview_created",
        "preview_id": preview_id,
        "actor_id": actor["id"],
        "at": now,
        "economic_hash": preview["economic_hash"],
    })
    return {key: value for key, value in preview.items() if key != "_id"}


async def approve_opening_preview(db, *, owner: str, actor: dict[str, Any], payload: OpeningApproveIn) -> dict[str, Any]:
    require_owner(actor)
    require_accounting_permission(actor, "accounting.opening_balances.approve")
    state = await _cutover(db, owner)
    if state.get("p02_shipping_cod_enabled") is True:
        raise HTTPException(409, "p02_must_remain_locked_during_p01_opening")
    if state.get("opening_balance_txn_group_id"):
        existing = await db.mz2_opening_balance_previews.find_one(
            {"_id": payload.preview_id, "user_id": owner},
            {"_id": 0},
        )
        if existing and existing.get("txn_group_id") == state.get("opening_balance_txn_group_id"):
            return {**existing, "state": "already_posted"}
        raise HTTPException(409, "opening_balance_already_committed")

    preview = await db.mz2_opening_balance_previews.find_one(
        {"_id": payload.preview_id, "user_id": owner, "status": "previewed"},
    )
    if not preview:
        raise HTTPException(404, "opening_preview_not_available")
    if state.get("opening_balance_preview_id") != payload.preview_id:
        raise HTTPException(409, "opening_preview_changed_refresh_required")
    if state.get("cutover_at") != preview.get("cutover_at"):
        raise HTTPException(409, "opening_cutover_changed_refresh_required")

    current_zero = await _required_zero_scope(
        db,
        owner,
        preview["lines"],
        preview["evidence_sections"],
    )
    if current_zero != preview.get("zero_scope"):
        raise HTTPException(409, "opening_account_scope_changed_refresh_required")

    any_mz2 = await db.general_ledger.count_documents({
        "user_id": owner,
        "metadata.operation_id": OPERATION_ID,
    })
    if any_mz2:
        raise HTTPException(409, "opening_must_precede_all_mz2_journals")

    entries = []
    for row in preview["lines"]:
        entries.append({
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "sub_account": row.get("sub_account"),
            "side": row["side"],
            "amount": float(Decimal(str(row["amount"]))),
            "entry_type": "opening_balance",
            "metadata": {
                "operation_id": OPERATION_ID,
                "source": "accounting_opening_balance_p01",
                "accounting_at": preview["cutover_at"],
                "opening_preview_id": preview["id"],
                "opening_category": row["category"],
                "opening_label": row["label"],
                "evidence_ref": row["evidence_ref"],
            },
        })

    result = await post_txn_group(
        db,
        user_id=owner,
        actor_id=actor["id"],
        actor_name=actor.get("name") or actor.get("email") or actor["id"],
        entries=entries,
        txn_type="mz2_opening_balance",
        notes="MZ2-FIN-CUTOVER-001 opening balance",
        metadata={
            "operation_id": OPERATION_ID,
            "source": "accounting_opening_balance_p01",
            "accounting_at": preview["cutover_at"],
            "opening_preview_id": preview["id"],
            "evidence_sheet_ref": preview["evidence_sheet_ref"],
        },
    )
    now = datetime.now(timezone.utc).isoformat()
    zero_accounts = [
        {
            **row,
            "opening_balance_txn_group_id": result["txn_group_id"],
            "accounting_at": preview["cutover_at"],
        }
        for row in preview["zero_scope"]
    ]
    await db.settings.update_one(
        {"user_id": owner},
        {"$set": {
            "mezan2_financial_cutover.opening_balance_approved_at": now,
            "mezan2_financial_cutover.opening_balance_approved_by": actor["id"],
            "mezan2_financial_cutover.opening_balance_txn_group_id": result["txn_group_id"],
            "mezan2_financial_cutover.opening_balance_zero_accounts": zero_accounts,
            "mezan2_financial_cutover.status": "prepared",
        }},
    )
    await db.mz2_opening_balance_previews.update_one(
        {"_id": preview["id"], "user_id": owner, "status": "previewed"},
        {"$set": {
            "status": "posted",
            "txn_group_id": result["txn_group_id"],
            "approved_by": actor["id"],
            "approved_at": now,
        }},
    )
    await db.mz2_opening_balance_audit.insert_one({
        "user_id": owner,
        "action": "opening_posted",
        "preview_id": preview["id"],
        "txn_group_id": result["txn_group_id"],
        "actor_id": actor["id"],
        "at": now,
        "economic_hash": preview["economic_hash"],
    })
    return {
        "state": "posted",
        "preview_id": preview["id"],
        "txn_group_id": result["txn_group_id"],
        "debit_total": result["debit_total"],
        "credit_total": result["credit_total"],
        "zero_accounts": zero_accounts,
    }


async def activate_p01(db, *, owner: str, actor: dict[str, Any], payload: OpeningActivateIn) -> dict[str, Any]:
    require_owner(actor)
    require_accounting_permission(actor, "accounting.opening_balances.approve")
    state = await _cutover(db, owner)
    if state.get("p02_shipping_cod_enabled") is True:
        raise HTTPException(409, "p02_must_remain_locked_during_p01_activation")
    if state.get("status") == "active":
        return {
            "state": "already_active",
            "operation_id": OPERATION_ID,
            "activated_at": state.get("activated_at"),
            "activation_ref": state.get("activation_ref"),
        }
    verified = await opening_posted_is_verified(db, user_id=owner, cutover=state)
    candidate = build_accounting_module_status(
        {**state, "status": "active"},
        opening_posted_verified=verified,
    )
    if not candidate["cutover"]["safe_active"]:
        missing = [item["id"] for item in candidate["readiness"] if not item["complete"]]
        raise HTTPException(409, detail={
            "code": "mz2_p01_activation_not_ready",
            "missing": missing,
        })
    now = datetime.now(timezone.utc).isoformat()
    await db.settings.update_one(
        {"user_id": owner},
        {"$set": {
            "mezan2_financial_cutover.status": "active",
            "mezan2_financial_cutover.activation_ref": payload.activation_ref,
            "mezan2_financial_cutover.activated_at": now,
            "mezan2_financial_cutover.activated_by": actor["id"],
        }},
    )
    await db.mz2_opening_balance_audit.insert_one({
        "user_id": owner,
        "action": "p01_activated",
        "actor_id": actor["id"],
        "at": now,
        "activation_ref": payload.activation_ref,
        "opening_balance_txn_group_id": state.get("opening_balance_txn_group_id"),
    })
    return {
        "state": "active",
        "operation_id": OPERATION_ID,
        "activated_at": now,
        "activation_ref": payload.activation_ref,
        "p02_shipping_cod_enabled": False,
    }


async def opening_state(db, *, owner: str) -> dict[str, Any]:
    state = await _cutover(db, owner)
    preview = None
    preview_id = str(state.get("opening_balance_preview_id") or "").strip()
    if preview_id:
        preview = await db.mz2_opening_balance_previews.find_one(
            {"_id": preview_id, "user_id": owner},
            {"_id": 0},
        )
    verified = await opening_posted_is_verified(db, user_id=owner, cutover=state)
    accounts = await db.accounts.find(
        {
            "user_id": owner,
            "status": {"$ne": "hidden"},
            "account_type": {"$in": ["bank", "cash"]},
        },
        {"_id": 0, "id": 1, "name": 1, "account_type": 1},
    ).sort([("account_type", 1), ("name", 1)]).to_list(MAX_OPENING_LINES)
    employees = await db.operating_salaries.find(
        {
            "user_id": owner,
            "archived": {"$ne": True},
            "is_archived": {"$ne": True},
            "deleted": {"$ne": True},
            "is_deleted": {"$ne": True},
        },
        {"_id": 0, "id": 1, "employee_id": 1, "name": 1, "status": 1},
    ).sort([("name", 1)]).to_list(MAX_OPENING_LINES)
    return {
        "operation_id": OPERATION_ID,
        "cutover": state,
        "opening_posted_verified": verified,
        "preview": preview,
        "categories": [
            {"id": key, **value}
            for key, value in CATEGORY_CATALOG.items()
        ],
        "providers": list(PROVIDERS),
        "accounts": accounts,
        "employees": [
            {
                "id": str(row.get("id") or row.get("employee_id") or ""),
                "name": row.get("name") or "",
                "status": row.get("status") or "active",
            }
            for row in employees
            if row.get("id") or row.get("employee_id")
        ],
    }


def install_opening_balance_routes(router, db, current_user) -> None:
    base = "/accounting-module/opening-balances"

    @router.get(base)
    async def get_state(user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        require_accounting_permission(actor, "accounting.opening_balances.view")
        return await opening_state(db, owner=accounting_owner_id(actor))

    @router.post(base + "/preview")
    async def preview(payload: OpeningPreviewIn, user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        owner = accounting_owner_id(actor)
        return await create_opening_preview(db, owner=owner, actor=actor, payload=payload)

    @router.post(base + "/approve")
    async def approve(payload: OpeningApproveIn, user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        owner = accounting_owner_id(actor)
        return await approve_opening_preview(db, owner=owner, actor=actor, payload=payload)

    @router.post(base + "/activate")
    async def activate(payload: OpeningActivateIn, user: dict = Depends(current_user)):
        actor = await fresh_actor(db, user)
        owner = accounting_owner_id(actor)
        return await activate_p01(db, owner=owner, actor=actor, payload=payload)
