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
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.business_rules import (
    evaluate as evaluate_rules, RulesDecision,
)
from integrations.qoyod.customer_resolver import (
    resolve_customer, ResolutionResult,
)
from integrations.qoyod.product_resolver import (
    resolve_products, ProductsResolutionResult,
)
from integrations.qoyod.preflight import run as preflight_run, PreflightResult
from integrations.qoyod.invoice_builder import (
    build_invoice_payload, build_receipt_payload,
    build_invoice_payment_payload,
    DryRunQoyodClient, is_dry_run_mode,
)
from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.write_lock import (
    QoyodWriteLockedError, set_write_lock_context, reset_write_lock_context,
    is_locked, record_blocked_attempt,
)
from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.dto import SalesOrderDTO
from integrations.qoyod.state_machine import transition, InvalidTransition
from integrations.qoyod.totals_guard import (
    validate_totals, TotalsGuardResult,
)
# Iter-001k — Selective Live Send guard (pipeline instrumentation).
from integrations.qoyod.selective_send_guard import (
    SelectiveSendPolicyBlocked,
    apply_send_date_to_qoyod_payload,
    assert_send_allowed,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


logger = logging.getLogger(__name__)


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
            # Iter-293.4-rev9 — Default source is `send_date`.
            "invoice_date_source":      "send_date",
            "trigger_once_only":        True,
            "dry_run_mode":             False,
        }
    if not doc.get("invoice_trigger_statuses"):
        legacy = doc.get("invoice_trigger_status")
        doc["invoice_trigger_statuses"] = [legacy] if legacy else ["completed"]
    if "trigger_once_only" not in doc:
        doc["trigger_once_only"] = True
    if "dry_run_mode" not in doc:
        doc["dry_run_mode"] = False
    # Iter-293.4-rev9 — Auto-migrate legacy tenants whose
    # invoice_date_source still points at Salla-side timestamps. Both
    # `completed_at` and the historical `trigger_status_date` default
    # are silently upgraded to `send_date` so قيود's issue_date always
    # reflects the current Asia/Riyadh moment. Operators who WANT the
    # old behaviour can set it explicitly to a non-empty non-send value.
    if doc.get("invoice_date_source") in ("completed_at",
                                           "trigger_status_date",
                                           None, ""):
        doc["invoice_date_source"] = "send_date"
    # Iter-293.5 — Selective Live Send Gate feature flag. False by
    # default; MUST be explicitly enabled per tenant. When False the
    # gate stays in defensive read-only mode: `pending-orders`
    # endpoint surfaces categorised rows but no auto-send fires.
    if "selective_live_send_enabled" not in doc:
        doc["selective_live_send_enabled"] = False
    return doc


async def _apply(db, row_id: str, patch: dict) -> None:
    await db.integration_inbox.update_one({"id": row_id}, patch)


def _writes_blocked(api_client: Any, settings: dict) -> bool:
    """Iter-293.4-rev5 — Single source of truth for the pipeline's
    PRE-flight lock check.

    Why this exists
    ───────────────
    The Global Write Lock has TWO independent enforcers:

      • `api_client._request` — defense-in-depth at the HTTP layer.
        Honoured by EVERY caller via `QoyodWriteLockedError`.

      • `pipeline.process_customer_resolved_row` — short-circuits
        BEFORE the network call so the row gets parked in
        `LOCKED_AWAITING_APPROVAL` cleanly (no exception traceback,
        no half-built audit). Previously this used `is_locked(settings)`
        which read the DB flag DIRECTLY and ignored the lock state of
        the api_client passed in.

    The bug
    ───────
    `one_shot_reprocess.reprocess_one_order` builds the api_client
    with `write_lock_enabled=False` when a valid per-order approval
    phrase is supplied (Iter-293.4-rev3). But the pipeline's pre-flight
    `is_locked(settings)` short-circuit returned True because the DB
    flag is intentionally still True. Result: the approved single
    order got parked at LOCKED_AWAITING_APPROVAL and never reached
    `create_invoice`.

    The fix
    ───────
    Trust the api_client's own lock state when one is supplied — that
    is the SOLE construct that knows whether the caller has been
    granted a per-order bypass. Fall back to `is_locked(settings)`
    only when no api_client is supplied (defensive — should not happen
    on the live paths, but keeps tests / direct invocations safe).
    """
    if api_client is not None:
        # An api_client was supplied — honour its lock state.
        # NOTE: Both real `QoyodAPIClient` and `DryRunQoyodClient`
        # carry a `write_lock_enabled` attribute (DryRun = always
        # False so the pre-check never trips in dry-run mode).
        return bool(getattr(api_client, "write_lock_enabled", False))
    return is_locked(settings)



