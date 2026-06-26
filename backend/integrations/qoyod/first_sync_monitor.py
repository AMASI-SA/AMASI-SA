"""Qoyod First-Sync Monitor — read-only operational diagnostic view.

Purpose (2026-06-27, user spec)
───────────────────────────────
Before flipping Dry Run off, the operator wants a single page that shows
end-to-end exactly what happened to the most recent N orders pushed
into the pipeline:

  • The Make.com raw webhook body
  • The canonical DTO after normalization
  • Each of the 4 Qoyod POSTs (customer → product → invoice → receipt)
      ‣ payload that WAS sent
      ‣ raw response received
      ‣ duration_ms
      ‣ resulting Qoyod ID
  • stage_history (transitions, timestamps, durations)
  • Pipeline outcome + last failure reason

The data already lives in `integration_inbox`. This module just shapes
it into an operator-friendly response.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Threshold above which a NORMALIZED/CUSTOMER_RESOLVED/INVOICE_CREATED
# row is treated as "stuck" — the worker should have processed it by
# then. The UI surfaces a "بانتظار العامل" badge & manual button.
STUCK_AFTER_SECONDS = 30
WAITING_STAGES = {
    "NORMALIZED", "RULES_APPLIED", "CUSTOMER_RESOLVED",
    "INVOICE_CREATED",  # intermediate, may stall before receipt
}


def _is_stuck(row: dict) -> dict | None:
    """Return `{stage, waited_seconds, reason}` if the row is stuck in
    an intermediate stage past the threshold, else `None`."""
    stage = row.get("pipeline_stage")
    if stage not in WAITING_STAGES:
        return None
    # Compute "waited" from the latest stage transition.
    history = row.get("stage_history") or []
    last_at = None
    for h in reversed(history):
        if h.get("to_stage") == stage:
            last_at = h.get("at")
            break
    if isinstance(last_at, str):
        try:
            last_at = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
        except Exception:
            last_at = None
    if not last_at:
        last_at = row.get("received_at")
    if not isinstance(last_at, datetime):
        return None
    now = datetime.now(timezone.utc)
    # Make `last_at` timezone-aware if it isn't.
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)
    waited = (now - last_at).total_seconds()
    if waited < STUCK_AFTER_SECONDS:
        return None
    return {
        "stage":  stage,
        "waited_seconds": int(waited),
        "reason": "بانتظار العامل (Background Worker) — قد يكون متأخراً.",
    }


def _isoize(v: Any) -> Any:
    """Recursively convert datetime → ISO strings so the response is
    JSON-serialisable. ObjectIds are stringified too."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _isoize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_isoize(x) for x in v]
    if hasattr(v, "binary") and hasattr(v, "__str__"):  # ObjectId-ish
        try:
            return str(v)
        except Exception:
            return None
    return v


