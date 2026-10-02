"""Explicit, owner-confirmed supplier debit identities. No legacy identity reads."""
from datetime import datetime, timezone
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from supplier_debit_setup_atomic import supplier_debit_setup_atomic_owner, SupplierDebitSetupDatabase
from accounting_ledger_v2 import verify_active_opening_v2, get_journal_v2
from accounting_module_contract import accounting_owner_id, require_owner
from accounting_write_control import fresh_actor

MAPPINGS = "mz2_supplier_debit_mappings_v2"
EXPENSES = "mz2_supplier_expense_identities_v2"
AUDIT = "mz2_supplier_debit_identity_audit_v2"
CONTRACT = "mz2_supplier_debit_identity_v1"


def fail(code, **details):
    raise HTTPException(409, detail={"code": code, **details})


def _active(row):
    return bool(row) and row.get("status") not in {"inactive", "archived", "deleted", "disabled"} and not any(
        row.get(k) is True for k in ("deleted", "is_deleted", "archived", "is_archived", "disabled")
    ) and not any(row.get(k) is False for k in ("active", "is_active")) and not any(row.get(k) for k in ("deleted_at", "archived_at"))


def mapping_id(owner, source_kind, source_id, variant_id=None):
    return hashlib.sha256(json.dumps([owner, source_kind, source_id, variant_id], separators=(",", ":")).encode()).hexdigest()


async def require_source(db, owner, source_kind, source_id, variant_id=None):
    if source_kind == "default":
        if source_id != owner or variant_id is not None:
            fail("MZ2_SUPPLIER_DEFAULT_SCOPE_INVALID")
        return
    if source_kind == "product":
        row = await db["mezan_products_v2"].find_one({"user_id": owner, "mezan_product_id": source_id})
        if not _active(row):
            fail("MZ2_SUPPLIER_PRODUCT_UNAVAILABLE")
        if variant_id is not None:
            variants = row.get("variants") or []
            matches = [v for v in variants if str(v.get("id")) == variant_id]
            if len(matches) != 1 or not _active(matches[0]):
                fail("MZ2_SUPPLIER_VARIANT_UNAVAILABLE")
        return
    if source_kind == "service" and variant_id is None:
        row = await db["mezan_cost_resources_v2"].find_one({"user_id": owner, "id": source_id, "kind": "service"})
        if _active(row):
            return
        fail("MZ2_SUPPLIER_SERVICE_UNAVAILABLE")
    fail("MZ2_SUPPLIER_MAPPING_SOURCE_INVALID")