def _build_policy_order_from_pipeline_scope(
    *,
    row: dict,
    canonical: dict,
    qoyod_customer_id: Any,
    products_resolution: Any = None,
    invoice_diagnostics: Optional[dict] = None,
    is_dry: bool = False,
) -> dict:
    """Iter-001k — Assemble the `order` dict expected by the Selective
    Send policy from what the pipeline already has in scope.

    Called at the invoice + payment write sites BEFORE any Qoyod API
    call. Purely-derived (no DB reads), synchronous, safe.
    """
    # Salla creation date — prefer canonical.order_date (normalised
    # YYYY-MM-DD), then raw_payload.data.date.date (Salla webhook).
    salla_created = canonical.get("order_date")
    if not salla_created:
        raw = (row.get("raw_payload") or {}).get("data") or {}
        dfield = raw.get("date") if isinstance(raw, dict) else None
        if isinstance(dfield, dict):
            salla_created = dfield.get("date")
        elif dfield:
            salla_created = dfield
        if not salla_created:
            salla_created = (raw.get("created_at")
                             if isinstance(raw, dict) else None)

    # Product status: pipeline reaches invoice site only past
    # PRODUCT_RESOLVED. Detect DRY-only mappings when in dry_run mode.
    # Iter-001k policy: TRUST upstream — if the pipeline transitioned
    # past PRODUCT_RESOLVED with no missing SKUs, treat as resolved
    # (products may have come from `default_product_id` settings
    # rather than the resolver's `resolved` list).
    resolved_count = 0
    dry_run_only = 0
    missing_skus: list[str] = []
    if products_resolution is not None:
        # `ProductsResolutionResult` shape (defensive access).
        resolved = getattr(products_resolution, "resolved", None) or []
        resolved_count = len(resolved)
        missing_skus = list(
            getattr(products_resolution, "missing", None) or [])
        if is_dry:
            dry_run_only = resolved_count

    totals_diff = float((invoice_diagnostics or {}).get("diff") or 0.0)
    totals_expected = float(
        (invoice_diagnostics or {}).get("expected_qoyod_total") or 0.0)
    totals_actual = float(
        (invoice_diagnostics or {}).get("salla_total")
        or canonical.get("total_amount") or 0.0)

    return {
        "order_number": (row.get("salla_order_number")
                         or canonical.get("order_number")),
        "salla_order_id": (row.get("salla_order_id")
                           or canonical.get("order_id")),
        "salla_order_created_at": salla_created,
        "status": canonical.get("order_status"),
        "payment_method": canonical.get("payment_method"),
        "existing_qoyod_invoice_id": row.get("qoyod_invoice_id"),
        "customer_status": {
            "resolved": qoyod_customer_id is not None,
            "qoyod_id": qoyod_customer_id,  # policy detects DRY:/PREVIEW:
            "reason": None,
        },
        "products_status": {
            # Trust upstream: if we reached the invoice site AND
            # there's no explicit `missing` list AND no DRY-only
            # flag, mark as resolved.
            "resolved": (not missing_skus and dry_run_only == 0),
            "resolved_count": resolved_count,
            "dry_run_only":   dry_run_only,
            "missing":        missing_skus,
        },
        "totals_status": {
            "valid":    abs(totals_diff) <= 0.01,
            "total":    totals_actual,
            "expected": totals_expected,
            "diff":     totals_diff,
        },
    }



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

    # ── Iter-2026-02.rev16 — Selective Auto-Send Gate ──────────────
    # When `selective_auto_send_enabled=true`, this gate is the ONE
    # control that opens automatic Qoyod writes. Enforces 9 hard
    # invariants (cutover, status allow-list, hard blocks, payment
    # method allow-list, mapping resolves, no real invoice yet).
    # A gate PASS is persisted on the row so subsequent stages
    # (CUSTOMER_RESOLVED → INVOICE_CREATED) reuse the decision
    # without re-computing. The gate PASS ALSO grants a SCOPED
    # write allowance for THIS ROW only — the DB
    # `production_writes_locked` flag is NEVER mutated.
    _sas_gate_passed = False
    if bool(settings.get("selective_auto_send_enabled", False)):
        from integrations.qoyod.selective_auto_send_gate import (
            evaluate_selective_auto_send_gate,
        )
        _sas = evaluate_selective_auto_send_gate(
            canonical=canonical, row=row, settings=settings)
        # Persist decision for audit / UI.
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {
                "selective_auto_send_gate": _sas.to_log_dict(),
                "selective_auto_send_gate_at":
                    datetime.now(timezone.utc).isoformat(),
            }})
        if not _sas.eligible:
            patch = transition(
                from_stage="NORMALIZED", to_stage="SKIPPED",
                actor="worker",
                note=f"selective_auto_send_gate: {_sas.reason}",
                existing_started_at=row.get("pipeline_started_at"),
            )
            patch.setdefault("$set", {})["selective_auto_send_gate"] = \
                _sas.to_log_dict()
            await _apply(db, row["id"], patch)
            return {
                "row_id":   row["id"],
                "outcome":  "SKIPPED",
                "reason":   _sas.reason,
                "detail":   _sas.detail,
                "trace_id": trace_id,
                "selective_auto_send_gate": _sas.to_log_dict(),
            }
        _sas_gate_passed = True

    # ── Status Eligibility Gate (Iter-282) ─────────────────────────
    # Status gate MUST run BEFORE totals_guard. Orders that are not
    # in an invoice-eligible status (e.g. `under_review`) must NEVER
    # touch the totals guard — otherwise a transient Salla payload
    # would DEAD_LETTER an order that is simply not finished yet.
    # The user directive (2026-02-27, Iter-282) is explicit:
    #   "إذا الحالة under_review يجب أن يذهب إلى SKIPPED، وليس DEAD_LETTER."
    # business_rules.evaluate() already encodes the eligibility
    # decision against `settings.invoice_trigger_statuses`.
    existing = await db.qoyod_invoices.find_one(
        {"user_id": user_id, "salla_order_id": dto.order_id},
        {"_id": 0, "status": 1, "qoyod_invoice_id": 1},
    )
    # Iter-2026-02.rev12 — already_sent guard applies ONLY when a
    # REAL قيود invoice id is present. `None`, `DRY:invoice:*`, and
    # `PREVIEW:invoice:*` sentinels are NOT real sends and MUST NOT
    # block a first live send. Mirrors `_is_real_invoice_id` used
    # elsewhere in the codebase (eligible_orders classifier).
    if existing is not None:
        from integrations.qoyod.eligible_orders import (
            _is_real_invoice_id,
        )
        if not _is_real_invoice_id(existing.get("qoyod_invoice_id")):
            existing = None
    decision: RulesDecision = evaluate_rules(
        dto, settings, existing_invoice_row=existing)
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
            "row_id":   row["id"],
            "outcome":  "SKIPPED",
            "reason":   decision.reason,
            "trace_id": trace_id,
        }

    # ── Totals Guard (Iter-273, ordering fix Iter-282) ─────────────
    # Runs AFTER status eligibility. If Make.com / Salla silently
    # dropped line items (so `items_sum != subtotal`), refuse the
    # row outright. NO auto-retry: the fix lives upstream.
    # The guard now also embeds Mezan-VAT-15% diagnostics so the
    # operator sees salla_total vs mezan_expected_total side-by-side.
    totals = validate_totals(canonical)
    if not totals.ok:
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"totals_guard": totals.to_log_dict()}},
        )
        patch = transition(
            from_stage="NORMALIZED", to_stage="FAILED_VALIDATION",
            actor="worker",
            note=f"totals guard refused: {totals.code}",
        )
        patch.setdefault("$set", {})["pipeline_error"] = {
            "code":    totals.code,
            "message": totals.message,
            "details": totals.details,
        }
        await _apply(db, row["id"], patch)
        # Move straight to DEAD_LETTER — totals mismatch is upstream-misconfigured,
        # retrying without a Make.com fix would just fail again.
        dead_patch = transition(
            from_stage="FAILED_VALIDATION", to_stage="DEAD_LETTER",
            actor="worker",
            note="totals mismatch is upstream — no auto-retry",
        )
        await _apply(db, row["id"], dead_patch)
        return {
            "row_id":   row["id"],
            "outcome":  "DEAD_LETTER",
            "reason":   totals.code,
            "trace_id": trace_id,
            "totals_guard": totals.to_log_dict(),
        }

    # Persist the Mezan VAT diagnostics on the inbox row even when
    # totals_guard passes — useful for audit + UI display.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"totals_guard": totals.to_log_dict(),
                  "mezan_vat_diagnostics":
                      (totals.details or {}).get("mezan_vat_diagnostics")}},
    )

    # If caller didn't pre-build an API client, build one now —
    # honouring dry_run_mode so the customer resolver doesn't reach
    # Qoyod when the operator is testing.
    if api_client is None:
        api_client, _is_dry = await _get_api_client(
            db, user_id, settings,
            scoped_write_allowance=_sas_gate_passed)
        if api_client is None:
            await _dead_letter(
                db, row_id=row["id"], from_stage="NORMALIZED",
                fail_stage="FAILED_CUSTOMER",
                error={"code": "no_credentials",
                       "message": "Qoyod API key not configured"},
                started_at=row.get("pipeline_started_at"),
            )
            return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                    "reason": "no_credentials"}

    # Existing invoice row — used by `trigger_once_only`.
    # Note: decision was already evaluated above (Iter-282 status gate
    # ordering). We now know the order is ELIGIBLE — proceed with
    # RULES_APPLIED transition and the rest of the pipeline.

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
        # Persist the EXACT payload we sent (or tried to send) to
        # Qoyod, plus the full customer_resolution log. This is the
        # only way the operator can verify post-mortem that the
        # `name` AND `contact_name` fields actually reached the API
        # — saves a debug round-trip and breaks any "did the fix
        # deploy?" doubt with concrete evidence.
        if res.qoyod_request_payload is not None:
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.customer_request":
                        res.qoyod_request_payload,
                    "qoyod_payloads.customer_request_at": _now(),
                    "customer_resolution": res.to_log_dict(),
                }},
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
async def _get_api_client(
    db, user_id: str, settings: dict,
    *,
    scoped_write_allowance: bool = False,
):
    """Return `(client, is_dry)`.

    Iter-2026-02.rev16 — When `scoped_write_allowance=True` the
    real client is constructed with `write_lock_enabled=False` so
    Selective Auto-Send (row-eligible after the gate) can POST
    despite `production_writes_locked=true` on disk. The DB flag is
    NEVER modified — this is a per-call bypass keyed on the gate's
    approval of THIS SPECIFIC ROW.

    Iter-294 — Real clients always carry the Global Write Lock snapshot
    so writes are refused at the api_client layer when
    `production_writes_locked=True`.
    """
    if is_dry_run_mode(settings):
        return DryRunQoyodClient(), True
    key = await get_api_key(db, user_id)
    if not key:
        return None, False
    return QoyodAPIClient(
        key,
        db=db, user_id=user_id,
        write_lock_enabled=(
            False if scoped_write_allowance
            else is_locked(settings)),
    ), False


