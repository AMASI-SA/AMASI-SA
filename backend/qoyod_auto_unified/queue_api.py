"""Patched unsent-orders response with authoritative queue counts."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from integrations.qoyod.unsent_orders import DUPLICATE, FAILED, SENT, UNSENT

from .common import RETRYABLE_SYNC_FAILURE_CODES
from .queue_counts import _proof_classification, _queue_audit


async def _list_unsent_orders_with_queue_counts(
    original: Callable[..., Awaitable[dict[str, Any]]],
    db: Any,
    *,
    user_id: str,
    days: int = 30,
    limit: int = 500,
    orders_user_id: Optional[str] = None,
    status: Optional[str] = None,
    salla_status: Optional[str] = None,
    search: Optional[str] = None,
    now: Optional[datetime] = None,
    from_date: Any = None,
    to_date: Any = None,
) -> dict[str, Any]:
    from integrations.qoyod import unsent_orders as unsent_module

    effective_owner = str(orders_user_id or user_id)
    result = await original(
        db,
        user_id=user_id,
        days=days,
        limit=limit,
        orders_user_id=orders_user_id,
        status=None,
        salla_status=salla_status,
        search=search,
        now=now,
        from_date=from_date,
        to_date=to_date,
    )
    audit, failures, queue = await _queue_audit(
        db,
        user_id=str(user_id),
        orders_user_id=effective_owner,
        days=days,
        search=search,
        now=now,
        from_date=from_date,
        to_date=to_date,
    )
    proof_by_reference = {
        str(proof.get("order_number") or ""): proof
        for proof in audit.get("orders") or []
    }

    counts = {SENT: 0, UNSENT: 0, FAILED: 0, DUPLICATE: 0}
    classification_by_reference: dict[str, dict[str, Any]] = {}
    for reference, proof in proof_by_reference.items():
        classification = _proof_classification(
            unsent_module, proof, failures.get(reference)
        )
        classification_by_reference[reference] = classification
        counts[classification["status"]] += 1

    rows: list[dict[str, Any]] = []
    for entry in result.get("orders") or []:
        reference = str(entry.get("order_number") or "")
        classification = classification_by_reference.get(reference)
        if classification:
            entry = {
                **entry,
                "status": classification["status"],
                "reason": classification["reason"],
                "failure_code": classification.get("failure_code"),
                "failure_source": classification.get("failure_source"),
                "retry_allowed": bool(
                    classification.get("retry_allowed", False)
                ),
                "payment_eligibility": (
                    proof_by_reference.get(reference) or {}
                ).get("payment_eligibility"),
            }
        if status and entry.get("status") != status:
            continue
        rows.append(entry)

    matched = sum(
        1
        for classification in classification_by_reference.values()
        if not status or classification["status"] == status
    )
    result.update({
        "counts": counts,
        "total": sum(counts.values()),
        "queue_counts": queue,
        "queue_policy": {
            "source_authority": "unified_orders",
            "selection_order": "oldest_first",
            "retryable_sync_failure_codes": sorted(
                RETRYABLE_SYNC_FAILURE_CODES
            ),
        },
        "matched_order_count": matched,
        "returned_order_count": len(rows),
        "truncated": matched > len(rows),
        "orders": rows,
    })
    return result
