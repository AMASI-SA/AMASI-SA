"""Qoyod Pipeline Orchestrator — Day 4 segment.

Drives `integration_inbox` rows from `NORMALIZED` through:
    `RULES_APPLIED`  (or `SKIPPED` if not eligible)
    → `CUSTOMER_RESOLVED`  (or `FAILED_CUSTOMER` → `DEAD_LETTER`)

Day 4 STOPS at `CUSTOMER_RESOLVED`. Subsequent steps (products,
invoice, receipt) are intentionally NOT triggered — they land in
Day 5 after the merchant reviews the customer-resolution output.

Failure routing (per user directive — same pattern as Day 3):
    • Validation/structural failure in rules → not possible here,
      rules are pure and total.
    • Customer resolution failure → FAILED_CUSTOMER → DEAD_LETTER
      (the row is NOT deleted, NOT auto-retried).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.business_rules import (
    evaluate as evaluate_rules, RulesDecision,
)
from integrations.qoyod.customer_resolver import (
    resolve_customer, ResolutionResult,
)
from integrations.qoyod.dto import SalesOrderDTO
from integrations.qoyod.state_machine import transition


# ─────────────────────────────────────────────────────────────────────
# Settings loader (single-tenant for MVP; matches routes._load_settings
# fall-backs so the orchestrator never sees a "half-old" doc).
# ─────────────────────────────────────────────────────────────────────
async def _load_settings(db, user_id: str) -> dict:
    doc = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0})
    if not doc:
        return {
            "invoice_trigger_statuses": ["completed"],
            "invoice_date_source":      "trigger_status_date",
            "trigger_once_only":        True,
        }
    if not doc.get("invoice_trigger_statuses"):
        legacy = doc.get("invoice_trigger_status")
        doc["invoice_trigger_statuses"] = [legacy] if legacy else ["completed"]
    if "trigger_once_only" not in doc:
        doc["trigger_once_only"] = True
    if doc.get("invoice_date_source") == "completed_at":
        doc["invoice_date_source"] = "trigger_status_date"
    return doc


async def _apply(db, row_id: str, patch: dict) -> None:
    await db.integration_inbox.update_one({"id": row_id}, patch)


async def _dead_letter(
    db, *, row_id: str, from_stage: str, fail_stage: str,
    error: dict, started_at: Optional[datetime] = None,
) -> str:
    """Two-hop transition: <from_stage> → <fail_stage> → DEAD_LETTER.

    Matches `webhook._dead_letter` so the operator sees identical
    semantics whether the failure happened in Day 3 or Day 4.
    """
    p1 = transition(from_stage=from_stage, to_stage=fail_stage,
                    actor="worker", error=error)
    p1.setdefault("$set", {})["pipeline_error"] = error
    await _apply(db, row_id, p1)
    p2 = transition(from_stage=fail_stage, to_stage="DEAD_LETTER",
                    actor="worker",
                    note="auto-routed: no retry — manual review required",
                    existing_started_at=started_at)
    await _apply(db, row_id, p2)
    return "DEAD_LETTER"


# ─────────────────────────────────────────────────────────────────────
# Per-row processor
# ─────────────────────────────────────────────────────────────────────
async def process_normalized_row(
    db, row: dict, *, api_client=None,
) -> dict:
    """Advance a single `NORMALIZED` row through rules → customer.

    Returns a small result dict for the orchestrating endpoint.

    Idempotency: this function checks `pipeline_stage` before each
    transition; calling it twice on the same row never double-advances.
    """
    if row.get("pipeline_stage") != "NORMALIZED":
        return {
            "row_id": row.get("id"),
            "skipped": True,
            "reason": "not_in_normalized_stage",
            "pipeline_stage": row.get("pipeline_stage"),
        }

    canonical = row.get("canonical_payload")
    if not canonical:
        # Shouldn't happen — NORMALIZED rows always carry the DTO.
        return await _dead_letter(
            db,
            row_id=row["id"],
            from_stage="NORMALIZED",
            fail_stage="FAILED_NORMALIZATION",
            error={"code": "canonical_payload_missing",
                   "message": "NORMALIZED row has no canonical_payload"},
            started_at=row.get("pipeline_started_at"),
        ) and {"row_id": row["id"], "outcome": "DEAD_LETTER"}

    user_id = row.get("user_id", "main")
    trace_id = row.get("trace_id")

    # Rehydrate the typed DTO so business_rules can use attribute access.
    try:
        dto = SalesOrderDTO(**canonical)
    except Exception as exc:   # defensive — corrupt persisted DTO
        await _dead_letter(
            db, row_id=row["id"], from_stage="NORMALIZED",
            fail_stage="FAILED_NORMALIZATION",
            error={"code": "canonical_payload_invalid",
                   "message": f"{exc.__class__.__name__}: {exc}"},
            started_at=row.get("pipeline_started_at"),
        )
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "canonical_payload_invalid"}

    settings = await _load_settings(db, user_id)

    # Existing invoice row — used by `trigger_once_only`.
    existing = await db.qoyod_invoices.find_one(
        {"user_id": user_id, "salla_order_id": dto.order_id},
        {"_id": 0, "status": 1},
    )

    decision: RulesDecision = evaluate_rules(
        dto, settings, existing_invoice_row=existing)

    # ── NORMALIZED → SKIPPED (not eligible) ─────────────────────────
    if not decision.eligible:
        patch = transition(
            from_stage="NORMALIZED", to_stage="SKIPPED",
            actor="worker",
            note=f"business_rule: {decision.reason}",
            existing_started_at=row.get("pipeline_started_at"),
        )
        patch.setdefault("$set", {})["business_rules_decision"] = \
            decision.to_log_dict()
        await _apply(db, row["id"], patch)
        return {
            "row_id":         row["id"],
            "outcome":        "SKIPPED",
            "reason":         decision.reason,
            "trace_id":       trace_id,
            "decision":       decision.to_log_dict(),
        }

    # ── NORMALIZED → RULES_APPLIED ──────────────────────────────────
    patch = transition(
        from_stage="NORMALIZED", to_stage="RULES_APPLIED",
        actor="worker",
        note=f"eligible · triggered_by={decision.triggered_by_status} · "
             f"invoice_date={decision.invoice_date_source}",
    )
    patch.setdefault("$set", {})["business_rules_decision"] = \
        decision.to_log_dict()
    await _apply(db, row["id"], patch)

    # ── RULES_APPLIED → CUSTOMER_RESOLVED ───────────────────────────
    res: ResolutionResult = await resolve_customer(
        db, user_id, dto.customer,
        trace_id=trace_id,
        default_customer_id=settings.get("default_customer_id"),
        api_client=api_client,
    )

    if not res.success:
        await _dead_letter(
            db, row_id=row["id"],
            from_stage="RULES_APPLIED",
            fail_stage="FAILED_CUSTOMER",
            error=res.error,
            started_at=row.get("pipeline_started_at"),
        )
        return {
            "row_id":   row["id"],
            "outcome":  "DEAD_LETTER",
            "reason":   "FAILED_CUSTOMER",
            "trace_id": trace_id,
            "decision": decision.to_log_dict(),
            "customer": res.to_log_dict(),
        }

    patch = transition(
        from_stage="RULES_APPLIED", to_stage="CUSTOMER_RESOLVED",
        actor="worker",
        note=("customer mapped from local store"
              if not res.created_new
              else "customer created in Qoyod"),
    )
    patch.setdefault("$set", {}).update({
        "customer_resolution": res.to_log_dict(),
        "qoyod_customer_id":   res.qoyod_customer_id,
    })
    await _apply(db, row["id"], patch)
    return {
        "row_id":   row["id"],
        "outcome":  "CUSTOMER_RESOLVED",
        "reason":   None,
        "trace_id": trace_id,
        "decision": decision.to_log_dict(),
        "customer": res.to_log_dict(),
    }


# ─────────────────────────────────────────────────────────────────────
# Batch entry point — what the `/pipeline/process-normalized` endpoint
# calls. Strict Day-4 ceiling: stops at CUSTOMER_RESOLVED.
# ─────────────────────────────────────────────────────────────────────
async def process_pending_normalized(
    db, user_id: str = "main", *,
    limit: int = 25,
    api_client=None,
) -> dict:
    """Drain up to `limit` NORMALIZED rows for the tenant.

    Sequential (not parallel) — Day 4 is a manual / observed run; we
    keep it sequential so a single failure doesn't stampede the log.
    Day 5 introduces a proper background worker with concurrency.
    """
    cursor = db.integration_inbox.find(
        {"user_id": user_id, "pipeline_stage": "NORMALIZED"},
        sort=[("received_at", 1)],
        limit=max(1, min(limit, 100)),
    )
    rows = []
    async for r in cursor:
        rows.append(r)

    results: list[dict] = []
    counters = {"customer_resolved": 0, "skipped": 0, "dead_letter": 0}
    for row in rows:
        out = await process_normalized_row(db, row, api_client=api_client)
        results.append(out)
        oc = out.get("outcome")
        if oc == "CUSTOMER_RESOLVED":
            counters["customer_resolved"] += 1
        elif oc == "SKIPPED":
            counters["skipped"] += 1
        elif oc == "DEAD_LETTER":
            counters["dead_letter"] += 1
    return {
        "ok": True,
        "processed": len(results),
        "counts": counters,
        "items": results,
    }
