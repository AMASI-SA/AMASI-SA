"""Exact-attempt readback and explicit fenced cancellation; never POST replay."""
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
import re
from uuid import UUID

from fastapi import HTTPException
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

OPERATIONS = "order_review_completion_operations"
EVENTS = "order_review_events"
WORKFLOWS = "order_review_workflows"
BINDING_FIELDS = (
    "contract_version", "client_request_id", "merchant_id", "actor_id",
    "origin_session_ref", "origin_session_epoch", "order_number",
    "approved_revision", "approval_fingerprint", "approval_token_sha256",
)


def unknown_attempt():
    return {"operation": None, "state": "unknown", "retry_post": False, "reconcile": "get_only"}


def validate_closure_binding(binding, *, user_id, actor_id, order_number):
    """A receipt may cancel its own consent, but can never authorize execution."""
    if (set(binding) != set(BINDING_FIELDS) or type(binding.get("contract_version")) is not int
            or binding["contract_version"] != 2 or binding.get("merchant_id") != user_id
            or binding.get("actor_id") != actor_id or binding.get("order_number") != order_number
            or type(binding.get("approved_revision")) is not int or binding["approved_revision"] < 0
            or type(binding.get("origin_session_epoch")) is not int or binding["origin_session_epoch"] != 1
            or not isinstance(binding.get("origin_session_ref"), str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", binding["origin_session_ref"]) is None
            or any(not isinstance(binding.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", binding[key]) is None
                   for key in ("approval_fingerprint", "approval_token_sha256"))):
        raise HTTPException(409, detail={"code": "readback_binding_conflict", **unknown_attempt()})
    request_id(binding["client_request_id"])
    return deepcopy(binding)


def request_id(value):
    try:
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, detail={"code": "review_client_request_id_invalid"}) from None


def approval_context(*, user_id, actor_id, order_number, revision, session):
    return {"contract_version": 2, "merchant_id": user_id, "actor_id": actor_id,
            "order_number": order_number, "approved_revision": revision,
            "origin_session_ref": session["origin_session_ref"],
            "origin_session_epoch": session["origin_session_epoch"]}


def make_binding(*, client_request_id, token, context):
    from order_review_approval import verify_token
    digest = verify_token(token, user_id=context["merchant_id"], order_number=context["order_number"],
                          revision=context["approved_revision"], context=context)
    return {**context, "client_request_id": request_id(client_request_id),
            "approval_fingerprint": digest,
            "approval_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest()}


def require_binding(operation, binding):
    if operation.get("readback_binding") != binding:
        raise HTTPException(409, detail={"code": "readback_binding_conflict",
                                        "retry_post": False, "reconcile": "get_only"})


async def ensure_readback_storage(db):
    await db[OPERATIONS].with_options(write_concern=WriteConcern("majority", j=True)).create_index(
        [("user_id", 1), ("readback_binding.client_request_id", 1)],
        name="uq_review_v2_client_request", unique=True,
        partialFilterExpression={"readback_binding.client_request_id": {"$type": "string"}},
    )


def committed_proof(operation, event):
    binding = operation.get("readback_binding") or {}
    revision = operation.get("committed_revision")
    if (set(binding) != set(BINDING_FIELDS) or binding.get("contract_version") != 2
            or operation.get("state") != "completed"
            or operation.get("completion_mode") != "mezan_local_v1"
            or operation.get("user_id") != binding.get("merchant_id")
            or operation.get("actor_id") != binding.get("actor_id")
            or operation.get("order_number") != binding.get("order_number")
            or operation.get("revision") != binding.get("approved_revision")
            or type(revision) is not int or revision != binding["approved_revision"] + 1
            or not event or event.get("_id") != operation["_id"] + ":completed"
            or event.get("operation_id") != operation["_id"]
            or event.get("event_type") != "order_review_completed"
            or event.get("actor_id") != binding["actor_id"]
            or event.get("user_id") != binding["merchant_id"]
            or event.get("order_number") != binding["order_number"]
            or event.get("readback_binding") != binding
            or event.get("committed_revision") != revision
            or event.get("occurred_at") != operation.get("completed_at")
            or not operation.get("completed_at")
            or (operation.get("result") or {}).get("salla_status_sync") != "not_requested"):
        raise HTTPException(503, headers={"Cache-Control": "no-store"}, detail={"code": "review_completion_evidence_incomplete",
                                        "state": "unknown", "retry_post": False, "reconcile": "get_only"})
    return {**binding, "operation_id": operation["_id"], "state": "completed", "commit_confirmed": True,
            "committed_revision": revision, "event_id": event["_id"], "event_type": event["event_type"],
            "completed_at": operation["completed_at"], "completion_mode": "mezan_local_v1",
            "salla_status_sync": "not_requested"}


def closed_proof(operation, event, workflow):
    binding = operation.get("readback_binding") or {}
    barrier = operation.get("barrier_revision")
    current_revision = (workflow or {}).get("revision")
    try:
        validate_closure_binding(binding, user_id=operation.get("user_id"),
            actor_id=operation.get("actor_id"), order_number=operation.get("order_number"))
        valid = (operation.get("state") == "not_committed" and operation.get("closure_contract_version") == 1
            and operation.get("completion_mode") == "mezan_local_v1"
            and operation.get("revision") == binding["approved_revision"]
            and type(barrier) is int and barrier > binding["approved_revision"]
            and type(current_revision) is int and current_revision >= barrier
            and event and event.get("_id") == operation["_id"] + ":closed"
            and event.get("operation_id") == operation["_id"]
            and event.get("event_type") == "order_review_attempt_closed"
            and event.get("user_id") == binding["merchant_id"]
            and event.get("actor_id") == binding["actor_id"]
            and event.get("order_number") == binding["order_number"]
            and event.get("readback_binding") == binding
            and event.get("barrier_revision") == barrier
            and event.get("closure_contract_version") == 1
            and event.get("occurred_at") == operation.get("closed_at")
            and bool(operation.get("closed_at")))
    except HTTPException:
        valid = False
    if not valid:
        raise HTTPException(503, headers={"Cache-Control": "no-store"}, detail={
            "code": "review_completion_evidence_incomplete", **unknown_attempt()})
    return {**binding, "operation_id": operation["_id"], "state": "not_committed", "commit_confirmed": False,
            "closure_contract_version": 1, "barrier_revision": barrier,
            "event_id": event["_id"], "event_type": event["event_type"], "closed_at": operation["closed_at"],
            "completion_mode": "mezan_local_v1", "refresh_required": True, "retry_post": False}


async def operation_result(scoped, operation):
    if not operation or operation.get("state") not in {"completed", "not_committed"}:
        return unknown_attempt()
    selector = {"user_id": operation["user_id"], "order_number": operation["order_number"]}
    workflow = await scoped[WORKFLOWS].find_one(selector) or {}
    closed = operation["state"] == "not_committed"
    event = await scoped[EVENTS].find_one({"_id": operation["_id"] + (":closed" if closed else ":completed"),
                                          "user_id": operation["user_id"]})
    proof = closed_proof(operation, event, workflow) if closed else committed_proof(operation, event)
    return {"operation": proof, "state": proof["state"], "current_revision": workflow.get("revision"),
            "current_operation_id": workflow.get("review_completion_operation_id")}


async def unresolved_operation(scoped, selector):
    """Ignore only verified closed attempts, never an arbitrary state label."""
    async for operation in scoped[OPERATIONS].find({**selector, "superseded_by": {"$exists": False}}):
        if operation.get("state") != "not_committed":
            return operation
        await operation_result(scoped, operation)
    return None


async def close_attempt(db, *, binding, user_id, actor_id, order_number, session_context, validate_order):
    """Atomically bar a delayed ORIGINAL request before proving non-commit.

    Absence alone proves nothing. Owner serialization plus the workflow revision
    write fences even an older replica which does not understand closure records.
    It must revalidate the signed workflow fingerprint in its commit transaction.
    """
    from operational_atomic import operational_owner
    from order_review_completion import _digest
    from review_session_fence import fence_review_session, SESSIONS
    binding = validate_closure_binding(binding, user_id=user_id, actor_id=actor_id, order_number=order_number)
    selector = {"user_id": user_id, "order_number": order_number}
    await ensure_readback_storage(db)

    async def close_owned(scoped):
        await fence_review_session(scoped, user_id=user_id, **session_context)
        existing = await scoped[OPERATIONS].find_one({"user_id": user_id,
            "readback_binding.client_request_id": binding["client_request_id"]})
        if existing:
            require_binding(existing, binding)
            return await operation_result(scoped, existing)
        # A new login can close an old receipt belonging to the SAME actor, but
        # cannot invent a foreign login family. Expired/purged evidence is unknown.
        origin = await scoped[SESSIONS].find_one({"_id": binding["origin_session_ref"],
            "user_id": user_id, "actor_id": actor_id, "epoch": binding["origin_session_epoch"]})
        if not origin:
            return unknown_attempt()
        workflow = await scoped[WORKFLOWS].find_one(selector) or {}
        revision = workflow.get("revision", 0)
        if (type(revision) is not int or revision < binding["approved_revision"]
                or workflow.get("stage", "pending_review") != "pending_review"
                or workflow.get("review_completion_operation_id")
                or workflow.get("completion_mode") not in (None, "mezan_local_v1")):
            return unknown_attempt()
        if await unresolved_operation(scoped, selector):
            return unknown_attempt()
        # Detect orphaned completion evidence too; never reinterpret corruption
        # or a legacy/provider operation as a safely rejected local attempt.
        if await scoped[EVENTS].find_one({**selector, "event_type": "order_review_completed"}):
            return unknown_attempt()
        await validate_order(scoped)
        barrier = revision + 1
        now = datetime.now(timezone.utc).isoformat()
        identity = "review_closed_" + _digest([user_id, order_number, binding["client_request_id"]])
        operation = {"_id": identity, **selector, "actor_id": actor_id, "revision": binding["approved_revision"],
            "readback_binding": binding, "state": "not_committed", "completion_mode": "mezan_local_v1",
            "closure_contract_version": 1, "barrier_revision": barrier, "closed_at": now, "created_at": now}
        event = {"_id": identity + ":closed", **selector, "actor_id": actor_id, "operation_id": identity,
            "event_type": "order_review_attempt_closed", "readback_binding": deepcopy(binding),
            "closure_contract_version": 1, "barrier_revision": barrier, "occurred_at": now}
        await scoped[WORKFLOWS].update_one(selector, {"$set": {"revision": barrier, "updated_at": now},
            "$setOnInsert": {**selector, "stage": "pending_review", "items": [], "operational_items": []}}, upsert=True)
        await scoped[OPERATIONS].insert_one(operation)
        await scoped[EVENTS].insert_one(event)
        return await operation_result(scoped, operation)

    return await operational_owner(db, user_id, close_owned, review_session=session_context)


async def read_attempt(db, *, user_id, actor_id, order_number, client_request_id):
    """One consistent primary read snapshot; never return a different/latest attempt."""
    from accounting_write_control import AccountingDatabase
    from order_review_approval import ReadSnapshot
    cid = request_id(client_request_id)
    raw_db = db.current() if isinstance(db, AccountingDatabase) else db
    async with await raw_db.client.start_session() as session:
        async with session.start_transaction(read_concern=ReadConcern("snapshot"),
                                             write_concern=WriteConcern("majority", j=True),
                                             read_preference=ReadPreference.PRIMARY):
            scoped = ReadSnapshot(raw_db, session)
            operation = await scoped[OPERATIONS].find_one({
                "user_id": user_id, "order_number": order_number, "actor_id": actor_id,
                "readback_binding.client_request_id": cid,
            })
            return await operation_result(scoped, operation)
