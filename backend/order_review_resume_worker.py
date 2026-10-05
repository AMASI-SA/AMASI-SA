"""Bounded resumption of explicitly recorded new review approvals only.

No order discovery, historical recovery, new approvals or direct workflow writes.
The completion engine remains the sole owner of provider and completion effects.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument

from operational_atomic import operational_owner
from order_review_completion import OPERATIONS, complete_review_operation

logger = logging.getLogger(__name__)
STATES = ("prepared", "syncing", "provider_confirmed")
MAX_ATTEMPTS = 8
CLAIM_SECONDS = 180
ATTEMPT_TIMEOUT = 90
POLL_SECONDS = 15
BATCH_LIMIT = 10


def now():
    return datetime.now(timezone.utc)


def delay(attempt):
    return min(30 * 2 ** max(0, attempt - 1), 1800)


async def resume_existing(db, op):
    # Import lazily: server imports the route factories before local startup.
    import order_review_routes as routes
    from auth import account_is_disabled

    required = ("_id", "user_id", "order_number", "actor_id", "order_fingerprint",
                "workflow_fingerprint", "source_fingerprint", "acceptance_snapshot", "items")
    if (any(not op.get(key) for key in required) or not isinstance(op.get("revision"), int)
            or "config_version" not in op["acceptance_snapshot"]):
        raise HTTPException(409, detail={"code": "review_resume_evidence_missing"})
    owner, number = op["user_id"], op["order_number"]

    async def instructions(scoped):
        actor = await scoped.users.find_one({"id": op["actor_id"]})
        if not actor or account_is_disabled(actor):
            raise HTTPException(403, detail={"code": "review_actor_unavailable"})
        routes._require_reviewer(actor)
        if routes._merchant_user_id(actor) != owner:
            raise HTTPException(403, detail={"code": "review_actor_owner_changed"})
        await routes.enforce_stage_instructions(scoped, user_id=owner,
            order_number=number, stage="pending_review", actor_id=op["actor_id"], order_wide=True)

    async def load(scoped):
        return await routes.get_order(routes.MongoOrderRepository(scoped),
                                      user_id=owner, order_number=number)

    async def sync(order):
        return await routes._sync_salla_reviewed(db, owner, order)

    await instructions(db)
    order = await load(db)
    source = await db.unified_orders.find_one({"user_id": owner, "order_number": number})
    if not source or (source.get("g47_salla_snapshot") or {}).get("requires_authoritative_refresh"):
        raise HTTPException(409, detail={"code": "component_authoritative_refresh_required"})
    workflow = await db.order_review_workflows.find_one({"user_id": owner, "order_number": number})
    # Claim/validate compare these current facts against the immutable stored
    # approval. Never call the HTTP complete route which builds a new snapshot.
    return await complete_review_operation(db, user_id=owner, actor_id=op["actor_id"],
        actor_name=op.get("actor_name", ""), order=order, workflow=workflow,
        frozen_items=op["items"], revision=op["revision"], load_order=load,
        sync_salla=sync, enforce_instructions=instructions, source_snapshot=source,
        approved_acceptance=op["acceptance_snapshot"], resume_operation_id=op["_id"])


async def finish_attempt(db, op, *, code, blocked, contended=False):
    async def finish(scoped):
        selector = {"_id": op["_id"], "user_id": op["user_id"],
                    "resume_claim": op["resume_claim"], "state": {"$in": list(STATES)}}
        current = await scoped[OPERATIONS].find_one(selector)
        if not current:
            return
        # A manual retry owns completion. Do not mark it blocked or consume a
        # background attempt budget merely because it won the completion lease.
        busy = current.get("lease_until", "") > now().isoformat()
        contended_now = contended or busy
        attempts = min(MAX_ATTEMPTS, max(0, int(current.get("resume_attempts", 0)) - int(contended_now)))
        values = {"resume_claim_until": "", "resume_claim": None,
                  "resume_attempts": attempts,
                  "resume_due_at": (now() + timedelta(seconds=delay(max(1, attempts)))).isoformat(),
                  "resume_last_error": code, "resume_last_attempt_at": now().isoformat()}
        if not contended_now and (blocked or attempts >= MAX_ATTEMPTS):
            values.update(state="requires_review", resume_previous_state=current["state"],
                          resume_block_reason=code, resume_blocked_at=now().isoformat())
        await scoped[OPERATIONS].update_one(selector, {"$set": values})
    await operational_owner(db, op["user_id"], finish)


async def run_once(db):
    instant = now().isoformat()
    query = {"auto_resume_version": 1, "state": {"$in": list(STATES)},
             "superseded_by": {"$exists": False},
             "$and": [
                 {"$or": [{"resume_due_at": {"$lte": instant}}, {"resume_due_at": {"$exists": False}}]},
                 {"$or": [{"resume_claim_until": {"$lte": instant}}, {"resume_claim_until": {"$exists": False}}]},
                 {"$or": [{"lease_until": {"$lte": instant}}, {"lease_until": {"$exists": False}}]},
             ]}
    candidates = await db[OPERATIONS].find(query, {"_id": 1}).sort("resume_due_at", 1).limit(BATCH_LIMIT).to_list(BATCH_LIMIT)
    processed = 0
    for candidate in candidates:
        # Separate bounded scheduler lease; completion still acquires its
        # original transactionally fenced lease, shared with manual callers.
        token = uuid.uuid4().hex
        op = await db[OPERATIONS].find_one_and_update({**query, "_id": candidate["_id"]},
            {"$set": {"resume_claim": token,
                      "resume_claim_until": (now() + timedelta(seconds=CLAIM_SECONDS)).isoformat()},
             "$inc": {"resume_attempts": 1}}, return_document=ReturnDocument.AFTER)
        if not op:
            continue
        processed += 1
        try:
            if op["resume_attempts"] > MAX_ATTEMPTS:
                await finish_attempt(db, op, code="review_resume_attempts_exhausted", blocked=True)
                continue
            await asyncio.wait_for(resume_existing(db, op), timeout=ATTEMPT_TIMEOUT)
        except HTTPException as exc:
            code = exc.detail.get("code", "review_resume_rejected") if isinstance(exc.detail, dict) else "review_resume_rejected"
            contended = code in {"review_completion_in_progress", "review_completion_lease_lost", "review_completion_lease_expired"}
            await finish_attempt(db, op, code=code,
                                 blocked=400 <= exc.status_code < 500 and exc.status_code not in {408, 429},
                                 contended=contended)
        except Exception as exc:
            # Never persist provider messages, customer payloads or credentials.
            await finish_attempt(db, op, code=type(exc).__name__, blocked=False)
    return processed


async def loop(db):
    while True:
        try:
            await run_once(db)
        except Exception as exc:
            logger.warning("Review resume cycle deferred: %s", type(exc).__name__)
        await asyncio.sleep(POLL_SECONDS)


async def start_worker(db):
    await db[OPERATIONS].create_index(
        [("auto_resume_version", 1), ("state", 1), ("resume_due_at", 1)], name="review_resume_due")
    await db[OPERATIONS].create_index(
        [("user_id", 1), ("order_number", 1), ("created_at", -1)], name="review_resume_order")
    return asyncio.create_task(loop(db), name="review-completion-auto-resume")


async def stop_worker(task):
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
