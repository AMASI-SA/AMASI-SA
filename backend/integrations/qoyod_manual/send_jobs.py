"""Durable, operator-requested Plan-B sends owned by the background worker.

HTTP acknowledgement never contacts Salla/Qoyod. A stable order slot coalesces
double clicks and uncertain retries. Expired executing jobs are held for review,
never replayed; the existing sender remains the invoice/idempotency authority.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from security_sensitive_routes import require_qoyod_security_owner
from integrations.qoyod_manual.send import ManualSendRefused
from integrations.qoyod_manual.canary_batch import SAFE_ALREADY_SENT_CODES

log = logging.getLogger(__name__)
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 15
MAX_SEND_SECONDS = 900
POLL_SECONDS = 3
TERMINAL = frozenset({"succeeded", "refused", "needs_review"})
# Refusal is retryable only when we can identify a pre-write business gate.
# Provider errors, malformed create responses and partial payments are unknown.
PRE_WRITE_REFUSALS = frozenset({
    "order_not_found", "not_qoyod_eligible_status", "no_salla_order_date",
    "before_floor_date", "not_completed", "zero_total_refused",
    "unsupported_invoice_currency", "foreign_currency_accounting_facts_missing",
    "foreign_currency_exchange_rate_unverified", "foreign_currency_total_unverified",
    "accounting_currency_conflict", "recovery_live_facts_changed",
    "payment_method_unmapped", "receiving_bank_missing", "qoyod_credentials_missing",
    "qoyod_preflight_payload_invalid", "qoyod_preflight_total_mismatch",
    "send_lock_unavailable",
    "manual_send_authority_changed",
})


def utcnow():
    return datetime.now(timezone.utc)


def _key(owner, order_number):
    return hashlib.sha256(f"main\0{owner}\0{order_number}".encode()).hexdigest()


def _public(row):
    job = {"id": row["_id"], "status": row["status"],
           "order_number": row["order_number"], "operation": row["operation"],
           "poll_after_seconds": 5}
    for key in ("created_at", "started_at", "finished_at"):
        value = row.get(key)
        if value is not None:
            job[key] = value.isoformat() if isinstance(value, datetime) else value
    if row.get("error"):
        job["error"] = row["error"]
    if row.get("result"):
        job["result"] = row["result"]
    return {"ok": True, "job": job}


async def ensure_indexes(db):
    await db.qoyod_manual_send_jobs.create_index(
        [("status", 1), ("created_at", 1)], name="manual_send_jobs_pending")
    await db.qoyod_manual_send_jobs.create_index(
        [("status", 1), ("lease_until", 1)], name="manual_send_jobs_recovery")
    await db.qoyod_manual_send_jobs.create_index(
        [("user_id", 1), ("order_number", 1), ("status", 1)],
        name="manual_send_jobs_order_fence")


async def enqueue(db, *, user, order_number, operation="send", request_id=None):
    actor = await require_qoyod_security_owner(db, user)
    order_number = str(order_number).strip()
    if not order_number or len(order_number) > 80:
        raise HTTPException(422, "invalid_order_number")
    if operation not in {"send", "retry"}:
        raise ValueError("invalid_manual_send_operation")
    request_id = str(request_id or "legacy").strip()
    if not request_id or len(request_id) > 128:
        raise HTTPException(422, "invalid_request_id")
    now = utcnow()
    row = {"_id": _key(actor["id"], order_number), "user_id": "main",
           "orders_user_id": actor["id"], "actor_id": actor["id"],
           "order_number": order_number, "operation": operation,
           "request_id": request_id, "status": "queued", "created_at": now,
           "generation": uuid.uuid4().hex}
    try:
        await db.qoyod_manual_send_jobs.insert_one(row)
        return _public(row)
    except DuplicateKeyError:
        existing = await db.qoyod_manual_send_jobs.find_one({"_id": row["_id"]})
    # Only an explicit *new* failed-order retry can rearm a known refusal.
    # Unknown/cancelled work and completed sends are never rearmed here.
    if (operation == "retry" and request_id != "legacy"
            and existing["status"] == "refused"
            and request_id != existing.get("request_id")):
        restarted = await db.qoyod_manual_send_jobs.find_one_and_update(
            {"_id": row["_id"], "generation": existing["generation"],
             "status": "refused"},
            {"$set": {key: value for key, value in row.items() if key != "_id"},
             "$unset": {key: "" for key in
                        ("error", "result", "started_at", "finished_at", "lease_until", "claim_token")}},
            return_document=ReturnDocument.AFTER)
        if restarted:
            return _public(restarted)
        existing = await db.qoyod_manual_send_jobs.find_one({"_id": row["_id"]})
    return _public(existing)


async def read_job(db, *, user, job_id):
    actor = await require_qoyod_security_owner(db, user)
    row = await db.qoyod_manual_send_jobs.find_one(
        {"_id": str(job_id), "orders_user_id": actor["id"]})
    if row is None:
        raise HTTPException(404, "manual_send_job_not_found")
    return _public(row)


def _result(result, order_number):
    return {"ok": True, "order_number": order_number,
            **{key: result[key] for key in
               ("invoice_id", "invoice_number", "payment_id", "invoice_only", "retry_outcome")
               if key in result}}


async def execute_send(db, row):
    # The worker rechecks current authority; a queued request is not a durable
    # permission grant if the owner is disabled or the binding is changed.
    try:
        await require_qoyod_security_owner(db, {"id": row["actor_id"]})
    except HTTPException as exc:
        raise ManualSendRefused("manual_send_authority_changed",
            "لم يعد تفويض إرسال الطلب متاحًا") from exc
    actor = f"manual-job:{row['actor_id']}:{row['generation']}"
    if row["operation"] == "retry":
        from integrations.qoyod_manual.failed_retry import retry_failed_order
        return await retry_failed_order(db, orders_user_id=row["orders_user_id"],
            order_number=row["order_number"], actor=actor)
    from integrations.qoyod_manual.auto_send import _refresh_and_verify_salla_status
    from integrations.qoyod_manual.send import manual_send_one
    eligible, refresh = await _refresh_and_verify_salla_status(
        db, orders_user_id=row["orders_user_id"], order_number=row["order_number"])
    if not eligible:
        raise ManualSendRefused("not_qoyod_eligible_status",
            "حالة الطلب الحالية في سلة ليست ضمن حالات الإرسال المسموحة")
    return await manual_send_one(db, user_id="main", orders_user_id=row["orders_user_id"],
        order_number=row["order_number"], actor=actor,
        allow_missing_salla_order_date=True, allow_historical_positive_total=True)


async def _hold_unknown(db, row):
    """Do not release invoice locks or reopen a newer operator resolution."""
    at = row.get("started_at") or row["created_at"]
    selector = {"_id": f"main:{row['order_number']}"}
    old = await db.qoyod_manual_auto_quarantines.find_one(selector)
    if old:
        last = old.get("resolved_at") or old.get("last_seen_at")
        if isinstance(last, datetime):
            last = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
            at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
            if last > at:
                return
        selector.update(status=old.get("status"), resolved_at=old.get("resolved_at"),
                        last_seen_at=old.get("last_seen_at"))
    patch = {"user_id": "main", "order_number": row["order_number"],
             "status": "open", "code": "manual_job_outcome_unknown",
             "message": "انقطعت محاولة إرسال؛ يلزم التحقق من نتيجتها قبل إعادة الإرسال",
             "detail": {"job_id": row["_id"]}, "run_id": row["generation"],
             "last_seen_at": utcnow()}
    if old:
        await db.qoyod_manual_auto_quarantines.update_one(selector, {"$set": patch})
    else:
        with suppress(DuplicateKeyError):
            await db.qoyod_manual_auto_quarantines.insert_one(
                {"_id": selector["_id"], **patch, "first_seen_at": utcnow()})


async def recover_expired(db):
    # Bounded, metadata-only recovery. Never rerun the sender to find out what
    # happened and never label a provider timeout as proof of no invoice.
    rows = db.qoyod_manual_send_jobs.find({"$or": [
        {"status": "running", "lease_until": {"$lte": utcnow()}},
        {"status": "needs_review", "hold_pending": True},
    ]}).limit(20)
    async for row in rows:
        selector = {"_id": row["_id"], "generation": row["generation"],
                    "claim_token": row["claim_token"]}
        if row["status"] == "running":
            changed = await db.qoyod_manual_send_jobs.update_one(
                {**selector, "status": "running", "lease_until": {"$lte": utcnow()}},
                {"$set": {"status": "needs_review", "hold_pending": True,
                          "finished_at": utcnow(), "error": {
                              "code": "manual_job_interrupted",
                              "message": "انقطعت المهمة؛ تحقق من نتيجة الإرسال قبل أي إعادة محاولة"}}})
            if not changed.modified_count:
                continue
        await _hold_unknown(db, row)
        await db.qoyod_manual_send_jobs.update_one(
            {**selector, "status": "needs_review"}, {"$unset": {"hold_pending": ""}})


async def run_once(db, *, executor=None):
    await recover_expired(db)
    token = uuid.uuid4().hex
    at = utcnow()
    row = await db.qoyod_manual_send_jobs.find_one_and_update(
        {"status": "queued"}, {"$set": {"status": "running", "claim_token": token,
            "started_at": at, "lease_until": at + timedelta(seconds=LEASE_SECONDS)}},
        sort=[("created_at", 1)], return_document=ReturnDocument.AFTER)
    if row is None:
        return False
    selector = {"_id": row["_id"], "generation": row["generation"],
                "claim_token": token, "status": "running"}
    lease_lost = asyncio.Event()

    async def before_write():
        if not await db.qoyod_manual_send_jobs.find_one(
                {**selector, "lease_until": {"$gt": utcnow()}}, {"_id": 1}):
            raise RuntimeError("manual_job_lease_lost")
        try:
            await require_qoyod_security_owner(db, {"id": row["actor_id"]})
        except HTTPException as exc:
            # A later write may follow an earlier successful provider effect.
            raise RuntimeError("manual_job_authority_changed") from exc

    from integrations.qoyod_manual.client import begin_job_write_guard, end_job_write_guard
    guard_token = begin_job_write_guard(before_write)
    execution = asyncio.create_task((executor or execute_send)(db, row),
                                    name="manual-send-job-execution")

    async def heartbeat():
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                renewed = await db.qoyod_manual_send_jobs.update_one(
                    {**selector, "lease_until": {"$gt": utcnow()}},
                    {"$set": {"lease_until": utcnow() + timedelta(seconds=LEASE_SECONDS)}})
                if renewed.matched_count != 1:
                    lease_lost.set()
                    execution.cancel()
                    return
            except Exception:
                lease_lost.set()
                execution.cancel()
                return

    beat = asyncio.create_task(heartbeat(), name="manual-send-job-heartbeat")
    patch = {"status": "needs_review", "error": {
        "code": "manual_job_interrupted",
        "message": "تعذر تأكيد النتيجة؛ تحقق من الفاتورة قبل إعادة الإرسال"}}
    try:
        result = await asyncio.wait_for(execution, MAX_SEND_SECONDS)
        patch = {"status": "succeeded", "result": _result(result, row["order_number"])}
    except ManualSendRefused as exc:
        if exc.code in SAFE_ALREADY_SENT_CODES:
            patch = {"status": "succeeded", "result": _result({
                "retry_outcome": "already_sent",
                "invoice_id": exc.extra.get("manual_qoyod_invoice_id") or exc.extra.get("qoyod_invoice_id"),
                "payment_id": exc.extra.get("manual_qoyod_payment_id"),
                "invoice_only": bool(exc.extra.get("invoice_only")),
            }, row["order_number"])}
        elif exc.code in PRE_WRITE_REFUSALS:
            patch = {"status": "refused", "error": {
                "code": exc.code, "message": exc.message}}
        else:
            patch = {"status": "needs_review", "error": {
                "code": exc.code,
                "message": "لم تُحسم نتيجة الإرسال بالكامل؛ يلزم التحقق قبل إعادة المحاولة"}}
    except HTTPException:
        patch = {"status": "needs_review", "error": {
            "code": "manual_job_execution_refused", "message": "يلزم التحقق من نتيجة الإرسال قبل إعادة المحاولة"}}
    except asyncio.CancelledError:
        if not lease_lost.is_set():
            raise
    except Exception as exc:
        log.warning("manual_send_job_failed type=%s", type(exc).__name__)
    finally:
        end_job_write_guard(guard_token)
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
        patch["finished_at"] = utcnow()
        patch["lease_until"] = utcnow()
        if patch["status"] == "needs_review":
            patch["hold_pending"] = True
        # A failed acknowledgement/DB write leaves the original running row
        # for conservative expiry recovery, never an automatic replay.
        async def finalize():
            changed = await db.qoyod_manual_send_jobs.update_one(
                {**selector, "lease_until": {"$gt": utcnow()}}, {"$set": patch})
            if changed.matched_count and patch["status"] == "needs_review":
                await _hold_unknown(db, row)
                await db.qoyod_manual_send_jobs.update_one(
                    {**selector, "status": "needs_review"}, {"$unset": {"hold_pending": ""}})
        with suppress(Exception):
            await asyncio.wait_for(finalize(), timeout=5)
    return True


async def run(db):
    while True:
        try:
            worked = await run_once(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("manual_send_job_worker_unavailable type=%s", type(exc).__name__)
            worked = False
        await asyncio.sleep(1 if worked else POLL_SECONDS)
