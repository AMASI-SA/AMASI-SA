"""Iter-293 — Webhook Activity Log.

Append-only audit trail of every webhook arrival from Make/Salla.

Design choices
──────────────
1. Separate collection (`qoyod_webhook_events`) from `integration_inbox`
   so a noisy upstream doesn't bloat the processing queue.
2. We log EVERY arrival, even duplicates and non-actionable events
   (order.created, order.updated, payment.updated, etc.). The Monitor
   UI is the operator's "tail -f" for webhook traffic.
3. TTL 7 days at the index level + soft cap 1000 rows enforced at
   query/list time (capped Mongo collections don't support TTL).
4. Best-effort: any failure to log MUST NOT break the live pipeline.
   We swallow exceptions internally and emit a single log line.

Schema (`qoyod_webhook_events` row)
───────────────────────────────────
    id                str   — uuid4
    user_id           str   — tenant ('main' in MVP)
    received_at       dt    — server-side arrival timestamp
    trace_id          str   — matches integration_inbox row when known
    event_type        str   — order_completed | order.updated | ...
    salla_order_id    str?  — for order-scoped events
    items_parsed_ok   bool  — adapter+normalizer succeeded
    items_count       int?  — items count when parsing ok
    skipped_reason    str?  — when actionable=False (e.g. unsupported_event)
    target_inbox_row_id str? — uuid in integration_inbox if pipeline ran
    pipeline_stage_after str? — outcome stage when pipeline ran
    http_response_status int — HTTP status returned to Make
    raw_payload_size  int   — bytes of body (for debugging huge payloads)
    error             dict? — if logging path itself encountered an error
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("qoyod.webhook_activity")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def record_webhook_event(
    db,
    *,
    user_id: str,
    trace_id: Optional[str],
    event_type: Optional[str],
    salla_order_id: Optional[str],
    items_parsed_ok: bool,
    items_count: Optional[int],
    skipped_reason: Optional[str],
    target_inbox_row_id: Optional[str],
    pipeline_stage_after: Optional[str],
    http_response_status: int,
    raw_payload_size: int,
) -> None:
    """Best-effort write to `qoyod_webhook_events`. Never raises."""
    try:
        doc = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "received_at": _now(),
            "trace_id": trace_id,
            "event_type": event_type or "unknown",
            "salla_order_id": str(salla_order_id) if salla_order_id else None,
            "items_parsed_ok": bool(items_parsed_ok),
            "items_count": int(items_count) if items_count is not None else None,
            "skipped_reason": skipped_reason,
            "target_inbox_row_id": target_inbox_row_id,
            "pipeline_stage_after": pipeline_stage_after,
            "http_response_status": int(http_response_status),
            "raw_payload_size": int(raw_payload_size),
        }
        await db.qoyod_webhook_events.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook_activity_log_failed", extra={"err": repr(exc)})


async def list_recent_events(
    db,
    *,
    user_id: str,
    limit: int = 50,
    event_type: Optional[str] = None,
    salla_order_id: Optional[str] = None,
    skipped_only: bool = False,
) -> list[dict]:
    """Fetch most-recent events with optional filters.

    Hard ceiling: 200 rows per call to protect the UI / DB.
    """
    limit = max(1, min(int(limit), 200))
    query: dict = {"user_id": user_id}
    if event_type:
        query["event_type"] = event_type
    if salla_order_id:
        query["salla_order_id"] = str(salla_order_id)
    if skipped_only:
        query["skipped_reason"] = {"$ne": None}
    cursor = (db.qoyod_webhook_events
              .find(query, {"_id": 0})
              .sort("received_at", -1)
              .limit(limit))
    return await cursor.to_list(length=limit)


async def get_event_counts(
    db, *, user_id: str, since_hours: int = 24,
) -> dict:
    """Return aggregate counts for the Monitor header.

    Returns:
        {
            "total":     int,
            "accepted":  int,  # http 2xx, parsed ok, not skipped
            "skipped":   int,  # skipped_reason != None
            "errors":    int,  # http >= 400 OR items_parsed_ok=False
            "by_event":  {event_type: count, ...},
            "since":     iso8601 timestamp,
        }
    """
    from datetime import timedelta
    since = _now() - timedelta(hours=max(1, int(since_hours)))
    pipeline = [
        {"$match": {"user_id": user_id, "received_at": {"$gte": since}}},
        {"$facet": {
            "total":    [{"$count": "n"}],
            "skipped":  [{"$match": {"skipped_reason": {"$ne": None}}},
                         {"$count": "n"}],
            "errors":   [{"$match": {"$or": [
                            {"http_response_status": {"$gte": 400}},
                            {"items_parsed_ok": False},
                         ]}}, {"$count": "n"}],
            "by_event": [{"$group": {"_id": "$event_type", "n": {"$sum": 1}}}],
        }},
    ]
    cursor = db.qoyod_webhook_events.aggregate(pipeline)
    result = (await cursor.to_list(length=1)) or [{}]
    f = result[0]
    total = (f.get("total") or [{}])[0].get("n", 0)
    skipped = (f.get("skipped") or [{}])[0].get("n", 0)
    errors = (f.get("errors") or [{}])[0].get("n", 0)
    accepted = max(0, total - skipped - errors)
    by_event = {row["_id"] or "unknown": row["n"]
                for row in (f.get("by_event") or [])}
    return {
        "total": total,
        "accepted": accepted,
        "skipped": skipped,
        "errors": errors,
        "by_event": by_event,
        "since": since.isoformat(),
    }


async def soft_cap_old_rows(db, *, user_id: str, keep: int = 1000) -> int:
    """Trim the collection to the most recent `keep` rows for a tenant.

    TTL handles long-term retention (7 days); this guards against
    short-burst spikes where the collection could balloon within the
    TTL window. Call lazily (e.g. once per `list_recent_events`).

    Returns the number of rows deleted.
    """
    keep = max(50, min(int(keep), 10_000))
    total = await db.qoyod_webhook_events.count_documents({"user_id": user_id})
    excess = total - keep
    if excess <= 0:
        return 0
    # Pick the N oldest rows by received_at ASC and delete by their ids.
    # Using `id` (uuid str) avoids races on identical received_at values.
    cursor = (db.qoyod_webhook_events
              .find({"user_id": user_id}, {"_id": 0, "id": 1})
              .sort("received_at", 1)
              .limit(excess))
    ids = [r["id"] for r in await cursor.to_list(length=excess) if r.get("id")]
    if not ids:
        return 0
    res = await db.qoyod_webhook_events.delete_many(
        {"user_id": user_id, "id": {"$in": ids}})
    return int(getattr(res, "deleted_count", 0) or 0)
