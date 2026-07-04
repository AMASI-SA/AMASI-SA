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

    # ── Build the invoice_payment step's response carefully ─────────
    # Iter-290h.6 — When a row succeeded after a prior failed attempt,
    # both `error` (stale from attempt 1) and `body` (fresh from
    # attempt 2) can sit under `qoyod_responses.invoice_payment`. The
    # drawer used to render the whole blob, mixing the OLD failure
    # JSON with the NEW success body. The fix here is to surface ONLY
    # the segment that matches the step's CURRENT status, plus a
    # tiny `previous_error` breadcrumb so the operator can still find
    # the historical attempt if they need it.
    _ip_step_status = _status_for_invoice_payment_step(row)
    _ip_raw_response = (responses.get("invoice_payment")
                        or responses.get("receipt"))
    if isinstance(_ip_raw_response, dict):
        if _ip_step_status == "success":
            # On success, return body + qoyod_id + timing, NOT the
            # stale 422 error from a previous attempt.
            _ip_step_response = {
                k: v for k, v in _ip_raw_response.items()
                if k != "error"
            }
            if _ip_raw_response.get("error"):
                _ip_step_response["previous_error"] = (
                    _ip_raw_response.get("error"))
        elif _ip_step_status == "failed":
            # On failure, return error + timing, NOT a phantom body
            # that doesn't exist (defensive — shouldn't happen).
            _ip_step_response = {
                k: v for k, v in _ip_raw_response.items()
                if k != "body"
            }
        else:
            _ip_step_response = _ip_raw_response
    else:
        _ip_step_response = _ip_raw_response

    # ── Order of steps the operator expects to see ──────────────────
    steps = [
        {
            "key":     "customer",
            "title":   "إنشاء/مطابقة العميل",
            "stage":   "CUSTOMER_RESOLVED",
            "payload": (row.get("customer_resolution") or {}).get(
                "qoyod_request_payload")
                       or (row.get("customer_resolution") or {}).get(
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
            "key":     "invoice_payment",   # Iter-290h — was "receipt"
            "title":   "ربط السداد بالفاتورة في قيود",
            "stage":   "INVOICE_PAYMENT_CREATED",
            "payload": (payloads.get("invoice_payment")
                        or payloads.get("receipt")),   # legacy fallback
            "response": _ip_step_response,
            "duration_ms": (
                (responses.get("invoice_payment") or {}).get("duration_ms")
                or (responses.get("receipt") or {}).get("duration_ms")
                or (timings.get("INVOICE_PAYMENT_CREATED") or {}).get("duration_ms")
                or (timings.get("RECEIPT_CREATED") or {}).get("duration_ms")
            ),
            "status": _ip_step_status,
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

        # Phase 1 product-catalog auto-seed diagnostics.
        "product_catalog_seed": row.get("product_catalog_seed"),
        "product_catalog_user_id": row.get("product_catalog_user_id"),
        "product_catalog_seed_at": row.get("product_catalog_seed_at"),
        "product_catalog_seed_source": row.get("product_catalog_seed_source"),
        "product_catalog_seed_error": row.get("product_catalog_seed_error"),
    })


def _status_for_invoice_payment_step(row: dict) -> str:
    """Iter-290h.3 — Decide the operator-facing status for the 4d
    payment-link step. This is more nuanced than the generic
    `_status_for_stage` because:

      • The step can fail under TWO failure stages (PAYMENT_LINK_FAILED
        or PAYMENT_METHOD_MAPPING_MISSING).
      • The success can be recorded under the NEW stage
        `INVOICE_PAYMENT_CREATED` OR the legacy `RECEIPT_CREATED`
        token (rows that completed before Iter-290h shipped).

    Bug fixed here — previously, when a row sat in PARTIAL_FAILURE
    after PAYMENT_LINK_FAILED but had no `qoyod_invoice_payment_id`,
    the monitor fell through to the legacy `RECEIPT_CREATED` check
    which returned "pending" instead of "failed". The operator saw
    the step as still in progress while قيود was actually rejecting
    the request. Now we explicitly recognise the new failure stages."""
    last_failed = row.get("last_failed_stage") or ""
    pipeline_stage = row.get("pipeline_stage") or ""

    # Iter-290h.6 — Success FIRST, failure second.
    # When a payment landed successfully after a prior failed attempt
    # (`qoyod_invoice_payment_id` is set OR `INVOICE_PAYMENT_CREATED`
    # is in the row's stage history), that fact is the ground truth.
    # Previously the function checked `last_failed_stage` first, so a
    # row that retried successfully — but whose `last_failed_stage`
    # hadn't been cleared — kept reporting the step as "failed".
    # Production order 268494278 exposed this on 2026-06-28.
    history_targets = {h.get("to_stage") for h in
                       (row.get("stage_history") or [])}
    if row.get("qoyod_invoice_payment_id"):
        return "success"
    if "INVOICE_PAYMENT_CREATED" in history_targets:
        return "success"
    if pipeline_stage == "COMPLETED":
        return "success"
    # Legacy /receipts path — historical rows only. Surface as
    # success so old completed orders don't suddenly turn red.
    if "RECEIPT_CREATED" in history_targets or row.get("qoyod_receipt_id"):
        if pipeline_stage != "FAILED_RECEIPT":
            return "success"

    # NEW-FLOW failures — the row is currently in a failed state with
    # no successful settlement on record.
    if last_failed in ("PAYMENT_LINK_FAILED",
                       "PAYMENT_METHOD_MAPPING_MISSING"):
        return "failed"
    if pipeline_stage in ("PAYMENT_LINK_FAILED",
                          "PAYMENT_METHOD_MAPPING_MISSING"):
        return "failed"
    # Legacy /receipts failure.
    if last_failed == "FAILED_RECEIPT" or pipeline_stage == "FAILED_RECEIPT":
        return "failed"

    # Default: still in progress.
    if pipeline_stage == "SKIPPED":
        return "skipped"
    return "pending"


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


# ─────────────────────────────────────────────────────────────────────
# Duplicate-attempt detection + cleanup (Iter-280)
# ─────────────────────────────────────────────────────────────────────
# Before Iter-280's idempotency-key fix, legacy Make flat payloads
# produced random `salla:unknown:<uuid>` keys, so every webhook for
# the same order created a fresh inbox row. Production rows from
# before the fix still show this duplication. These helpers let the
# operator inspect groups and archive all-but-one of each group.
DUPLICATE_CONFIRM_TOKEN = "MERGE"


class DuplicateMergeRefused(Exception):
    """Raised when the duplicate-merge request fails its safety checks."""


def _extract_event_and_status_from_row(row: dict) -> tuple[str, str]:
    """Best-effort extraction of (event_type, status_slug) for grouping.
    Mirrors what `derive_idempotency_key` reads, but tolerant of every
    historical shape we have written to the inbox.
    """
    raw = row.get("raw_payload") or {}
    adapted = row.get("adapted_payload") or {}
    canonical = row.get("canonical_payload") or {}

    # Event — canonical first, then adapter wrap, then raw.
    event = (canonical.get("metadata") or {}).get("source_event") \
            or (adapted.get("event") if isinstance(adapted, dict) else None) \
            or (raw.get("event") if isinstance(raw, dict) else None) \
            or (raw.get("event_type") if isinstance(raw, dict) else None) \
            or "order"
    event = str(event).strip() or "order"

    # Status slug — canonical first, then raw root keys.
    status = canonical.get("order_status")
    if not status and isinstance(raw, dict):
        # First try nested raw.data.status (canonical Salla shape).
        data_node = raw.get("data") if isinstance(raw.get("data"), dict) else None
        if data_node:
            st_node = data_node.get("status")
            if isinstance(st_node, dict):
                for k in ("slug", "name", "key"):
                    v = st_node.get(k)
                    if isinstance(v, str) and v.strip():
                        status = v.strip().lower()
                        break
            elif isinstance(st_node, str) and st_node.strip():
                status = st_node.strip().lower()
        # Fall back to legacy ROOT keys.
        if not status:
            for k in ("order_status_slug", "status_slug", "order_status",
                      "current_status", "status"):
                v = raw.get(k)
                if isinstance(v, str) and v.strip():
                    status = v.strip().lower()
                    break
    return event, (status or "none")


async def find_duplicate_groups(
    db, *, user_id: str, only_failed: bool = True,
) -> list[dict]:
    """Return the list of duplicate groups.

    A "group" = ≥2 inbox rows sharing the same
    `(salla_order_number, event, status_slug)` tuple for this tenant.

    Each group dict:
      {
        "order_number":  "268632361",
        "event":         "order_completed",
        "status_slug":   "completed",
        "attempts":      [{trace_id, received_at, pipeline_stage,
                            idempotency_key, dry_run}, ... newest-first],
        "latest_trace":  "33c07a10...",
        "oldest_trace":  "eac68e66...",
      }

    Set `only_failed=False` to include groups whose latest attempt
    completed successfully (rare — only happens if the user
    re-triggered a webhook by hand AFTER the order was already
    invoiced; we still surface so they can clean noise).
    """
    q: dict = {"user_id": user_id, "salla_order_number": {"$ne": None}}
    if only_failed:
        # Surface groups that contain at least one failed terminal row
        # (those are the actionable duplicates). We filter after
        # grouping below; the query still narrows to rows with an
        # order number so we don't scan the world.
        pass

    # In-memory grouping — duplicates are rare; the operator only
    # sees ~50 rows max.
    groups: dict[tuple[str, str, str], list[dict]] = {}
    cursor = db.integration_inbox.find(
        q, sort=[("received_at", -1)])
    async for row in cursor:
        order_number = row.get("salla_order_number")
        if not order_number:
            continue
        event, status = _extract_event_and_status_from_row(row)
        key = (str(order_number), event, status)
        groups.setdefault(key, []).append({
            "trace_id":        row.get("trace_id"),
            "row_id":          row.get("id"),
            "received_at":     row.get("received_at"),
            "pipeline_stage":  row.get("pipeline_stage"),
            "idempotency_key": row.get("idempotency_key"),
            "dry_run":         bool(row.get("dry_run")),
            "qoyod_invoice_id": row.get("qoyod_invoice_id"),
        })

    out: list[dict] = []
    for (order_number, event, status), attempts in groups.items():
        if len(attempts) < 2:
            continue
        if only_failed:
            # Skip groups that ended successfully — they're not the
            # user's pain point and likely include legitimate
            # transitions (under_review → completed) that we don't
            # want to dedupe.
            has_failed = any(a.get("pipeline_stage") in FAILED_STAGES
                              for a in attempts)
            if not has_failed:
                continue
        # newest first by received_at
        attempts.sort(key=lambda a: a.get("received_at") or "", reverse=True)
        out.append({
            "order_number":  order_number,
            "event":         event,
            "status_slug":   status,
            "attempts":      attempts,
            "attempt_count": len(attempts),
            "latest_trace":  (attempts[0] or {}).get("trace_id"),
            "oldest_trace":  (attempts[-1] or {}).get("trace_id"),
            # Sticky-keep suggestion: if exactly ONE attempt has a
            # non-DEAD_LETTER stage, prefer that. Else prefer the
            # newest. The operator can override.
            "suggested_keep_trace": _suggest_keep_trace(attempts),
        })
    # newest groups first
    out.sort(
        key=lambda g: (g["attempts"][0].get("received_at") or ""),
        reverse=True,
    )
    return out


def _suggest_keep_trace(attempts: list[dict]) -> str | None:
    """Heuristic — prefer the most progressed attempt; fall back to
    the newest one if all are failed at the same level."""
    if not attempts:
        return None
    # Rank by progress: COMPLETED > non-failed-terminal > FAILED > DEAD_LETTER.
    def progress_score(a: dict) -> tuple[int, str]:
        stage = a.get("pipeline_stage") or ""
        if stage == "COMPLETED":
            score = 4
        elif stage == "PARTIAL_FAILURE":
            score = 3
        elif stage in FAILED_STAGES:
            score = 1
        elif stage in SUCCESS_STAGES:
            score = 4
        else:
            score = 2
        return (score, a.get("received_at") or "")
    best = max(attempts, key=progress_score)
    return best.get("trace_id")


async def archive_duplicate_attempts(
    db, *, user_id: str, order_number: str,
    event: str, status_slug: str,
    keep_trace_id: str, confirm_token: str, actor: str,
) -> dict:
    """Archive every duplicate attempt in the group EXCEPT
    `keep_trace_id`. Safety:
      • Confirm token must equal `MERGE`.
      • The keep_trace_id MUST exist in the group, else refuse.
      • NEVER touches Qoyod itself.
      • NEVER touches any row outside the group's
        (order_number, event, status_slug) tuple.
      • Archive insert happens BEFORE delete (recoverable).
    """
    if (confirm_token or "").strip() != DUPLICATE_CONFIRM_TOKEN:
        raise DuplicateMergeRefused(
            f"confirm_token must equal {DUPLICATE_CONFIRM_TOKEN!r}")
    if not order_number or not keep_trace_id:
        raise DuplicateMergeRefused(
            "order_number and keep_trace_id are required")

    # Fetch all rows for this order_number; filter by event+status in
    # python (same logic as find_duplicate_groups so the group is
    # consistent with what the operator saw).
    rows: list[dict] = []
    async for row in db.integration_inbox.find(
            {"user_id": user_id, "salla_order_number": order_number}):
        ev, st = _extract_event_and_status_from_row(row)
        if ev == event and st == status_slug:
            rows.append(row)

    if len(rows) < 2:
        raise DuplicateMergeRefused(
            f"group has only {len(rows)} attempt(s) — nothing to merge")

    keep = next((r for r in rows if r.get("trace_id") == keep_trace_id), None)
    if not keep:
        raise DuplicateMergeRefused(
            f"keep_trace_id={keep_trace_id} not in group "
            f"(order_number={order_number}, event={event}, "
            f"status={status_slug})")

    losers = [r for r in rows if r.get("trace_id") != keep_trace_id]
    if not losers:
        return {"matched": len(rows), "archived": 0, "deleted": 0,
                "archive_ids": [], "kept_trace": keep_trace_id}

    now = datetime.now(timezone.utc)
    archive_docs = []
    loser_trace_ids = []
    for row in losers:
        doc = dict(row)
        original_id = doc.pop("_id", None)
        doc["archived_at"] = now.isoformat()
        doc["archived_by"] = actor or "system"
        doc["archive_reason"] = "duplicate_attempt_merged"
        doc["duplicate_group"] = {
            "order_number": order_number,
            "event":        event,
            "status_slug":  status_slug,
            "kept_trace":   keep_trace_id,
        }
        doc["original_inbox_id"] = str(original_id) if original_id else None
        archive_docs.append(doc)
        if row.get("trace_id"):
            loser_trace_ids.append(row["trace_id"])

    # 1) Insert archive copies first.
    ins = await db.integration_inbox_archive.insert_many(archive_docs)
    archive_ids = [str(x) for x in ins.inserted_ids]

    # 2) Delete losers — strict filter constrained to the trace_ids
    # we know about + tenant.
    delete_q = {
        "user_id": user_id,
        "salla_order_number": order_number,
        "trace_id": {"$in": loser_trace_ids},
    }
    res = await db.integration_inbox.delete_many(delete_q)

    # 3) Stamp the kept row with attempt history so the operator can
    # see the duplicates that were merged into it.
    merged_summary = [
        {"trace_id": d.get("trace_id"),
         "received_at": d.get("received_at"),
         "pipeline_stage": d.get("pipeline_stage")}
        for d in losers
    ]
    await db.integration_inbox.update_one(
        {"user_id": user_id, "trace_id": keep_trace_id},
        {"$set": {
            "duplicate_attempts_merged_at": now.isoformat(),
            "duplicate_attempts_merged_by": actor or "system",
        },
         "$push": {
            "duplicate_attempts_archive": {"$each": merged_summary},
         }},
    )

    return {
        "matched":      len(rows),
        "archived":     len(archive_ids),
        "deleted":      int(res.deleted_count),
        "archive_ids":  archive_ids,
        "kept_trace":   keep_trace_id,
        "merged_traces": loser_trace_ids,
    }