# ─────────────────────────────────────────────────────────────────────
# Per-row shaper
# ─────────────────────────────────────────────────────────────────────
def shape_inbox_row_for_monitor(row: dict) -> dict:
    """Reduce a raw `integration_inbox` document into the structured
    timeline the operator sees in the UI."""
    history = row.get("stage_history") or []
    payloads = row.get("qoyod_payloads") or {}
    responses = row.get("qoyod_responses") or {}
    canonical = row.get("canonical_payload") or {}
    raw       = row.get("raw_payload") or row.get("raw") or {}

    # ── Compute per-stage timings from history ──────────────────────
    timings: dict[str, dict] = {}
    last_at = row.get("pipeline_started_at") or row.get("received_at")
    for h in history:
        to_stage = h.get("to_stage")
        at = h.get("at")
        if isinstance(at, str):
            try:
                at = datetime.fromisoformat(at.replace("Z", "+00:00"))
            except Exception:
                at = None
        if to_stage and last_at and at:
            dur = (at - last_at).total_seconds()
            timings[to_stage] = {
                "reached_at": at, "duration_ms": int(dur * 1000)}
        last_at = at or last_at

    # ── Order of steps the operator expects to see ──────────────────
    steps = [
        {
            "key":     "customer",
            "title":   "إنشاء/مطابقة العميل",
            "stage":   "CUSTOMER_RESOLVED",
            "payload": (row.get("customer_resolution") or {}).get(
                "customer_payload") or {},
            "response": {
                "qoyod_id":  row.get("qoyod_customer_id"),
                "created_new": (row.get("customer_resolution") or {})
                                  .get("created_new"),
                "lookup_keys": (row.get("customer_resolution") or {})
                                  .get("lookup_keys"),
            },
            "duration_ms": (timings.get("CUSTOMER_RESOLVED") or {})
                              .get("duration_ms"),
            "status": _status_for_stage(
                "CUSTOMER_RESOLVED", row, "FAILED_CUSTOMER"),
        },
        {
            "key":     "product",
            "title":   "إنشاء/مطابقة المنتجات",
            "stage":   "PRODUCT_RESOLVED",
            "payload": (row.get("product_resolution") or {}).get(
                "items") or [],
            "response": {
                "items": [
                    {"sku": r.get("sku"),
                     "qoyod_id": r.get("qoyod_product_id"),
                     "created_new": r.get("created_new")}
                    for r in ((row.get("product_resolution") or {})
                              .get("items") or [])
                ],
            },
            "duration_ms": (timings.get("PRODUCT_RESOLVED") or {})
                              .get("duration_ms"),
            "status": _status_for_stage(
                "PRODUCT_RESOLVED", row, "FAILED_PRODUCT"),
        },
        {
            "key":     "invoice",
            "title":   "إنشاء الفاتورة في قيود",
            "stage":   "INVOICE_CREATED",
            "payload": payloads.get("invoice"),
            "response": responses.get("invoice"),
            "duration_ms": (responses.get("invoice") or {})
                            .get("duration_ms")
                          or (timings.get("INVOICE_CREATED") or {})
                            .get("duration_ms"),
            "status": _status_for_stage(
                "INVOICE_CREATED", row, "FAILED_INVOICE"),
        },
        {
            "key":     "receipt",
            "title":   "إنشاء سند القبض في قيود",
            "stage":   "RECEIPT_CREATED",
            "payload": payloads.get("receipt"),
            "response": responses.get("receipt"),
            "duration_ms": (responses.get("receipt") or {})
                            .get("duration_ms")
                          or (timings.get("RECEIPT_CREATED") or {})
                            .get("duration_ms"),
            "status": _status_for_stage(
                "RECEIPT_CREATED", row, "FAILED_RECEIPT"),
        },
    ]

    return _isoize({
        "trace_id":           row.get("trace_id"),
        "inbox_id":           row.get("id"),
        "received_at":        row.get("received_at"),
        "pipeline_stage":     row.get("pipeline_stage"),
        "pipeline_outcome":   row.get("pipeline_outcome"),
        "pipeline_started_at": row.get("pipeline_started_at"),
        "pipeline_finished_at": row.get("pipeline_finished_at"),
        "pipeline_duration_ms": row.get("pipeline_duration_ms"),
        "last_success_stage": row.get("last_success_stage"),
        "last_failed_stage":  row.get("last_failed_stage"),
        "attempts":           row.get("attempts", 0),
        "dry_run":            row.get("dry_run", False),
        "stuck":              _is_stuck(row),
        "order_summary": {
            "order_id":     canonical.get("order_id"),
            "order_number": canonical.get("order_number"),
            "total_amount": canonical.get("total_amount"),
            "currency":     canonical.get("currency"),
            "items_count":  len(canonical.get("items") or []),
            "payment_method": canonical.get("payment_method"),
            "customer_name":  (canonical.get("customer") or {}).get("name"),
        },
        "make_raw_payload":   raw if raw else None,
        "canonical_dto":      canonical,
        "qoyod_steps":        steps,
        "stage_history":      history,
        "business_rules_decision": row.get("business_rules_decision"),
        "preflight":          row.get("preflight"),
    })


