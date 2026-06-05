"""Background Excel-import job manager — iter-59.

Why this exists
---------------
Before iter-59, `POST /api/analyses` parsed the Excel file with openpyxl
(blocking the event loop) and then awaited `find_one + update_one` for
EVERY row sequentially. For a 1,000-row Salla export that's ~2,000 DB
round-trips plus several seconds of CPU work all inside a single request.
While that ran, Make.com webhook POSTs queued up behind it because the
event loop was starved.

After iter-59
-------------
1.  POST /api/analyses returns in <100 ms with a `job_id`.
2.  The heavy work runs in an `asyncio.create_task` that:
       • offloads `parse_salla_excel` to a thread (CPU-bound)
       • upserts orders in BATCHES of `BATCH_SIZE`
       • `await asyncio.sleep(0)` between batches → other coroutines
         (webhook ingestion, dashboard, etc.) get scheduled.
3.  Per-(user, order_number) `asyncio.Lock` keeps Excel & Make from
    racing on the same document, but DIFFERENT orders run in parallel.
4.  `import_jobs` collection persists status + per-row errors so the UI
    can show progress without blocking either source.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from orders_db import upsert_order
from shipping_companies import scrub_shipping_company as _scrub_ship  # iter-72

logger = logging.getLogger(__name__)

# Tune via env if needed — 50 rows per batch is a good middle ground.
BATCH_SIZE = 50

# Track running background tasks so they aren't GC'd mid-flight.
_RUNNING_TASKS: set[asyncio.Task] = set()

# Per-order locks (process-local). Keyed by (user_id, order_number).
# Locks are created on demand and never auto-cleaned — they're tiny
# (asyncio.Lock ≈ 200 bytes) and the lifetime of the process bounds them.
_ORDER_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def get_order_lock(user_id: str, order_number: str) -> asyncio.Lock:
    """Return a process-local async lock for (user_id, order_number).
    Same key → same Lock object across coroutines; different keys never block."""
    key = (user_id, order_number)
    lock = _ORDER_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ORDER_LOCKS[key] = lock
    return lock


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Persistence helpers ───────────────────────────────────────────────────
async def create_job(db, *, user_id: str, filename: str, total_rows: int,
                     params: dict) -> dict:
    """Insert a fresh import_job in `queued` state."""
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "filename": filename,
        "status": "queued",              # queued | processing | completed | failed
        "total_rows": int(total_rows or 0),
        "processed_rows": 0,
        "created_count": 0,
        "updated_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "errors": [],                    # cap at 50 most-recent rows
        "params": params or {},
        "created_at": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "analysis_id": None,             # set when the wrapped analysis doc is finalized
        "error_message": None,
    }
    await db.import_jobs.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def _patch_job(db, job_id: str, patch: dict) -> None:
    await db.import_jobs.update_one({"id": job_id}, {"$set": patch})


async def _push_job_errors(db, job_id: str, errs: list[dict]) -> None:
    """Append errors and bump error_count. Cap stored errors at 50 (latest)."""
    if not errs:
        return
    await db.import_jobs.update_one(
        {"id": job_id},
        {
            "$push": {"errors": {"$each": errs, "$slice": -50}},
            "$inc": {"error_count": len(errs)},
        },
    )


# ── Background worker ─────────────────────────────────────────────────────
async def run_excel_job(
    *,
    db,
    job_id: str,
    user_id: str,
    file_content: bytes,
    filename: str,
    params: dict,
):
    """Parse the uploaded Excel and stream upserts in batches.

    Errors in individual rows DO NOT fail the job — they're logged to the
    job's `errors` list. The job is marked `failed` only if parsing itself
    (or some unrecoverable infrastructure error) raises.
    """
    # Imports kept local to avoid circular deps with server.py
    from excel_parser import parse_salla_excel
    from report_builder import build_report
    from auth import (
        ensure_user_settings,
        DEFAULT_PAYMENT_METHODS,
        DEFAULT_SHIPPING_COMPANIES,
    )

    await _patch_job(db, job_id, {"status": "processing", "started_at": _now_iso()})

    # 1. Parse the file off-thread (openpyxl is sync CPU-bound).
    try:
        parsed = await asyncio.to_thread(parse_salla_excel, file_content)
    except Exception as exc:
        logger.exception("Excel parse failed for job %s", job_id)
        await _patch_job(db, job_id, {
            "status": "failed",
            "completed_at": _now_iso(),
            "error_message": f"تعذر قراءة الملف: {exc}",
        })
        return

    individual = parsed.get("orders_individual") or []
    await _patch_job(db, job_id, {"total_rows": len(individual)})

    settings = await ensure_user_settings(db, user_id)

    # 2. Build the analysis report (also off-thread — it's pure CPU).
    try:
        report = await asyncio.to_thread(
            build_report,
            parsed,
            settings.get("payment_methods", DEFAULT_PAYMENT_METHODS),
            settings.get("shipping_companies", DEFAULT_SHIPPING_COMPANIES),
            float(params.get("snapchat_ads") or 0),
            float(params.get("tiktok_ads") or 0),
            float(params.get("instagram_ads") or 0),
            float(params.get("product_costs") or 0),
        )
    except Exception as exc:
        logger.exception("Build report failed for job %s", job_id)
        await _patch_job(db, job_id, {
            "status": "failed",
            "completed_at": _now_iso(),
            "error_message": f"تعذر بناء التقرير: {exc}",
        })
        return

    # 3. Upsert orders in batches, yielding the event loop between batches.
    from server import _normalize_date_str  # late import to avoid cycle

    created = updated = skipped = 0
    batch_errors: list[dict] = []
    processed = 0

    for i in range(0, len(individual), BATCH_SIZE):
        batch = individual[i : i + BATCH_SIZE]

        for o in batch:
            order_number = (o.get("order_number") or "").strip()
            if not order_number:
                skipped += 1
                processed += 1
                continue
            try:
                order_date = _normalize_date_str(o.get("order_date_raw") or "")
                incoming = {
                    "order_id": o.get("order_id") or "",
                    "order_date": order_date,
                    "order_date_raw": o.get("order_date_raw") or "",
                    "order_date_inferred": False,
                    "order_status": o.get("order_status") or "",
                    "customer_name": o.get("customer_name") or "",
                    "customer_mobile": o.get("customer_mobile") or "",
                    "payment_method": o.get("payment_method") or "",
                    "shipping_company": _scrub_ship(o.get("shipping_company") or ""),
                    "shipping_cost": float(o.get("shipping_cost") or 0),
                    "subtotal": float(o.get("subtotal") or 0),
                    "discount": float(o.get("discount") or 0),
                    "total_amount": float(o.get("total_amount") or 0),
                    "currency": o.get("currency") or "",
                    "source": o.get("source") or "",
                }
                # Per-order lock prevents Excel + Make from racing on the
                # same doc. Different orders proceed in parallel.
                lock = get_order_lock(user_id, order_number)
                async with lock:
                    res = await upsert_order(
                        db, user_id, order_number, incoming,
                        source="excel", raw=o,
                    )
                if res["created"]:
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                batch_errors.append({
                    "row_index": processed,
                    "order_number": order_number,
                    "error": str(exc)[:300],
                    "at": _now_iso(),
                })
                logger.warning("Row upsert failed [%s] order=%s: %s",
                               job_id, order_number, exc)
            processed += 1

        # Persist progress + flush any errors so the UI can stream them.
        await _patch_job(db, job_id, {
            "processed_rows": processed,
            "created_count": created,
            "updated_count": updated,
            "skipped_count": skipped,
        })
        if batch_errors:
            await _push_job_errors(db, job_id, batch_errors)
            batch_errors = []
        # Yield the event loop so concurrent webhook handlers can run.
        await asyncio.sleep(0)

    # 4. Persist the analysis record (same shape as before so the rest of
    #    the app — dashboard, reports, history — needs zero changes).
    analysis_id = str(uuid.uuid4())
    analysis = {
        "id": analysis_id,
        "user_id": user_id,
        "name": params.get("name") or filename,
        "filename": filename,
        "source": "excel",
        "date": params.get("date") or datetime.now(timezone.utc).date().isoformat(),
        "created_at": _now_iso(),
        "report": report,
        "orders_imported": created,
        "orders_updated": updated,
        "import_job_id": job_id,
    }
    try:
        await db.analyses.insert_one(analysis)
    except Exception as exc:
        logger.exception("analyses insert failed for job %s", job_id)
        await _patch_job(db, job_id, {
            "status": "failed",
            "completed_at": _now_iso(),
            "error_message": f"فشل حفظ التحليل: {exc}",
        })
        return

    await _patch_job(db, job_id, {
        "status": "completed",
        "completed_at": _now_iso(),
        "analysis_id": analysis_id,
    })


def schedule_excel_job(*, db, job_id: str, user_id: str,
                       file_content: bytes, filename: str, params: dict) -> None:
    """Fire-and-forget: kick off the background worker and keep a strong ref."""
    task = asyncio.create_task(
        run_excel_job(
            db=db, job_id=job_id, user_id=user_id,
            file_content=file_content, filename=filename, params=params,
        )
    )
    _RUNNING_TASKS.add(task)
    task.add_done_callback(_RUNNING_TASKS.discard)


# ── HTTP routes ───────────────────────────────────────────────────────────
def attach_import_jobs_routes(parent_router: APIRouter, db) -> None:
    from auth import get_current_user_from_db

    router = APIRouter(prefix="/import-jobs", tags=["import-jobs"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    @router.get("")
    async def list_jobs(
        limit: int = 30,
        user: dict = Depends(current_user),
    ):
        """Return the user's most recent import jobs (UI polls this)."""
        limit = max(1, min(int(limit or 30), 200))
        cur = (
            db.import_jobs.find({"user_id": user["id"]}, {"_id": 0, "errors": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        items = await cur.to_list(limit)
        return {"items": items, "count": len(items)}

    @router.get("/{job_id}")
    async def get_job(job_id: str, user: dict = Depends(current_user)):
        doc = await db.import_jobs.find_one(
            {"id": job_id, "user_id": user["id"]}, {"_id": 0}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="مهمة الاستيراد غير موجودة")
        return doc

    @router.delete("/{job_id}")
    async def delete_job(job_id: str, user: dict = Depends(current_user)):
        # Refuse to delete a job that's still running so we don't lose status.
        doc = await db.import_jobs.find_one(
            {"id": job_id, "user_id": user["id"]}, {"_id": 0, "status": 1}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="مهمة الاستيراد غير موجودة")
        if doc.get("status") in {"queued", "processing"}:
            raise HTTPException(status_code=400, detail="لا يمكن حذف مهمة قيد المعالجة")
        await db.import_jobs.delete_one({"id": job_id, "user_id": user["id"]})
        return {"ok": True}

    parent_router.include_router(router)


async def ensure_import_jobs_indexes(db) -> None:
    await db.import_jobs.create_index([("user_id", 1), ("created_at", -1)])
    await db.import_jobs.create_index("id", unique=True)
