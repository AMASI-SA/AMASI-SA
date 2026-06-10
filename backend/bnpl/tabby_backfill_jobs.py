"""Tabby backfill — job-based batched runner (Iter-116).

Replaces the all-in-one sync that timed-out behind Cloudflare with a
small, resumable batch model.  Each call processes ~5 pages (≤100
payments) then returns a tiny JSON so the proxy never sees a response
larger than a couple of KB.

Job document (collection `bnpl_sync_jobs`):
{
    job_id, user_id, provider, status (running|done|error),
    cutoff_date,
    last_offset, pages_read,
    fetched, saved,
    first_date, last_date,
    started_at, updated_at,
    error_msg,
}
"""
from __future__ import annotations

import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .clients.tabby import TabbyClient, TabbyError
from .config_store import get_raw_secrets
from .sync_service import (
    _extract_refund_rows,
    _merge_into_unified_orders,
    _normalise_payment,
)


PAGE_SIZE = 20         # Tabby max
PAGES_PER_BATCH = 5    # 5 × 20 = 100 payments per call → fits Cloudflare


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_jobs_indexes(db) -> None:
    try:
        await db.bnpl_sync_jobs.create_index(
            [("user_id", 1), ("provider", 1), ("status", 1)],
            name="bnpl_jobs_lookup",
        )
    except Exception:
        pass


