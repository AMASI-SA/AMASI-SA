"""Diagnostic-only exclusion tracer for the Plan-B "pending orders"
list.

READ-ONLY. This module simulates the exact filter chain used by
`list_pending_orders` for a caller-supplied list of order_numbers,
and reports — per order — every gate that decided whether the order
appears in Page B or not.

REMOVE this module (and its route + tests) once we've stabilised the
Plan-B pending list unification.
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Any, Optional

from integrations.qoyod.eligible_orders import QOYOD_SYNC_START_DATE
from integrations.qoyod_manual.pending import (
    _matches_status, _salla_order_created_date,
    _already_sent, _sent_in_any_local_record,
    SUPPORTED_STATUSES,
)


_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)


async def _qoyod_ref_hit(db, *, user_id: str,
                         order_number: str) -> Optional[dict]:
    """Return the qoyod_invoices row matching strictly by reference."""
    on = str(order_number).strip()
    if not on:
        return None
    return await db.qoyod_invoices.find_one(
        {"user_id": user_id,
         "$or": [
             {"reference":          on},
             {"salla_order_number": on},
             {"external_reference": on},
             {"source_reference":   on},
         ]},
        {"_id": 0, "qoyod_invoice_id": 1, "invoice_number": 1,
         "reference": 1, "status": 1, "remaining": 1,
         "total": 1, "paid_amount": 1, "created_at": 1},
        sort=[("created_at", -1)],
    )


async def _all_traces(db, *, user_id: str,
                      order_number: str) -> list[dict]:
    """All integration_inbox rows for this order_number, newest first."""
    on = str(order_number).strip()
    if not on:
        return []
    cursor = db.integration_inbox.find(
        {"user_id": user_id, "salla_order_number": on},
        {"_id": 0, "id": 1, "trace_id": 1, "salla_order_number": 1,
         "salla_order_id": 1, "received_at": 1,
         "manual_qoyod_invoice_id": 1,
         "manual_qoyod_payment_id": 1, "qoyod_invoice_id": 1,
         "canonical_payload.order_status": 1,
         "canonical_payload.order_status_native": 1,
         "canonical_payload.order_date": 1,
         "canonical_payload.created_at": 1,
         "canonical_payload.total_amount": 1,
         "raw_payload.data.date": 1,
         "raw_payload.data.created_at": 1},
    ).sort("received_at", -1)
    return [r async for r in cursor]


def _would_be_in_top_limit(
    all_recent_rows: list[str], target_row_id: str, limit: int,
) -> bool:
    """Simulate the `sort received_at DESC + limit` window."""
    return target_row_id in all_recent_rows[:limit]


async def diagnose_pending_exclusion(
    db, *, user_id: str, order_numbers: list[str],
    status: str = "delivered",
    days: int = 60, limit: int = 200,
) -> dict:
    """For each order_number, return a comprehensive diagnostic on
    whether/why it appears in `list_pending_orders(status=...)`.

    Parameters mirror the pending endpoint EXACTLY: same defaults,
    same status set. Any drift here would defeat the purpose."""
    if status not in SUPPORTED_STATUSES:
        return {"ok": False, "error": "unsupported_status",
                "supported": list(SUPPORTED_STATUSES)}

    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 1000))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Build the SAME cursor Page B uses, then rank each row so we can
    # tell if the caller's target order made the top-`limit` window.
    top_cursor = db.integration_inbox.find(
        {"user_id": user_id, "received_at": {"$gte": cutoff}},
        {"_id": 0, "id": 1, "salla_order_number": 1, "received_at": 1},
    ).sort("received_at", -1).limit(limit)
    top_ids: list[str] = []
    top_order_numbers_seen: dict[str, int] = {}
    idx = 0
    async for r in top_cursor:
        top_ids.append(r.get("id") or "")
        _on = str(r.get("salla_order_number") or "")
        if _on and _on not in top_order_numbers_seen:
            top_order_numbers_seen[_on] = idx
        idx += 1

    results: list[dict] = []
    for raw_on in order_numbers:
        on = str(raw_on or "").strip()
        entry: dict[str, Any] = {
            "order_number": on,
            "checks": {},
            "verdict": None,
            "primary_exclusion_reason": None,
        }
        if not on:
            entry["verdict"] = "invalid_input"
            entry["primary_exclusion_reason"] = "empty_order_number"
            results.append(entry)
            continue

        # 1. Traces in integration_inbox (newest first).
        traces = await _all_traces(db, user_id=user_id, order_number=on)
        entry["in_integration_inbox"] = len(traces) > 0
        entry["inbox_trace_count"]    = len(traces)
        newest = traces[0] if traces else None

        # 2. قيود ref hit (strict by reference).
        inv = await _qoyod_ref_hit(db, user_id=user_id, order_number=on)
        entry["in_qoyod_invoices_reference"] = bool(inv)
        entry["qoyod_reference_hit"]         = inv or None

        if not newest:
            entry["verdict"] = "excluded"
            entry["primary_exclusion_reason"] = "no_inbox_row"
            results.append(entry)
            continue

        # 3. Extract fields from newest trace.
        canon = newest.get("canonical_payload") or {}
        received = newest.get("received_at")
        salla_date = _salla_order_created_date(newest)
        entry["received_at"] = (received.isoformat()
                                 if hasattr(received, "isoformat")
                                 else received)
        entry["salla_order_date"] = (salla_date.isoformat()
                                      if salla_date else None)
        entry["salla_status_slug"]   = canon.get("order_status")
        entry["salla_status_native"] = canon.get("order_status_native")
        entry["newest_trace_id"]     = newest.get("trace_id")
        entry["newest_row_id"]       = newest.get("id")

        # 4. Run each gate in the SAME order as list_pending_orders.
        checks = entry["checks"]

        # Gate A: received_at within days window.
        rx_pass = False
        if isinstance(received, datetime):
            # Normalise tz.
            rx = received.replace(tzinfo=timezone.utc) \
                if received.tzinfo is None else received
            rx_pass = rx >= cutoff
        checks["passes_received_at_window"] = rx_pass

        # Gate B: newest row is in the top-`limit` window (only
        # meaningful if received_at also passes).
        checks["passes_top_limit_window"] = (
            entry["newest_row_id"] in top_ids)

        # Gate C: Salla-source date resolvable.
        checks["passes_salla_date_extraction"] = salla_date is not None

        # Gate D: Salla date >= FLOOR (2026-07-01).
        checks["passes_floor_date"] = (
            salla_date is not None and salla_date >= _FLOOR_DATE)

        # Gate E: Salla status matches the requested tab.
        checks["passes_status_matcher"] = _matches_status(newest, status)
        checks["status_matcher_target"] = status

        # Gate F: newest trace has NO already-sent marker.
        already, marker_ref = _already_sent(newest)
        checks["excluded_by_already_sent_marker"] = already
        checks["already_sent_marker_ref"]         = marker_ref

        # Gate G: cross-trace sent.
        cross = await _sent_in_any_local_record(
            db, user_id=user_id, order_number=on,
            salla_order_id=str(newest.get("salla_order_id") or "") or None)
        checks["excluded_by_cross_trace_sent"] = bool(cross)
        checks["cross_trace_invoice_id"]      = cross

        # ── Compose final verdict — first failing gate wins.
        reason: Optional[str] = None
        if not checks["passes_received_at_window"]:
            reason = "received_at_out_of_window"
        elif not checks["passes_top_limit_window"]:
            reason = "outside_top_limit_window"
        elif not checks["passes_salla_date_extraction"]:
            reason = "no_salla_date"
        elif not checks["passes_floor_date"]:
            reason = "pre_floor_date"
        elif not checks["passes_status_matcher"]:
            reason = "status_mismatch"
        elif checks["excluded_by_already_sent_marker"]:
            reason = "already_sent_marker"
        elif checks["excluded_by_cross_trace_sent"]:
            reason = "cross_trace_sent"

        if reason is None:
            entry["verdict"] = "would_appear_in_page_b"
            entry["primary_exclusion_reason"] = None
        else:
            entry["verdict"] = "excluded"
            entry["primary_exclusion_reason"] = reason

        results.append(entry)

    return {
        "ok":                  True,
        "status":              status,
        "days":                days,
        "limit":               limit,
        "floor_date":          _FLOOR_DATE.isoformat(),
        "cutoff":              cutoff.isoformat(),
        "top_limit_window_size": len(top_ids),
        "unique_orders_in_window": len(top_order_numbers_seen),
        "orders":              results,
    }
