"""Backfill Gate — strict default for Go-Live activation.

User directive 2026-02-27:
    The operator clicked '🚀 تفعيل وضع الإنتاج'. The platform MUST NOT
    silently push old in-flight (Dry-Run era) rows to Qoyod. Default
    behaviour is "now-forward only" — only webhooks arriving AFTER
    `go_live_activated_at` reach Qoyod. Backfill is an explicit
    opt-in via settings.

Mechanism
─────────
Every worker tick, BEFORE the auto-requeue + drain steps, we look
for rows that are:
    • in flight (NORMALIZED / CUSTOMER_RESOLVED / PRODUCT_RESOLVED)
    • dated BEFORE `go_live_activated_at`
    • not dry-run (live mode is on by definition once activated)

If `settings.backfill_mode == "now_forward_only"` (default), we
transition each such row to SKIPPED with reason
`pre_activation_skipped`. Rows are NEVER deleted — they remain in
the inbox for audit and monitoring.

If `settings.backfill_mode == "backfill_unsent"`, this gate is a
no-op and the worker drains the rows normally (operator opted in).

Pre-activation (`go_live_activated_at` not set yet): no-op. The
gate only activates after Go-Live is flipped on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from integrations.qoyod.state_machine import transition


logger = logging.getLogger(__name__)


_IN_FLIGHT_STAGES = ("NORMALIZED", "CUSTOMER_RESOLVED", "PRODUCT_RESOLVED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_settings(db, user_id: str) -> dict:
    s = await db.qoyod_settings.find_one({"user_id": user_id}) or {}
    return s


def _activation_watermark(settings: dict) -> datetime | None:
    a = settings.get("go_live_activated_at") or settings.get("activated_at")
    if isinstance(a, str):
        try:
            a = datetime.fromisoformat(a.replace("Z", "+00:00"))
        except ValueError:
            return None
    return a if isinstance(a, datetime) else None


async def skip_pre_activation_rows(
    db, *, user_id: str, limit: int = 200,
) -> dict:
    """Skip pre-activation in-flight rows when `backfill_mode` says so.

    Returns `{ok, mode, scanned, skipped, items}`.

    `items` carries one entry per skipped row so the operator can
    audit which orders were intentionally NOT sent to Qoyod. Empty
    list when the gate is a no-op.
    """
    settings = await _load_settings(db, user_id)
    activated_at = _activation_watermark(settings)
    mode = (settings.get("backfill_mode") or "now_forward_only").strip()

    # No activation yet → gate is a no-op (nothing to compare against).
    if not activated_at:
        return {"ok": True, "mode": mode, "scanned": 0,
                "skipped": 0, "items": [], "reason": "not_activated_yet"}

    # Operator explicitly chose backfill → don't touch anything.
    if mode == "backfill_unsent":
        return {"ok": True, "mode": mode, "scanned": 0,
                "skipped": 0, "items": [],
                "reason": "operator_opted_into_backfill"}

    q = {
        "user_id":        user_id,
        "pipeline_stage": {"$in": list(_IN_FLIGHT_STAGES)},
        "received_at":    {"$lt": activated_at},
    }
    items: list[dict[str, Any]] = []
    scanned = 0
    cursor = db.integration_inbox.find(q, limit=max(1, min(limit, 500)))
    async for row in cursor:
        scanned += 1
        row_id = row.get("id")
        cur_stage = row.get("pipeline_stage")
        # In-flight → SKIPPED is allowed by the state machine (every
        # HAPPY_PATH stage has a SKIPPED edge). Re-check defensively.
        try:
            patch = transition(
                from_stage=cur_stage, to_stage="SKIPPED",
                actor="backfill_gate",
                note=("pre_activation_skipped: row received_at < "
                      f"go_live_activated_at ({activated_at.isoformat()[:19]})"
                      " and backfill_mode=now_forward_only"),
            )
        except Exception:
            logger.exception(
                "qoyod backfill_gate: refused transition %s→SKIPPED for row %s",
                cur_stage, row_id)
            continue
        patch.setdefault("$set", {}).update({
            "skipped_reason":       "pre_activation_skipped",
            "skipped_at":           _now(),
            "skipped_by":           "backfill_gate",
            # rev44 — pre-activation is a FATAL skip by decree.
            "skip_class":           "fatal",
            "skip_class_reason":    "pre_activation_skipped",
        })
        await db.integration_inbox.update_one({"id": row_id}, patch)
        items.append({
            "row_id":            row_id,
            "trace_id":          row.get("trace_id"),
            "previous_stage":    cur_stage,
            "received_at":       row.get("received_at"),
            "order_number":      (row.get("canonical_payload") or {}).get(
                                    "order_number"),
        })

    return {
        "ok":      True,
        "mode":    mode,
        "scanned": scanned,
        "skipped": len(items),
        "items":   items,
        "activated_at": activated_at.isoformat(),
    }
