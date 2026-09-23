"""Financial accounts and the reviewed, evidence-locked V2 opening workflow."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from accounting_atomic import atomic_owner
from accounting_ledger_v2 import (
    AccountingLedgerV2Error,
    post_opening_journal_v2,
    reverse_journal_v2,
)
from accounting_module_contract import (
    EVIDENCE_SECTIONS,
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
}
ACCOUNT_TYPES = ("bank", "cash", "ad_prepaid_wallet", "ad_payable", "overdraft")
OPENING_CATEGORIES = (
    "banks_cash", "providers", "couriers_cod", "suppliers", "payroll",
    "inventory", "equity", "customers", "tax", "other",
)
EVIDENCE_SECTION_IDS = frozenset(row["id"] for row in EVIDENCE_SECTIONS)
OPENING_PURPOSE = "opening_balance"
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024


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


class OpeningLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal[
        "banks_cash", "providers", "couriers_cod", "suppliers", "payroll",
        "inventory", "equity", "customers", "tax", "other",
    ]
    entity_type: str = Field(min_length=1, max_length=120)
    entity_id: str = Field(min_length=1, max_length=160)
    sub_account: str | None = Field(default=None, max_length=160)
    side: Literal["debit", "credit"]
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="SAR", pattern=r"^[A-Za-z]{3}$")
    sar_amount: Decimal = Field(gt=0)
    fx_rate: Decimal = Field(gt=0)
    evidence_file_id: str = Field(min_length=1, max_length=160)


class OpeningDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=160)
    cutover_at: datetime
    lines: list[OpeningLine] = Field(min_length=2, max_length=1_000)
    replaces_draft_id: str | None = Field(default=None, max_length=160)


class OpeningAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=160)
    note: str = Field(min_length=3, max_length=1_000)
    effective_at: datetime | None = None


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["transition_blocked", "v2_active"]
    expected_revision: int = Field(ge=0)
    activation_ref: str = Field(default="", max_length=160)


def _draft_content(payload: OpeningDraftCreate) -> dict[str, Any]:
    return {
        "cutover_at": payload.cutover_at.astimezone(timezone.utc).isoformat(),
        "lines": [line.model_dump(mode="json") for line in payload.lines],
    }


def _balanced(lines: list[OpeningLine]) -> tuple[Decimal, Decimal]:
    debit = sum((line.sar_amount for line in lines if line.side == "debit"), Decimal())
    credit = sum((line.sar_amount for line in lines if line.side == "credit"), Decimal())
    if debit.quantize(Decimal(".01")) != credit.quantize(Decimal(".01")):
        raise HTTPException(409, detail={"code": "opening_balance_not_balanced"})
    return debit, credit


async def _verified_evidence(
    db: Any,
    *,
    owner: str,
    file_ids: set[str],
    approval_version: int | None = None,
    approved_by: str | None = None,
    approved_at: str | None = None,
) -> list[dict[str, Any]]:
    rows = await db.mz2_opening_evidence.find(
        {"user_id": owner, "source_file_id": {"$in": sorted(file_ids)}}
    ).to_list(len(file_ids) + 1)
    by_id = {str(row.get("source_file_id")): row for row in rows}
    if set(by_id) != file_ids:
        raise HTTPException(409, detail={"code": "opening_evidence_missing_or_foreign"})
    snapshots: list[dict[str, Any]] = []
    for file_id in sorted(file_ids):
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
            or evidence.get("purpose") != OPENING_PURPOSE
            or evidence.get("section_id") not in EVIDENCE_SECTION_IDS
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
            "section_id": evidence["section_id"],
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
        "cutover_at": draft["cutover_at"],
        "lines": draft["lines"],
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
        file_ids={snapshot["source_file_id"] for snapshot in snapshots},
        approval_version=draft["version"],
        approved_by=draft["reviewed_by"],
        approved_at=draft["reviewed_at"],
    )
    if current != snapshots:
        raise HTTPException(409, detail={"code": "opening_evidence_snapshot_changed"})
    expected_hash = _canonical_hash({
        "draft_id": draft["id"],
        "approval_version": draft["version"],
        "cutover_at": draft["cutover_at"],
        "lines": draft["lines"],
        "evidence": snapshots,
    })
    if expected_hash != draft.get("approval_hash"):
        raise HTTPException(409, detail={"code": "opening_evidence_snapshot_hash_mismatch"})


def _action_hash(action: str, payload: OpeningAction) -> str:
    return _canonical_hash({"action": action, **payload.model_dump(mode="json")})


def _action_replay(draft: dict[str, Any], action: str, payload: OpeningAction) -> bool:
    stored = (draft.get("actions") or {}).get(action) or {}
    if stored.get("idempotency_key") != payload.idempotency_key:
        return False
    if stored.get("request_hash") != _action_hash(action, payload):
        raise HTTPException(409, detail={"code": "opening_action_idempotency_conflict"})
    return True


def _v2_entries(draft: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, line in enumerate(draft["lines"], start=1):
        result.append({
            "leg_key": f"opening:{index}:{line['category']}:{line['entity_type']}:{line['entity_id']}",
            "entity_type": line["entity_type"],
            "entity_id": line["entity_id"],
            "sub_account": line.get("sub_account"),
            "entry_type": "opening_balance",
            "amount": line["sar_amount"],
            "side": line["side"],
            "metadata": {
                "opening_category": line["category"],
                "source_currency": line["currency"],
                "source_amount": line["amount"],
                "fx_rate": line["fx_rate"],
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


def install_financial_account_routes(router: Any, db: Any, current_user: Any) -> None:
    base = "/accounting-module/financial-accounts"

    async def actor_for(user: dict[str, Any], permission: str) -> tuple[dict[str, Any], str]:
        actor = await fresh_actor(db, user)
        _require(actor, permission)
        return actor, _owner(actor)

    @router.get(base + "/definitions")
    async def definitions(user: dict = Depends(current_user)):
        await actor_for(user, "accounts_view")
        return {
            "account_types": list(ACCOUNT_TYPES),
            "opening_categories": list(OPENING_CATEGORIES),
            "evidence_sections": list(EVIDENCE_SECTIONS),
            "purpose": OPENING_PURPOSE,
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
        actor, owner = await actor_for(user, "accounts_manage")
        content = payload.model_dump(mode="json")
        content["currency"] = payload.currency.upper()
        request_hash = _canonical_hash(content)
        existing = await db.mz2_financial_accounts.find_one(
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
            await db.mz2_financial_accounts.insert_one(document)
        except DuplicateKeyError:
            prior = await db.mz2_financial_accounts.find_one(
                {"user_id": owner, "idempotency_key": payload.idempotency_key}
            )
            if not prior or prior.get("request_hash") != request_hash:
                raise HTTPException(409, detail={"code": "financial_account_idempotency_conflict"}) from None
            return {**_public(prior), "existing": True}
        return {**_public(document), "existing": False}

    @router.patch(base + "/accounts/{account_id}")
    async def update_account(account_id: str, payload: AccountUpdate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounts_manage")
        changes = payload.model_dump(exclude={"version"}, exclude_unset=True)
        if not changes:
            raise HTTPException(422, "financial_account_changes_required")
        changes.update({"updated_at": _now(), "updated_by": str(actor["id"])})
        row = await db.mz2_financial_accounts.find_one_and_update(
            {"user_id": owner, "id": account_id, "version": payload.version, "status": "active"},
            {"$set": changes, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not row:
            raise HTTPException(409, "financial_account_state_or_version_conflict")
        return _public(row)

    @router.delete(base + "/accounts/{account_id}")
    async def archive_account(account_id: str, payload: AccountArchive, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounts_manage")
        row = await db.mz2_financial_accounts.find_one_and_update(
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

    opening = base + "/opening-balances"

    @router.post(opening + "/evidence")
    async def upload_evidence(
        purpose: str = Form(...), section_id: str = Form(...),
        file: UploadFile = File(...), user: dict = Depends(current_user),
    ):
        actor, owner = await actor_for(user, "drafts_manage")
        if purpose != OPENING_PURPOSE or section_id not in EVIDENCE_SECTION_IDS:
            raise HTTPException(422, detail={"code": "opening_evidence_classification_invalid"})
        content = await file.read()
        if not content or len(content) > MAX_EVIDENCE_BYTES:
            raise HTTPException(422, detail={"code": "opening_evidence_size_invalid"})
        file_id = str(uuid.uuid4())
        try:
            digest = await preserve_original(db, owner, file_id, content)
        except ValueError as error:
            raise HTTPException(409, detail={"code": "opening_evidence_preserve_failed", "message": str(error)}) from error
        document = {
            "_id": f"{owner}:{file_id}", "id": file_id, "user_id": owner,
            "source_file_id": file_id, "filename": file.filename, "purpose": purpose,
            "section_id": section_id, "sha256": digest, "size": len(content),
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
        actor, owner = await actor_for(user, "drafts_manage")
        debit, credit = _balanced(payload.lines)
        await _verified_evidence(db, owner=owner, file_ids={line.evidence_file_id for line in payload.lines})
        content = _draft_content(payload)
        request_hash = _canonical_hash(content)

        async def create(scoped):
            existing_request = await scoped.mz2_opening_balance_drafts.find_one(
                {"user_id": owner, "idempotency_key": payload.idempotency_key}
            )
            if existing_request:
                if existing_request.get("request_hash") != request_hash:
                    raise HTTPException(409, detail={"code": "opening_draft_idempotency_conflict"})
                return {**_public(existing_request), "existing": True}
            active = await scoped.mz2_opening_balance_drafts.find_one(
                {"user_id": owner, "active_slot": "opening"}
            )
            draft_id = str(uuid.uuid4())
            if active:
                if active.get("id") != payload.replaces_draft_id or active.get("status") != "draft":
                    raise HTTPException(409, detail={
                        "code": "opening_draft_replacement_required",
                        "active_draft_id": active.get("id"),
                    })
                await scoped.mz2_opening_balance_drafts.update_one(
                    {"_id": active["_id"], "status": "draft"},
                    {"$set": {"status": "replaced", "replaced_by": draft_id, "updated_at": _now()}, "$unset": {"active_slot": ""}},
                )
            document = {
                "_id": f"{owner}:{draft_id}", "id": draft_id, "user_id": owner,
                "idempotency_key": payload.idempotency_key, "request_hash": request_hash,
                "cutover_at": content["cutover_at"], "lines": content["lines"],
                "evidence_file_ids": sorted({line.evidence_file_id for line in payload.lines}),
                "debit_total": str(debit.quantize(Decimal(".01"))),
                "credit_total": str(credit.quantize(Decimal(".01"))),
                "status": "draft", "active_slot": "opening", "version": 1,
                "created_at": _now(), "created_by": str(actor["id"]), "actions": {},
            }
            await scoped.mz2_opening_balance_drafts.insert_one(document)
            return {**_public(document), "existing": False}

        return await atomic_owner(db, owner, create)

    @router.post(opening + "/drafts/{draft_id}/review")
    async def review_draft(draft_id: str, payload: OpeningAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "review")

        async def review(scoped):
            draft = await scoped.mz2_opening_balance_drafts.find_one({"user_id": owner, "id": draft_id})
            if not draft:
                raise HTTPException(404, "opening_draft_not_found")
            if _action_replay(draft, "review", payload):
                return {**_public(draft), "existing": True}
            if draft.get("status") != "draft" or draft.get("version") != payload.version:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            reviewed_at = _now()
            snapshots = await _verified_evidence(
                scoped, owner=owner, file_ids=set(draft["evidence_file_ids"]),
                approval_version=payload.version + 1, approved_by=str(actor["id"]),
                approved_at=reviewed_at,
            )
            approval_hash = _approval_hash(draft, snapshots)
            row = await scoped.mz2_opening_balance_drafts.find_one_and_update(
                {"_id": draft["_id"], "status": "draft", "version": payload.version},
                {"$set": {
                    "status": "reviewed", "review_note": payload.note,
                    "reviewed_by": str(actor["id"]), "reviewed_at": reviewed_at,
                    "evidence_snapshot": snapshots, "approval_hash": approval_hash,
                    "actions.review": {
                        "idempotency_key": payload.idempotency_key,
                        "request_hash": _action_hash("review", payload),
                    }, "updated_at": reviewed_at,
                }, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            return {**_public(row), "existing": False}

        return await atomic_owner(db, owner, review)

    @router.post(opening + "/drafts/{draft_id}/post")
    async def post_draft(draft_id: str, payload: OpeningAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "post")

        async def post(scoped):
            draft = await scoped.mz2_opening_balance_drafts.find_one({"user_id": owner, "id": draft_id})
            if not draft:
                raise HTTPException(404, "opening_draft_not_found")
            if _action_replay(draft, "post", payload):
                return {**_public(draft), "existing": True}
            if draft.get("status") != "reviewed" or draft.get("version") != payload.version:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            await _assert_snapshot(scoped, owner, draft)
            await assert_open_journal_periods(scoped, owner, [{"metadata": {"accounting_at": draft["cutover_at"]}}])
            await assert_writer_allowed(scoped, owner, "v2")
            try:
                result = await post_opening_journal_v2(
                    scoped._db, user_id=owner, actor_id=str(actor["id"]),
                    actor_name=actor.get("name") or actor.get("email") or str(actor["id"]),
                    opening_operation_id=draft["id"], approved_preview_hash=draft["approval_hash"],
                    effective_at=draft["cutover_at"], entries=_v2_entries(draft),
                    mongo_session=scoped._session,
                )
            except AccountingLedgerV2Error as error:
                raise _http_from_ledger(error) from error
            posted_at = _now()
            row = await scoped.mz2_opening_balance_drafts.find_one_and_update(
                {"_id": draft["_id"], "status": "reviewed", "version": payload.version},
                {"$set": {
                    "status": "posted", "txn_group_id": result["group"]["txn_group_id"],
                    "post_note": payload.note, "posted_by": str(actor["id"]),
                    "posted_at": posted_at, "actions.post": {
                        "idempotency_key": payload.idempotency_key,
                        "request_hash": _action_hash("post", payload),
                    }, "updated_at": posted_at,
                }, "$inc": {"version": 1}, "$unset": {"active_slot": ""}},
                return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            return {**_public(row), "existing": bool(result.get("existing"))}

        return await atomic_owner(db, owner, post)

    @router.post(opening + "/drafts/{draft_id}/reverse")
    async def reverse_draft(draft_id: str, payload: OpeningAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "reverse")
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
            effective_at = payload.effective_at.astimezone(timezone.utc).isoformat()
            await assert_open_journal_periods(scoped, owner, [{"metadata": {"accounting_at": effective_at}}])
            await assert_writer_allowed(scoped, owner, "v2")
            try:
                result = await reverse_journal_v2(
                    scoped._db, user_id=owner, actor_id=str(actor["id"]),
                    actor_name=actor.get("name") or actor.get("email") or str(actor["id"]),
                    original_txn_group_id=draft["txn_group_id"], effective_at=effective_at,
                    reason=payload.note, mongo_session=scoped._session,
                )
            except AccountingLedgerV2Error as error:
                raise _http_from_ledger(error) from error
            reversed_at = _now()
            row = await scoped.mz2_opening_balance_drafts.find_one_and_update(
                {"_id": draft["_id"], "status": "posted", "version": payload.version},
                {"$set": {
                    "status": "reversed", "reversal_txn_group_id": result["group"]["txn_group_id"],
                    "reverse_note": payload.note, "reversed_by": str(actor["id"]),
                    "reversed_at": reversed_at, "actions.reverse": {
                        "idempotency_key": payload.idempotency_key,
                        "request_hash": _action_hash("reverse", payload),
                    }, "updated_at": reversed_at,
                }, "$inc": {"version": 1}},
                return_document=ReturnDocument.AFTER,
            )
            if not row:
                raise HTTPException(409, "opening_draft_state_or_version_conflict")
            return {**_public(row), "existing": bool(result.get("existing"))}

        return await atomic_owner(db, owner, reverse)

    @router.get(base + "/transition")
    async def get_transition(user: dict = Depends(current_user)):
        _, owner = await actor_for(user, "accounts_view")
        return await transition_state(db, owner)

    @router.post(base + "/transition")
    async def set_transition(payload: TransitionRequest, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "accounts_manage")

        async def advance(scoped):
            if payload.target == "v2_active":
                pending = await scoped.mz2_opening_balance_drafts.find_one(
                    {"user_id": owner, "status": "draft", "active_slot": "opening"}
                )
                if pending:
                    raise HTTPException(409, detail={"code": "opening_balance_review_required"})
            return await advance_transition(
                scoped, owner=owner, actor_id=str(actor["id"]), target=payload.target,
                expected_revision=payload.expected_revision, activation_ref=payload.activation_ref,
            )

        return await atomic_owner(db, owner, advance)