def _status_for_stage(stage: str, row: dict, fail_stage: str) -> str:
    """Decide one of: pending / success / failed / skipped for a step."""
    last_success = row.get("last_success_stage") or ""
    pipeline_stage = row.get("pipeline_stage") or ""
    if row.get("last_failed_stage") == fail_stage or pipeline_stage == fail_stage:
        return "failed"
    # Skipped (business rules said: do not send)
    if pipeline_stage == "SKIPPED":
        return "skipped"
    # Has the pipeline already reached/passed this stage?
    from integrations.qoyod.state_machine import HAPPY_PATH
    try:
        target_idx  = HAPPY_PATH.index(stage)
        success_idx = HAPPY_PATH.index(last_success) if last_success else -1
        if success_idx >= target_idx:
            return "success"
    except ValueError:
        pass
    return "pending"


# ─────────────────────────────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────────────────────────────
async def list_recent_for_monitor(
    db, *, user_id: str, limit: int = 5,
    only_outcomes: list[str] | None = None,
) -> list[dict]:
    """Return the latest N inbox rows reduced for the monitor UI."""
    q: dict = {"user_id": user_id}
    if only_outcomes:
        q["pipeline_outcome"] = {"$in": list(only_outcomes)}
    cursor = db.integration_inbox.find(
        q, sort=[("received_at", -1)], limit=max(1, min(limit, 25)))
    out: list[dict] = []
    async for row in cursor:
        out.append(shape_inbox_row_for_monitor(row))
    return out


async def get_row_for_monitor(
    db, *, user_id: str, trace_id: str,
) -> dict | None:
    row = await db.integration_inbox.find_one(
        {"user_id": user_id, "trace_id": trace_id})
    if not row:
        return None
    return shape_inbox_row_for_monitor(row)


# ─────────────────────────────────────────────────────────────────────
# Aggregate stats (sidebar badges + monitor counters)
# ─────────────────────────────────────────────────────────────────────
# Stages bucketed for the operator dashboard.
SUCCESS_STAGES = {"COMPLETED"}
FAILED_STAGES = {"DEAD_LETTER", "PARTIAL_FAILURE"}
SKIPPED_STAGES = {"SKIPPED"}
# Anything else (NEW, RECEIVED, VALIDATED, NORMALIZED, RULES_APPLIED,
# CUSTOMER_RESOLVED, PRODUCT_RESOLVED, INVOICE_CREATED, RECEIPT_CREATED,
# RETRYING, NEEDS_ENRICHMENT, FAILED_* in-flight) is "processing".


async def get_monitor_stats(db, *, user_id: str) -> dict:
    """Return aggregate counts across `integration_inbox` rows for the
    current tenant — used by the sidebar alert dot + monitor page
    counter badges.

    Buckets
    ───────
    • processing  — anything not in a terminal bucket
    • failed      — DEAD_LETTER + PARTIAL_FAILURE
    • success     — COMPLETED
    • skipped     — SKIPPED (business rule excluded the order)
    • dry_failed  — subset of `failed` where `dry_run is true`
                    (this is what the "archive failed tests" button
                    will target; surfaced so the UI shows the button
                    only when there's something to clean up)
    """
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id": {
                "stage": "$pipeline_stage",
                "dry_run": {"$ifNull": ["$dry_run", False]},
            },
            "n": {"$sum": 1},
        }},
    ]
    counts = {"processing": 0, "failed": 0, "success": 0,
              "skipped": 0, "dry_failed": 0, "total": 0}
    async for row in db.integration_inbox.aggregate(pipeline):
        stage = (row.get("_id") or {}).get("stage")
        is_dry = bool((row.get("_id") or {}).get("dry_run"))
        n = int(row.get("n") or 0)
        counts["total"] += n
        if stage in SUCCESS_STAGES:
            counts["success"] += n
        elif stage in FAILED_STAGES:
            counts["failed"] += n
            if is_dry:
                counts["dry_failed"] += n
        elif stage in SKIPPED_STAGES:
            counts["skipped"] += n
        else:
            counts["processing"] += n
    return counts


