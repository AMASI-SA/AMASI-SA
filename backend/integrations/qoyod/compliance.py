"""Qoyod Compliance Watch — Pre-Day 3 Refinement.

Surfaces orders that "should be in Qoyod by now" but aren't, so an
operator can intervene before invoices are silently lost.

The compliance layer is purely **read-only**. It scans
`unified_orders` (the Salla orders ledger) and joins against
`qoyod_invoices` (the Qoyod send-state ledger) to produce two
buckets:

  • orphan_orders     — Salla status = "تم التنفيذ" AND no
                        corresponding `qoyod_invoices` row.
  • problem_orders    — `qoyod_invoices` row exists but its
                        `eligibility_status` is NOT `sent_to_qoyod`.

The Dashboard Alert (rendered only on the Qoyod page per user spec)
calls `compliance_summary()` to get counts. The Invoices Data Grid
calls `list_orphan_orders()` for the detailed table.

ADR-001 compliance:
   #4  Canonical Domain  — single source for eligibility computation.
   #11 Multi-Tenant      — every query is scoped by `user_id`.
   #13 Versioning        — output dict carries `schema_version: 1`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.models import (
    ELIGIBILITY_STATUSES, ELIGIBILITY_REASONS,
)


# Statuses that the user defined as the trigger for "must be in Qoyod"
# (Arabic "تم التنفيذ" + the English aliases the order_status_policy
# already classifies as `confirmed`).
COMPLETED_TRIGGER_STATUSES: frozenset[str] = frozenset({
    "تم التنفيذ",
    "completed",
    # Note: "تم التوصيل" (delivered) is also category=confirmed but the
    # user explicitly singled out "تم التنفيذ" as the Qoyod trigger
    # — so we only include the trigger value, not the wider category.
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def classify_eligibility(
    order: dict, qoyod_row: Optional[dict] = None,
) -> tuple[str, str]:
    """Return (eligibility_status, eligibility_reason) for a single
    Salla order.

    Inputs:
        order      — unified_orders document (Salla side).
        qoyod_row  — matching qoyod_invoices document, if any.

    Output:
        (status, reason)  — both come from the closed vocabularies
        ELIGIBILITY_STATUSES and ELIGIBILITY_REASONS.

    Pure function — no DB access, no time-of-day dependencies.
    """
    status = str(order.get("order_status") or "").strip()
    if status not in COMPLETED_TRIGGER_STATUSES:
        return ("not_eligible", "order_status_not_completed")

    # From here on, the order IS supposed to be in Qoyod.
    if qoyod_row is None:
        return ("eligible_pending", "order_completed_ready_to_send")

    qoyod_status = str(qoyod_row.get("status") or "").strip()
    if qoyod_status == "sent":
        return ("sent_to_qoyod", "already_sent")
    if qoyod_status == "invoice_sent_receipt_failed":
        return ("invoice_sent_receipt_failed", "qoyod_api_error")
    if qoyod_status in ("failed", "retrying"):
        # Decide reason from last_error if present, otherwise generic.
        err = qoyod_row.get("last_error") or {}
        code = str(err.get("code") or "")
        if "customer" in code:
            return ("failed_before_qoyod", "missing_customer_data")
        if "product" in code:
            return ("failed_before_qoyod", "missing_product_mapping")
        if "payment" in code:
            return ("failed_before_qoyod", "payment_method_mapping_missing")
        return ("failed_before_qoyod", "qoyod_api_error")
    # status == "pending" / "skipped" → still pending from compliance
    if qoyod_status == "skipped":
        return ("not_eligible", "order_status_not_completed")
    return ("eligible_pending", "order_completed_ready_to_send")


async def _fetch_qoyod_rows(db, user_id: str,
                            order_ids: list[str]) -> dict[str, dict]:
    """Bulk-load qoyod_invoices rows keyed by `salla_order_id`."""
    if not order_ids:
        return {}
    cursor = db.qoyod_invoices.find(
        {"user_id": user_id, "salla_order_id": {"$in": order_ids}},
        {"_id": 0, "salla_order_id": 1, "status": 1, "last_error": 1,
         "updated_at": 1, "qoyod_invoice_id": 1, "qoyod_invoice_number": 1,
         "qoyod_receipt_id": 1, "eligibility_status": 1, "pipeline_stage": 1,
         "stage_history": 1, "trace_id": 1},
    )
    rows: dict[str, dict] = {}
    async for r in cursor:
        rows[r["salla_order_id"]] = r
    return rows


async def list_orphan_orders(
    db, user_id: str,
    limit: int = 200,
    since: Optional[datetime] = None,
) -> list[dict]:
    """Return Salla orders that are eligible (status = "تم التنفيذ")
    but are missing from `qoyod_invoices` OR present-but-not-sent.

    Each item carries enough fields for the UI table (no join needed).
    """
    q: dict[str, Any] = {
        "user_id": user_id,
        "order_status": {"$in": list(COMPLETED_TRIGGER_STATUSES)},
    }
    if since is not None:
        q["order_date"] = {"$gte": since}

    proj = {
        "_id": 0, "salla_order_id": 1, "order_id": 1, "reference_id": 1,
        "order_status": 1, "order_date": 1, "completed_at": 1,
        "customer_name": 1, "customer_phone": 1, "customer_email": 1,
        "payment_method": 1, "total_amount": 1, "total": 1,
    }
    cursor = db.unified_orders.find(q, proj).sort(
        "order_date", -1).limit(limit)
    orders: list[dict] = []
    async for o in cursor:
        oid = o.get("salla_order_id") or o.get("order_id") \
              or o.get("reference_id")
        if not oid:
            continue
        o["__lookup_id"] = str(oid)
        orders.append(o)

    qrows = await _fetch_qoyod_rows(
        db, user_id, [o["__lookup_id"] for o in orders])

    output: list[dict] = []
    for o in orders:
        qrow = qrows.get(o["__lookup_id"])
        status, reason = classify_eligibility(o, qrow)
        if status == "sent_to_qoyod":
            # Already in Qoyod — not an orphan, skip from this list.
            continue
        output.append({
            "salla_order_id":      o["__lookup_id"],
            "order_status":        o.get("order_status"),
            "order_date":          o.get("order_date"),
            "completed_at":        o.get("completed_at"),
            "customer_name":       o.get("customer_name"),
            "customer_phone":      o.get("customer_phone"),
            "customer_email":      o.get("customer_email"),
            "payment_method":      o.get("payment_method"),
            "total_amount":        o.get("total_amount") or o.get("total"),
            "eligibility_status":  status,
            "eligibility_reason":  reason,
            "qoyod_status":        (qrow or {}).get("status"),
            "qoyod_invoice_id":    (qrow or {}).get("qoyod_invoice_id"),
            "qoyod_trace_id":      (qrow or {}).get("trace_id"),
            "qoyod_invoice_row":   (qrow or {}).get("salla_order_id")
                                   and qrow.get("trace_id"),
        })
    return output


async def reconciliation_check(db, user_id: str) -> dict:
    """Reconciliation card — three numbers + drilldown trigger.

    Output:
        {
          schema_version:           1,
          generated_at:             iso,
          eligible_orders_count:    int,   # Salla orders that SHOULD be in Qoyod
          qoyod_invoices_count:     int,   # rows successfully sent (status="sent")
          difference:               int,   # eligible - sent (>= 0 normally)
          has_diff:                 bool,
          drilldown_url:            str,   # frontend deep-link
          oldest_unsent_at:         iso | null,
        }

    Pure aggregation — never modifies state.
    """
    eligible = await db.unified_orders.count_documents({
        "user_id": user_id,
        "order_status": {"$in": list(COMPLETED_TRIGGER_STATUSES)},
    })
    sent = await db.qoyod_invoices.count_documents({
        "user_id": user_id, "status": "sent",
    })
    diff = max(0, eligible - sent)

    oldest_unsent = None
    if diff > 0:
        # Surface the date of the oldest eligible order that ISN'T sent.
        # Cheap heuristic: oldest completed order overall — refinement
        # comes in Day 4-5 when we join properly.
        oldest = await db.unified_orders.find_one(
            {"user_id": user_id,
             "order_status": {"$in": list(COMPLETED_TRIGGER_STATUSES)}},
            sort=[("order_date", 1)],
            projection={"_id": 0, "order_date": 1, "completed_at": 1},
        )
        if oldest:
            oldest_unsent = oldest.get("completed_at") or oldest.get("order_date")

    return {
        "schema_version":        1,
        "generated_at":          _now(),
        "eligible_orders_count": eligible,
        "qoyod_invoices_count":  sent,
        "difference":            diff,
        "has_diff":              diff > 0,
        "drilldown_url":         "/integrations/qoyod/invoices?filter=unsent",
        "oldest_unsent_at":      oldest_unsent,
    }



async def compliance_summary(db, user_id: str) -> dict:
    """Aggregate counts for the Dashboard Alert card on the Qoyod page.

    Output:
        {
          schema_version: 1,
          generated_at:    iso8601,
          completed_orders_total:  int,   # Salla "تم التنفيذ"
          sent_to_qoyod:           int,
          eligible_pending:        int,
          failed_before_qoyod:     int,
          invoice_sent_receipt_failed: int,
          oldest_pending_at:       iso8601 | null,
        }
    """
    completed_total = await db.unified_orders.count_documents({
        "user_id": user_id,
        "order_status": {"$in": list(COMPLETED_TRIGGER_STATUSES)},
    })
    sent = await db.qoyod_invoices.count_documents({
        "user_id": user_id, "status": "sent",
    })
    receipt_failed = await db.qoyod_invoices.count_documents({
        "user_id": user_id, "status": "invoice_sent_receipt_failed",
    })
    failed_before = await db.qoyod_invoices.count_documents({
        "user_id": user_id,
        "status": {"$in": ["failed", "retrying"]},
    })
    # Pending = eligible orders that AREN'T in qoyod_invoices with status=sent.
    # Cheap upper-bound: total completed - sent - receipt_failed - failed_before.
    eligible_pending = max(
        0, completed_total - sent - receipt_failed - failed_before)

    # Oldest pending order date (for the "you have orders waiting since X" note).
    oldest = await db.unified_orders.find_one(
        {"user_id": user_id,
         "order_status": {"$in": list(COMPLETED_TRIGGER_STATUSES)}},
        sort=[("order_date", 1)],
        projection={"_id": 0, "order_date": 1, "completed_at": 1},
    )
    oldest_at = None
    if oldest:
        oldest_at = oldest.get("completed_at") or oldest.get("order_date")

    return {
        "schema_version":              1,
        "generated_at":                _now(),
        "completed_orders_total":      completed_total,
        "sent_to_qoyod":               sent,
        "eligible_pending":            eligible_pending,
        "failed_before_qoyod":         failed_before,
        "invoice_sent_receipt_failed": receipt_failed,
        "oldest_pending_at":           oldest_at,
        # Echo the closed vocabularies so the UI knows what to render.
        "eligibility_statuses":        list(ELIGIBILITY_STATUSES),
        "eligibility_reasons":         list(ELIGIBILITY_REASONS),
    }
