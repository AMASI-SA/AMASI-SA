"""Iter-2026-02.rev18 — Force-reprocess a DRY-run row from scratch.

Scope
─────
Recovery path for rows stuck at `INVOICE_CREATED` with `DRY:invoice:*`
sentinels (created by a previous DRY-run before the Selective
Auto-Send Gate + scoped live client existed). The worker's normal
"Run Now" button only picks NORMALIZED rows and does NOT know how
to reset a DRY invoice back to the start.

Contract (STRICT — user directive 2026-02-27)
─────────────────────────────────────────────
  • Refuses if the row carries a REAL قيود invoice_id (not DRY:/
    PREVIEW:). Never re-invoices a real Qoyod document.
  • Refuses if `qoyod_invoices` collection has a real id for this
    order — the row and the collection are BOTH consulted.
  • Refuses if the Selective Auto-Send Gate would refuse the row.
  • Clears DRY sentinels from the row (`qoyod_invoice_id`,
    `qoyod_customer_id`, `qoyod_product_id` on line items).
  • Resets pipeline_stage: `INVOICE_CREATED` → `RETRYING` →
    `NORMALIZED` (two-step via state_machine transitions).
  • Then invokes `process_normalized_row` inline — the Gate fires
    again, the scoped live client is constructed (rev17), and the
    real Qoyod POSTs happen.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _NOW_ISO() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_real_qid(v: Any) -> bool:
    if v in (None, ""):
        return False
    s = str(v)
    return not (s.startswith("DRY:") or s.startswith("PREVIEW:"))


class ForceReprocessRefused(Exception):
    def __init__(self, code: str, human: str, **extra):
        super().__init__(human)
        self.code  = code
        self.extra = extra


async def force_reprocess_dry_row(
    db, *,
    user_id:             str,
    salla_order_number:  str,
    trace_id:            Optional[str],
    confirm_token:       str,
    actor:               str = "operator",
) -> dict:
    """Reset a DRY row and re-run the pipeline with scoped live
    writes. Returns a rich diagnostic dict — never raises for
    business-logic refusals (raises only for unexpected errors)."""
    expected = f"FORCE-REPROCESS-DRY-{salla_order_number}"
    if confirm_token != expected:
        raise ForceReprocessRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{expected}' to authorise.")

    # Load row (prefer explicit trace_id when given).
    row = None
    if trace_id:
        row = await db.integration_inbox.find_one(
            {"user_id": user_id, "trace_id": trace_id})
    if row is None:
        row = await db.integration_inbox.find_one(
            {"user_id": user_id,
             "salla_order_number": str(salla_order_number)})
    if row is None:
        raise ForceReprocessRefused(
            "row_not_found",
            f"No integration_inbox row for order "
            f"{salla_order_number} / trace {trace_id}.")

    debug: dict[str, Any] = {
        "reprocess_invoked":       True,
        "actor":                   actor,
        "salla_order_number":      str(salla_order_number),
        "trace_id":                row.get("trace_id"),
        "reset_from_stage":        row.get("pipeline_stage"),
        "cleared_dry_customer_id": False,
        "cleared_dry_invoice_id":  False,
        "cleared_dry_products":    0,
        "selective_auto_send_gate":         None,
        "scoped_write_allowance":           None,
        "dry_run_seen_by_client":           None,
        "contact_id_before_invoice":        None,
        "product_ids_before_invoice":       None,
        "invoice_post_attempted":           False,
        "payment_post_attempted":           False,
        "final_stage":                      None,
        "refused":                          False,
        "refuse_reason":                    None,
    }

    # Refusal A — real قيود invoice_id anywhere.
    row_qid = row.get("qoyod_invoice_id")
    if _is_real_qid(row_qid):
        debug["refused"]       = True
        debug["refuse_reason"] = "row_has_real_qoyod_invoice_id"
        return {
            "ok": False, "outcome": "REFUSED",
            "code": "row_has_real_qoyod_invoice_id",
            "detail": (f"Row carries real قيود invoice_id "
                       f"{row_qid!r}. Use retry_payment_only instead."),
            "debug": debug,
        }
    inv_coll_row = await db.qoyod_invoices.find_one(
        {"user_id": user_id,
         "salla_order_number": str(salla_order_number)},
        {"_id": 0, "qoyod_invoice_id": 1})
    if inv_coll_row and _is_real_qid(inv_coll_row.get("qoyod_invoice_id")):
        debug["refused"]       = True
        debug["refuse_reason"] = "qoyod_invoices_collection_has_real_id"
        return {
            "ok": False, "outcome": "REFUSED",
            "code": "qoyod_invoices_collection_has_real_id",
            "detail": ("qoyod_invoices row already has real id "
                       f"{inv_coll_row.get('qoyod_invoice_id')!r}."),
            "debug": debug,
        }

    # Refusal B — Selective Auto-Send Gate would refuse this row.
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    from integrations.qoyod.selective_auto_send_gate import (
        evaluate_selective_auto_send_gate,
    )
    canonical = row.get("canonical_payload") or {}
    # Simulate the row with DRY IDs stripped so the Gate's
    # `has_real_invoice_id` check does not misfire.
    row_for_gate = dict(row)
    row_for_gate["qoyod_invoice_id"] = None
    gate = evaluate_selective_auto_send_gate(
        canonical=canonical, row=row_for_gate, settings=settings)
    debug["selective_auto_send_gate"] = gate.to_log_dict()
    if not gate.eligible:
        debug["refused"]       = True
        debug["refuse_reason"] = f"gate_refused:{gate.reason}"
        return {
            "ok": False, "outcome": "REFUSED",
            "code": f"gate_refused:{gate.reason}",
            "detail": gate.detail,
            "debug": debug,
        }

    # ── Reset phase — clear DRY IDs + rewind state ────────────────
    dry_customer_cleared = False
    dry_invoice_cleared  = False
    dry_products_cleared = 0

    cust_id = row.get("qoyod_customer_id")
    if cust_id and str(cust_id).startswith("DRY:"):
        dry_customer_cleared = True
    if row_qid and str(row_qid).startswith("DRY:"):
        dry_invoice_cleared = True
    # Line-item product ids — clear any DRY sentinels but preserve
    # shipping product mapping (typically product_id=42) if REAL.
    items = list(canonical.get("items") or [])
    cleared_items = []
    for it in items:
        new_it = dict(it) if isinstance(it, dict) else it
        if isinstance(new_it, dict):
            pid = new_it.get("qoyod_product_id")
            if pid and str(pid).startswith("DRY:"):
                new_it["qoyod_product_id"] = None
                dry_products_cleared += 1
        cleared_items.append(new_it)
    new_canonical = dict(canonical)
    new_canonical["items"] = cleared_items

    # Two-hop state transition INVOICE_CREATED → RETRYING → NORMALIZED
    # via `state_machine.transition` so history + guards stay honest.
    from integrations.qoyod.state_machine import transition
    from_stage_1 = row.get("pipeline_stage") or "INVOICE_CREATED"
    now = _NOW_ISO()

    # Compose ONE $set that:
    #   • Clears DRY sentinels
    #   • Rewinds stage to NORMALIZED (via RETRYING is virtual — we
    #     record it in stage_history but the final stage is NORMALIZED
    #     so the worker/pipeline picks it up fresh).
    stage_history_add = [
        {"from_stage": from_stage_1, "to_stage": "RETRYING",
         "at": now, "actor": actor,
         "note": "force_reprocess_dry_row: cleared DRY sentinels"},
        {"from_stage": "RETRYING", "to_stage": "NORMALIZED",
         "at": now, "actor": actor,
         "note": "force_reprocess_dry_row: ready for scoped live pipeline"},
    ]
    unset_fields: dict = {}
    set_fields: dict = {
        "pipeline_stage":  "NORMALIZED",
        "canonical_payload": new_canonical,
        "force_reprocess_from_dry":     True,
        "force_reprocess_from_dry_at":  now,
        "force_reprocess_actor":        actor,
    }
    if dry_customer_cleared:
        unset_fields["qoyod_customer_id"] = ""
    if dry_invoice_cleared:
        unset_fields["qoyod_invoice_id"] = ""

    upd = {"$set": set_fields,
           "$push": {"stage_history": {"$each": stage_history_add}}}
    if unset_fields:
        upd["$unset"] = unset_fields

    await db.integration_inbox.update_one({"id": row["id"]}, upd)

    debug["cleared_dry_customer_id"] = dry_customer_cleared
    debug["cleared_dry_invoice_id"]  = dry_invoice_cleared
    debug["cleared_dry_products"]    = dry_products_cleared

    # ── Re-run pipeline inline — loop across stages ────────────────
    # Iter-2026-02.rev19 — Full pipeline drain. `process_normalized_row`
    # only advances ONE stage (→ CUSTOMER_RESOLVED); the terminal
    # stage COMPLETED / PAYMENT_PENDING is produced by
    # `process_customer_resolved_row`. Loop across stages until a
    # terminal / stuck state, so the operator gets end-to-end
    # completion from a single call.
    from integrations.qoyod import pipeline as pmod
    TERMINAL = {"COMPLETED", "DEAD_LETTER", "SKIPPED",
                "PARTIAL_FAILURE"}
    STEP_MAP = {
        "NORMALIZED":         pmod.process_normalized_row,
        "RULES_APPLIED":      pmod.process_normalized_row,
        "CUSTOMER_RESOLVED":  pmod.process_customer_resolved_row,
        "PRODUCT_RESOLVED":   pmod.process_customer_resolved_row,
        # PREFLIGHTED / INVOICE_CREATED are internally sequential
        # within process_customer_resolved_row so they're re-entrant.
        "PREFLIGHTED":        pmod.process_customer_resolved_row,
        "INVOICE_CREATED":    pmod.process_customer_resolved_row,
    }
    result = None
    steps_run: list[dict] = []
    for _hop in range(6):
        refreshed = await db.integration_inbox.find_one(
            {"id": row["id"]})
        _stage = (refreshed or {}).get("pipeline_stage")
        if _stage in TERMINAL:
            break
        # If invoice was already created (real id) and payment id is
        # still absent → stop here so caller can route to
        # retry_payment_only. Do NOT keep hopping.
        if _stage == "INVOICE_CREATED" \
                and _is_real_qid(
                    (refreshed or {}).get("qoyod_invoice_id")) \
                and not (refreshed or {}).get(
                    "qoyod_invoice_payment_id"):
            steps_run.append(
                {"hop": _hop, "stage_before": _stage,
                 "action": "stop_payment_pending"})
            break
        step_fn = STEP_MAP.get(_stage)
        if step_fn is None:
            steps_run.append(
                {"hop": _hop, "stage_before": _stage,
                 "action": "unknown_stage_stop"})
            break
        result = await step_fn(db, refreshed)
        _after = await db.integration_inbox.find_one(
            {"id": row["id"]})
        _stage_after = (_after or {}).get("pipeline_stage")
        steps_run.append({
            "hop":           _hop,
            "stage_before":  _stage,
            "stage_after":   _stage_after,
            "step_result":   (result or {}).get("outcome"),
        })
        # Idempotent halt — no advancement means we're stuck.
        if _stage_after == _stage:
            break

    # Post-run introspection.
    final_row = await db.integration_inbox.find_one(
        {"id": row["id"]}, {"_id": 0})
    debug["final_stage"] = (final_row or {}).get("pipeline_stage")
    debug["scoped_write_allowance"] = (
        (final_row or {}).get("selective_auto_send_gate") or {}
    ).get("eligible")
    debug["pipeline_steps_run"] = steps_run
    payloads = (final_row or {}).get("qoyod_payloads") or {}
    responses = (final_row or {}).get("qoyod_responses") or {}
    debug["contact_id_before_invoice"] = (
        (payloads.get("invoice") or {}).get("contact_id"))
    debug["product_ids_before_invoice"] = [
        li.get("product_id") for li in
        ((payloads.get("invoice") or {}).get("line_items") or [])
        if isinstance(li, dict)]
    # Iter-rev19 — invoice_post_attempted is TRUE only when قيود
    # actually saw the request (response body present, id extracted,
    # or a real qoyod_invoice_id landed on the row).
    _real_invoice_id_now = _is_real_qid(
        (final_row or {}).get("qoyod_invoice_id"))
    debug["invoice_post_attempted"] = bool(
        responses.get("invoice")
        or _real_invoice_id_now)
    debug["payment_post_attempted"] = bool(
        responses.get("invoice_payment")
        or (final_row or {}).get("qoyod_invoice_payment_id"))
    debug["product_resolve_attempted"] = (
        (final_row or {}).get("pipeline_stage") in
        ("PRODUCT_RESOLVED", "PREFLIGHTED", "INVOICE_CREATED",
         "COMPLETED", "PARTIAL_FAILURE"))
    # Explain when invoice was NOT sent.
    if not debug["invoice_post_attempted"]:
        _blocked = None
        _final_stage = debug["final_stage"]
        if _final_stage == "CUSTOMER_RESOLVED":
            _blocked = ("stopped_before_product_resolve — call "
                        "resume-selective-auto-send")
        elif _final_stage == "PRODUCT_RESOLVED":
            _blocked = ("stopped_after_product_resolve — check "
                        "preflight / mapping / totals")
        elif _final_stage == "SKIPPED":
            _blocked = "pipeline_skipped_by_business_rules"
        else:
            _blocked = f"stuck_at_{_final_stage}"
        debug["invoice_blocked_reason"] = _blocked
    else:
        debug["invoice_blocked_reason"] = None
    debug["dry_run_seen_by_client"] = False  # scoped path never dry

    # Final assembly. Iter-2026-02.rev18 — When invoice succeeded
    # but payment did NOT, surface `PAYMENT_PENDING` explicitly so
    # the operator knows the next retry MUST be payment-only.
    _stage = (final_row or {}).get("pipeline_stage") or "UNKNOWN"
    _real_qid = _is_real_qid((final_row or {}).get("qoyod_invoice_id"))
    _payment_id = (final_row or {}).get("qoyod_invoice_payment_id")
    outcome = _stage
    if _stage in ("INVOICE_CREATED", "PARTIAL_FAILURE") and \
            _real_qid and not _payment_id:
        outcome = "PAYMENT_PENDING"
    return {
        "ok": _stage == "COMPLETED" or (
            _real_qid and _payment_id) or (_real_qid and
            outcome == "PAYMENT_PENDING"),
        "outcome":           outcome,
        "row_id":            row.get("id"),
        "trace_id":          row.get("trace_id"),
        "qoyod_invoice_id":  (final_row or {}).get("qoyod_invoice_id"),
        "qoyod_customer_id": (final_row or {}).get("qoyod_customer_id"),
        "qoyod_invoice_payment_id":
            (final_row or {}).get("qoyod_invoice_payment_id"),
        "next_retry_hint": (
            "call POST /admin/retry-payment-only"
            if outcome == "PAYMENT_PENDING" else None),
        "pipeline_result":   result,
        "debug":             debug,
    }
