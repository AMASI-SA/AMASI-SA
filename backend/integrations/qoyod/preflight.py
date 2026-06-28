"""Pre-flight Checklist — runs BEFORE any Qoyod POST.

Per user directive (Pre-Day 5): every invoice must pass six checks.
If ANY fails the row never reaches `INVOICE_CREATED`.

Checklist:
  1. Customer  — `qoyod_customer_id` populated (set by Day 4 / 4a).
  2. Products  — all line items have a `qoyod_product_id` (4b output).
  3. Tax       — `default_tax_id` configured on settings OR every line
                 item carries its own tax id (rare today).
  4. Payment   — Salla payment method present AND mapped in settings.
  5. Status    — order canonical status is in `invoice_trigger_statuses`.
  6. Idempotency — `trigger_once_only` honored.

Output: `PreflightResult(passed: bool, failures: list[dict])`.
Pure — no DB writes. The orchestrator records the result on the row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from integrations.qoyod.payment_methods import (
    resolve_payment_account, provider_family, is_pending_payment_status,
)


@dataclass
class PreflightResult:
    passed: bool
    failures: list[dict] = field(default_factory=list)

    def to_log_dict(self) -> dict:
        return {"passed": self.passed,
                "failures": self.failures,
                "checks_run": 6}


def run(
    *,
    dto_dict: dict,            # canonical SalesOrderDTO as dict
    settings: dict,
    qoyod_customer_id: Optional[str],
    product_resolutions: list[dict],  # 4b output [{sku, qoyod_product_id, ...}]
    existing_invoice_row: Optional[dict] = None,
) -> PreflightResult:
    failures: list[dict] = []

    # 1) Customer
    if not qoyod_customer_id:
        failures.append({"check": "customer",
                         "code": "missing_qoyod_customer_id",
                         "message": "Day-4 customer resolution did not produce an id"})

    # 2) Products
    items = dto_dict.get("items") or []
    unresolved = []
    res_by_sku = {r.get("sku"): r for r in (product_resolutions or [])}
    for it in items:
        sku = it.get("sku")
        r = res_by_sku.get(sku) or {}
        if not r.get("qoyod_product_id"):
            unresolved.append(sku or "<no-sku>")
    if unresolved:
        failures.append({"check": "products",
                         "code": "missing_product_mapping",
                         "message": f"{len(unresolved)} item(s) lack Qoyod product id",
                         "items": unresolved[:20]})

    # 3) Tax — Iter-285 honors `settings.tax_mode`.
    tax_mode = (settings.get("tax_mode") or "customer_first").strip()
    if tax_mode == "customer_first":
        # customer_first mode does NOT require default_tax_id; the
        # builder uses zero_tax_id (if configured) or omits tax_id.
        # No hard refusal for missing tax config in this mode.
        pass
    else:
        has_default_tax = bool(settings.get("default_tax_id"))
        items_have_tax  = all(it.get("tax_amount") is not None for it in items)
        if not has_default_tax and not items_have_tax:
            failures.append({"check": "tax",
                             "code": "missing_tax_configuration",
                             "message": "default_tax_id not set and items have no tax_amount"})

    # 4) Payment method mapping (alias-aware — Iter 2026-02-26)
    pm_native = dto_dict.get("payment_method") or dto_dict.get("payment_method_native")
    # Iter-292 — Pending/transitional statuses (waiting, awaiting_payment,
    # etc.) are NOT payment methods. We don't require a Qoyod account
    # mapping for them; the row is simply not eligible for receipt creation.
    pm_is_pending = is_pending_payment_status(pm_native)
    pm_mapped = None if pm_is_pending else resolve_payment_account(settings, pm_native)
    if pm_is_pending:
        # Soft skip — surface in checks_run but not as a failure.
        pass
    elif not pm_native:
        failures.append({"check": "payment_method",
                         "code": "missing_payment_method",
                         "message": "order has no payment_method on the DTO"})
    elif not pm_mapped:
        family = provider_family(pm_native)
        msg = (f"no Qoyod account mapped for payment_method={pm_native!r}")
        if family and family != (pm_native or "").lower():
            msg += (f" (try mapping its base provider '{family}' "
                    f"or this variant directly)")
        failures.append({"check": "payment_method",
                         "code": "payment_method_mapping_missing",
                         "message": msg,
                         "extra": {"payment_method": pm_native,
                                   "provider_family": family}})

    # 5) Status
    triggers = settings.get("invoice_trigger_statuses") or ["completed"]
    canonical = (dto_dict.get("order_status") or "").strip().lower()
    if canonical not in triggers:
        failures.append({"check": "order_status",
                         "code": "status_not_in_triggers",
                         "message": f"status={canonical!r} not in triggers={triggers}"})

    # 6) Idempotency
    if existing_invoice_row and existing_invoice_row.get("status") == "sent":
        if settings.get("trigger_once_only", True):
            failures.append({"check": "idempotency",
                             "code": "already_sent",
                             "message": "an invoice for this order is already 'sent'"})

    # 6.5) Iter-290 — Inventory id required on every invoice line.
    # Qoyod's /invoices validator rejects the entire payload with
    # "inventory id missing in a line item" if even one line is bare,
    # regardless of product.type=service or is_non_stock=true. The
    # operator must paste one default warehouse id into settings before
    # any invoice POST.
    if not (settings.get("default_inventory_id") or "").strip():
        failures.append({
            "check": "inventory_id",
            "code":  "missing_default_inventory_id",
            "message": "حقل default_inventory_id فارغ — قيود يطلب inventory_id "
                       "على كل سطر فاتورة. أنشئ مستودعاً افتراضياً في قيود "
                       "وانسخ id الخاص به إلى الإعدادات.",
        })

    # 6.6) Iter-290f — Shipping product id required when shipping > 0.
    # Qoyod /invoices requires product_id on every line. Salla emits
    # shipping at the order level; we add it as a line, but it needs
    # a real Qoyod product to bind to (or Qoyod rejects the line).
    try:
        _ship = float(dto_dict.get("shipping_amount") or 0.0)
    except (TypeError, ValueError):
        _ship = 0.0
    if _ship > 0:
        if not (str(settings.get("default_shipping_product_id") or "")).strip():
            failures.append({
                "check": "shipping_product_id",
                "code":  "missing_default_shipping_product_id",
                "message": (
                    "هذا الطلب يحتوي شحن (shipping_amount > 0) لكن إعداد "
                    "default_shipping_product_id فارغ. أنشئ منتجاً واحداً "
                    "في قيود اسمه 'شحن - ميزان' وانسخ id الخاص به للإعدادات."),
            })

    # 7) Iter-285 — Invoice ↔ Receipt reconciliation (customer_first mode).
    # Iter-290e — SKIP this check when `invoice_total_policy == match_salla_total`
    # is active: that policy has its own pre-POST math guard in pipeline.py
    # which is shipping-aware. The old customer_first estimator predates
    # shipping support and would false-positive on any order with shipping.
    policy = (settings.get("invoice_total_policy") or "match_salla_total").lower()
    if tax_mode == "customer_first" and policy != "match_salla_total":
        from integrations.qoyod.invoice_builder import (
            estimated_invoice_total,
        )
        try:
            est_total = estimated_invoice_total(dto_dict, settings)
        except Exception as exc:  # pragma: no cover — defensive
            est_total = None
            failures.append({
                "check":   "invoice_receipt_reconciliation",
                "code":    "estimation_failed",
                "message": f"{type(exc).__name__}: {exc}",
            })
        receipt_amount = float(dto_dict.get("total_amount") or 0.0)
        if est_total is not None:
            diff = round(est_total - receipt_amount, 2)
            tolerance = max(0.10, 0.005 * receipt_amount)  # 50 hallalat OR 0.5% — whichever wider
            if abs(diff) > tolerance:
                failures.append({
                    "check": "invoice_receipt_reconciliation",
                    "code":  "invoice_total_mismatch_with_receipt",
                    "message": (
                        f"estimated_invoice_total={est_total} would NOT "
                        f"match receipt_amount={receipt_amount} "
                        f"(diff={diff} SAR, tolerance={round(tolerance,2)})"),
                    "extra": {
                        "estimated_invoice_total": est_total,
                        "receipt_amount":          receipt_amount,
                        "diff":                    diff,
                        "tolerance":               round(tolerance, 2),
                        "tax_mode":                tax_mode,
                    },
                })

    return PreflightResult(passed=not failures, failures=failures)