async def process_customer_resolved_row(
    db, row: dict, *, api_client=None,
) -> dict:
    """Advance a single CUSTOMER_RESOLVED row through:
        4b products → preflight → 4c invoice → 4d receipt → COMPLETED.

    Honours dry_run_mode (no Qoyod POST), records payload snapshots,
    routes receipt-only failures to PARTIAL_FAILURE.
    """
    if row.get("pipeline_stage") != "CUSTOMER_RESOLVED":
        return {"row_id": row.get("id"), "skipped": True,
                "reason": "not_in_customer_resolved_stage",
                "pipeline_stage": row.get("pipeline_stage")}

    user_id = row.get("user_id", "main")
    trace_id = row.get("trace_id")
    started_at = row.get("pipeline_started_at")
    canonical = row.get("canonical_payload") or {}
    qoyod_customer_id = row.get("qoyod_customer_id")
    settings = await _load_settings(db, user_id)

    # Iter-294 — Stamp write-lock audit context for this row so any
    # api_client write-block records this order_number + trace_id.
    # contextvars are task-isolated; the set value naturally goes out
    # of scope when this coroutine returns.
    set_write_lock_context(
        order_number=str(canonical.get("order_number")
                         or row.get("salla_order_number") or "") or None,
        trace_id=trace_id,
        callsite="pipeline.process_customer_resolved_row",
    )

    # Iter-2026-02.rev16 — Re-evaluate Selective Auto-Send gate at
    # this stage too. The gate PASS on entry to NORMALIZED does NOT
    # imply the row is still eligible now — the operator may have
    # disabled the switch mid-flight, or the payment_method mapping
    # may have been unmapped. Idempotent: safe to re-run.
    _sas_gate_passed = False
    if bool(settings.get("selective_auto_send_enabled", False)):
        from integrations.qoyod.selective_auto_send_gate import (
            evaluate_selective_auto_send_gate,
        )
        _sas = evaluate_selective_auto_send_gate(
            canonical=canonical, row=row, settings=settings)
        if not _sas.eligible:
            patch = transition(
                from_stage="CUSTOMER_RESOLVED", to_stage="SKIPPED",
                actor="worker",
                note=("selective_auto_send_gate re-eval failed: "
                      f"{_sas.reason}"),
            )
            patch.setdefault("$set", {})[
                "selective_auto_send_gate"] = _sas.to_log_dict()
            await _apply(db, row["id"], patch)
            return {
                "row_id":  row["id"],
                "outcome": "SKIPPED",
                "reason":  _sas.reason,
                "detail":  _sas.detail,
                "trace_id": trace_id,
                "selective_auto_send_gate": _sas.to_log_dict(),
            }
        _sas_gate_passed = True

    # Resolve client (real or dry-run).
    client_provided = api_client is not None
    is_dry = is_dry_run_mode(settings)
    if not client_provided:
        api_client, is_dry = await _get_api_client(
            db, user_id, settings,
            scoped_write_allowance=_sas_gate_passed)
        if api_client is None:
            await _dead_letter(
                db, row_id=row["id"], from_stage="CUSTOMER_RESOLVED",
                fail_stage="FAILED_PRODUCT",
                error={"code": "credentials_missing",
                       "message": "Qoyod API key not configured"},
                started_at=started_at)
            return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                    "reason": "credentials_missing"}

    # ── 4b PRODUCTS ─────────────────────────────────────────────────
    prod_res: ProductsResolutionResult = await resolve_products(
        db, user_id, canonical.get("items") or [], settings,
        trace_id=trace_id, api_client=api_client)
    if not prod_res.success:
        await _dead_letter(
            db, row_id=row["id"], from_stage="CUSTOMER_RESOLVED",
            fail_stage="FAILED_PRODUCT", error=prod_res.error,
            started_at=started_at)
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "FAILED_PRODUCT",
                "products": prod_res.to_log_dict()}

    product_resolutions = [
        {"sku": i.sku, "qoyod_product_id": i.qoyod_product_id,
         "created_new": i.created_new}
        for i in prod_res.items
    ]
    p = transition(from_stage="CUSTOMER_RESOLVED",
                   to_stage="PRODUCT_RESOLVED", actor="worker",
                   note=(f"{sum(1 for i in prod_res.items if i.created_new)} "
                         f"product(s) created · "
                         f"{sum(1 for i in prod_res.items if not i.created_new)} mapped"))
    p.setdefault("$set", {})["product_resolution"] = prod_res.to_log_dict()
    await _apply(db, row["id"], p)

    # ── PREFLIGHT CHECKLIST ─────────────────────────────────────────
    decision = row.get("business_rules_decision") or {}
    existing_invoice = await db.qoyod_invoices.find_one(
        {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
        {"_id": 0, "status": 1, "qoyod_invoice_id": 1})
    # Iter-2026-02.rev12 — treat DRY:/PREVIEW:/null invoice ids as
    # "no prior send" for preflight idempotency (`already_sent`
    # check). Only real قيود ids count.
    if existing_invoice is not None:
        from integrations.qoyod.eligible_orders import (
            _is_real_invoice_id,
        )
        if not _is_real_invoice_id(
                existing_invoice.get("qoyod_invoice_id")):
            existing_invoice = None
    pf: PreflightResult = preflight_run(
        dto_dict=canonical, settings=settings,
        qoyod_customer_id=qoyod_customer_id,
        product_resolutions=product_resolutions,
        existing_invoice_row=existing_invoice,
    )
    if not pf.passed:
        # Pre-flight failure is BEFORE invoice. Treat it as FAILED_INVOICE
        # → DEAD_LETTER so the operator can see exactly which check failed.
        await _dead_letter(
            db, row_id=row["id"], from_stage="PRODUCT_RESOLVED",
            fail_stage="FAILED_INVOICE",
            error={"code": "preflight_failed",
                   "message": "pre-flight checklist did not pass",
                   "preflight": pf.to_log_dict()},
            started_at=started_at)
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "preflight_failed",
                "preflight": pf.to_log_dict()}

    # ── 4c INVOICE — build payload, snapshot, POST ──────────────────
    # Reconstruct datetime from ISO so the payload builder gets a real obj.
    from datetime import datetime
    inv_date_iso = (decision or {}).get("invoice_date")
    inv_date = (datetime.fromisoformat(inv_date_iso.replace("Z", "+00:00"))
                if inv_date_iso else None)
    invoice_payload = build_invoice_payload(
        dto_dict=canonical, qoyod_customer_id=qoyod_customer_id,
        product_resolutions=product_resolutions,
        invoice_date=inv_date, settings=settings,
    )

    # ─── DRY-Run Leak Preflight (Iter-267, P0) ────────────────────
    # Hard refuse if ANY id we're about to send carries the `DRY:`
    # prefix from the Dry-Run era. Belt-and-suspenders: the product
    # resolver already quarantines such mappings, but a single line
    # that slipped through MUST stop the invoice before it touches
    # Qoyod. Production order 268670571 hit this on 2026-02-27.
    #
    # ONLY active in PRODUCTION (dry_run_mode=False). In dry-run mode
    # the stub `DRY:*` ids ARE expected and must not be refused.
    if not settings.get("dry_run_mode", False):
        leaked: list[str] = []
        if str(qoyod_customer_id).startswith("DRY:"):
            leaked.append(f"contact_id={qoyod_customer_id}")
        for li in (invoice_payload.get("invoice", {}).get("line_items") or []):
            pid = li.get("product_id")
            if pid is None or str(pid).startswith("DRY:"):
                leaked.append(f"product_id={pid}")
        if leaked:
            err = {
                "code":    "dry_run_product_id_leaked_to_production",
                "message": ("منع الإرسال: تم اكتشاف معرّفات Dry-Run في "
                            "payload الفاتورة (" + ", ".join(leaked) + "). "
                            "هذا تسرّب من فترة الاختبار. يجب إعادة "
                            "إنشاء المنتج/العميل في قيود فعلياً."),
                "leaked_ids":     leaked,
                "remediation":    "rebuild_mapping_against_real_qoyod",
            }
            await _dead_letter(
                db, row_id=row["id"],
                from_stage="PRODUCT_RESOLVED",
                fail_stage="FAILED_INVOICE",
                error=err,
                started_at=row.get("pipeline_started_at"),
            )
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_blocked_preflight": invoice_payload,
                    "qoyod_payloads.invoice_blocked_at":         _now(),
                }},
            )
            return {
                "row_id":   row["id"],
                "outcome":  "DEAD_LETTER",
                "reason":   "dry_run_product_id_leaked_to_production",
                "leaked":   leaked,
                "trace_id": trace_id,
            }

    # ─── Iter-290e — extract diagnostics & pre-POST totals guard ─────
    # build_invoice_payload returns {"invoice": {...}, "_diagnostics": {...}}
    # — the diagnostics MUST NOT be sent to Qoyod. Pop and keep for
    # auditing + the math guard below.
    invoice_diagnostics = invoice_payload.pop("_diagnostics", None) or {}
    # Math guard: if our reverse-engineered discount math doesn't land
    # within 0.10 SAR of Salla's total, refuse to POST. Accounting
    # correctness > resilience here — a wrong invoice is worse than a
    # missing one (operator can manually retry once the math is right).
    if (not settings.get("dry_run_mode", False)
            and invoice_diagnostics.get("pricing_mode") == "match_salla_total"):
        diff = abs(float(invoice_diagnostics.get("difference") or 0.0))
        if diff > 0.10:
            # ── Iter-293.1 — Refine the error code so operators see a
            # specific actionable diagnostic instead of a generic
            # "mismatch". Three cases, ordered by specificity:
            #
            #   (a) COD fee exists but `default_cod_fee_product_id` is
            #       missing → MISSING_COD_FEE_PRODUCT_ID.
            #   (b) Diff is large AND payment_method is COD AND no
            #       cod_fee surfaced in the payload → strong signal
            #       that Make.com / Salla webhook isn't sending the
            #       order-level fee. MISSING_ORDER_LEVEL_CHARGE with
            #       suspected_charge=cod_fee.
            #   (c) Anything else → fall back to the legacy mismatch
            #       code (defensive — should never fire on COD orders
            #       after the two checks above).
            cod_fee_amt = float(invoice_diagnostics.get("cod_fee_amount") or 0.0)
            cod_fee_missing_product = bool(
                invoice_diagnostics.get("cod_fee_missing_product"))
            pm = (canonical.get("payment_method")
                  or canonical.get("payment_method_native") or "").lower()
            from .payment_methods import is_cod_family
            is_cod = is_cod_family(pm)

            if cod_fee_missing_product:
                err = {
                    "code":    "MISSING_COD_FEE_PRODUCT_ID",
                    "cod_fee_detected":    True,
                    "cod_fee_amount":      cod_fee_amt,
                    "cod_fee_source_path": invoice_diagnostics.get("cod_fee_source_path"),
                    "cod_fee_source_type": invoice_diagnostics.get("cod_fee_source_type"),
                    "inferred_from_delta": False,
                    "message": (
                        f"الطلب يحوي رسوم COD = {cod_fee_amt} SAR (من مصدر "
                        f"{invoice_diagnostics.get('cod_fee_source_path')}) "
                        "لكن إعدادات قيود لا تحوي `default_cod_fee_product_id`. "
                        "افتح إعدادات قيود → معرّفات افتراضية، وأضف منتج "
                        "قيود لرسوم COD (SKU = MEZAN_COD_FEE)."
                    ),
                    "diagnostics": invoice_diagnostics,
                }
            elif is_cod and cod_fee_amt == 0:
                err = {
                    "code":            "MISSING_ORDER_LEVEL_CHARGE",
                    "cod_fee_detected": False,
                    "inferred_from_delta": True,  # the delta exists but
                                                  # we REFUSE to act on it
                    "salla_total":     invoice_diagnostics.get("salla_total"),
                    "items_total":     invoice_diagnostics.get("expected_qoyod_total"),
                    "missing_delta":   round(diff, 2),
                    "payment_method":  pm,
                    "suspected_charge": "cod_fee",
                    "message": (
                        f"الفرق {diff:.2f} SAR بين إجمالي سلة "
                        f"({invoice_diagnostics.get('salla_total')}) "
                        f"وإجمالي أسطر المنتجات "
                        f"({invoice_diagnostics.get('expected_qoyod_total')}) "
                        f"غير مدعوم بحقل صريح في Payload. "
                        "لن يُحوَّل تلقائياً إلى COD Fee — قد يحتاج "
                        "سيناريو Make إعادة ضبط ليُرسل `amounts.cash_on_delivery` "
                        "أو أي حقل رسوم أصلي من سلة."
                    ),
                    "diagnostics": invoice_diagnostics,
                }
            else:
                err = {
                    "code":    "invoice_total_mismatch_before_post",
                    "message": (f"منع الإرسال (Iter-290e): الفرق بين إجمالي قيود "
                                f"المتوقع ({invoice_diagnostics.get('expected_qoyod_total')}) "
                                f"وإجمالي سلة ({invoice_diagnostics.get('salla_total')}) "
                                f"= {diff:.2f} SAR > 0.10. لن تُنشأ فاتورة بمبلغ "
                                f"غير مطابق للمبلغ المدفوع."),
                    "diagnostics": invoice_diagnostics,
                }
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_blocked_preflight": invoice_payload,
                    "qoyod_payloads.invoice_diagnostics":       invoice_diagnostics,
                    "qoyod_payloads.invoice_blocked_at":        _now(),
                }})
            await _dead_letter(
                db, row_id=row["id"], from_stage="PRODUCT_RESOLVED",
                fail_stage="FAILED_INVOICE", error=err,
                started_at=row.get("pipeline_started_at"),
            )
            return {
                "row_id":  row["id"], "outcome": "DEAD_LETTER",
                "reason":  err["code"],
                "trace_id": trace_id,
            }

    # Snapshot BEFORE attempting POST.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"qoyod_payloads.invoice": invoice_payload,
                  "qoyod_payloads.invoice_diagnostics": invoice_diagnostics,
                  "qoyod_payloads.invoice_snapshot_at": _now(),
                  "preflight": pf.to_log_dict()}},
    )

    qoyod_invoice_id = None
    qoyod_invoice_number = None
    invoice_idem = f"mzn-{trace_id}-invoice"
    inv_resp_raw: Any = None
    inv_started_ms = int(_now().timestamp() * 1000)
    # Iter-001k — Selective Send decision captured at the invoice site
    # so the payment site can reuse the SAME frozen send_timestamp.
    # `None` when we short-circuit via `existing_qid` (invoice was
    # already created in a prior run) OR when running in dry-run mode.
    selective_send_decision = None

    # Iter-291 — Idempotent invoice short-circuit. When a previous run
    # successfully created the Qoyod invoice but the receipt failed
    # afterwards (PARTIAL_FAILURE), retrying the row must NOT create a
    # duplicate invoice in Qoyod. Reuse the stored id and jump straight
    # to the receipt step.
    existing_qid = row.get("qoyod_invoice_id")
    if existing_qid and not is_dry:
        qoyod_invoice_id = str(existing_qid)
        qoyod_invoice_number = row.get("qoyod_invoice_number")
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {
                "qoyod_responses.invoice.reused_from_previous_run": True,
                "qoyod_responses.invoice.reused_qoyod_id": qoyod_invoice_id,
                "qoyod_responses.invoice.reused_at": _now(),
            }})
        # Skip the create_invoice POST entirely and fall through to
        # the post-success branch which advances the stage to
        # INVOICE_CREATED (it tolerates re-applying the same stage).
    else:
        # ── Iter-001k — Selective Live Send guard (STRICTEST layer) ─
        # Runs BEFORE `_writes_blocked` so a policy block returns a
        # dedicated `SELECTIVE_SEND_BLOCKED:<code>` stage that names
        # the exact reason (gate_disabled / q2_cutoff / bank_hold /
        # trigger_status_not_enabled / etc.).
        # DRY-run pipelines skip this — they use a DryRunQoyodClient
        # that never touches api.qoyod.com.
        #
        # SKIP when `_writes_blocked` would fire anyway: the existing
        # LOCKED_AWAITING_APPROVAL path is preserved for the pure
        # write-lock case (no gate, no Q2, no DRY, etc. — just lock).
        # That keeps Iter-293.4-rev5 semantics intact.
        if not is_dry and not _writes_blocked(api_client, settings):
            policy_order = _build_policy_order_from_pipeline_scope(
                row=row, canonical=canonical,
                qoyod_customer_id=qoyod_customer_id,
                products_resolution=prod_res,
                invoice_diagnostics=invoice_diagnostics,
                is_dry=is_dry,
            )
            # Iter-001k — Delegate write-lock resolution to
            # `_writes_blocked` (single source of truth). This honours
            # the Iter-293.4 per-order unlock: an api_client with
            # `write_lock_enabled=False` (as produced by
            # one_shot_reprocess after a valid approval_phrase) is
            # treated as effectively unlocked for the policy, without
            # ever mutating the DB `production_writes_locked` flag.
            _policy_settings = dict(settings)
            _policy_settings["production_writes_locked"] = \
                _writes_blocked(api_client, settings)
            try:
                selective_send_decision = assert_send_allowed(
                    order=policy_order, settings=_policy_settings)
            except SelectiveSendPolicyBlocked as blocked:
                # Persist blocker + park the row. No Qoyod call.
                await db.integration_inbox.update_one(
                    {"id": row["id"]},
                    {"$set": {
                        "qoyod_payloads.invoice_selective_blocked_payload":
                            invoice_payload,
                        "qoyod_payloads.invoice_selective_blocked_at":
                            _now(),
                        "pipeline_stage":
                            f"SELECTIVE_SEND_BLOCKED:"
                            f"{blocked.blocker_code}",
                        "selective_send_blocker_code":
                            blocked.blocker_code,
                        "selective_send_blocker_reason":
                            blocked.blocker_reason,
                        "selective_send_blocked_step":  "invoice",
                        "selective_send_blocked_at":    _now(),
                    }})
                return {
                    "row_id":         row["id"],
                    "outcome":        "SELECTIVE_SEND_BLOCKED",
                    "reason":         blocked.blocker_code,
                    "blocker_reason": blocked.blocker_reason,
                    "step":           "invoice",
                    "trace_id":       trace_id,
                    "note": ("Selective Send policy refused this order "
                             "BEFORE any Qoyod API call. See "
                             "selective_send_blocker_code."),
                }
            # Policy allowed — stamp payload dates with send_date_riyadh.
            invoice_payload = apply_send_date_to_qoyod_payload(
                invoice_payload, selective_send_decision)

        # ── Iter-293.3 — Production Writes Kill Switch ────────────
        # ZATCA-sensitive guard: AFTER the Mezan↔Qoyod↔ZATCA chain is
        # live, any uncontrolled POST to `api.qoyod.com` could push a
        # tax-incorrect invoice to ZATCA. To prevent that the operator
        # can set `production_writes_locked = true` in settings.
        #
        # While locked:
        #   • The pipeline runs all the way through (normalize →
        #     preflight → product/customer resolve → invoice payload
        #     build → totals guard). NOTHING is sent to Qoyod.
        #   • The fully-built `invoice_payload` is persisted in
        #     `qoyod_payloads.invoice_locked_payload` for review.
        #   • The row's pipeline_stage becomes `LOCKED_AWAITING_APPROVAL`.
        #   • Operator reviews via the Preview-Reprocess endpoint,
        #     then explicitly approves the order via the existing
        #     `one_shot_reprocess` (which honours its own approval
        #     flow). New webhooks DO NOT auto-retry.
        #
        # This is INDEPENDENT of `dry_run_mode` (which uses DRY:* stub
        # ids and is intended for offline simulation). Production
        # writes lock keeps real ids but skips the POST.
        if _writes_blocked(api_client, settings):
            # Iter-293.4-rev2 — Pre-check must ALSO write to the audit
            # collection so /admin/write-lock-report surfaces the
            # blocked attempt. Previously only the api_client._request
            # path persisted; the pre-check short-circuited BEFORE
            # the client was called and the audit log missed it.
            attempt_id = await record_blocked_attempt(
                db, user_id=user_id, action="create_invoice",
                method="POST", path="/invoices",
                payload=invoice_payload,
                idempotency_key=invoice_idem,
            )
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_locked_payload": invoice_payload,
                    "qoyod_payloads.invoice_locked_at":      _now(),
                    "pipeline_stage":                        "LOCKED_AWAITING_APPROVAL",
                    "lock_reason":                           "production_writes_locked",
                    "lock_step":                             "invoice",
                    "lock_attempt_id":                       attempt_id,
                    "lock_diagnostics":                      invoice_diagnostics,
                }},
            )
            return {
                "row_id":     row["id"],
                "outcome":    "LOCKED_AWAITING_APPROVAL",
                "reason":     "production_writes_locked",
                "step":       "invoice",
                "attempt_id": attempt_id,
                "trace_id":   trace_id,
                "note":       ("Production writes are locked. Use the "
                               "Preview-Reprocess endpoint to review, "
                               "then one_shot_reprocess to send after "
                               "explicit per-order approval."),
            }

        try:
            inv_resp = await api_client.create_invoice(invoice_payload,
                                                       idem=invoice_idem)
            inv_resp_raw = inv_resp
            # Extract id/number — tolerant to a few shapes.
            if isinstance(inv_resp, dict):
                inv = inv_resp.get("invoice") if isinstance(inv_resp.get("invoice"), dict) else inv_resp
                qoyod_invoice_id = str(inv.get("id")) if inv.get("id") is not None else None
                qoyod_invoice_number = inv.get("number") or inv.get("reference")
        except QoyodWriteLockedError as exc:
            # Iter-294 — Safety-net catch (api_client refused the write
            # because production_writes_locked flipped to True after the
            # pre-check above). Snapshot and surface cleanly.
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_locked_payload": invoice_payload,
                    "qoyod_payloads.invoice_locked_at":      _now(),
                    "pipeline_stage":                        "LOCKED_AWAITING_APPROVAL",
                    "lock_reason":                           "production_writes_locked",
                    "lock_step":                             "invoice",
                    "lock_attempt_id":                       exc.attempt_id,
                    "lock_diagnostics":                      invoice_diagnostics,
                }})
            return {"row_id": row["id"],
                    "outcome": "LOCKED_AWAITING_APPROVAL",
                    "reason":  "production_writes_locked",
                    "step":    "invoice",
                    "attempt_id": exc.attempt_id,
                    "trace_id": trace_id}
        except QoyodAPIError as exc:
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_responses.invoice.error":      exc.to_log_dict(),
                    "qoyod_responses.invoice.received_at": _now(),
                    "qoyod_responses.invoice.duration_ms":
                        int(_now().timestamp() * 1000) - inv_started_ms,
                }})
            await _dead_letter(
                db, row_id=row["id"], from_stage="PRODUCT_RESOLVED",
                fail_stage="FAILED_INVOICE", error=exc.to_log_dict(),
                started_at=started_at)
            return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                    "reason": "FAILED_INVOICE"}

    # Persist raw invoice response (success path) — First-Sync-Monitor.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {
            "qoyod_responses.invoice.body":        inv_resp_raw,
            "qoyod_responses.invoice.received_at": _now(),
            "qoyod_responses.invoice.duration_ms":
                int(_now().timestamp() * 1000) - inv_started_ms,
            "qoyod_responses.invoice.qoyod_id":    qoyod_invoice_id,
            "qoyod_responses.invoice.qoyod_number": qoyod_invoice_number,
        }})

    if not qoyod_invoice_id:
        await _dead_letter(
            db, row_id=row["id"], from_stage="PRODUCT_RESOLVED",
            fail_stage="FAILED_INVOICE",
            error={"code": "qoyod_response_missing_id",
                   "message": "create_invoice returned no id"},
            started_at=started_at)
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "FAILED_INVOICE"}

    p = transition(from_stage="PRODUCT_RESOLVED",
                   to_stage="INVOICE_CREATED", actor="worker",
                   note=("DRY-RUN: invoice payload built, no POST"
                         if is_dry else f"invoice {qoyod_invoice_number} created"))
    p.setdefault("$set", {}).update({
        "qoyod_invoice_id":     qoyod_invoice_id,
        "qoyod_invoice_number": qoyod_invoice_number,
        "dry_run":              is_dry,
    })
    await _apply(db, row["id"], p)

    # Mirror to qoyod_invoices ledger (idempotent upsert).
    await db.qoyod_invoices.update_one(
        {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
        {"$set": {
            "schema_version":      1,
            "user_id":             user_id,
            "trace_id":            trace_id,
            "salla_order_id":      canonical.get("order_id"),
            "salla_order_number":  canonical.get("order_number"),
            "salla_order_status":  canonical.get("order_status_native"),
            "qoyod_invoice_id":    qoyod_invoice_id,
            "qoyod_invoice_number":qoyod_invoice_number,
            "qoyod_customer_id":   qoyod_customer_id,
            "customer_label":      (canonical.get("customer") or {}).get("name"),
            "total_amount":        canonical.get("total_amount"),
            "tax_amount":          canonical.get("tax_amount"),
            "items_count":         len(canonical.get("items") or []),
            "payment_method":      canonical.get("payment_method"),
            "pipeline_stage":      "INVOICE_CREATED",
            "status":              ("sent" if not is_dry else "pending"),
            "dry_run":             is_dry,
            "updated_at":          _now(),
         },
         "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": _now()}},
        upsert=True,
    )

    # ── Iter-293.4-rev7 — Post-create total verification ────────────
    # Production order 269571122 (2026-XX) revealed that قيود's
    # server-side total can differ from Salla's by 0.01 SAR when the
    # line-level discount rounds differently (قيود rounds discount to
    # 2 decimals BEFORE applying VAT; Mezan's simulator used 4-decimal
    # discounts). After ZATCA wiring, any mismatch (even one halala)
    # MUST stop the pipeline pending accountant review — auto-creating
    # a receipt would lock-in the wrong total in the books.
    #
    # Read the قيود-actual total from the response (best-effort across
    # several shape variants), compare with Salla's, persist the
    # comparison, and transition the row to INVOICE_CREATED_TOTAL_MISMATCH
    # if the difference exceeds 0.005 SAR. NO further pipeline work.
    if not is_dry and qoyod_invoice_id:
        salla_total_for_verify = canonical.get("total_amount")
        mezan_expected_for_verify = (invoice_diagnostics or {}).get(
            "mezan_expected_total")
        qoyod_actual_total = None
        for src in (
            inv_resp_raw,
            (inv_resp_raw.get("invoice")
             if isinstance(inv_resp_raw, dict) else None),
        ):
            if not isinstance(src, dict):
                continue
            for k in ("total", "total_amount", "balance", "grand_total"):
                if src.get(k) is not None:
                    try:
                        qoyod_actual_total = float(src[k])
                    except (TypeError, ValueError):
                        qoyod_actual_total = None
                    if qoyod_actual_total is not None:
                        break
            if qoyod_actual_total is not None:
                break

        try:
            diff_value = (
                None if (qoyod_actual_total is None
                         or salla_total_for_verify is None)
                else round(float(qoyod_actual_total)
                           - float(salla_total_for_verify), 4)
            )
        except (TypeError, ValueError):
            diff_value = None

        totals_comparison = {
            "salla_total":           salla_total_for_verify,
            "mezan_expected_total":  mezan_expected_for_verify,
            "qoyod_actual_total":    qoyod_actual_total,
            "difference":            diff_value,
            # Iter-293.4-rev8 — Tri-state rounding policy.
            #
            #   |diff| <= 0.005       → no warning (essentially zero).
            #   0.005 < |diff| <= 0.01 → ACCEPTED rounding gap.
            #                            Pipeline continues; row carries a
            #                            permanent `rounding_warning=True`
            #                            flag and lands at
            #                            COMPLETED_WITH_ROUNDING_WARNING.
            #   |diff| > 0.01         → BLOCKER. Pipeline halts at
            #                            INVOICE_CREATED_TOTAL_MISMATCH.
            #                            Accountant must review.
            "warning_tolerance_sar": 0.005,    # below = no warning
            "blocker_tolerance_sar": 0.01,     # above = blocker
            "rounding_warning":      (
                diff_value is not None
                and abs(diff_value) > 0.005
                and abs(diff_value) <= 0.01),
            "mismatch":              (diff_value is not None
                                      and abs(diff_value) > 0.01),
            "reason":                ("qoyod_server_side_rounding"
                                      if (diff_value is not None
                                          and abs(diff_value) > 0.005)
                                      else None),
            "checked_at":            _now(),
        }
        # Persist the comparison so the diagnostic endpoint surfaces
        # it even if the row never re-enters the pipeline.
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"totals_comparison": totals_comparison}})
        await db.qoyod_invoices.update_one(
            {"user_id":        user_id,
             "salla_order_id": canonical.get("order_id")},
            {"$set": {"totals_comparison": totals_comparison}})

        if totals_comparison["mismatch"]:
            # Transition the inbox row INVOICE_CREATED →
            # INVOICE_CREATED_TOTAL_MISMATCH and STOP.
            mismatch_error = {
                "code":    "qoyod_actual_total_mismatch",
                "message": (
                    "الفاتورة تم إنشاؤها في قيود لكن إجمالي قيود الفعلي "
                    f"({qoyod_actual_total}) يختلف عن Salla "
                    f"({salla_total_for_verify}) بمقدار "
                    f"{diff_value:+.2f} SAR. تم إيقاف السجل عند "
                    "INVOICE_CREATED_TOTAL_MISMATCH — لا سند قبض، "
                    "لا إكمال تلقائي. يلزم قرار محاسبي."
                ),
                "salla_total":         salla_total_for_verify,
                "mezan_expected_total": mezan_expected_for_verify,
                "qoyod_actual_total":   qoyod_actual_total,
                "difference":           diff_value,
                "qoyod_invoice_id":     qoyod_invoice_id,
                "qoyod_invoice_number": qoyod_invoice_number,
            }
            try:
                p_mm = transition(
                    from_stage="INVOICE_CREATED",
                    to_stage="INVOICE_CREATED_TOTAL_MISMATCH",
                    actor="worker",
                    error=mismatch_error,
                )
                p_mm.setdefault("$set", {})["pipeline_error"] = mismatch_error
                await _apply(db, row["id"], p_mm)
            except InvalidTransition as _exc:    # pragma: no cover
                logger.warning(
                    "INVOICE_CREATED → INVOICE_CREATED_TOTAL_MISMATCH "
                    "refused by state machine: %s", _exc)
            # Mirror the mismatch state to the ledger so an external
            # dashboard / report can filter on it.
            await db.qoyod_invoices.update_one(
                {"user_id":        user_id,
                 "salla_order_id": canonical.get("order_id")},
                {"$set": {
                    "pipeline_stage": "INVOICE_CREATED_TOTAL_MISMATCH",
                    "last_error":     mismatch_error,
                    "updated_at":     _now(),
                }})
            logger.warning(
                "QOYOD_ACTUAL_TOTAL_MISMATCH order=%s trace=%s "
                "qoyod_invoice_id=%s diff=%s SAR — pipeline halted.",
                canonical.get("order_number") or canonical.get("order_id"),
                trace_id, qoyod_invoice_id, diff_value,
            )
            return {
                "row_id":            row["id"],
                "outcome":           "INVOICE_CREATED_TOTAL_MISMATCH",
                "reason":            "qoyod_actual_total_mismatch",
                "qoyod_invoice_id":  qoyod_invoice_id,
                "qoyod_invoice_number": qoyod_invoice_number,
                "totals_comparison": totals_comparison,
                "trace_id":          trace_id,
                "note":              ("لا يتم إنشاء سند قبض ولا إكمال "
                                       "تلقائي للسجل عند فروقات الإجمالي. "
                                       "يلزم قرار محاسبي يدوي."),
            }
        # else: totals match. Fall through to the normal posting-mode
        # branch below.

    # ── 4d INVOICE PAYMENT (Iter-290h — replaces standalone Receipt) ──
    #
    # Why this exists
    # ───────────────
    # The previous flow called `POST /receipts` which produced a
    # STANDALONE Qoyod receipt — the invoice balance was never closed
    # and the receipt sat in Qoyod's "غير مستعمل" (unallocated) list.
    # The correct Qoyod flow per `apidoc.qoyod.com` is `POST
    # /invoice_payments` which registers the payment ON the invoice.
    #
    # New stage flow
    # ──────────────
    #     INVOICE_CREATED
    #     → PAYMENT_METHOD_MAPPING_MISSING (pre-POST guard)
    #     → PAYMENT_LINK_FAILED            (Qoyod 4xx/5xx)
    #     → INVOICE_PAYMENT_CREATED        (happy path)
    #     → COMPLETED
    #
    # No fallback to /receipts. Per user spec — "إذا فشل ربط السند
    # بالفاتورة، لا تسجل الطلب كناجح".

    # ── Iter-293.4-rev6 — posting_mode FIRST (BEFORE auto_receipt) ──
    # Critical ordering fix: the payment-method posting_mode is an
    # ACCOUNTING decision baked into the order itself. COD orders
    # MUST always post as `credit_invoice_only` (invoice only, no
    # invoice_payment) regardless of whether the operator has
    # `auto_receipt` enabled. Previously the auto_receipt guard fired
    # BEFORE posting_mode resolution, so COD orders with
    # `auto_receipt=False` got stuck at INVOICE_CREATED — invoice
    # was created in Qoyod but the row never reached COMPLETED.
    # Production order 269571122 hit this on 2026-XX-XX.
    from .payment_methods import (
        resolve_posting_mode,
        POSTING_MODE_CREDIT_INVOICE_ONLY,
        POSTING_MODE_DISABLED,
    )
    pm_for_mode = (canonical.get("payment_method")
                   or canonical.get("payment_method_native"))
    _posting_mode = resolve_posting_mode(settings, pm_for_mode)

    if _posting_mode == POSTING_MODE_CREDIT_INVOICE_ONLY:
        # COD path — invoice exists in Qoyod, no receipt. Mark row + invoice
        # as COMPLETED so the monitor doesn't think the payment step
        # failed. qoyod_invoice_payment_id stays NULL by design.
        #
        # Iter-293.4-rev8 — Honour the rounding-warning flag from the
        # post-create verification above. When |diff| in (0.005, 0.01]
        # the row lands at `COMPLETED_WITH_ROUNDING_WARNING` instead
        # of plain `COMPLETED`, with the totals_comparison preserved
        # so a daily report can list orders that carry the warning.
        _tc = (locals().get("totals_comparison") or {}) if not is_dry else {}
        _rounding_warning = bool(_tc.get("rounding_warning"))
        _final_stage = ("COMPLETED_WITH_ROUNDING_WARNING"
                        if _rounding_warning else "COMPLETED")
        _ledger_status = ("completed_with_rounding_warning"
                          if _rounding_warning else "sent")
        await db.qoyod_invoices.update_one(
            {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
            {"$set": {
                "status":                _ledger_status,
                "pipeline_stage":        _final_stage,
                "posting_mode":          _posting_mode,
                "qoyod_invoice_payment_id": None,
                "qoyod_receipt_id":      None,
                "paid_amount":           0,
                "remaining_amount":      canonical.get("total_amount"),
                "rounding_warning":      _rounding_warning,
                "updated_at":            _now(),
            }})
        p = transition(from_stage="INVOICE_CREATED",
                       to_stage=_final_stage, actor="worker",
                       note=(
                           "credit_invoice_only — COD posted as credit "
                           "invoice, no receipt "
                           + (f"(rounding gap {_tc.get('difference'):+.2f} "
                              "SAR accepted as warning)"
                              if _rounding_warning else
                              "(correct accounting)")),
                       existing_started_at=started_at)
        p.setdefault("$set", {})["posting_mode"] = _posting_mode
        if _rounding_warning:
            p["$set"]["rounding_warning"] = True
        await _apply(db, row["id"], p)
        return {"row_id":               row["id"],
                "outcome":              _final_stage,
                "reason":               ("credit_invoice_only_with_rounding_warning"
                                          if _rounding_warning
                                          else "credit_invoice_only"),
                "posting_mode":         _posting_mode,
                "qoyod_invoice_id":     qoyod_invoice_id,
                "qoyod_invoice_payment_id": None,
                "totals_comparison":    _tc or None,
                "rounding_warning":     _rounding_warning,
                "dry_run":              is_dry}

    if _posting_mode == POSTING_MODE_DISABLED:
        # Payment method is intentionally not synced. We've already
        # created the invoice (4c) — that may not be ideal, but it's
        # the conservative default until the operator removes the
        # invoice_trigger_status for these methods. Mark as INVOICE_CREATED.
        await db.qoyod_invoices.update_one(
            {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
            {"$set": {"posting_mode": _posting_mode,
                      "pipeline_stage": "INVOICE_CREATED",
                      "updated_at": _now()}})
        return {"row_id": row["id"], "outcome": "INVOICE_CREATED",
                "reason": "posting_mode_disabled",
                "posting_mode": _posting_mode,
                "qoyod_invoice_id": qoyod_invoice_id, "dry_run": is_dry}

    # auto_receipt / create_receipts capability gate (runs AFTER
    # posting_mode so it only governs pre-paid methods that would
    # normally build an invoice_payment).
    if not (settings.get("auto_receipt", True)
            and (settings.get("capabilities") or {}).get("create_receipts", True)):
        # Invoice-payment step disabled by capability (e.g. tenant
        # using Qoyod's auto-payment plugin externally). Stop at
        # INVOICE_CREATED as success.
        return {"row_id": row["id"], "outcome": "INVOICE_CREATED",
                "reason": "invoice_payment_disabled_by_capability",
                "posting_mode": _posting_mode,
                "dry_run": is_dry,
                "qoyod_invoice_id": qoyod_invoice_id}

    payment_payload, idem_fingerprint = build_invoice_payment_payload(
        qoyod_invoice_id=qoyod_invoice_id,
        dto_dict=canonical, invoice_date=inv_date, settings=settings)

    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"qoyod_payloads.invoice_payment": payment_payload,
                  "qoyod_payloads.invoice_payment_fingerprint": idem_fingerprint,
                  "qoyod_payloads.invoice_payment_snapshot_at": _now()}},
    )

    # ── Pre-POST guard 1: payment account mapping must be set ──────
    # Iter-290h.6 — wire field is `account_id` (not `account`).
    if payment_payload["invoice_payment"].get("account_id") is None:
        err = {
            "code":            "payment_method_mapping_missing",
            "failed_at_stage": "PAYMENT_METHOD_MAPPING_MISSING",
            "salla_payment_method": (canonical.get("payment_method")
                                     or canonical.get("payment_method_native")),
            "message": (
                "لم يتم ضبط Qoyod payment_method_id لطريقة الدفع "
                f"'{canonical.get('payment_method')}' في الإعدادات. "
                "افتح إعدادات قيود ← طرق الدفع، وضبط حساب قيود "
                "لهذه الطريقة قبل إعادة المحاولة."
            ),
        }
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"qoyod_responses.invoice_payment.error": err,
                      "qoyod_responses.invoice_payment.received_at": _now()}})
        p = transition(from_stage="INVOICE_CREATED",
                       to_stage="PAYMENT_METHOD_MAPPING_MISSING",
                       actor="worker", error=err)
        p.setdefault("$set", {})["pipeline_error"] = err
        await _apply(db, row["id"], p)
        p2 = transition(from_stage="PAYMENT_METHOD_MAPPING_MISSING",
                        to_stage="PARTIAL_FAILURE", actor="worker",
                        note="invoice in Qoyod · payment_method mapping needed",
                        existing_started_at=started_at)
        await _apply(db, row["id"], p2)
        await db.qoyod_invoices.update_one(
            {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
            {"$set": {"status": "invoice_sent_payment_method_missing",
                      "pipeline_stage": "PARTIAL_FAILURE",
                      "last_error": err, "updated_at": _now()}})
        return {"row_id": row["id"], "outcome": "PARTIAL_FAILURE",
                "reason": "PAYMENT_METHOD_MAPPING_MISSING",
                "qoyod_invoice_id": qoyod_invoice_id}

    # ── Pre-POST guard 2: DB-side idempotency on the fingerprint ─────
    # Per user spec — `order_id + invoice_id + payment_method + amount`.
    # If a matching row already exists in `qoyod_invoice_payments` with
    # a real Qoyod id, short-circuit straight to COMPLETED instead of
    # double-posting.
    existing_payment = await db.qoyod_invoice_payments.find_one({
        "user_id":          user_id,
        "salla_order_id":   idem_fingerprint["order_id"],
        "qoyod_invoice_id": idem_fingerprint["qoyod_invoice_id"],
        "payment_method":   idem_fingerprint["payment_method"],
        "amount":           idem_fingerprint["amount"],
    }, {"_id": 0, "qoyod_invoice_payment_id": 1})
    qoyod_invoice_payment_id: Optional[str] = None
    payment_resp_raw: Any = None
    payment_started_ms = int(_now().timestamp() * 1000)

    if existing_payment and existing_payment.get("qoyod_invoice_payment_id"):
        # Already posted in a previous run — reuse the id.
        qoyod_invoice_payment_id = str(existing_payment["qoyod_invoice_payment_id"])
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {
                "qoyod_responses.invoice_payment.idempotent_short_circuit": True,
                "qoyod_responses.invoice_payment.qoyod_id": qoyod_invoice_payment_id,
            }})
    else:
        # ── Iter-001k — Selective Send guard for payment step ──────
        # Same rules as invoice site: skip when the legacy write-lock
        # path would fire anyway (keeps LOCKED_AWAITING_APPROVAL for
        # the pure write-lock case).
        payment_decision = selective_send_decision
        if payment_decision is None and not is_dry \
                and not _writes_blocked(api_client, settings):
            policy_order_pay = _build_policy_order_from_pipeline_scope(
                row=row, canonical=canonical,
                qoyod_customer_id=qoyod_customer_id,
                products_resolution=prod_res,
                invoice_diagnostics=None,
                is_dry=is_dry,
            )
            # Iter-001k — At the payment site, the existing invoice id
            # is EXPECTED (that's what we're paying against). Strip it
            # from the policy order so the `already_sent` check (which
            # is intended to prevent DUPLICATE invoice creation) does
            # not misfire on the payment path.
            policy_order_pay["existing_qoyod_invoice_id"] = None
            # Iter-001k — same per-order unlock resolution as the
            # invoice site (see comment there).
            _policy_settings_pay = dict(settings)
            _policy_settings_pay["production_writes_locked"] = \
                _writes_blocked(api_client, settings)
            try:
                payment_decision = assert_send_allowed(
                    order=policy_order_pay,
                    settings=_policy_settings_pay)
            except SelectiveSendPolicyBlocked as blocked:
                await db.integration_inbox.update_one(
                    {"id": row["id"]},
                    {"$set": {
                        "qoyod_payloads.invoice_payment_selective_blocked_payload":
                            payment_payload,
                        "qoyod_payloads.invoice_payment_selective_blocked_at":
                            _now(),
                        "pipeline_stage":
                            f"SELECTIVE_SEND_BLOCKED:"
                            f"{blocked.blocker_code}",
                        "selective_send_blocker_code":
                            blocked.blocker_code,
                        "selective_send_blocker_reason":
                            blocked.blocker_reason,
                        "selective_send_blocked_step":
                            "invoice_payment",
                        "selective_send_blocked_at": _now(),
                    }})
                return {
                    "row_id":         row["id"],
                    "outcome":        "SELECTIVE_SEND_BLOCKED",
                    "reason":         blocked.blocker_code,
                    "blocker_reason": blocked.blocker_reason,
                    "step":           "invoice_payment",
                    "qoyod_invoice_id": qoyod_invoice_id,
                    "trace_id":       trace_id,
                }
        # Stamp payment payload dates from the (invoice or fresh)
        # decision so invoice + payment share `send_date_riyadh`.
        if payment_decision is not None:
            payment_payload = apply_send_date_to_qoyod_payload(
                payment_payload, payment_decision)

        # ── Iter-293.4 — Global Write Lock pre-check for invoice_payment ─
        # Mirror the pre-check on the create_invoice path (line 652)
        # so the operator sees a clean LOCKED_AWAITING_APPROVAL outcome
        # instead of an exception bubbling up from api_client._request.
        # The API client itself enforces the lock as a safety net.
        if _writes_blocked(api_client, settings):
            # Iter-293.4-rev2 — Persist to audit log so /admin/write-lock-report
            # surfaces this attempt (previously invisible to the report).
            payment_idem = (
                f"mzn-{trace_id}-invoice-payment-{idem_fingerprint['qoyod_invoice_id']}")
            attempt_id = await record_blocked_attempt(
                db, user_id=user_id, action="create_invoice_payment",
                method="POST", path="/invoice_payments",
                payload=payment_payload,
                idempotency_key=payment_idem,
            )
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_payment_locked_payload": payment_payload,
                    "qoyod_payloads.invoice_payment_locked_at":      _now(),
                    "pipeline_stage":                                "LOCKED_AWAITING_APPROVAL",
                    "lock_reason":                                   "production_writes_locked",
                    "lock_step":                                     "invoice_payment",
                    "lock_attempt_id":                               attempt_id,
                }},
            )
            return {
                "row_id":     row["id"],
                "outcome":    "LOCKED_AWAITING_APPROVAL",
                "reason":     "production_writes_locked",
                "step":       "invoice_payment",
                "attempt_id": attempt_id,
                "qoyod_invoice_id": qoyod_invoice_id,
                "trace_id":   trace_id,
                "note":       ("Production writes are locked. The "
                               "invoice was already created in Qoyod; "
                               "the invoice_payment step has been parked "
                               "for explicit approval."),
            }

        payment_idem = (
            f"mzn-{trace_id}-invoice-payment-{idem_fingerprint['qoyod_invoice_id']}")
        try:
            payment_resp = await api_client.create_invoice_payment(
                payment_payload, idem=payment_idem)
            payment_resp_raw = payment_resp
            if isinstance(payment_resp, dict):
                r = (payment_resp.get("invoice_payment")
                     if isinstance(payment_resp.get("invoice_payment"), dict)
                     else payment_resp)
                qoyod_invoice_payment_id = (
                    str(r.get("id")) if r.get("id") is not None else None)
        except QoyodWriteLockedError as exc:
            # Safety net — should be unreachable thanks to the pre-check
            # above, but if the lock was toggled to True mid-request the
            # api_client raised this. Persist locked snapshot + surface
            # cleanly.
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_payment_locked_payload": payment_payload,
                    "qoyod_payloads.invoice_payment_locked_at":      _now(),
                    "pipeline_stage":                                "LOCKED_AWAITING_APPROVAL",
                    "lock_reason":                                   "production_writes_locked",
                    "lock_step":                                     "invoice_payment",
                    "lock_attempt_id":                               exc.attempt_id,
                }})
            return {"row_id": row["id"],
                    "outcome": "LOCKED_AWAITING_APPROVAL",
                    "reason": "production_writes_locked",
                    "step":   "invoice_payment",
                    "attempt_id": exc.attempt_id,
                    "qoyod_invoice_id": qoyod_invoice_id}
        except QoyodAPIError as exc:
            err_log = exc.to_log_dict()
            err_log["request_body_json"] = payment_payload
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_responses.invoice_payment.error":      err_log,
                    "qoyod_responses.invoice_payment.received_at": _now(),
                    "qoyod_responses.invoice_payment.duration_ms":
                        int(_now().timestamp() * 1000) - payment_started_ms,
                }})
            # Partial failure! Invoice exists, payment-link does not.
            p = transition(from_stage="INVOICE_CREATED",
                           to_stage="PAYMENT_LINK_FAILED", actor="worker",
                           error=err_log)
            p.setdefault("$set", {})["pipeline_error"] = err_log
            await _apply(db, row["id"], p)
            p2 = transition(from_stage="PAYMENT_LINK_FAILED",
                            to_stage="PARTIAL_FAILURE", actor="worker",
                            note="invoice in Qoyod · payment_link failed · review",
                            existing_started_at=started_at)
            await _apply(db, row["id"], p2)
            await db.qoyod_invoices.update_one(
                {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
                {"$set": {"status": "invoice_sent_payment_link_failed",
                          "pipeline_stage": "PARTIAL_FAILURE",
                          "last_error": err_log, "updated_at": _now()}})
            return {"row_id": row["id"], "outcome": "PARTIAL_FAILURE",
                    "reason": "PAYMENT_LINK_FAILED",
                    "qoyod_invoice_id": qoyod_invoice_id}

    # ── Happy path: payment linked ──────────────────────────────────
    # Persist into qoyod_invoice_payments ledger (DB-side idempotency
    # store + audit). Upsert on the fingerprint tuple.
    await db.qoyod_invoice_payments.update_one(
        {
            "user_id":          user_id,
            "salla_order_id":   idem_fingerprint["order_id"],
            "qoyod_invoice_id": idem_fingerprint["qoyod_invoice_id"],
            "payment_method":   idem_fingerprint["payment_method"],
            "amount":           idem_fingerprint["amount"],
        },
        {"$set": {
            "user_id":                   user_id,
            "trace_id":                  trace_id,
            "salla_order_id":            idem_fingerprint["order_id"],
            "salla_order_number":        canonical.get("order_number"),
            "qoyod_invoice_id":          idem_fingerprint["qoyod_invoice_id"],
            "qoyod_invoice_payment_id":  qoyod_invoice_payment_id,
            "payment_method":            idem_fingerprint["payment_method"],
            "payment_method_id":         idem_fingerprint["payment_method_id"],
            "amount":                    idem_fingerprint["amount"],
            "currency":                  canonical.get("currency") or "SAR",
            "dry_run":                   is_dry,
            "updated_at":                _now(),
        },
         "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": _now()}},
        upsert=True,
    )

    p = transition(from_stage="INVOICE_CREATED",
                   to_stage="INVOICE_PAYMENT_CREATED", actor="worker",
                   note=("DRY-RUN: invoice_payment payload built, no POST"
                         if is_dry else "invoice_payment recorded ON invoice in Qoyod"))
    p.setdefault("$set", {})["qoyod_invoice_payment_id"] = qoyod_invoice_payment_id
    # Iter-290h.6 — Clear stale failure breadcrumbs from any previous
    # attempt on this row. Without this the monitor's
    # `_status_for_invoice_payment_step` keeps reporting the step as
    # "failed" because `last_failed_stage=PAYMENT_LINK_FAILED` is left
    # over from the first (truly failed) attempt, AND the drawer
    # shows BOTH a stale `error` and the fresh `body` under the same
    # step. Once the payment landed on قيود the prior failure is no
    # longer the source of truth.
    p["$set"]["last_failed_stage"] = None
    p["$set"]["pipeline_error"]    = None
    await _apply(db, row["id"], p)
    # Persist raw response — First-Sync-Monitor.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {
            "qoyod_responses.invoice_payment.body":        payment_resp_raw,
            "qoyod_responses.invoice_payment.received_at": _now(),
            "qoyod_responses.invoice_payment.duration_ms":
                int(_now().timestamp() * 1000) - payment_started_ms,
            "qoyod_responses.invoice_payment.qoyod_id":    qoyod_invoice_payment_id,
            # Iter-290h.6 — explicitly clear the stale error so the
            # drawer doesn't mix the old 422 response with the fresh
            # success body.
            "qoyod_responses.invoice_payment.error":       None,
        }})

    p = transition(from_stage="INVOICE_PAYMENT_CREATED", to_stage="COMPLETED",
                   actor="worker",
                   note=("DRY-RUN COMPLETED — no Qoyod POSTs were made"
                         if is_dry else "invoice + invoice_payment pushed to Qoyod"),
                   existing_started_at=started_at)
    await _apply(db, row["id"], p)
    await db.qoyod_invoices.update_one(
        {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
        {"$set": {"qoyod_invoice_payment_id": qoyod_invoice_payment_id,
                  "pipeline_stage":  "COMPLETED",
                  "status":          ("sent" if not is_dry else "pending"),
                  "sent_at":         _now() if not is_dry else None,
                  "updated_at":      _now()}})

    return {"row_id": row["id"], "outcome": "COMPLETED",
            "dry_run": is_dry,
            "qoyod_invoice_id":         qoyod_invoice_id,
            "qoyod_invoice_payment_id": qoyod_invoice_payment_id}


async def process_pending_customer_resolved(
    db, user_id: str = "main", *, limit: int = 25, api_client=None,
) -> dict:
    cursor = db.integration_inbox.find(
        {"user_id": user_id, "pipeline_stage": "CUSTOMER_RESOLVED"},
        sort=[("received_at", 1)], limit=max(1, min(limit, 100)),
    )
    rows = []
    async for r in cursor:
        rows.append(r)
    counters = {"completed": 0, "partial_failure": 0, "dead_letter": 0,
                "invoice_only": 0}
    items: list[dict] = []
    for row in rows:
        out = await process_customer_resolved_row(db, row, api_client=api_client)
        items.append(out)
        oc = out.get("outcome")
        if oc == "COMPLETED":
            counters["completed"] += 1
        elif oc == "PARTIAL_FAILURE":
            counters["partial_failure"] += 1
        elif oc == "DEAD_LETTER":
            counters["dead_letter"] += 1
        elif oc == "INVOICE_CREATED":
            counters["invoice_only"] += 1
    return {"ok": True, "processed": len(items), "counts": counters,
            "items": items}


# ─── Day 4 Report (read-only aggregation) ───────────────────────────
async def day4_report(db, user_id: str) -> dict:
    """Aggregates eligibility outcomes across all `integration_inbox` rows
    for the tenant — answers "how did Day 4 rules + customer resolution
    play out so far?". Used by the dashboard card."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$pipeline_stage", "n": {"$sum": 1}}},
    ]
    by_stage = {}
    async for d in db.integration_inbox.aggregate(pipeline):
        by_stage[d["_id"]] = d["n"]

    # Detail buckets
    skipped_reasons = {}
    async for d in db.integration_inbox.aggregate([
        {"$match": {"user_id": user_id, "pipeline_stage": "SKIPPED"}},
        {"$group": {"_id": "$business_rules_decision.reason", "n": {"$sum": 1}}},
    ]):
        skipped_reasons[d["_id"] or "unknown"] = d["n"]

    dead_letter_by_stage = {}
    async for d in db.integration_inbox.aggregate([
        {"$match": {"user_id": user_id, "pipeline_stage": "DEAD_LETTER"}},
        {"$group": {"_id": "$last_failed_stage", "n": {"$sum": 1}}},
    ]):
        dead_letter_by_stage[d["_id"] or "unknown"] = d["n"]

    return {
        "schema_version": 1,
        "generated_at":   _now(),
        "by_stage":       by_stage,
        "skipped_reasons": skipped_reasons,
        "dead_letter_by_stage": dead_letter_by_stage,
        "totals": {
            "normalized":          by_stage.get("NORMALIZED", 0),
            "customer_resolved":   by_stage.get("CUSTOMER_RESOLVED", 0),
            "skipped":             by_stage.get("SKIPPED", 0),
            "dead_letter":         by_stage.get("DEAD_LETTER", 0),
            "partial_failure":     by_stage.get("PARTIAL_FAILURE", 0),
            "completed":           by_stage.get("COMPLETED", 0),
        },
    }


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
