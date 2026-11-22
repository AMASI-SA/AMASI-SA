"""Actor-bound recovery of an immutable workspace command, without money writes.

The browser retains only a random recovery UUID. Financial fields stay behind
current tenant, operation and resource authorization. Registration does not
reserve, execute or approve money; the existing financial endpoints retain
their validation, idempotency and aggregate CAS. Retiring a recovery record
removes its payload and keeps a tombstone so a delayed registration cannot
resurrect it.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import re
from typing import Literal

from fastapi import Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, UUID4, ValidationError
from pymongo.errors import DuplicateKeyError

from operational_balance_access import guard_access, load_access
from operational_balance_service import entity
from operational_balance_store import STATES, check_authorization, digest, fail, mutate, now, read, request_key
from operational_balance_workspace import WorkspaceEntry, entry_intent, movement_body
from operational_workspace_refunds import (
    PROVIDERS, RefundCancel, RefundConfirm, RefundExecution, RefundReconcile, RefundRequest,
    _find_case, refund_order,
)
from operational_workspace_customer_collections import CollectionRecord


PENDING_COMMANDS = "operational_workspace_pending_commands_v1"
ACTION_MODELS = {
    "entry": WorkspaceEntry,
    "refund.create": RefundRequest,
    "refund.confirm": RefundConfirm,
    "refund.cancel": RefundCancel,
    "refund.reconcile": RefundReconcile,
    "refund.record_execution": RefundExecution,
    "customer.collection": CollectionRecord,
}
CACHE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class PendingRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["entry", "refund.create", "refund.confirm", "refund.cancel", "refund.reconcile", "refund.record_execution", "customer.collection"]
    target_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    command: dict


class PendingScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")


def _not_found():
    fail("operational_pending_command_not_found", "العملية المحفوظة غير متاحة", 404)


def _retired():
    fail("operational_pending_command_retired", "أُغلقت استعادة هذه العملية؛ حدّث السجل قبل إضافة عملية جديدة", 410)


def _conflict():
    fail("operational_pending_command_conflict", "هذا المعرف مرتبط بعملية محفوظة مختلفة؛ استعد العملية نفسها", 409)


def _require_scope(expected, owner, actor):
    if expected != digest([owner, actor["id"]]):
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 409)


def _normalize(payload):
    try:
        parsed = ACTION_MODELS[payload.action].model_validate(payload.command)
        command = parsed.command() if isinstance(parsed, WorkspaceEntry) else parsed.model_dump()
    except ValidationError:
        fail("operational_pending_command_invalid", "بيانات العملية المحفوظة غير صحيحة", 422)
    needs_target = payload.action not in {"entry", "refund.create", "refund.record_execution", "customer.collection"}
    if bool(payload.target_id) != needs_target:
        fail("operational_pending_command_target", "نوع العملية لا يطابق طلب الاسترجاع المحدد", 422)
    return {"action": payload.action, "target_id": payload.target_id, "command": command}


def _refund_operation(action):
    return "customer.refund_request" if action in {"refund.create", "refund.cancel"} else "customer.refund_execute"


async def _case_resource(db, owner, actor, row, policy):
    case = _find_case(await read(db, owner), row["target_id"], policy)
    if row["action"] == "refund.cancel" and not policy.owner_override and case.get("created_by") != actor["id"]:
        fail("operational_refund_cancel_forbidden", "إلغاء الطلب متاح لمن سجله أو للمالك", 403)
    if row["action"] == "refund.confirm" and case.get("method") != "bank":
        fail("operational_refund_provider_execution_pending", "طلب المنصة ينتظر تأكيد المصدر ولا يقبل تأكيدًا محليًا", 409)
    if row["action"] == "refund.reconcile" and case.get("method") != "original":
        fail("operational_refund_original_unavailable", "مطابقة دليل المنصة تخص طريقة المزود الأصلي", 422)
    return case["refund_source_type"], case["refund_source_id"]


async def _requirements(db, owner, actor, row, policy):
    command, action = row["command"], row["action"]
    if action == "entry":
        operation, resources = entry_intent(command)
        operations = [operation]
        if command.get("source_type") == "employee_custody":
            operations.append("custody.spend")
        return operations, [list(resource) for resource in resources]
    if action == "customer.collection":
        policy.require_operation("customer.collection")
        return ["customer.collection"], [[command["source_type"], command["source_id"]]]
    operation = _refund_operation(action)
    policy.require_operation(operation)
    if action not in {"refund.create", "refund.record_execution"}:
        resource = await _case_resource(db, owner, actor, row, policy)
    elif command["method"] == "bank":
        if not command.get("bank_id"):
            fail("operational_refund_bank_required", "اختر البنك الذي سيُرد منه المبلغ", 422)
        resource = ("bank", command["bank_id"])
    else:
        if command.get("bank_id"):
            fail("operational_refund_hidden_bank", "طريقة الاسترجاع المختارة لا تستخدم بنكًا", 422)
        order = await refund_order(db, owner, command["order_number"])
        if order["provider"] not in PROVIDERS:
            fail("operational_refund_original_unavailable", "لا يوجد مزود دفع أصلي متاح لهذا الطلب", 422)
        resource = ("provider", order["provider"])
    return [operation], [list(resource)]


@asynccontextmanager
async def _authorized(db, owner, actor, row):
    from operational_web_page_policy import enforce_pending_action
    async with guard_access(db, actor, owner, row["operations"], row["resources"]) as policy:
        enforce_pending_action(policy.actor, row["action"])
        if row.get("target_id"):
            resource = await _case_resource(db, owner, actor, row, policy)
            if [list(resource)] != row["resources"]:
                fail("operational_access_changed", "تغير مصدر العملية؛ أعد فتح السجل", 409)
        for kind, identity in row["resources"]:
            await entity(db, owner, kind, identity, "SAR")
        await check_authorization()
        yield
        # Recheck after every awaited database operation and before returning
        # a financial payload; an earlier grant is not a read credential.
        await check_authorization()
        current = await load_access(db, actor, owner)
        current.require_enabled()
        for operation in row["operations"]:
            current.require_operation(operation)
        for kind, identity in row["resources"]:
            current.require_use(kind, identity)
        if ((policy.version, policy.owner_override) != (current.version, current.owner_override)
                or any(policy.resource(kind, identity) != current.resource(kind, identity)
                       for kind, identity in row["resources"])):
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)


def _payout_identity(row):
    command = row["command"]
    if (row["action"] != "entry" or command.get("side") != "for_us"
            or command.get("reason") not in {None, "payment"} or not command.get("source_id")):
        return None
    request = command["request_id"]
    if request != row["_id"] and not re.fullmatch(r"workspace-[0-9a-z]{1,13}-[0-9a-z]{0,20}-[0-9a-z]{0,20}", request):
        return None
    operation, resources = entry_intent(command)
    payload = movement_body(command, operation, resources)
    return request_key("movement:" + row["actor_id"], request), digest(payload)


async def _payout_resolution(db, row, *, discard=False):
    identity = _payout_identity(row)
    if identity is None:
        if discard:
            fail("operational_discard_unsupported", "هذه العملية تحتاج متابعة من سجلها", 422)
        return None
    key, fingerprint = identity
    path = "requests." + key
    owner_query = {"_id": row["owner_id"], "owner_id": row["owner_id"]}
    # Requests stay inline even when the audit and snapshot are archived.
    stored = await db[STATES].find_one(owner_query, {path: 1})
    receipt = (stored or {}).get("requests", {}).get(key)
    if discard and receipt is None:
        # Use the same size-checked, authorized CAS as the money write. The
        # losing writer replays the decision, even if it started before discard.
        async def cancel_if_pending(state):
            rejection = {"_operational_rejected": True, "status": 409,
                         "detail": {"code": "operational_payout_discarded", "not_applied": True,
                                    "message": "أُلغي طلب السداد قبل تسجيل أي مبلغ."}}
            return state["requests"].setdefault(key, {"hash": fingerprint, "result": rejection})
        receipt = await mutate(db, row["owner_id"], cancel_if_pending)
    if not receipt:
        if discard:
            fail("operational_discard_unconfirmed", "تعذر تأكيد إلغاء المحاولة. أعد التحقق من حالتها.")
        return {"status": "pending"}
    if receipt.get("hash") != fingerprint:
        _conflict()
    result = receipt["result"]
    if result.get("_operational_rejected"):
        detail = result.get("detail") or {}
        return {"status": "discarded" if detail.get("code") == "operational_payout_discarded" else "rejected",
                "detail": detail}
    command = row["command"]
    if (not result.get("id") or result.get("party_id") != command["party_id"]
            or result.get("party_type") != command["party_type"]):
        _conflict()
    return {"status": "saved", "result": {"saved": True, "movement_id": result["id"],
            "party_type": result["party_type"], "party_id": result["party_id"]}}


async def _reply(db, row):
    response = {"id": row["_id"], "action": row["action"],
                "target_id": row["target_id"], "command": deepcopy(row["command"])}
    resolution = await _payout_resolution(db, row)
    if resolution is not None:
        response["resolution"] = resolution
    return JSONResponse(response, headers=CACHE_HEADERS)


def install_pending_command_routes(router, db, current_user, scope):
    async def load(user, recovery_id, expected):
        actor, owner, _ = await scope(db, user, "workspace")
        policy = await load_access(db, actor, owner)
        policy.require_enabled()
        _require_scope(expected, owner, actor)
        # Even an owner cannot retrieve another actor's private command by ID.
        bound = {"_id": str(recovery_id), "owner_id": owner,
                 "actor_id": actor["id"], "session_scope": expected}
        return actor, owner, policy, bound

    async def existing_reply(actor, owner, bound, row, expected_hash=None):
        async with _authorized(db, owner, actor, row):
            collection = db[PENDING_COMMANDS]
            current = await collection.find_one(bound)
            if current is None:
                _not_found()
            if current["status"] == "retired":
                _retired()
            if expected_hash is not None and current["command_hash"] != expected_hash:
                _conflict()
            return await _reply(db, current)

    @router.post("/workspace/pending-commands/{recovery_id}")
    async def register(recovery_id: UUID4, payload: PendingRegistration, user=Depends(current_user)):
        normalized = _normalize(payload)
        actor, owner, policy, bound = await load(user, recovery_id, normalized["command"]["expected_session_scope"])
        collection = db[PENDING_COMMANDS]
        command_hash = digest(normalized)
        previous = await collection.find_one(bound)
        if previous is not None:
            return await existing_reply(actor, owner, bound, previous, command_hash)
        operations, resources = await _requirements(db, owner, actor, normalized, policy)
        row = {**bound, **normalized, "command_hash": command_hash, "operations": operations,
               "resources": resources, "status": "pending", "created_at": now()}
        async with _authorized(db, owner, actor, row):
            try:
                # The global unique ID pins actor, tenant and content across
                # processes. No last-write-wins or update of a command exists.
                await collection.insert_one(row)
            except DuplicateKeyError:
                pass
            stored = await collection.find_one(bound)
            if stored is None:
                _not_found()
            if stored["status"] == "retired":
                _retired()
            if stored["command_hash"] != command_hash:
                _conflict()
            return await _reply(db, stored)

    @router.get("/workspace/pending-commands/{recovery_id}")
    async def retrieve(recovery_id: UUID4, expected_session_scope: str = Query(pattern=r"^[a-f0-9]{64}$"), user=Depends(current_user)):
        actor, owner, _, bound = await load(user, recovery_id, expected_session_scope)
        collection = db[PENDING_COMMANDS]
        row = await collection.find_one(bound)
        if row is None:
            _not_found()
        return await existing_reply(actor, owner, bound, row)

    @router.post("/workspace/pending-commands/{recovery_id}/discard")
    async def discard(recovery_id: UUID4, payload: PendingScope, user=Depends(current_user)):
        actor, owner, _, bound = await load(user, recovery_id, payload.expected_session_scope)
        row = await db[PENDING_COMMANDS].find_one(bound)
        if row is None:
            _not_found()
        if row["status"] == "retired":
            _retired()
        async with _authorized(db, owner, actor, row):
            result = await _payout_resolution(db, row, discard=True)
            return JSONResponse({"id": str(recovery_id), **result}, headers=CACHE_HEADERS)

    @router.delete("/workspace/pending-commands/{recovery_id}")
    async def retire(recovery_id: UUID4, payload: PendingScope, user=Depends(current_user)):
        actor, owner, _, bound = await load(user, recovery_id, payload.expected_session_scope)
        collection = db[PENDING_COMMANDS]
        row = await collection.find_one(bound)
        if row is None:
            _not_found()
        async with _authorized(db, owner, actor, row):
            await collection.update_one({**bound, "status": "pending", "command_hash": row["command_hash"]},
                {"$set": {"status": "retired", "retired_at": now()}, "$unset": {"command": ""}})
            # This acknowledges recovery cleanup only. It neither cancels a
            # financial request nor asserts that an ambiguous write was absent.
            return JSONResponse({"deleted": True, "id": str(recovery_id)}, headers=CACHE_HEADERS)
