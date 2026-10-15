"""Durable nonfinancial inbox; never acknowledge an event before persistence.

Payloads contain only accounting evidence. Replay is explicit and bounded;
processing and the completion marker share one owner transaction. Errors leave
the event pending and visible. The original economic identity owns deduplication.
"""
from datetime import datetime, timezone
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern
from accounting_atomic import atomic_owner
from accounting_receivable_service import digest


async def ingest(db, *, owner, kind, evidence):
    key = digest([owner, kind, evidence])
    from accounting_atomic import SessionDatabase
    from accounting_write_control import AccountingDatabase
    target = db.current() if isinstance(db, AccountingDatabase) else db
    inbox = target.mz2_ingress_events
    if not isinstance(target, SessionDatabase):
        inbox = inbox.with_options(write_concern=WriteConcern("majority", j=True))
    try:
        await inbox.update_one({"_id": key}, {"$setOnInsert": {
            "user_id": owner, "kind": kind, "evidence": evidence, "state": "pending",
            "received_at": datetime.now(timezone.utc).isoformat(),
        }}, upsert=True)
    except DuplicateKeyError:
        pass
    try:
        return await process_event(db, owner, key)
    except HTTPException as exc:
        if exc.status_code == 423:
            return {"state": "deferred", "event_id": key, "financial_write": False,
                    "reason": "mz2_writes_paused"}
        raise


async def process_event(db, owner, key):
    async def apply(scoped):
        row = await scoped.mz2_ingress_events.find_one({"_id": key, "user_id": owner})
        if not row:
            raise HTTPException(404, "accounting_ingress_event_missing")
        if row["state"] == "processed":
            result = row["result"]
            return {**result, "state": "already_posted"} if row["kind"] == "sale" else result
        evidence = row["evidence"]
        if row["kind"] == "sale":
            from accounting_receivable_service import execute
            result = await execute(scoped, owner=owner, actor_id=owner,
                actor_name="bnpl_bridge", provider=evidence["provider"],
                payment_id=evidence.get("provider_id"), incoming=evidence)
        elif row["kind"] == "refund_observation":
            from accounting_refund_drafts import observe_refund
            result = await observe_refund(scoped, owner=owner, **evidence, _queued=True)
        else:
            raise HTTPException(409, "unknown_accounting_ingress_kind")
        await scoped.mz2_ingress_events.update_one({"_id": key, "user_id": owner}, {"$set": {
            "state": "pending" if result.get("state") == "needs_review" else "processed", "result": result,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }})
        return result
    return await atomic_owner(db, owner, apply)


async def replay_pending(db, owner):
    from accounting_write_control import write_state
    if (await write_state(db, owner))["paused"]:
        raise HTTPException(423, "mz2_writes_paused")
    rows = await db.mz2_ingress_events.find({"user_id": owner, "state": "pending"},
        {"_id": 1}).sort([("received_at", 1), ("_id", 1)]).limit(50).to_list(50)
    processed = 0
    pending_review = []
    for row in rows:
        try:
            result = await process_event(db, owner, row["_id"])
            if result.get("state") == "needs_review":
                pending_review.append(row["_id"])
            else:
                processed += 1
        except Exception as exc:
            # No exception text or customer evidence exposed. Do not consume
            # failed events; an operator can resolve evidence and retry.
            if isinstance(exc, HTTPException) and exc.status_code == 423:
                break
            pending_review.append(row["_id"])
    return {"processed": processed, "pending_review": pending_review,
            "pending_events": await db.mz2_ingress_events.count_documents({
                "user_id": owner, "state": "pending"})}
