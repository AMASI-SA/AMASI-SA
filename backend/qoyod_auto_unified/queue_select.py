"""Bounded retry scheduling and oldest-first candidate selection."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from integrations.qoyod.candidate_orders import API_STATUS_TO_KEY, build_candidate_audit
from integrations.qoyod.eligible_orders import QOYOD_SYNC_START_DATE

from .common import RETRYABLE_SYNC_FAILURE_CODES, _TENANT, _now


async def _seed_retry_schedule(db: Any) -> int:
    """Give legacy sync quarantines one bounded retry without hot-looping."""
    now = _now()
    result = await db.qoyod_manual_auto_quarantines.update_many(
        {
            "user_id": _TENANT,
            "status": "open",
            "code": {"$in": sorted(RETRYABLE_SYNC_FAILURE_CODES)},
            "next_retry_at": {"$exists": False},
        },
        {"$set": {
            "next_retry_at": now,
            "recovery_class": "sync_retryable",
            "retry_schedule_seeded_at": now,
            "retry_schedule_seeded_by": "qoyod-unified-source-fix",
        }},
    )
    return int(getattr(result, "modified_count", 0) or 0)


def _retry_delay(code: str) -> timedelta:
    if code in {
        "authoritative_payment_method_still_pending",
        "authoritative_payment_needs_verification",
    }:
        return timedelta(minutes=10)
    if code == "salla_status_refresh_failed":
        return timedelta(minutes=2)
    return timedelta(minutes=1)


def _oldest_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("order_date") or "9999-12-31"),
        str(row.get("order_number") or ""),
    )


async def _load_candidate_rows_oldest_first(
    auto_send_module: Any,
    db: Any,
    *,
    settings: dict[str, Any],
    orders_user_id: str,
    batch_limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    await _seed_retry_schedule(db)
    snapshot = await build_candidate_audit(
        db,
        orders_user_id=str(orders_user_id),
        markers_user_id=_TENANT,
        marker_user_ids=(_TENANT, str(orders_user_id)),
        from_date=QOYOD_SYNC_START_DATE,
    )
    all_candidates = sorted(
        (
            row for row in (snapshot.get("orders") or [])
            if row.get("worker_candidate") is True
        ),
        key=_oldest_key,
    )
    quarantined = await auto_send_module._open_quarantined_order_numbers(
        db,
        [str(row.get("order_number") or "") for row in all_candidates],
    )
    runnable_candidates = [
        row for row in all_candidates
        if str(row.get("order_number") or "") not in quarantined
    ]
    pending_statuses = auto_send_module._configured_pending_statuses(settings)
    candidate_groups = [
        sorted(
            (
                row for row in runnable_candidates
                if auto_send_module._pending_row_matches_status(
                    row, pending_status
                )
            ),
            key=_oldest_key,
        )
        for pending_status in pending_statuses
    ]

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_group_size = max((len(group) for group in candidate_groups), default=0)
    for index in range(max_group_size):
        for group in candidate_groups:
            if index >= len(group):
                continue
            row = group[index]
            order_number = str(row.get("order_number") or "")
            if not order_number or order_number in seen:
                continue
            seen.add(order_number)
            candidates.append(row)
            if len(candidates) >= max(1, int(batch_limit)):
                break
        if len(candidates) >= max(1, int(batch_limit)):
            break

    authoritative_status_counts = {
        API_STATUS_TO_KEY[pending_status]: sum(
            auto_send_module._pending_row_matches_status(row, pending_status)
            for row in all_candidates
        )
        for pending_status in pending_statuses
    }
    runnable_status_counts = {
        API_STATUS_TO_KEY[pending_status]: len(group)
        for pending_status, group in zip(pending_statuses, candidate_groups)
    }
    captured_at = snapshot.get("captured_at") or _now().isoformat()
    fingerprint = (
        snapshot.get("snapshot_fingerprint")
        or (snapshot.get("reference_hashes") or {}).get("worker_candidates")
    )
    return candidates, {
        "authoritative_backlog_count": len(all_candidates),
        "runnable_candidate_count": len(runnable_candidates),
        "open_quarantined_candidate_count": max(
            0, len(all_candidates) - len(runnable_candidates)
        ),
        "status_candidate_count": sum(runnable_status_counts.values()),
        "batch_candidate_count": len(candidates),
        "selection_order": "oldest_first",
        "candidate_snapshot": {
            "source_authority": snapshot.get("source_authority"),
            "orders_user_id": str(orders_user_id),
            "from_date": snapshot.get("from_date"),
            "to_date": snapshot.get("to_date"),
            "captured_at": captured_at,
            "snapshot_fingerprint": fingerprint,
            "status_counts": (
                snapshot.get("worker_candidate_status_counts")
                or authoritative_status_counts
            ),
            "status_display_counts": snapshot.get(
                "worker_candidate_status_display_counts"
            ) or {},
            "runnable_status_counts": runnable_status_counts,
            "selection_order": "oldest_first",
        },
    }
