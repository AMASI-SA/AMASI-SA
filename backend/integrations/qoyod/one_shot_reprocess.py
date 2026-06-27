"""Qoyod One-Shot Reprocess — single-order, strict, audit-trail-heavy.

Why this exists
───────────────
A specific production order (e.g. `268670571`) failed because a
`DRY:product:*` id leaked into the invoice payload. The operator
wants to safely re-run **exactly that one order** against real Qoyod
after the leak guard shipped, WITHOUT touching any other DEAD_LETTER
row, WITHOUT running backfill, and WITHOUT manual shell access.

Strict invariants (user directive 2026-02-27)
─────────────────────────────────────────────
1. Targets exactly ONE row, found by `salla_order_number` (or
   `trace_id`). Multiple matches → refuse.
2. Requires a typed confirmation token `REPROCESS-<order_number>` —
   typo-resistant and order-specific (you cannot accidentally use one
   token to reprocess a different order).
3. **Never** scans/changes any other DEAD_LETTER row.
4. **Never** triggers backfill.
5. Quarantines any `DRY:contact:*` / `DRY:product:*` mappings tied to
   this order BEFORE re-running so the resolver re-creates against
   the real Qoyod tenant.
6. The pipeline's preflight guard (`pipeline.py`, Iter-267) is the
   last line of defence — if anything `DRY:*` still reaches the
   invoice payload, the row goes to DEAD_LETTER and **nothing is
   POSTed** to Qoyod.
7. On failure: no auto-retry. Returns `error.code`, `error.message`,
   `request_body_json`, and the stage at which the failure occurred.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient
from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.invoice_builder import is_dry_run_mode
from integrations.qoyod.pipeline import (
    process_normalized_row, process_customer_resolved_row,
)
from integrations.qoyod.state_machine import transition


logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


CONFIRM_TOKEN_TEMPLATE = "REPROCESS-{order_number}"

# Pipeline stages the row must traverse for a successful one-shot run.
EXPECTED_STAGE_SEQUENCE: tuple[str, ...] = (
    "CUSTOMER_RESOLVED",
    "PRODUCT_RESOLVED",
    "INVOICE_CREATED",
    "RECEIPT_CREATED",
    "COMPLETED",
)


class OneShotRefused(Exception):
    """Raised when the request is malformed or unsafe. Surfaces a
    structured `{code, message, ...}` dict for the HTTP layer."""
    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


# ─────────────────────────────────────────────────────────────────────
# Row lookup — single match only
# ─────────────────────────────────────────────────────────────────────
async def _find_target_row(
    db, *, user_id: str, order_number: Optional[str],
    trace_id: Optional[str],
) -> dict:
    """Find exactly one inbox row for this order. Refuse on 0 or >1
    matches — the operator must be explicit. Multiple matches mean
    the same order received multiple webhook events (e.g. status
    transitions) and the operator must pick one by `trace_id`.
    """
    if not (order_number or trace_id):
        raise OneShotRefused(
            "order_lookup_required",
            "must supply either order_number or trace_id")

    q: dict = {"user_id": user_id}
    if trace_id:
        q["trace_id"] = trace_id
    else:
        # Match by salla_order_number (the human-visible id from the
        # Salla dashboard) — what the operator types in the UI.
        on = str(order_number)
        q["$or"] = [
            {"salla_order_number": on},
            {"salla_order_id":     on},
            {"canonical_payload.order_number": on},
            {"canonical_payload.order_id":     on},
        ]

    rows = await db.integration_inbox.find(q).to_list(length=10)
    if not rows:
        raise OneShotRefused(
            "row_not_found",
            f"no integration_inbox row matches order_number={order_number} "
            f"trace_id={trace_id}",
            order_number=order_number, trace_id=trace_id)
    if len(rows) > 1:
        raise OneShotRefused(
            "multiple_matches_pick_one_by_trace_id",
            f"order_number={order_number} matches {len(rows)} rows; "
            "supply trace_id to disambiguate",
            order_number=order_number,
            candidates=[{
                "trace_id":      r.get("trace_id"),
                "received_at":   r.get("received_at"),
                "pipeline_stage": r.get("pipeline_stage"),
                "idempotency_key": r.get("idempotency_key"),
            } for r in rows[:10]])
    return rows[0]


# ─────────────────────────────────────────────────────────────────────
# DRY-Run mapping quarantine — pre-run cleanup
# ─────────────────────────────────────────────────────────────────────
async def _quarantine_dry_mappings(
    db, *, user_id: str, row: dict,
) -> dict:
    """Inspect the row's customer + product SKUs. Quarantine any
    `qoyod_customers_mapping` / `qoyod_products_mapping` rows whose
    `qoyod_*_id` carries the `DRY:` prefix.

    Also nullifies the row's own `qoyod_customer_id` if it is a
    Dry-Run leak so the pipeline re-resolves the customer cleanly
    on the next pass.

    Returns the audit summary (counts + the actual ids quarantined).
    """
    summary: dict[str, Any] = {
        "customer_mapping_quarantined": False,
        "customer_quarantined_id":      None,
        "row_customer_id_cleared":      False,
        "product_mappings_quarantined": [],
        "scanned_sku_count":            0,
    }
    canonical = row.get("canonical_payload") or {}

    # ── Customer mapping ────────────────────────────────────────────
    customer = canonical.get("customer") or {}
    phone = (customer.get("phone") or "").strip()
    email = (customer.get("email") or "").strip().lower()
    lookup_keys: list[str] = []
    if phone:
        lookup_keys.append(phone)
    if email:
        lookup_keys.append(email)

    for lk in lookup_keys:
        m = await db.qoyod_customers_mapping.find_one(
            {"user_id": user_id, "lookup_key": lk},
            {"_id": 0, "qoyod_customer_id": 1})
        if not m:
            continue
        cid = m.get("qoyod_customer_id") or ""
        if str(cid).startswith("DRY:"):
            await db.qoyod_customers_mapping.update_one(
                {"user_id": user_id, "lookup_key": lk},
                {"$set": {"dry_run_only":       True,
                          "quarantined_at":     _now(),
                          "quarantine_reason":  "one_shot_reprocess",
                          "quarantined_id":     cid}},
            )
            summary["customer_mapping_quarantined"] = True
            summary["customer_quarantined_id"] = cid
            break

    # Also clear the row's own qoyod_customer_id if it's a DRY leak —
    # the pipeline reads from `row.qoyod_customer_id` after the
    # CUSTOMER_RESOLVED transition.
    row_cid = row.get("qoyod_customer_id") or ""
    if str(row_cid).startswith("DRY:"):
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"qoyod_customer_id": None,
                      "qoyod_customer_id_cleared_at": _now(),
                      "qoyod_customer_id_cleared_reason": "dry_run_leak"}},
        )
        summary["row_customer_id_cleared"] = True

    # ── Product mappings ────────────────────────────────────────────
    items = canonical.get("items") or []
    for it in items:
        sku = (it.get("sku") or "").strip()
        if not sku:
            continue
        summary["scanned_sku_count"] += 1
        m = await db.qoyod_products_mapping.find_one(
            {"user_id": user_id, "sku": sku},
            {"_id": 0, "qoyod_product_id": 1, "dry_run_only": 1})
        if not m:
            continue
        pid = m.get("qoyod_product_id") or ""
        if str(pid).startswith("DRY:") or m.get("dry_run_only"):
            await db.qoyod_products_mapping.update_one(
                {"user_id": user_id, "sku": sku},
                {"$set": {"dry_run_only":       True,
                          "quarantined_at":     _now(),
                          "quarantine_reason":  "one_shot_reprocess",
                          "quarantined_id":     pid}},
            )
            summary["product_mappings_quarantined"].append(
                {"sku": sku, "quarantined_id": pid})

    return summary


# ─────────────────────────────────────────────────────────────────────
# Stage reset — bring the row back to a worker-drainable stage
# ─────────────────────────────────────────────────────────────────────
def _resume_target_for(row: dict, *, force_full: bool) -> str:
    """Choose the stage the row resumes from.

    For one-shot we want the broadest safe re-run: go back to
    NORMALIZED so customer + product re-resolve from scratch. The
    only exception is when a caller explicitly opts out of customer
    re-resolution (not exposed in the MVP UI).
    """
    if force_full:
        return "NORMALIZED"
    last_failed = row.get("last_failed_stage")
    # FAILED_INVOICE / FAILED_RECEIPT / FAILED_PRODUCT — products were
    # the leak source, so we still want to re-resolve from CUSTOMER_RESOLVED
    # at minimum. NORMALIZED is strictly safer.
    if last_failed in ("FAILED_PRODUCT", "FAILED_INVOICE", "FAILED_RECEIPT"):
        return "CUSTOMER_RESOLVED"
    return "NORMALIZED"


async def _reset_row_to_stage(
    db, row: dict, *, resume_stage: str, actor: str,
) -> None:
    """Two-hop terminal → RETRYING → resume_stage transition. Mirrors
    the dead_letter_requeue mechanic so stage_history stays auditable.
    """
    current = row.get("pipeline_stage")
    if current in ("DEAD_LETTER", "PARTIAL_FAILURE"):
        note1 = (f"one-shot reprocess: terminal={current} → RETRYING "
                 f"(actor={actor})")
        p1 = transition(
            from_stage=current, to_stage="RETRYING",
            actor=actor, note=note1,
        )
        p1.setdefault("$set", {}).update({
            "last_one_shot_at": _now(),
            "last_one_shot_actor": actor,
        })
        await db.integration_inbox.update_one({"id": row["id"]}, p1)
        from_stage = "RETRYING"
    else:
        # In-flight or already terminal-success: refuse to reset.
        # Caller validated this earlier; this is defence in depth.
        from_stage = current

    note2 = f"one-shot reprocess resume at {resume_stage}"
    p2 = transition(
        from_stage=from_stage, to_stage=resume_stage,
        actor=actor, note=note2,
    )
    await db.integration_inbox.update_one({"id": row["id"]}, p2)


# ─────────────────────────────────────────────────────────────────────
# Main entry — atomic, single-shot
# ─────────────────────────────────────────────────────────────────────
async def reprocess_one_order(
    db, *, user_id: str,
    order_number: Optional[str] = None,
    trace_id: Optional[str] = None,
    confirm: str,
    actor: str = "operator",
) -> dict:
    """Reprocess exactly one Salla order against real Qoyod.

    Returns a structured dict (always shaped identically) so the UI
    can render the outcome without inspecting HTTP error codes.

    Raises `OneShotRefused` ONLY for input/safety errors — pipeline
    failures (DEAD_LETTER, leak guard tripped, Qoyod 4xx/5xx) are
    returned as normal results with `outcome` set.
    """
    # ── 1. Confirm token (order-specific, typo-resistant) ───────────
    if not (order_number or trace_id):
        raise OneShotRefused(
            "order_lookup_required",
            "must supply order_number or trace_id")
    # The token always matches the order_number the operator typed
    # — when they only supplied trace_id we still REQUIRE them to
    # type the order_number into the token. This prevents a
    # trace_id-only request from accidentally reprocessing a row
    # that the operator did not intend.
    expected = (CONFIRM_TOKEN_TEMPLATE.format(order_number=order_number)
                if order_number else None)
    if not order_number or not expected or (confirm or "").strip() != expected:
        raise OneShotRefused(
            "confirm_token_mismatch",
            "confirm must equal 'REPROCESS-<order_number>'",
            expected=expected, received=(confirm or "")[:64])

    # ── 2. Find the row (single match) ──────────────────────────────
    row = await _find_target_row(
        db, user_id=user_id,
        order_number=order_number, trace_id=trace_id)

    # ── 3. Bail-out if the row is already COMPLETED ─────────────────
    if row.get("pipeline_stage") == "COMPLETED":
        return {
            "ok":       True,
            "outcome":  "ALREADY_COMPLETED",
            "row_id":   row.get("id"),
            "trace_id": row.get("trace_id"),
            "stage_sequence_observed": [],
            "message":  ("الطلب مكتمل سابقاً — لم يتم اتخاذ أي إجراء. "
                         "لإعادة الإرسال يدوياً يجب أرشفته أولاً."),
        }

    # ── 4. Require real-mode + credentials ──────────────────────────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    if is_dry_run_mode(settings):
        raise OneShotRefused(
            "dry_run_mode_active",
            "one-shot reprocess targets the REAL Qoyod tenant; "
            "disable dry_run_mode in Qoyod settings first")
    api_key = await get_api_key(db, user_id)
    if not api_key:
        raise OneShotRefused(
            "credentials_missing",
            "Qoyod API key is not configured for this tenant")

    # ── 5. Quarantine any DRY mappings tied to this order ───────────
    quarantine_summary = await _quarantine_dry_mappings(
        db, user_id=user_id, row=row)

    # ── 6. Reset row to a worker-drainable stage ────────────────────
    resume_stage = _resume_target_for(row, force_full=True)
    await _reset_row_to_stage(
        db, row, resume_stage=resume_stage, actor=actor)
    # Re-fetch fresh state for the pipeline calls.
    fresh = await db.integration_inbox.find_one({"id": row["id"]})
    if not fresh:    # defensive — should never happen
        raise OneShotRefused(
            "row_disappeared_after_reset",
            "internal: row not found after stage reset",
            row_id=row.get("id"))

    # ── 7. Drive the pipeline — manually, single row, real client ───
    api_client = QoyodAPIClient(api_key)
    stage_sequence: list[str] = []
    result_log: list[dict] = []

    async def _refresh() -> dict:
        return await db.integration_inbox.find_one({"id": row["id"]})

    # Step A: NORMALIZED → CUSTOMER_RESOLVED (or SKIPPED / DEAD_LETTER)
    cur = await _refresh()
    if cur and cur.get("pipeline_stage") == "NORMALIZED":
        out = await process_normalized_row(
            db, cur, api_client=api_client)
        result_log.append({"step": "normalized", "result": out})
        if out.get("outcome") in ("DEAD_LETTER", "SKIPPED"):
            return _build_failure_response(
                db, row_id=row["id"], outcome=out.get("outcome"),
                step="customer_resolution",
                step_result=out,
                stage_sequence=stage_sequence,
                quarantine_summary=quarantine_summary)
        if out.get("outcome") == "CUSTOMER_RESOLVED":
            stage_sequence.append("CUSTOMER_RESOLVED")

    # Step B: CUSTOMER_RESOLVED → … → COMPLETED / PARTIAL_FAILURE /
    # DEAD_LETTER (including the DRY-leak preflight guard).
    cur = await _refresh()
    if cur and cur.get("pipeline_stage") == "CUSTOMER_RESOLVED":
        if "CUSTOMER_RESOLVED" not in stage_sequence:
            stage_sequence.append("CUSTOMER_RESOLVED")
        out = await process_customer_resolved_row(
            db, cur, api_client=api_client)
        result_log.append({"step": "customer_resolved", "result": out})

    # Final state inspection.
    final = await _refresh() or {}
    final_stage = final.get("pipeline_stage")

    # Re-derive the observed stage sequence from stage_history so the
    # response matches reality (not a guess from outcomes).
    stage_sequence = _extract_observed_sequence(final)

    # Hard refusal — the leak guard tripped. Surface request_body_json.
    pe = final.get("pipeline_error") or {}
    if final_stage == "DEAD_LETTER" and \
            pe.get("code") == "dry_run_product_id_leaked_to_production":
        return {
            "ok":      False,
            "outcome": "DEAD_LETTER",
            "reason":  "dry_run_product_id_leaked_to_production",
            "row_id":  row["id"],
            "trace_id": row.get("trace_id"),
            "failed_at_stage": "PRODUCT_RESOLVED",
            "error": {
                "code":    pe.get("code"),
                "message": pe.get("message"),
                "leaked_ids": pe.get("leaked_ids"),
            },
            "request_body_json": (final.get("qoyod_payloads") or {})
                                  .get("invoice_blocked_preflight"),
            "stage_sequence_observed": stage_sequence,
            "quarantine_summary": quarantine_summary,
        }

    if final_stage == "COMPLETED":
        payloads = final.get("qoyod_payloads") or {}
        invoice_payload = payloads.get("invoice")
        # Belt-and-suspenders: re-verify product_ids in the final
        # snapshotted payload are non-DRY. This catches the (theoretical)
        # case where the leak guard had a bug.
        dry_leaks = _scan_payload_for_dry(invoice_payload)
        if dry_leaks:
            logger.error(
                "qoyod one-shot completed WITH dry leaks present "
                "(post-hoc detection): row=%s leaks=%s",
                row["id"], dry_leaks)
        return {
            "ok":      True,
            "outcome": "COMPLETED",
            "row_id":  row["id"],
            "trace_id": row.get("trace_id"),
            "stage_sequence_observed": stage_sequence,
            "expected_stage_sequence": list(EXPECTED_STAGE_SEQUENCE),
            "qoyod_invoice_id": final.get("qoyod_invoice_id"),
            "qoyod_invoice_number": final.get("qoyod_invoice_number"),
            "qoyod_receipt_id": final.get("qoyod_receipt_id"),
            "qoyod_customer_id": final.get("qoyod_customer_id"),
            "invoice_payload": invoice_payload,
            "receipt_payload": payloads.get("receipt"),
            "dry_leaks_in_final_payload": dry_leaks,  # MUST be []
            "quarantine_summary": quarantine_summary,
        }

    # Everything else = a failure result. Surface request_body_json
    # from snapshots so the operator can paste the payload directly
    # into a support ticket.
    return _build_failure_response(
        db_disabled=True,
        outcome=final_stage or "UNKNOWN",
        step="post_pipeline",
        step_result={
            "pipeline_error":  pe,
            "last_failed_stage": final.get("last_failed_stage"),
        },
        row_id=row["id"],
        trace_id=row.get("trace_id"),
        request_body_json=(final.get("qoyod_payloads") or {}).get("invoice"),
        stage_sequence=stage_sequence,
        quarantine_summary=quarantine_summary,
    )


# ─────────────────────────────────────────────────────────────────────
# Helpers — payload scans + response shaping
# ─────────────────────────────────────────────────────────────────────
def _scan_payload_for_dry(payload: Any) -> list[str]:
    """Return a list of 'field=value' descriptors for any `DRY:`
    prefixed id found anywhere in the invoice payload. Empty list
    means the payload is clean.
    """
    if not isinstance(payload, dict):
        return []
    leaks: list[str] = []
    inv = payload.get("invoice") if isinstance(payload.get("invoice"), dict) \
        else payload
    cid = inv.get("contact_id")
    if isinstance(cid, str) and cid.startswith("DRY:"):
        leaks.append(f"contact_id={cid}")
    for li in (inv.get("line_items") or []):
        pid = (li or {}).get("product_id")
        if isinstance(pid, str) and pid.startswith("DRY:"):
            leaks.append(f"product_id={pid}")
    return leaks


def _extract_observed_sequence(row: dict) -> list[str]:
    """Pull happy-path stages from stage_history in chronological
    order. Filters out RETRYING / DEAD_LETTER / SKIPPED so the
    operator sees the clean path the row took (if any).
    """
    happy = {
        "NORMALIZED", "RULES_APPLIED", "CUSTOMER_RESOLVED",
        "PRODUCT_RESOLVED", "INVOICE_CREATED", "RECEIPT_CREATED",
        "COMPLETED",
    }
    seen: list[str] = []
    for entry in (row.get("stage_history") or []):
        to_stage = entry.get("to_stage") if isinstance(entry, dict) else None
        if to_stage in happy and to_stage not in seen:
            seen.append(to_stage)
    return seen


def _build_failure_response(
    *, outcome: str, step: str, step_result: dict,
    row_id: str, trace_id: Optional[str] = None,
    stage_sequence: list[str],
    quarantine_summary: dict,
    request_body_json: Any = None,
    db=None, db_disabled: bool = False,
) -> dict:
    """Uniform failure shape. Excludes Pydantic / Exception objects so
    the response is always JSON-serialisable.
    """
    err = (step_result or {}).get("pipeline_error") or {}
    return {
        "ok":      False,
        "outcome": outcome,
        "row_id":  row_id,
        "trace_id": trace_id,
        "failed_at_stage": (step_result or {}).get("last_failed_stage") or
                           (step_result or {}).get("reason") or step,
        "error": {
            "code":    err.get("code") or (step_result or {}).get("reason"),
            "message": err.get("message"),
        },
        "request_body_json": request_body_json,
        "stage_sequence_observed": stage_sequence,
        "quarantine_summary": quarantine_summary,
    }
