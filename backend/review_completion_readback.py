"""Exact-attempt readback. No provider calls, admission journal, or POST replay."""
import hashlib
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
            if not operation or operation.get("state") != "completed":
                return {"operation": None, "state": "unknown", "retry_post": False, "reconcile": "get_only"}
            event = await scoped[EVENTS].find_one({"_id": operation["_id"] + ":completed", "user_id": user_id})
            proof = committed_proof(operation, event)
            workflow = await scoped[WORKFLOWS].find_one({"user_id": user_id, "order_number": order_number}) or {}
            return {"operation": proof, "state": "completed",
                    "current_revision": workflow.get("revision"),
                    "current_operation_id": workflow.get("review_completion_operation_id")}
