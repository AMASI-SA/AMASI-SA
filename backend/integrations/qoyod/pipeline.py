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

    # ── Totals Guard (Iter-273) ────────────────────────────────────
    # Runs BEFORE any Qoyod-bound side-effect. If Make.com / Salla
    # silently dropped line items (so `items_sum != subtotal`) or the
    # header math diverges, refuse the row outright. NO auto-retry:
    # the fix lives upstream, so retrying without a Make.com change
    # would just fail again. Operator-facing audit + DEAD_LETTER.
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

    # Snapshot BEFORE attempting POST.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"qoyod_payloads.invoice": invoice_payload,
                  "qoyod_payloads.invoice_snapshot_at": _now(),
                  "preflight": pf.to_log_dict()}},
    )

    qoyod_invoice_id = None
    qoyod_invoice_number = None
    invoice_idem = f"mzn-{trace_id}-invoice"
    inv_resp_raw: Any = None
    inv_started_ms = int(_now().timestamp() * 1000)
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

    # ── 4d RECEIPT ──────────────────────────────────────────────────
    if not (settings.get("auto_receipt", True)
            and (settings.get("capabilities") or {}).get("create_receipts", True)):
        # Receipt disabled by capability — stop at INVOICE_CREATED as success.
        return {"row_id": row["id"], "outcome": "INVOICE_CREATED",
                "reason": "receipt_disabled_by_capability",
                "dry_run": is_dry,
                "qoyod_invoice_id": qoyod_invoice_id}

    receipt_payload = build_receipt_payload(
        qoyod_invoice_id=qoyod_invoice_id,
        dto_dict=canonical, invoice_date=inv_date, settings=settings)
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"qoyod_payloads.receipt": receipt_payload,
                  "qoyod_payloads.receipt_snapshot_at": _now()}},
    )
    receipt_idem = f"mzn-{trace_id}-receipt"
    qoyod_receipt_id = None
    rcpt_resp_raw: Any = None
    rcpt_started_ms = int(_now().timestamp() * 1000)
    try:
        rcpt_resp = await api_client.create_receipt(receipt_payload,
                                                    idem=receipt_idem)
        rcpt_resp_raw = rcpt_resp
        if isinstance(rcpt_resp, dict):
            r = rcpt_resp.get("receipt") if isinstance(rcpt_resp.get("receipt"), dict) else rcpt_resp
            qoyod_receipt_id = str(r.get("id")) if r.get("id") is not None else None
    except QoyodAPIError as exc:
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {
                "qoyod_responses.receipt.error":      exc.to_log_dict(),
                "qoyod_responses.receipt.received_at": _now(),
                "qoyod_responses.receipt.duration_ms":
                    int(_now().timestamp() * 1000) - rcpt_started_ms,
            }})
        # Partial failure! Invoice exists, receipt does not.
        p = transition(from_stage="INVOICE_CREATED",
                       to_stage="FAILED_RECEIPT", actor="worker",
                       error=exc.to_log_dict())
        p.setdefault("$set", {})["pipeline_error"] = exc.to_log_dict()
        await _apply(db, row["id"], p)
        p2 = transition(from_stage="FAILED_RECEIPT",
                        to_stage="PARTIAL_FAILURE", actor="worker",
                        note="invoice already in Qoyod · manual receipt review",
                        existing_started_at=started_at)
        await _apply(db, row["id"], p2)
        await db.qoyod_invoices.update_one(
            {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
            {"$set": {"status": "invoice_sent_receipt_failed",
                      "pipeline_stage": "PARTIAL_FAILURE",
                      "last_error": exc.to_log_dict(),
                      "updated_at": _now()}})
        return {"row_id": row["id"], "outcome": "PARTIAL_FAILURE",
                "reason": "FAILED_RECEIPT",
                "qoyod_invoice_id": qoyod_invoice_id}

    p = transition(from_stage="INVOICE_CREATED",
                   to_stage="RECEIPT_CREATED", actor="worker",
                   note=("DRY-RUN: receipt payload built, no POST"
                         if is_dry else "receipt created in Qoyod"))
    p.setdefault("$set", {})["qoyod_receipt_id"] = qoyod_receipt_id
    await _apply(db, row["id"], p)
    # Persist raw receipt response (success path) — First-Sync-Monitor.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {
            "qoyod_responses.receipt.body":        rcpt_resp_raw,
            "qoyod_responses.receipt.received_at": _now(),
            "qoyod_responses.receipt.duration_ms":
                int(_now().timestamp() * 1000) - rcpt_started_ms,
            "qoyod_responses.receipt.qoyod_id":    qoyod_receipt_id,
        }})

    p = transition(from_stage="RECEIPT_CREATED", to_stage="COMPLETED",
                   actor="worker",
                   note=("DRY-RUN COMPLETED — no Qoyod POSTs were made"
                         if is_dry else "invoice + receipt pushed to Qoyod"),
                   existing_started_at=started_at)
    await _apply(db, row["id"], p)
    await db.qoyod_invoices.update_one(
        {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
        {"$set": {"qoyod_receipt_id": qoyod_receipt_id,
                  "pipeline_stage":  "COMPLETED",
                  "status":          ("sent" if not is_dry else "pending"),
                  "sent_at":         _now() if not is_dry else None,
                  "updated_at":      _now()}})

    return {"row_id": row["id"], "outcome": "COMPLETED",
            "dry_run": is_dry,
            "qoyod_invoice_id": qoyod_invoice_id,
            "qoyod_receipt_id": qoyod_receipt_id}


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