async def require_opening_identity(db, owner, entity_type, entity_id, sub_account):
    """Revalidate the explicit selected identity in the current approved Opening.

    Zero lines remain authoritative identities, but never invent a balance.
    The database must be bound to the posting/setup transaction.
    """
    category = {("asset", "inventory"): "inventory_asset", ("tax", "input_vat"): "input_vat"}.get((entity_type, sub_account))
    if not category or not entity_id:
        fail("MZ2_SUPPLIER_OPENING_IDENTITY_INVALID")
    settings = await db["settings"].find_one({"user_id": owner}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    verified = (await db.verified_opening(cutover) if isinstance(db, SupplierDebitSetupDatabase) else
                await verify_active_opening_v2(db._db, user_id=owner, cutover=cutover, mongo_session=db._session))
    if not verified:
        fail("MZ2_SUPPLIER_OPENING_IDENTITY_UNVERIFIED")
    draft = await db["mz2_opening_balance_drafts"].find_one({"user_id": owner, "status": "posted", "txn_group_id": cutover.get("opening_active_txn_group_id")})
    manifest = (draft or {}).get("preview_manifest") or {}
    preview_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if not manifest or preview_hash != (draft or {}).get("preview_hash") or manifest.get("lines") != (draft or {}).get("lines") or manifest.get("draft_id") != (draft or {}).get("id") or manifest.get("cutover_at") != (draft or {}).get("cutover_at"):
        fail("MZ2_SUPPLIER_OPENING_IDENTITY_MANIFEST_INVALID")
    active_id = cutover["opening_active_txn_group_id"]
    if active_id.startswith("zero:"):
        # Zero-only openings have no journal. Reuse the approved evidence
        # contract at its reviewed version and require its posted audit record.
        from accounting_financial_accounts import _assert_snapshot
        await _assert_snapshot(db, owner, {**draft, "version": draft["version"] - 1})
        audit = await db["mz2_opening_balance_audit"].find_one({"user_id": owner, "draft_id": draft["id"], "event_type": "opening_posted", "manifest.txn_group_id": active_id})
        if not audit or (audit.get("manifest") or {}).get("preview_hash") != preview_hash or (audit.get("manifest") or {}).get("approval_hash") != draft.get("approval_hash"):
            fail("MZ2_SUPPLIER_OPENING_IDENTITY_MANIFEST_INVALID")
    else:
        if isinstance(db, SupplierDebitSetupDatabase):
            metadata = await db.opening_metadata(active_id)
        else:
            journal = await get_journal_v2(db, user_id=owner, txn_group_id=active_id)
            metadata = ((journal or {}).get("group", {}).get("metadata") or {})
        if metadata.get("approved_preview_hash") != preview_hash:
            fail("MZ2_SUPPLIER_OPENING_IDENTITY_MANIFEST_INVALID")
    matches = [line for line in manifest["lines"] if line.get("category") == category and
               line.get("entity_type") == entity_type and line.get("entity_id") == entity_id and line.get("sub_account") == sub_account]
    if not _active(draft) or len(matches) != 1 or not _active(matches[0]) or matches[0].get("ledger_currency") != "SAR":
        fail("MZ2_SUPPLIER_OPENING_IDENTITY_REQUIRED", entity_type=entity_type, entity_id=entity_id, sub_account=sub_account)
    return {"entity_type": entity_type, "entity_id": entity_id, "sub_account": sub_account, "currency": "SAR",
            "opening_txn_group_id": cutover["opening_active_txn_group_id"]}


async def _require_reviewed_setup_identity(db, owner, row):
    """Explicit approved draft may prepare setup; it never authorizes posting."""
    from accounting_financial_accounts import _assert_snapshot
    draft = await db["mz2_opening_balance_drafts"].find_one({"user_id": owner,
        "id": row["opening_draft_id"], "status": "reviewed", "active_slot": "opening"})
    manifest = (draft or {}).get("preview_manifest") or {}
    preview_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if (not draft or not _active(draft) or draft.get("reviewed_by") != owner or not draft.get("reviewed_at")
            or not manifest or preview_hash != draft.get("preview_hash") or manifest.get("lines") != draft.get("lines")
            or manifest.get("draft_id") != draft.get("id") or manifest.get("cutover_at") != draft.get("cutover_at")):
        fail("MZ2_SUPPLIER_REVIEWED_OPENING_REQUIRED")
    await _assert_snapshot(db, owner, draft)
    approval = await db["mz2_opening_balance_audit"].find_one({"user_id": owner, "draft_id": draft["id"],
        "event_type": "review_approved", "manifest.approval_hash": draft.get("approval_hash"),
        "manifest.preview_hash": preview_hash, "manifest.approval_version": draft["version"]})
    matches = [line for line in manifest["lines"] if line.get("category") == "inventory_asset" and
        line.get("entity_type") == "asset" and line.get("entity_id") == row["entity_id"] and line.get("sub_account") == "inventory"]
    if not approval or len(matches) != 1 or not _active(matches[0]) or matches[0].get("ledger_currency") != "SAR":
        fail("MZ2_SUPPLIER_REVIEWED_OPENING_REQUIRED")


async def _require_financial_identity(db, owner, row, *, setup=False):
    if row.get("currency") != "SAR":
        fail("MZ2_SUPPLIER_DEBIT_CURRENCY_INVALID")
    treatment = row.get("financial_treatment")
    if treatment in {"INVENTORY_ASSET", "CAPITALIZE_TO_INVENTORY"}:
        if row.get("entity_type") != "asset" or row.get("sub_account") != "inventory":
            fail("MZ2_SUPPLIER_INVENTORY_IDENTITY_INVALID")
        if setup and row.get("opening_draft_id"):
            await _require_reviewed_setup_identity(db, owner, row)
        else:
            await require_opening_identity(db, owner, "asset", row.get("entity_id"), "inventory")
    elif treatment == "EXPENSE":
        identity = await db[EXPENSES].find_one({"user_id": owner, "id": row.get("entity_id"), "contract": CONTRACT})
        if not _active(identity) or identity.get("status") != "active" or type(identity.get("version")) is not int or identity["version"] < 1 or not identity.get("confirmed_at") or identity.get("confirmed_by") != owner or identity.get("currency") != "SAR" or identity.get("entity_type") != "expense" or identity.get("sub_account") is not None or row.get("entity_type") != "expense" or row.get("sub_account") is not None:
            fail("MZ2_SUPPLIER_EXPENSE_IDENTITY_REQUIRED")
    else:
        fail("MZ2_SUPPLIER_FINANCIAL_TREATMENT_REQUIRED")


async def resolve_debit(db, owner, source_kind, source_id, variant_id=None):
    await require_source(db, owner, source_kind, source_id, variant_id)
    candidates = [(source_kind, source_id, variant_id)]
    if source_kind == "product":
        if variant_id is not None:
            candidates.append(("product", source_id, None))
        candidates.append(("default", owner, None))
    row = None
    for kind, identity, variant in candidates:
        row = await db[MAPPINGS].find_one({"_id": mapping_id(owner, kind, identity, variant), "user_id": owner})
        if row is not None:
            break  # Explicitly disabled specific mappings never fall through.
    if row is None:
        fail("MZ2_SUPPLIER_PRODUCT_DEBIT_IDENTITY_REQUIRED" if source_kind == "product" else "MZ2_SUPPLIER_SERVICE_DEBIT_IDENTITY_REQUIRED")
    if (row.get("source_kind"), row.get("source_id"), row.get("variant_id")) != (kind, identity, variant) or row.get("id") != mapping_id(owner, kind, identity, variant):
        fail("MZ2_SUPPLIER_DEBIT_MAPPING_IDENTITY_INVALID")
    if not _active(row) or row.get("status") != "active" or row.get("contract") != CONTRACT or row.get("confirmed_by") != owner or not row.get("confirmed_at") or type(row.get("version")) is not int or row["version"] < 1:
        fail("MZ2_SUPPLIER_DEBIT_MAPPING_INACTIVE")
    if (source_kind == "product" and row.get("financial_treatment") != "INVENTORY_ASSET") or (source_kind == "service" and row.get("financial_treatment") not in {"CAPITALIZE_TO_INVENTORY", "EXPENSE"}):
        fail("MZ2_SUPPLIER_FINANCIAL_TREATMENT_REQUIRED")
    await _require_financial_identity(db, owner, row)
    return {k: v for k, v in row.items() if k != "_id"}


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    reason: str = Field(min_length=3, max_length=500)


class ExpenseCreate(Confirmation):
    name: str = Field(min_length=2, max_length=120)
    request_id: str = Field(min_length=8, max_length=160)


class MappingPut(Confirmation):
    source_kind: Literal["product", "service", "default"]
    source_id: str = Field(min_length=1, max_length=200)
    variant_id: str | None = Field(default=None, min_length=1, max_length=200)
    financial_treatment: Literal["INVENTORY_ASSET", "CAPITALIZE_TO_INVENTORY", "EXPENSE"]
    entity_type: Literal["asset", "expense"]
    entity_id: str = Field(min_length=1, max_length=200)
    sub_account: Literal["inventory"] | None = None
    currency: Literal["SAR"]
    status: Literal["active", "inactive"] = "active"
    version: int = Field(ge=0, strict=True)
    opening_draft_id: str | None = Field(default=None, min_length=1, max_length=200)


class ExpenseState(Confirmation):
    version: int = Field(ge=1, strict=True)
    status: Literal["active", "inactive"]


async def _owner(db, user):
    actor = await fresh_actor(db, user)
    require_owner(actor)
    return actor, accounting_owner_id(actor)


async def save_mapping(db, owner, actor, payload):
    await require_source(db, owner, payload.source_kind, payload.source_id, payload.variant_id)
    values = payload.model_dump()
    if (payload.source_kind in {"product", "default"} and payload.financial_treatment != "INVENTORY_ASSET") or (payload.source_kind == "service" and payload.financial_treatment not in {"EXPENSE", "CAPITALIZE_TO_INVENTORY"}):
        fail("MZ2_SUPPLIER_FINANCIAL_TREATMENT_REQUIRED")
    await _require_financial_identity(db, owner, values, setup=True)
    key = mapping_id(owner, payload.source_kind, payload.source_id, payload.variant_id)
    previous = await db[MAPPINGS].find_one({"_id": key, "user_id": owner})
    if (previous or {}).get("version", 0) != payload.version:
        fail("MZ2_SUPPLIER_MAPPING_VERSION_CONFLICT")
    row = {**values, "_id": key, "id": key, "user_id": owner, "contract": CONTRACT,
           "version": payload.version + 1, "confirmed_at": datetime.now(timezone.utc).isoformat(), "confirmed_by": actor["id"]}
    await db[MAPPINGS].replace_one({"_id": key, "user_id": owner}, row, upsert=True)
    await db[AUDIT].insert_one({"user_id": owner, "action": "mapping_confirmed", "before": previous, "after": row, "actor_id": actor["id"], "at": row["confirmed_at"]})
    return {k: v for k, v in row.items() if k != "_id"}


def make_supplier_debit_router(db, current_user):
    router = APIRouter(prefix="/supplier-debit-mappings-v2", tags=["supplier-debit-identities-v2"])

    @router.get("")
    async def context(user=Depends(current_user)):
        _, owner = await _owner(db, user)
        result = {}
        for key, collection in (("mappings", MAPPINGS), ("expense_identities", EXPENSES)):
            rows = await db[collection].find({"user_id": owner}, {"_id": 0}).to_list(1001)
            if len(rows) > 1000:
                fail("MZ2_SUPPLIER_IDENTITY_SCOPE_TOO_LARGE")
            result[key] = rows
        return result

    @router.put("")
    async def put_mapping(payload: MappingPut, user=Depends(current_user)):
        _, owner = await _owner(db, user)
        async def write(scoped):
            actor, actual_owner = await _owner(scoped, user)
            if actual_owner != owner:
                fail("MZ2_SUPPLIER_OWNER_CHANGED")
            return await save_mapping(scoped, owner, actor, payload)
        return await supplier_debit_setup_atomic_owner(db, owner, write)

    @router.post("/expense-identities")
    async def create_expense(payload: ExpenseCreate, user=Depends(current_user)):
        _, owner = await _owner(db, user)
        identity_id = "mse_" + uuid.uuid5(uuid.NAMESPACE_URL, owner + ":supplier-expense:" + payload.request_id).hex
        async def write(scoped):
            actor, actual_owner = await _owner(scoped, user)
            if actual_owner != owner:
                fail("MZ2_SUPPLIER_OWNER_CHANGED")
            values = payload.model_dump()
            previous = await scoped[EXPENSES].find_one({"_id": identity_id, "user_id": owner})
            if previous:
                if previous.get("creation_request") != values:
                    fail("MZ2_SUPPLIER_EXPENSE_REQUEST_CONFLICT")
                return {k: v for k, v in previous.items() if k != "_id"}
            row = {"_id": identity_id, "id": identity_id, "user_id": owner, "contract": CONTRACT,
                   "name": payload.name, "currency": "SAR", "entity_type": "expense", "sub_account": None,
                   "status": "active", "version": 1, "confirmed_by": actor["id"], "confirmed_at": datetime.now(timezone.utc).isoformat(),
                   "creation_request": values}
            await scoped[EXPENSES].insert_one(row)
            await scoped[AUDIT].insert_one({"user_id": owner, "action": "expense_identity_confirmed", "after": row, "actor_id": actor["id"], "at": row["confirmed_at"]})
            return {k: v for k, v in row.items() if k != "_id"}
        return await supplier_debit_setup_atomic_owner(db, owner, write)

    @router.put("/expense-identities/{identity_id}")
    async def expense_state(identity_id: str, payload: ExpenseState, user=Depends(current_user)):
        _, owner = await _owner(db, user)
        async def write(scoped):
            actor, actual_owner = await _owner(scoped, user)
            if actual_owner != owner:
                fail("MZ2_SUPPLIER_OWNER_CHANGED")
            previous = await scoped[EXPENSES].find_one({"_id": identity_id, "user_id": owner, "contract": CONTRACT})
            if not previous or previous.get("version") != payload.version:
                fail("MZ2_SUPPLIER_EXPENSE_VERSION_CONFLICT")
            row = {**previous, "status": payload.status, "version": payload.version + 1,
                   "confirmed_by": actor["id"], "confirmed_at": datetime.now(timezone.utc).isoformat()}
            await scoped[EXPENSES].replace_one({"_id": identity_id, "user_id": owner}, row)
            await scoped[AUDIT].insert_one({"user_id": owner, "action": "expense_identity_state_confirmed", "before": previous, "after": row, "reason": payload.reason, "actor_id": actor["id"], "at": row["confirmed_at"]})
            return {k: v for k, v in row.items() if k != "_id"}
        return await supplier_debit_setup_atomic_owner(db, owner, write)

    return router
