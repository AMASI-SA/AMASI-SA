"""Preview Reprocess — re-run the whole pipeline IN MEMORY without
touching Qoyod (api.qoyod.com).

User scenario (2026-02-27, Iter-281)
─────────────────────────────────────
Operator wants to debug a specific order (e.g. `268632361`) that
DEAD_LETTERed in production. The existing `one_shot_reprocess` refuses
when `dry_run_mode` is ON because it targets the REAL Qoyod tenant.
The operator does NOT want to flip Dry Run off yet.

This module is the safe sibling: it loads the row's stored
`raw_payload`, re-runs the full chain (adapter → normalizer →
business rules → product/customer resolver preview → invoice/receipt
builders → preflight) and returns ALL the structured diagnostics
WITHOUT ANY network call to Qoyod.

Strict invariants
─────────────────
1. NEVER calls `api.qoyod.com`. The function literally has no api_client.
2. NEVER mutates the inbox row (read-only).
3. Reports `would_send_to_qoyod = True/False` for each step.
4. If the row already has a real (non-DRY) Qoyod invoice, surfaces
   `invoice_already_created` with the existing qoyod_invoice_id so
   the operator never accidentally double-bills.
5. Returns a uniform dict — never raises (every failure path is
   captured in the response).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from integrations.qoyod.legacy_adapter import adapt as adapt_legacy
from integrations.qoyod.normalizer import (
    normalize, validate as normalizer_validate, NormalizationError,
)
from integrations.qoyod.business_rules import evaluate as evaluate_rules
from integrations.qoyod.invoice_builder import (
    build_invoice_payload, build_receipt_payload,
)
from integrations.qoyod.customer_resolver import _build_contact_payload
from integrations.qoyod.product_resolver import _build_product_payload
from integrations.qoyod.preflight import run as preflight_run
from integrations.qoyod.totals_guard import (
    validate_totals as totals_guard_check,
)
from integrations.qoyod.payment_methods import resolve_payment_account

from datetime import datetime


logger = logging.getLogger(__name__)


async def preview_reprocess_one_order(
    db, *, user_id: str,
    order_number: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> dict:
    """Re-run a single inbox row through the pipeline AS A SIMULATION.

    No Qoyod side-effects. No DB writes. Returns a structured
    diagnostic so the UI can show every step.

    Either `order_number` or `trace_id` must be supplied; `trace_id`
    takes precedence when both are given (avoids ambiguity when an
    order has multiple inbox rows).
    """
    out: dict[str, Any] = {
        "ok": True,
        "mode": "preview",
        "qoyod_request_sent": False,
        "would_send_to_qoyod": {
            "customer": False, "products": False,
            "invoice":  False, "receipt":  False,
        },
        "created_ids": {
            "customer_id": None,
            "product_ids": [],
            "invoice_id":  None,
            "receipt_id":  None,
        },
        "row": None,
        "stages": {},
        "errors": [],
        "idempotency": None,
    }

    # ── 1) Locate the inbox row ─────────────────────────────────────
    if not (order_number or trace_id):
        return _err(out, "missing_lookup",
                    "must supply order_number or trace_id",
                    stage="lookup")
    q: dict = {"user_id": user_id}
    if trace_id:
        q["trace_id"] = trace_id
    else:
        on = str(order_number)
        cands: list[Any] = [on]
        try:
            cands.append(int(on))
        except (TypeError, ValueError):
            pass
        q["$or"] = []
        for v in cands:
            q["$or"].extend([
                {"salla_order_number": v},
                {"salla_order_id":     v},
                {"canonical_payload.order_number": v},
                {"canonical_payload.order_id":     v},
            ])
    rows = await db.integration_inbox.find(q).to_list(length=20)
    if not rows:
        return _err(out, "row_not_found",
                    f"no inbox row matches order_number={order_number} "
                    f"trace_id={trace_id}",
                    stage="lookup")
    if len(rows) > 1 and not trace_id:
        return _err(out, "multiple_matches_pick_one",
                    f"order_number={order_number} matches {len(rows)} "
                    f"rows; supply trace_id to disambiguate",
                    stage="lookup",
                    candidates=[{
                        "trace_id":       r.get("trace_id"),
                        "received_at":    r.get("received_at"),
                        "pipeline_stage": r.get("pipeline_stage"),
                    } for r in rows[:10]])
    row = rows[0]
    out["row"] = {
        "trace_id":       row.get("trace_id"),
        "order_number":   row.get("salla_order_number"),
        "received_at":    row.get("received_at"),
        "pipeline_stage": row.get("pipeline_stage"),
        "dry_run":        bool(row.get("dry_run")),
    }

    # ── 2) Idempotency check — never silently double-bill ───────────
    existing_invoice = await db.qoyod_invoices.find_one(
        {"user_id": user_id,
         "salla_order_id": row.get("salla_order_id")
                          or str(row.get("salla_order_number") or "")},
        {"_id": 0, "status": 1, "qoyod_invoice_id": 1,
         "qoyod_invoice_number": 1, "dry_run": 1},
    )
    if existing_invoice:
        qid = existing_invoice.get("qoyod_invoice_id") or ""
        is_real = qid and not str(qid).startswith("DRY:")
        if is_real and existing_invoice.get("status") in (
                "sent", "invoice_sent_receipt_failed", "completed"):
            out["idempotency"] = {
                "blocked": True,
                "code":    "invoice_already_created",
                "message": (
                    "فاتورة قيود حقيقية موجودة سابقاً لهذا الطلب. لن يتم "
                    "بناء payload فاتورة جديد لحماية الدفاتر من التكرار."),
                "existing_qoyod_invoice_id":     existing_invoice.get("qoyod_invoice_id"),
                "existing_qoyod_invoice_number": existing_invoice.get("qoyod_invoice_number"),
                "existing_status":               existing_invoice.get("status"),
            }
        else:
            out["idempotency"] = {
                "blocked": False,
                "existing_qoyod_invoice_id":     qid or None,
                "existing_status":               existing_invoice.get("status"),
                "dry_run_existing":              bool(existing_invoice.get("dry_run")),
            }
    else:
        out["idempotency"] = {"blocked": False, "existing_qoyod_invoice_id": None}

    # ── 3) Adapter ──────────────────────────────────────────────────
    raw = row.get("raw_payload") or {}
    try:
        adapted, adapter_meta = adapt_legacy(raw)
    except Exception as exc:    # pragma: no cover — defensive
        return _err(out, "adapter_exception",
                    f"{type(exc).__name__}: {exc}",
                    stage="adapter")
    out["stages"]["adapter"] = {
        "ok": True,
        "adapter_applied": adapter_meta.get("adapter_applied"),
        "items_source":    adapter_meta.get("items_source"),
        "legacy_status_slug": adapter_meta.get("legacy_status_slug"),
        "adapted_payload_preview": _shallow_preview(adapted),
    }

    # ── 4) Validate + Normalize ─────────────────────────────────────
    valid, val_err = normalizer_validate(adapted)
    if not valid:
        return _err(out, "validation_failed",
                    (val_err or {}).get("message")
                    or "payload failed structural validation",
                    stage="validate",
                    extra={"validate_error": val_err})
    try:
        received_at_raw = row.get("received_at")
        if isinstance(received_at_raw, str):
            try:
                received_at_dt = datetime.fromisoformat(
                    received_at_raw.replace("Z", "+00:00"))
            except Exception:
                received_at_dt = None
        elif isinstance(received_at_raw, datetime):
            received_at_dt = received_at_raw
        else:
            received_at_dt = None
        dto = normalize(adapted, received_at=received_at_dt)
    except NormalizationError as exc:
        return _err(out, exc.code, exc.message,
                    stage="normalize",
                    extra={"normalizer_error": exc.to_log_dict()})
    except Exception as exc:    # pragma: no cover — defensive
        return _err(out, "normalize_exception",
                    f"{type(exc).__name__}: {exc}",
                    stage="normalize")
    canonical = dto.model_dump(mode="json")
    out["stages"]["normalize"] = {
        "ok": True,
        "canonical_preview": {
            "order_id":       canonical.get("order_id"),
            "order_number":   canonical.get("order_number"),
            "order_status":   canonical.get("order_status"),
            "order_status_native": canonical.get("order_status_native"),
            "currency":       canonical.get("currency"),
            "total_amount":   canonical.get("total_amount"),
            "subtotal":       canonical.get("subtotal"),
            "tax_amount":     canonical.get("tax_amount"),
            "shipping_amount": canonical.get("shipping_amount"),
            "discount_amount": canonical.get("discount_amount"),
            "items_count":    len(canonical.get("items") or []),
        },
        "items": [
            {"sku": it.get("sku"), "name": it.get("name"),
             "quantity": it.get("quantity"),
             "unit_price": it.get("unit_price"),
             "tax_amount": it.get("tax_amount"),
             "discount_amount": it.get("discount_amount"),
             "total": it.get("total")}
            for it in (canonical.get("items") or [])
        ],
        "live_vs_stored_drift": _drift(
            stored=row.get("canonical_payload") or {},
            live=canonical,
        ),
    }

    # ── 5) Totals guard ─────────────────────────────────────────────
    try:
        tg = totals_guard_check(canonical)
    except Exception as exc:    # pragma: no cover
        out["stages"]["totals_guard"] = {
            "ok": False,
            "exception": f"{type(exc).__name__}: {exc}",
        }
    else:
        out["stages"]["totals_guard"] = {
            "ok":          tg.ok,
            "code":        tg.code,
            "message":     tg.message,
            "details":     tg.details,
        }
        # Hoist mezan_vat_diagnostics to a top-level slot for easy UI
        # consumption (Iter-282).
        mvd = (tg.details or {}).get("mezan_vat_diagnostics")
        if mvd:
            out["mezan_vat"] = mvd

    # ── 6) Settings ─────────────────────────────────────────────────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}

    # ── 7) Business rules decision ──────────────────────────────────
    rules = None
    try:
        rules = evaluate_rules(
            dto, settings,
            existing_invoice_row=existing_invoice)
        rules_dict = rules.to_log_dict()
    except Exception as exc:   # pragma: no cover
        rules_dict = {"exception": f"{type(exc).__name__}: {exc}"}
    out["stages"]["business_rules"] = rules_dict

    # ── 8) Customer payload preview ─────────────────────────────────
    try:
        customer_payload = _build_contact_payload(dto.customer)
    except Exception as exc:   # pragma: no cover
        return _err(out, "customer_builder_exception",
                    f"{type(exc).__name__}: {exc}",
                    stage="build_customer_payload")
    out["stages"]["customer_preview"] = {
        "ok": True,
        "request_body": customer_payload,
        "endpoint":     "POST /customers",
        "would_send_to_qoyod": False,
    }
    out["would_send_to_qoyod"]["customer"] = False

    # ── 9) Product payload previews (per SKU) ───────────────────────
    product_previews = []
    for it in (canonical.get("items") or []):
        try:
            p = _build_product_payload(it, settings)
        except Exception as exc:   # pragma: no cover
            p = {"_exception": f"{type(exc).__name__}: {exc}"}
        product_previews.append({
            "sku":           it.get("sku"),
            "name":          it.get("name"),
            "request_body": p,
            "endpoint":     "POST /products",
        })
    out["stages"]["products_preview"] = {
        "ok": True,
        "items": product_previews,
        "would_send_to_qoyod": False,
    }
    out["would_send_to_qoyod"]["products"] = False

    # ── 10) Invoice payload preview ─────────────────────────────────
    # Use synthetic ids so the operator can see what shape we'd send.
    fake_customer_id = "PREVIEW:customer:<pending>"
    fake_product_res = [{"sku": it.get("sku"),
                         "qoyod_product_id": f"PREVIEW:product:{it.get('sku')}"}
                        for it in (canonical.get("items") or [])]
    try:
        invoice_payload = build_invoice_payload(
            dto_dict=canonical,
            qoyod_customer_id=fake_customer_id,
            product_resolutions=fake_product_res,
            invoice_date=(getattr(rules, "invoice_date", None) if rules else None)
                         or dto.completed_at
                         or dto.paid_at
                         or dto.order_date,
            settings=settings,
        )
    except Exception as exc:   # pragma: no cover
        return _err(out, "invoice_builder_exception",
                    f"{type(exc).__name__}: {exc}",
                    stage="build_invoice_payload")
    out["stages"]["invoice_preview"] = {
        "ok": True,
        "request_body": invoice_payload,
        "endpoint":     "POST /invoices",
        "would_send_to_qoyod": False,
    }
    out["would_send_to_qoyod"]["invoice"] = False

    # ── 11) Receipt payload preview ─────────────────────────────────
    fake_invoice_id = "PREVIEW:invoice:<pending>"
    try:
        receipt_payload = build_receipt_payload(
            qoyod_invoice_id=fake_invoice_id,
            dto_dict=canonical,
            invoice_date=(getattr(rules, "invoice_date", None) if rules else None)
                         or dto.completed_at
                         or dto.paid_at
                         or dto.order_date,
            settings=settings,
        )
    except Exception as exc:   # pragma: no cover
        return _err(out, "receipt_builder_exception",
                    f"{type(exc).__name__}: {exc}",
                    stage="build_receipt_payload")
    payment_account = resolve_payment_account(
        settings, canonical.get("payment_method")
                 or canonical.get("payment_method_native"))
    out["stages"]["receipt_preview"] = {
        "ok": True,
        "request_body": receipt_payload,
        "endpoint":     "POST /receipts",
        "would_send_to_qoyod": False,
        "resolved_account_id": payment_account,
    }
    out["would_send_to_qoyod"]["receipt"] = False

    # ── 11b) Iter-285 — Invoice/Receipt reconciliation summary ─────
    # Surfaces the tax-mode contract: estimated invoice total Qoyod
    # WILL compute vs the receipt amount we WILL post. UI uses these
    # to render the "Customer-First" / "Mezan 15%" badges and the
    # green/red reconciled state.
    from integrations.qoyod.invoice_builder import (
        estimated_invoice_total, _get_tax_mode,
    )
    try:
        est_invoice_total = estimated_invoice_total(canonical, settings)
    except Exception as exc:   # pragma: no cover
        est_invoice_total = None
    receipt_amount = canonical.get("total_amount") or 0.0
    tax_mode = _get_tax_mode(settings)
    mvd = (totals_guard_check(canonical).details
           if False else (out.get("mezan_vat") or {}))
    diff = (round(est_invoice_total - float(receipt_amount), 2)
            if est_invoice_total is not None else None)
    tolerance = max(0.10, 0.005 * float(receipt_amount or 0))
    out["tax_mode"] = tax_mode
    out["reconciliation"] = {
        "tax_mode":                  tax_mode,
        "salla_declared_total":      round(float(receipt_amount), 2),
        "mezan_expected_total":      (mvd or {}).get("mezan_expected_total"),
        "tax_difference":            (mvd or {}).get("tax_difference"),
        "estimated_invoice_total":   est_invoice_total,
        "receipt_amount":            round(float(receipt_amount), 2),
        "tolerance":                 round(tolerance, 2),
        "invoice_receipt_reconciled": (
            est_invoice_total is not None
            and abs(diff) <= tolerance),
        "diff":                      diff,
    }

    # ── 12) Preflight summary ───────────────────────────────────────
    try:
        pf = preflight_run(
            dto_dict=canonical, settings=settings,
            qoyod_customer_id=fake_customer_id,
            product_resolutions=fake_product_res,
            existing_invoice_row=existing_invoice,
        )
        out["stages"]["preflight"] = {
            "ok":       pf.passed,
            "failures": pf.failures,
        }
    except Exception as exc:   # pragma: no cover
        out["stages"]["preflight"] = {
            "ok": False,
            "exception": f"{type(exc).__name__}: {exc}",
        }
    return out


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _err(out: dict, code: str, message: str, *,
         stage: str, extra: Optional[dict] = None,
         candidates: Optional[list] = None) -> dict:
    out["ok"] = False
    out["error_code"] = code
    out["message"] = message
    out["failed_at_stage"] = stage
    out["errors"].append({"stage": stage, "code": code, "message": message})
    if extra:
        out.setdefault("extra", {}).update(extra)
    if candidates:
        out["candidates"] = candidates
    return out


def _shallow_preview(payload: Any, max_items: int = 5) -> dict:
    """Return a compact preview of the adapted payload (top-level
    keys + first N items) to keep the response small."""
    if not isinstance(payload, dict):
        return {"_type": type(payload).__name__}
    data = payload.get("data") if isinstance(payload.get("data"), dict) \
           else payload
    items = (data.get("items") or [])[:max_items]
    return {
        "event":         payload.get("event"),
        "order_id":      data.get("id") or data.get("reference_id"),
        "currency":      data.get("currency"),
        "status_node":   data.get("status"),
        "amounts":       data.get("amounts"),
        "items_preview": items,
        "items_count":   len((data.get("items") or [])),
    }


def _drift(stored: dict, live: dict) -> dict:
    """Compare a small set of key fields between stored vs freshly
    computed canonical. Surfaces drift for the UI."""
    stored_items = (stored.get("items") or [None])
    live_items = (live.get("items") or [None])
    s_first = stored_items[0] if stored_items else None
    l_first = live_items[0] if live_items else None
    fields = ("unit_price", "tax_amount", "discount_amount", "total",
              "quantity")
    item_drift = {}
    if isinstance(s_first, dict) and isinstance(l_first, dict):
        for f in fields:
            if s_first.get(f) != l_first.get(f):
                item_drift[f] = {
                    "stored": s_first.get(f),
                    "live":   l_first.get(f),
                }
    top_drift = {}
    for f in ("subtotal", "tax_amount", "shipping_amount",
              "discount_amount", "total_amount", "order_status"):
        if stored.get(f) != live.get(f):
            top_drift[f] = {"stored": stored.get(f), "live": live.get(f)}
    return {
        "any_drift": bool(item_drift or top_drift),
        "first_item_drift": item_drift,
        "top_level_drift":  top_drift,
    }
