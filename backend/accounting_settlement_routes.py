"""Unified P01 settlement routes for MZ2-FIN-CUTOVER-001.

This module owns the draft -> review -> post lifecycle. It reuses the verified
statement parsers, never reads legacy balances, and posts one idempotent ledger
group only after independent accounting permission checks.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from accounting_module_contract import (
    OPERATION_ID,
    accounting_owner_id,
    accounting_permissions_for_user,
    require_accounting_permission,
)
from accounting_module_status_routes import fresh_accounting_user
from accounting_settlement_service import (
    PROVIDERS,
    PROVIDER_LABELS,
    amounts_from_settlement_file,
    build_journal_preview,
    build_review_reasons,
    calculate_settlement_totals,
    canonical_provider,
    has_blocking_reasons,
    normalize_amounts,
    period_from_file,
    post_reviewed_settlement,
    provider_label,
    settlement_idempotency_key,
    statement_reference_from_file,
)
from excel_upload_security import read_safe_xlsx_upload
from ledger_core import write_audit
from settlements_import.service import _apply_entries, import_file

MAX_FILE_BYTES = 10 * 1024 * 1024
DRAFT_EDITABLE_STATUSES = {"draft", "needs_review", "rejected"}
DRAFT_STATUSES = {
    "draft",
    "needs_review",
    "ready_for_review",
    "reviewed",
    "posting",
    "posted",
    "rejected",
}
BINDING_SOURCE_KINDS = {
    "provider_statement",
    "provider_invoice",
    "owner_confirmed",
    "legacy_copy",
}
_INDEXED_DATABASES: set[int] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _public(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if not document:
        return document
    out = dict(document)
    out.pop("_id", None)
    return out


class ProviderBankBindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_account_id: str = Field(min_length=1, max_length=120)
    source_kind: Literal[
        "provider_statement",
        "provider_invoice",
        "owner_confirmed",
        "legacy_copy",
    ] = "owner_confirmed"
    confirmed: bool = False
    evidence_ref: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=1000)


class DraftFromFileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_file_id: str = Field(min_length=1, max_length=120)
    bank_account_id: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=1000)


class DraftPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_account_id: Optional[str] = Field(default=None, max_length=120)
    statement_reference: Optional[str] = Field(default=None, max_length=180)
    statement_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_from: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_to: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    amounts: Optional[dict[str, Any]] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    manual_override_reason: Optional[str] = Field(default=None, max_length=1000)
    source_review_acknowledged: Optional[bool] = None


class ManualMatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settlement_entry_id: str = Field(min_length=1, max_length=120)
    order_number: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=3, max_length=1000)


class DraftActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: Optional[str] = Field(default=None, max_length=1000)


class DraftRejectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=1000)


async def ensure_accounting_settlement_indexes(db) -> None:
    key = id(db)
    if key in _INDEXED_DATABASES:
        return
    try:
        await db.accounting_provider_bank_bindings_v2.create_index(
            [("user_id", 1), ("provider", 1)],
            unique=True,
            name="uniq_accounting_provider_bank_v2",
        )
        await db.accounting_settlements_v2.create_index(
            [("user_id", 1), ("idempotency_key", 1)],
            unique=True,
            name="uniq_accounting_settlement_draft_v2",
        )
        await db.accounting_settlements_v2.create_index(
            [("user_id", 1), ("status", 1), ("updated_at", -1)],
            name="accounting_settlement_status_v2",
        )
    except Exception:
        # Route registration tests use lightweight objects. Real Mongo retries
        # on the first write if index creation was temporarily unavailable.
        return
    _INDEXED_DATABASES.add(key)


async def _scope(db, user: dict[str, Any], permission: str) -> tuple[dict[str, Any], str]:
    fresh = await fresh_accounting_user(db, user)
    require_accounting_permission(fresh, permission)
    owner_id = accounting_owner_id(fresh)
    if not owner_id:
        raise HTTPException(403, "لا يوجد مالك بيانات محاسبية مرتبط بالمستخدم")
    return fresh, owner_id


async def _find_bank(db, owner_id: str, bank_id: str) -> dict[str, Any] | None:
    if not _clean(bank_id):
        return None
    return await db.accounts.find_one(
        {
            "user_id": owner_id,
            "id": _clean(bank_id),
            "account_type": {"$in": ["bank", "cash"]},
        },
        {"_id": 0, "id": 1, "name": 1, "account_type": 1},
    )


def _settings_bank_key(provider: str) -> str:
    provider = canonical_provider(provider)
    suffix = "imkan" if provider == "emkan" else provider
    return f"default_bank_for_{suffix}"


async def _binding_view(db, owner_id: str, provider: str) -> dict[str, Any]:
    provider = canonical_provider(provider)
    doc = await db.accounting_provider_bank_bindings_v2.find_one(
        {"user_id": owner_id, "provider": provider},
        {"_id": 0},
    )
    if doc:
        bank = await _find_bank(db, owner_id, doc.get("bank_account_id"))
        return {
            **doc,
            "provider_label": provider_label(provider),
            "configured": bool(bank),
            "bank_account_name": (bank or {}).get("name"),
            "bank_account_type": (bank or {}).get("account_type"),
            "needs_confirmation": doc.get("verification_status") != "verified",
        }

    settings_key = _settings_bank_key(provider)
    settings = await db.settings.find_one(
        {"user_id": owner_id},
        {"_id": 0, settings_key: 1},
    ) or {}
    bank = await _find_bank(db, owner_id, settings.get(settings_key))
    if bank:
        return {
            "provider": provider,
            "provider_label": provider_label(provider),
            "bank_account_id": bank["id"],
            "bank_account_name": bank.get("name"),
            "bank_account_type": bank.get("account_type"),
            "source_kind": "legacy_copy",
            "verification_status": "unverified",
            "configured": True,
            "needs_confirmation": True,
            "evidence_ref": None,
            "notes": "منسوخ من ربط الإعدادات السابق؛ يلزم تأكيده داخل المحاسبة",
        }
    return {
        "provider": provider,
        "provider_label": provider_label(provider),
        "bank_account_id": None,
        "bank_account_name": None,
        "bank_account_type": None,
        "source_kind": None,
        "verification_status": "missing",
        "configured": False,
        "needs_confirmation": True,
        "evidence_ref": None,
        "notes": None,
    }


async def _verified_binding_bank_id(db, owner_id: str, provider: str) -> str | None:
    view = await _binding_view(db, owner_id, provider)
    if view.get("verification_status") != "verified":
        return None
    return _clean(view.get("bank_account_id")) or None


async def _file_or_404(db, owner_id: str, file_id: str) -> dict[str, Any]:
    doc = await db.settlement_files.find_one(
        {"id": file_id, "user_id": owner_id},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(404, "ملف التسوية غير موجود")
    return doc


async def _draft_or_404(db, owner_id: str, draft_id: str) -> dict[str, Any]:
    doc = await db.accounting_settlements_v2.find_one(
        {"id": draft_id, "user_id": owner_id},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(404, "مسودة التسوية غير موجودة")
    return doc


async def _source_review_count(db, owner_id: str, file_id: str) -> int:
    return int(await db.settlement_entries.count_documents({
        "user_id": owner_id,
        "file_id": file_id,
        "review_required": True,
        "review_resolved_at": {"$exists": False},
    }))


async def _unmatched_entries(db, owner_id: str, file_id: str) -> list[dict[str, Any]]:
    return await db.settlement_entries.find(
        {
            "user_id": owner_id,
            "file_id": file_id,
            "matched": {"$ne": True},
        },
        {
            "_id": 0,
            "id": 1,
            "order_number": 1,
            "provider_order_id": 1,
            "event_type": 1,
            "actual_gross_amount": 1,
            "actual_net_amount": 1,
            "settlement_reference": 1,
        },
    ).sort("created_at", 1).to_list(200)


async def _bank_snapshot_for_preview(
    db, owner_id: str, bank_account_id: str | None
) -> dict[str, Any] | None:
    bank = await _find_bank(db, owner_id, _clean(bank_account_id))
    return _public(bank)


def _review_file_view(draft: dict[str, Any]) -> dict[str, Any]:
    snapshot = draft.get("source_snapshot") or {}
    header = dict(snapshot.get("header") or {})
    if draft.get("statement_reference"):
        header["statement_id"] = draft["statement_reference"]
    return {
        "header": header,
        "totals": snapshot.get("totals") or draft.get("amounts") or {},
        "matched": snapshot.get("matched") or 0,
        "unmatched": snapshot.get("unmatched") or 0,
    }


async def _recomputed_draft(
    db,
    *,
    owner_id: str,
    draft: dict[str, Any],
) -> dict[str, Any]:
    bank = await _bank_snapshot_for_preview(
        db, owner_id, draft.get("bank_account_id")
    )
    reasons = build_review_reasons(
        file_doc=_review_file_view(draft),
        amounts=draft.get("amounts") or {},
        bank_account_id=(bank or {}).get("id"),
        source_review_count=int(draft.get("source_review_count") or 0),
    )
    calculation = calculate_settlement_totals(draft.get("amounts") or {})
    preview = None
    try:
        if bank:
            preview = build_journal_preview(
                provider=draft["provider"],
                bank_account_id=bank["id"],
                bank_account_name=bank.get("name") or "",
                amounts=draft.get("amounts") or {},
            )
    except ValueError:
        preview = None
    return {
        **draft,
        "bank_account_id": (bank or {}).get("id"),
        "bank_account_name": (bank or {}).get("name"),
        "bank_account_type": (bank or {}).get("account_type"),
        "review_reasons": reasons,
        "calculation": calculation,
        "journal_preview": preview,
    }


async def _create_draft_from_file(
    db,
    *,
    owner_id: str,
    actor: dict[str, Any],
    file_doc: dict[str, Any],
    bank_account_id: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    await ensure_accounting_settlement_indexes(db)
    provider = canonical_provider(file_doc.get("provider"))
    statement_reference = statement_reference_from_file(file_doc)
    source_hash = _clean(file_doc.get("file_hash"))
    idempotency_key = settlement_idempotency_key(
        user_id=owner_id,
        provider=provider,
        statement_reference=statement_reference or f"missing:{file_doc['id']}",
        source_hash=source_hash,
    )
    existing = await db.accounting_settlements_v2.find_one(
        {"user_id": owner_id, "idempotency_key": idempotency_key},
        {"_id": 0},
    )
    if existing:
        return {**existing, "duplicate": True}

    selected_bank_id = _clean(bank_account_id) or await _verified_binding_bank_id(
        db, owner_id, provider
    )
    if selected_bank_id and not await _find_bank(db, owner_id, selected_bank_id):
        raise HTTPException(400, "البنك المختار غير موجود أو لا يتبع المتجر")

    period_from, period_to, statement_date = period_from_file(file_doc)
    amounts = amounts_from_settlement_file(file_doc)
    source_review_count = await _source_review_count(
        db, owner_id, file_doc["id"]
    )
    now = _now()
    draft = {
        "id": str(uuid.uuid4()),
        "user_id": owner_id,
        "operation_id": OPERATION_ID,
        "provider": provider,
        "provider_label": provider_label(provider),
        "status": "draft",
        "version": 1,
        "statement_reference": statement_reference,
        "statement_date": statement_date,
        "period_from": period_from,
        "period_to": period_to,
        "source_kind": "provider_statement",
        "source_file_id": file_doc["id"],
        "source_file_hash": source_hash,
        "source_review_count": source_review_count,
        "source_snapshot": {
            "filename": file_doc.get("filename") or file_doc.get("file_name"),
            "file_hash": source_hash,
            "header": file_doc.get("header") or {},
            "totals": file_doc.get("totals") or {},
            "matched": int(file_doc.get("matched") or 0),
            "unmatched": int(file_doc.get("unmatched") or 0),
            "unmatched_orders": file_doc.get("unmatched_orders") or [],
            "unmatched_entries": await _unmatched_entries(
                db, owner_id, file_doc["id"]
            ),
            "uploaded_at": file_doc.get("uploaded_at"),
        },
        "bank_account_id": selected_bank_id,
        "amounts": amounts,
        "notes": notes or "",
        "manual_override_reason": None,
        "review_reasons": [],
        "calculation": {},
        "journal_preview": None,
        "idempotency_key": idempotency_key,
        "created_by": actor.get("id"),
        "created_by_name": actor.get("name") or actor.get("email"),
        "created_at": now,
        "updated_by": actor.get("id"),
        "updated_at": now,
        "submitted_at": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "posted_by": None,
        "posted_at": None,
        "ledger_txn_group_id": None,
        "bank_snapshot": None,
        "revision_log": [],
    }
    draft = await _recomputed_draft(db, owner_id=owner_id, draft=draft)
    draft["status"] = (
        "needs_review" if has_blocking_reasons(draft["review_reasons"])
        else "draft"
    )
    await db.accounting_settlements_v2.insert_one(dict(draft))
    await write_audit(
        db,
        user_id=owner_id,
        actor_id=str(actor.get("id") or ""),
        actor_name=actor.get("name") or actor.get("email") or "",
        entity_type="payment_gateway",
        entity_id=provider,
        action="create_settlement_draft",
        notes=statement_reference,
        after_state={
            "draft_id": draft["id"],
            "source_file_id": file_doc["id"],
            "status": draft["status"],
            "review_reasons": draft["review_reasons"],
        },
    )
    return _public(draft)


def install_accounting_settlement_routes(router, db, current_user):
    @router.get("/accounting-module/settlements/context")
    async def settlement_context(user: dict = Depends(current_user)):
        actor, owner_id = await _scope(
            db, user, "accounting.settlements.view"
        )
        banks = await db.accounts.find(
            {
                "user_id": owner_id,
                "account_type": {"$in": ["bank", "cash"]},
            },
            {"_id": 0, "id": 1, "name": 1, "account_type": 1},
        ).sort("name", 1).to_list(500)
        bindings = [
            await _binding_view(db, owner_id, provider)
            for provider in PROVIDERS
        ]
        pipeline = [
            {"$match": {"user_id": owner_id}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        counts: dict[str, int] = {}
        async for row in db.accounting_settlements_v2.aggregate(pipeline):
            counts[str(row.get("_id") or "unknown")] = int(row.get("count") or 0)
        permissions = accounting_permissions_for_user(actor)
        return {
            "operation_id": OPERATION_ID,
            "providers": [
                {"id": key, "label": PROVIDER_LABELS[key]}
                for key in PROVIDERS
            ],
            "banks": banks,
            "bindings": bindings,
            "status_counts": counts,
            "permissions": permissions,
            "can_create_draft": "accounting.drafts.create" in permissions,
            "can_post": "accounting.settlements.post" in permissions,
            "can_manage_rules": "accounting.rules.manage" in permissions,
            "legacy_financial_data_included": False,
        }

    @router.put("/accounting-module/settlements/bindings/{provider}")
    async def save_provider_binding(
        provider: str,
        payload: ProviderBankBindingIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.rules.manage"
        )
        provider = canonical_provider(provider)
        bank = await _find_bank(db, owner_id, payload.bank_account_id)
        if not bank:
            raise HTTPException(400, "الحساب البنكي غير موجود أو لا يتبع المتجر")
        if not payload.confirmed:
            raise HTTPException(
                400,
                "يجب تأكيد أن هذا هو البنك الحالي للتسويات قبل الحفظ",
            )
        await ensure_accounting_settlement_indexes(db)
        before = await db.accounting_provider_bank_bindings_v2.find_one(
            {"user_id": owner_id, "provider": provider},
            {"_id": 0},
        )
        now = _now()
        doc = {
            "user_id": owner_id,
            "provider": provider,
            "provider_label": provider_label(provider),
            "bank_account_id": bank["id"],
            "bank_account_name": bank.get("name") or "",
            "bank_account_type": bank.get("account_type"),
            "source_kind": payload.source_kind,
            "verification_status": "verified",
            "evidence_ref": _clean(payload.evidence_ref) or None,
            "notes": payload.notes or "",
            "approved_by": actor.get("id"),
            "approved_by_name": actor.get("name") or actor.get("email"),
            "approved_at": now,
            "updated_at": now,
        }
        await db.accounting_provider_bank_bindings_v2.update_one(
            {"user_id": owner_id, "provider": provider},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        # Keep the existing settlement engine compatible. This is a copy into
        # Mezan 2 settings, never a dynamic read from legacy Mezan.
        await db.settings.update_one(
            {"user_id": owner_id},
            {"$set": {
                _settings_bank_key(provider): bank["id"],
                "updated_at": now,
            }},
            upsert=True,
        )
        await write_audit(
            db,
            user_id=owner_id,
            actor_id=str(actor.get("id") or ""),
            actor_name=actor.get("name") or actor.get("email") or "",
            entity_type="payment_gateway",
            entity_id=provider,
            action="update_provider_settlement_bank",
            notes=payload.notes or "",
            before_state=before,
            after_state=doc,
        )
        return await _binding_view(db, owner_id, provider)

    @router.post("/accounting-module/settlements/drafts/upload")
    async def upload_settlement_draft(
        file: UploadFile = File(...),
        provider: str = Form(...),
        statement_date: Optional[str] = Form(default=None),
        bank_account_id: Optional[str] = Form(default=None),
        notes: Optional[str] = Form(default=None),
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.drafts.create"
        )
        provider = canonical_provider(provider)
        content = await read_safe_xlsx_upload(file, max_bytes=MAX_FILE_BYTES)
        try:
            imported = await import_file(
                db,
                owner_id,
                filename=file.filename or "settlement.xlsx",
                content=content,
                provider_hint=provider,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        file_id = imported.get("file_id")
        if not file_id:
            raise HTTPException(500, "لم يرجع مستورد التسويات مرجعًا للملف")
        if statement_date:
            value = _clean(statement_date)[:10]
            if not re_full_date(value):
                raise HTTPException(400, "صيغة تاريخ الكشف يجب أن تكون YYYY-MM-DD")
            await db.settlement_files.update_one(
                {"id": file_id, "user_id": owner_id},
                {"$set": {"header.settlement_date": value}},
            )
        file_doc = await _file_or_404(db, owner_id, file_id)
        draft = await _create_draft_from_file(
            db,
            owner_id=owner_id,
            actor=actor,
            file_doc=file_doc,
            bank_account_id=bank_account_id,
            notes=notes,
        )
        return {"import": imported, "draft": draft}

    @router.post("/accounting-module/settlements/drafts/from-file")
    async def draft_from_existing_file(
        payload: DraftFromFileIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.drafts.create"
        )
        file_doc = await _file_or_404(
            db, owner_id, payload.settlement_file_id
        )
        return await _create_draft_from_file(
            db,
            owner_id=owner_id,
            actor=actor,
            file_doc=file_doc,
            bank_account_id=payload.bank_account_id,
            notes=payload.notes,
        )

    @router.get("/accounting-module/settlements/drafts")
    async def list_settlement_drafts(
        status: Optional[str] = Query(default=None),
        provider: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        user: dict = Depends(current_user),
    ):
        _actor, owner_id = await _scope(
            db, user, "accounting.settlements.view"
        )
        query: dict[str, Any] = {"user_id": owner_id}
        if status:
            statuses = [_clean(item) for item in status.split(",") if _clean(item)]
            invalid = [item for item in statuses if item not in DRAFT_STATUSES]
            if invalid:
                raise HTTPException(400, f"حالات غير معروفة: {', '.join(invalid)}")
            query["status"] = {"$in": statuses}
        if provider:
            query["provider"] = canonical_provider(provider)
        docs = await db.accounting_settlements_v2.find(
            query, {"_id": 0}
        ).sort("updated_at", -1).to_list(limit)
        return {"items": docs, "count": len(docs)}

    @router.get("/accounting-module/settlements/drafts/{draft_id}")
    async def get_settlement_draft(
        draft_id: str,
        user: dict = Depends(current_user),
    ):
        _actor, owner_id = await _scope(
            db, user, "accounting.settlements.view"
        )
        return await _draft_or_404(db, owner_id, draft_id)

    @router.patch("/accounting-module/settlements/drafts/{draft_id}")
    async def update_settlement_draft(
        draft_id: str,
        payload: DraftPatchIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.drafts.create"
        )
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") not in DRAFT_EDITABLE_STATUSES:
            raise HTTPException(409, "لا يمكن تعديل المسودة بعد إرسالها للمراجعة")
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(400, "لا يوجد تعديل")
        if "amounts" in changes:
            if not _clean(payload.manual_override_reason):
                raise HTTPException(
                    400,
                    "سبب التعديل اليدوي إلزامي عند تغيير مبالغ الكشف",
                )
            changes["amounts"] = normalize_amounts(changes["amounts"])
        if changes.get("source_review_acknowledged") is True:
            if not _clean(payload.manual_override_reason):
                raise HTTPException(
                    400,
                    "سبب معالجة بند المصدر إلزامي",
                )
            changes["source_review_count"] = 0
            changes["source_review_resolution"] = {
                "resolved_by": actor.get("id"),
                "resolved_by_name": actor.get("name") or actor.get("email"),
                "resolved_at": _now(),
                "reason": payload.manual_override_reason,
            }
            await db.settlement_entries.update_many(
                {
                    "user_id": owner_id,
                    "file_id": current.get("source_file_id"),
                    "review_required": True,
                    "review_resolved_at": {"$exists": False},
                },
                {"$set": {
                    "review_resolved_at": _now(),
                    "review_resolved_by": actor.get("id"),
                    "review_resolution_reason": payload.manual_override_reason,
                }},
            )
        if "bank_account_id" in changes:
            bank_id = _clean(changes["bank_account_id"])
            if bank_id and not await _find_bank(db, owner_id, bank_id):
                raise HTTPException(400, "البنك المختار غير موجود أو لا يتبع المتجر")
            changes["bank_account_id"] = bank_id or None
        changes["statement_reference"] = (
            _clean(changes["statement_reference"])
            if "statement_reference" in changes else current.get("statement_reference")
        )
        before = {
            key: current.get(key)
            for key in (
                "bank_account_id",
                "statement_reference",
                "statement_date",
                "period_from",
                "period_to",
                "amounts",
                "notes",
                "status",
            )
        }
        next_doc = {**current, **changes}
        next_doc["version"] = int(current.get("version") or 1) + 1
        next_doc["updated_by"] = actor.get("id")
        next_doc["updated_at"] = _now()
        next_doc = await _recomputed_draft(
            db, owner_id=owner_id, draft=next_doc
        )
        next_doc["status"] = (
            "needs_review" if has_blocking_reasons(next_doc["review_reasons"])
            else "draft"
        )
        revision = {
            "version": next_doc["version"],
            "actor_id": actor.get("id"),
            "actor_name": actor.get("name") or actor.get("email"),
            "at": next_doc["updated_at"],
            "reason": payload.manual_override_reason or "draft_update",
            "before": before,
            "after": {
                key: next_doc.get(key)
                for key in before
            },
        }
        next_doc.setdefault("revision_log", list(current.get("revision_log") or []))
        next_doc["revision_log"].append(revision)
        replace = dict(next_doc)
        replace.pop("_id", None)
        await db.accounting_settlements_v2.replace_one(
            {"id": draft_id, "user_id": owner_id},
            replace,
        )
        return replace

    @router.post("/accounting-module/settlements/drafts/{draft_id}/match-entry")
    async def match_settlement_entry(
        draft_id: str,
        payload: ManualMatchIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.drafts.create"
        )
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") not in DRAFT_EDITABLE_STATUSES:
            raise HTTPException(409, "لا يمكن تعديل المطابقة بعد إرسال المسودة")
        entry = await db.settlement_entries.find_one(
            {
                "id": payload.settlement_entry_id,
                "user_id": owner_id,
                "file_id": current.get("source_file_id"),
            },
            {"_id": 0},
        )
        if not entry:
            raise HTTPException(404, "سطر التسوية غير موجود")
        target = await db.unified_orders.find_one(
            {
                "user_id": owner_id,
                "order_number": _clean(payload.order_number),
            },
            {"_id": 0, "order_number": 1},
        )
        if not target:
            raise HTTPException(404, "رقم الطلب غير موجود في بيانات سلة")
        original_reference = entry.get("order_number")
        patched_entry = {
            **entry,
            "order_number": target["order_number"],
        }
        match_result = await _apply_entries(
            db,
            owner_id,
            current["provider"],
            [patched_entry],
            file_id=current["source_file_id"],
        )
        if int(match_result.get("matched") or 0) != 1:
            raise HTTPException(409, "تعذر تطبيق المطابقة على الطلب المحدد")
        now = _now()
        await db.settlement_entries.update_one(
            {"id": payload.settlement_entry_id, "user_id": owner_id},
            {"$set": {
                "original_order_number": original_reference,
                "order_number": target["order_number"],
                "matched": True,
                "manual_match": True,
                "manual_match_reason": payload.reason,
                "manual_matched_by": actor.get("id"),
                "manual_matched_at": now,
            }},
        )
        unmatched_entries = await _unmatched_entries(
            db, owner_id, current["source_file_id"]
        )
        total_rows = await db.settlement_entries.count_documents({
            "user_id": owner_id,
            "file_id": current["source_file_id"],
        })
        unmatched_count = len(unmatched_entries)
        unmatched_orders = [
            str(item.get("provider_order_id") or item.get("order_number") or "")
            for item in unmatched_entries
        ]
        await db.settlement_files.update_one(
            {"id": current["source_file_id"], "user_id": owner_id},
            {"$set": {
                "matched": max(int(total_rows) - unmatched_count, 0),
                "unmatched": unmatched_count,
                "unmatched_orders": unmatched_orders,
            }},
        )
        next_doc = {
            **current,
            "source_snapshot": {
                **(current.get("source_snapshot") or {}),
                "matched": max(int(total_rows) - unmatched_count, 0),
                "unmatched": unmatched_count,
                "unmatched_orders": unmatched_orders,
                "unmatched_entries": unmatched_entries,
            },
            "updated_by": actor.get("id"),
            "updated_at": now,
            "version": int(current.get("version") or 1) + 1,
        }
        next_doc = await _recomputed_draft(
            db, owner_id=owner_id, draft=next_doc
        )
        next_doc["status"] = (
            "needs_review" if has_blocking_reasons(next_doc["review_reasons"])
            else "draft"
        )
        replace = dict(next_doc)
        replace.pop("_id", None)
        await db.accounting_settlements_v2.replace_one(
            {"id": draft_id, "user_id": owner_id},
            replace,
        )
        await write_audit(
            db,
            user_id=owner_id,
            actor_id=str(actor.get("id") or ""),
            actor_name=actor.get("name") or actor.get("email") or "",
            entity_type="payment_gateway",
            entity_id=current["provider"],
            action="manual_match_settlement_entry",
            notes=payload.reason,
            before_state={
                "entry_id": entry.get("id"),
                "order_number": original_reference,
            },
            after_state={
                "entry_id": entry.get("id"),
                "order_number": target["order_number"],
                "remaining_unmatched": unmatched_count,
            },
        )
        return replace

    @router.post("/accounting-module/settlements/drafts/{draft_id}/submit")
    async def submit_settlement_draft(
        draft_id: str,
        payload: DraftActionIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.drafts.create"
        )
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") not in DRAFT_EDITABLE_STATUSES:
            raise HTTPException(409, "المسودة ليست في حالة قابلة للإرسال")
        current = await _recomputed_draft(
            db, owner_id=owner_id, draft=current
        )
        if has_blocking_reasons(current["review_reasons"]):
            raise HTTPException(
                409,
                {
                    "code": "settlement_review_reasons_open",
                    "reasons": current["review_reasons"],
                },
            )
        now = _now()
        update = {
            "status": "ready_for_review",
            "submitted_by": actor.get("id"),
            "submitted_by_name": actor.get("name") or actor.get("email"),
            "submitted_at": now,
            "submission_notes": payload.notes or "",
            "review_reasons": [],
            "calculation": current["calculation"],
            "journal_preview": current["journal_preview"],
            "bank_account_id": current.get("bank_account_id"),
            "bank_account_name": current.get("bank_account_name"),
            "bank_account_type": current.get("bank_account_type"),
            "updated_at": now,
        }
        await db.accounting_settlements_v2.update_one(
            {"id": draft_id, "user_id": owner_id},
            {"$set": update},
        )
        return {**current, **update}

    @router.post("/accounting-module/settlements/drafts/{draft_id}/review")
    async def review_settlement_draft(
        draft_id: str,
        payload: DraftActionIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.settlements.post"
        )
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") != "ready_for_review":
            raise HTTPException(409, "المسودة ليست جاهزة للمراجعة")
        current = await _recomputed_draft(
            db, owner_id=owner_id, draft=current
        )
        if has_blocking_reasons(current["review_reasons"]):
            raise HTTPException(409, "ظهرت أسباب مراجعة جديدة؛ أعد المسودة للمعالجة")
        now = _now()
        update = {
            "status": "reviewed",
            "reviewed_by": actor.get("id"),
            "reviewed_by_name": actor.get("name") or actor.get("email"),
            "reviewed_at": now,
            "review_notes": payload.notes or "",
            "review_reasons": [],
            "calculation": current["calculation"],
            "journal_preview": current["journal_preview"],
            "updated_at": now,
        }
        await db.accounting_settlements_v2.update_one(
            {"id": draft_id, "user_id": owner_id, "status": "ready_for_review"},
            {"$set": update},
        )
        return {**current, **update}

    @router.post("/accounting-module/settlements/drafts/{draft_id}/reject")
    async def reject_settlement_draft(
        draft_id: str,
        payload: DraftRejectIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.settlements.post"
        )
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") not in {"ready_for_review", "reviewed"}:
            raise HTTPException(409, "لا يمكن رفض السجل في حالته الحالية")
        now = _now()
        update = {
            "status": "rejected",
            "rejected_by": actor.get("id"),
            "rejected_by_name": actor.get("name") or actor.get("email"),
            "rejected_at": now,
            "rejection_reason": payload.reason,
            "updated_at": now,
        }
        await db.accounting_settlements_v2.update_one(
            {"id": draft_id, "user_id": owner_id},
            {"$set": update},
        )
        return {**current, **update}

    @router.post("/accounting-module/settlements/drafts/{draft_id}/post")
    async def post_settlement_draft(
        draft_id: str,
        payload: DraftActionIn,
        user: dict = Depends(current_user),
    ):
        actor, owner_id = await _scope(
            db, user, "accounting.settlements.post"
        )
        current = await _draft_or_404(db, owner_id, draft_id)
        if current.get("status") != "reviewed":
            raise HTTPException(409, "يجب مراجعة التسوية قبل ترحيلها")
        claimed = await db.accounting_settlements_v2.update_one(
            {"id": draft_id, "user_id": owner_id, "status": "reviewed"},
            {"$set": {
                "status": "posting",
                "posting_by": actor.get("id"),
                "posting_at": _now(),
            }},
        )
        if getattr(claimed, "matched_count", 0) != 1:
            raise HTTPException(409, "بدأ مستخدم آخر ترحيل هذه التسوية")
        try:
            result = await post_reviewed_settlement(
                db,
                owner_id=owner_id,
                actor=actor,
                draft={**current, "status": "reviewed"},
            )
        except Exception as exc:
            await db.accounting_settlements_v2.update_one(
                {"id": draft_id, "user_id": owner_id, "status": "posting"},
                {"$set": {
                    "status": "reviewed",
                    "last_post_error": str(getattr(exc, "detail", exc))[:1000],
                    "updated_at": _now(),
                }},
            )
            raise
        now = _now()
        update = {
            "status": "posted",
            "posted_by": actor.get("id"),
            "posted_by_name": actor.get("name") or actor.get("email"),
            "posted_at": now,
            "post_notes": payload.notes or "",
            "ledger_txn_group_id": result["txn_group_id"],
            "bank_snapshot": result["bank_snapshot"],
            "posted_preview": result["preview"],
            "updated_at": now,
            "last_post_error": None,
        }
        await db.accounting_settlements_v2.update_one(
            {"id": draft_id, "user_id": owner_id, "status": "posting"},
            {"$set": update},
        )
        return {**current, **update, "ledger": result}

    return router


def re_full_date(value: str) -> bool:
    if len(value) != 10:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


__all__ = [
    "BINDING_SOURCE_KINDS",
    "DRAFT_STATUSES",
    "ProviderBankBindingIn",
    "DraftFromFileIn",
    "DraftPatchIn",
    "ManualMatchIn",
    "ensure_accounting_settlement_indexes",
    "install_accounting_settlement_routes",
]
