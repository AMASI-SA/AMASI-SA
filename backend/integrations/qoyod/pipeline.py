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
from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.dto import SalesOrderDTO
from integrations.qoyod.state_machine import transition
from integrations.qoyod.totals_guard import (
    validate_totals, TotalsGuardResult,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
            "dry_run_mode":             False,
        }
    if not doc.get("invoice_trigger_statuses"):
        legacy = doc.get("invoice_trigger_status")
        doc["invoice_trigger_statuses"] = [legacy] if legacy else ["completed"]
    if "trigger_once_only" not in doc:
        doc["trigger_once_only"] = True
    if "dry_run_mode" not in doc:
        doc["dry_run_mode"] = False
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
        {"_id": 0, "status": 1},
    )
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
        api_client, _is_dry = await _get_api_client(db, user_id, settings)
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
async def _get_api_client(db, user_id: str, settings: dict):
    """Return a Qoyod client (real or DryRun) based on settings."""
    if is_dry_run_mode(settings):
        return DryRunQoyodClient(), True
    key = await get_api_key(db, user_id)
    if not key:
        return None, False
    return QoyodAPIClient(key), False


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

    # Resolve client (real or dry-run).
    client_provided = api_client is not None
    is_dry = is_dry_run_mode(settings)
    if not client_provided:
        api_client, is_dry = await _get_api_client(db, user_id, settings)
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
                "reason":  "invoice_total_mismatch_before_post",
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
        try:
            inv_resp = await api_client.create_invoice(invoice_payload,
                                                       idem=invoice_idem)
            inv_resp_raw = inv_resp
            # Extract id/number — tolerant to a few shapes.
            if isinstance(inv_resp, dict):
                inv = inv_resp.get("invoice") if isinstance(inv_resp.get("invoice"), dict) else inv_resp
                qoyod_invoice_id = str(inv.get("id")) if inv.get("id") is not None else None
                qoyod_invoice_number = inv.get("number") or inv.get("reference")
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
    if not (settings.get("auto_receipt", True)
            and (settings.get("capabilities") or {}).get("create_receipts", True)):
        # Invoice-payment step disabled by capability (e.g. tenant
        # using Qoyod's auto-payment plugin externally). Stop at
        # INVOICE_CREATED as success.
        return {"row_id": row["id"], "outcome": "INVOICE_CREATED",
                "reason": "invoice_payment_disabled_by_capability",
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
    if payment_payload["invoice_payment"].get("account") is None:
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