async def start_tabby_backfill(
    db, user_id: str, *, cutoff_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Initialise a new job and run the first batch."""
    secrets = await get_raw_secrets(db, user_id, "tabby")
    if not secrets.get("secret_key"):
        return {"ok": False, "error": "Tabby secret_key not set"}

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "user_id": user_id,
        "provider": "tabby",
        "status": "running",
        "cutoff_date": (cutoff_date or "")[:10] or None,
        "last_offset": 0,
        "pages_read": 0,
        "fetched": 0,
        "saved": 0,
        "first_date": None,
        "last_date": None,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
        "error_msg": None,
    }
    await db.bnpl_sync_jobs.insert_one(job)
    return await _run_one_batch(db, job_id)


async def continue_tabby_backfill(db, job_id: str) -> Dict[str, Any]:
    """Process the next batch of an existing job."""
    return await _run_one_batch(db, job_id)


async def get_job_status(db, job_id: str) -> Optional[Dict[str, Any]]:
    job = await db.bnpl_sync_jobs.find_one({"job_id": job_id}, {"_id": 0})
    return job


async def _run_one_batch(db, job_id: str) -> Dict[str, Any]:
    started = time.monotonic()
    job = await db.bnpl_sync_jobs.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        return {"ok": False, "error": "Unknown job_id"}
    if job.get("status") == "done":
        return {"ok": True, "job_id": job_id, "status": "done",
                "pages_read": job.get("pages_read", 0),
                "total_fetched": job.get("fetched", 0),
                "total_saved": job.get("saved", 0),
                "skipped_old": job.get("skipped_old", 0),
                "oldest_date_seen": job.get("first_date") or "",
                "newest_date_seen": job.get("last_date") or "",
                "stop_reason": job.get("stop_reason") or "already_done"}

    uid = job["user_id"]
    cutoff = job.get("cutoff_date") or ""
    try:
        secrets = await get_raw_secrets(db, uid, "tabby")
        if not secrets.get("secret_key"):
            raise RuntimeError("Tabby secret_key not set")

        client = TabbyClient(
            secret_key=secrets["secret_key"],
            merchant_code=secrets.get("merchant_code") or "",
            base_url=secrets.get("api_base_url") or "https://api.tabby.sa",
        )

        offset = int(job.get("last_offset") or 0)
        pages_done = 0
        batch_fetched = 0           # items SEEN from Tabby in this batch
        batch_saved = 0             # items WRITTEN to payment_transactions
        batch_skipped_old = 0       # items dropped client-side by cutoff
        batch_item_errors = 0       # rows that errored (kept job alive)
        first_date = job.get("first_date")   # OLDEST date envelope so far
        last_date = job.get("last_date")     # NEWEST date envelope so far
        finished = False
        stop_reason = ""
        sample_errors: list = []

        for _ in range(PAGES_PER_BATCH):
            # ⚠️ NO date filter sent to Tabby — pagination only.
            # Cutoff is applied CLIENT-SIDE per item (skip, never break)
            # because Tabby's payment ordering is NOT guaranteed.
            page = await client.list_payments(limit=PAGE_SIZE, offset=offset)
            items = (page or {}).get("payments") if isinstance(page, dict) else []
            items = items or []
            pages_done += 1

            if not items:
                finished = True
                stop_reason = "empty_page"
                break

            for it in items:
                batch_fetched += 1
                created = (it.get("created_at") or "")[:10]

                # Always update date envelope (even for skipped items)
                # so the merchant sees the FULL range Tabby is returning.
                if created:
                    if first_date is None or created < first_date:
                        first_date = created
                    if last_date is None or created > last_date:
                        last_date = created

                # Client-side cutoff: SKIP (do not break) — Tabby
                # ordering is not chronological for this account.
                if cutoff and created and created < cutoff:
                    batch_skipped_old += 1
                    continue

                pid = (it.get("id") or "").strip()
                if not pid:
                    continue

                try:
                    txn = _normalise_payment(it, uid)
                    await db.payment_transactions.update_one(
                        {"user_id": uid, "provider": "tabby", "provider_id": pid},
                        {"$set": {k: v for k, v in txn.items() if k != "id"},
                         "$setOnInsert": {"id": txn["id"], "created_at": _now_iso()}},
                        upsert=True,
                    )
                    batch_saved += 1

                    for rfd in _extract_refund_rows(it, uid):
                        rid = rfd.get("provider_refund_id") or ""
                        if not rid:
                            continue
                        await db.payment_refunds.update_one(
                            {"user_id": uid, "provider": "tabby",
                             "provider_refund_id": rid},
                            {"$set": {k: v for k, v in rfd.items() if k != "id"},
                             "$setOnInsert": {"id": rfd["id"],
                                              "created_at": _now_iso()}},
                            upsert=True,
                        )

                    await _merge_into_unified_orders(db, uid, txn)
                except Exception as item_exc:  # noqa: BLE001
                    # Per-item guard: a single malformed/legacy row must
                    # NOT kill the whole backfill job.  Log and continue.
                    batch_item_errors += 1
                    if len(sample_errors) < 5:
                        sample_errors.append({
                            "provider_id": pid,
                            "error": f"{type(item_exc).__name__}: {item_exc}",
                        })

            offset += len(items)
            if len(items) < PAGE_SIZE:
                finished = True
                stop_reason = "short_page"
                break

        if not finished and not stop_reason:
            stop_reason = "batch_limit_reached"

        new_status = "done" if finished else "running"
        update = {
            "status": new_status,
            "last_offset": offset,
            "pages_read": int(job.get("pages_read") or 0) + pages_done,
            "fetched": int(job.get("fetched") or 0) + batch_fetched,
            "saved": int(job.get("saved") or 0) + batch_saved,
            "skipped_old": int(job.get("skipped_old") or 0) + batch_skipped_old,
            "item_errors": int(job.get("item_errors") or 0) + batch_item_errors,
            "first_date": first_date,
            "last_date": last_date,
            "stop_reason": stop_reason,
            "last_sample_errors": sample_errors,
            "updated_at": _now_iso(),
        }
        await db.bnpl_sync_jobs.update_one(
            {"job_id": job_id}, {"$set": update},
        )

        return {
            "ok": True,
            "job_id": job_id,
            "status": new_status,
            "last_offset": update["last_offset"],
            "pages_read": update["pages_read"],
            "total_fetched": update["fetched"],
            "total_saved": update["saved"],
            "skipped_old": update["skipped_old"],
            "item_errors": update["item_errors"],
            "sample_errors": sample_errors,
            "oldest_date_seen": update["first_date"] or "",
            "newest_date_seen": update["last_date"] or "",
            "stop_reason": stop_reason,
            "batch_duration_seconds": round(time.monotonic() - started, 2),
        }

    except TabbyError as exc:
        await db.bnpl_sync_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "error",
                      "error_msg": f"TabbyError: {exc}",
                      "updated_at": _now_iso()}},
        )
        return {"ok": False, "job_id": job_id,
                "error": f"TabbyError: {exc}"}
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc(limit=10)
        await db.bnpl_sync_jobs.update_one(
            {"job_id": job_id},
            {"$set": {"status": "error",
                      "error_msg": f"{type(exc).__name__}: {exc}",
                      "error_traceback": tb,
                      "updated_at": _now_iso()}},
        )
        return {"ok": False, "job_id": job_id,
                "error": f"{type(exc).__name__}: {exc}"}