# ─────────────────────────────────────────────────────────────────────
# Archive failed dry-run tests
# ─────────────────────────────────────────────────────────────────────
# The "أرشفة فشل الاختبار القديم" button — only ever touches
# `integration_inbox` rows that are BOTH:
#   • in a failed terminal bucket (DEAD_LETTER or PARTIAL_FAILURE), AND
#   • were processed in Dry Run mode (`dry_run is true`).
#
# It NEVER touches:
#   • COMPLETED rows (real or dry)
#   • any non-failed stage
#   • any real (production) row, even if failed
#   • any data inside Qoyod itself (this is a local-only op)
#
# Behaviour: copy matched rows to `integration_inbox_archive` (with
# `archived_at` + `archived_by` + `archive_reason`), then delete from
# `integration_inbox`. This is recoverable — the archive collection
# is never auto-pruned.
ARCHIVE_CONFIRM_TOKEN = "CLEAN"


class ArchiveRefused(Exception):
    """Raised when the archive request fails its safety checks."""


async def archive_failed_dry_run_tests(
    db, *, user_id: str, confirm_token: str, actor: str,
) -> dict:
    """Archive (move + delete) DEAD_LETTER + PARTIAL_FAILURE dry-run
    rows for `user_id`. Returns `{matched, archived, deleted, archive_ids}`.

    Raises `ArchiveRefused` when the confirm token is wrong.
    """
    if (confirm_token or "").strip() != ARCHIVE_CONFIRM_TOKEN:
        raise ArchiveRefused(
            f"confirm_token must equal {ARCHIVE_CONFIRM_TOKEN!r}")

    # Strict filter — both conditions must hold.
    q = {
        "user_id": user_id,
        "pipeline_stage": {"$in": list(FAILED_STAGES)},
        "dry_run": True,
    }

    now = datetime.now(timezone.utc)
    matched: list[dict] = []
    async for row in db.integration_inbox.find(q):
        matched.append(row)

    if not matched:
        return {"matched": 0, "archived": 0, "deleted": 0, "archive_ids": []}

    # Stamp the archive metadata on each doc before insert.
    archive_docs = []
    archive_keys = []
    for row in matched:
        doc = dict(row)
        # Drop _id so Mongo assigns a fresh ObjectId in the archive
        # collection (avoids unique-index collisions on re-archive).
        original_id = doc.pop("_id", None)
        doc["archived_at"] = now.isoformat()
        doc["archived_by"] = actor or "system"
        doc["archive_reason"] = "dry_run_failed_test_cleanup"
        doc["original_inbox_id"] = str(original_id) if original_id else None
        archive_docs.append(doc)
        # We delete by `id` (string field that mirrors `_id` for our
        # PyObjectId pattern) plus the strict filter as a belt-and-
        # suspenders measure so we NEVER delete something outside the
        # filter even if races occur.
        archive_keys.append(row.get("id") or row.get("trace_id"))

    # 1) Insert archive copies first — if this fails, nothing is lost.
    ins = await db.integration_inbox_archive.insert_many(archive_docs)
    archive_ids = [str(x) for x in ins.inserted_ids]

    # 2) Delete the matched rows from the live collection — strict
    # filter again so we cannot drift outside the safety boundary.
    trace_ids = [r.get("trace_id") for r in matched if r.get("trace_id")]
    if trace_ids:
        delete_q = dict(q)
        delete_q["trace_id"] = {"$in": trace_ids}
    else:
        # Fallback: use the row ids (PyObjectId string mirror) if
        # trace_id is somehow missing. Still keeps the strict filter.
        ids = [k for k in archive_keys if k]
        delete_q = dict(q)
        if ids:
            delete_q["id"] = {"$in": ids}
    res = await db.integration_inbox.delete_many(delete_q)
    return {
        "matched": len(matched),
        "archived": len(archive_ids),
        "deleted": int(res.deleted_count),
        "archive_ids": archive_ids,
    }
