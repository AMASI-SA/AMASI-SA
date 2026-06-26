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

    # 3) Tax
    has_default_tax = bool(settings.get("default_tax_id"))
    items_have_tax  = all(it.get("tax_amount") is not None for it in items)
    if not has_default_tax and not items_have_tax:
        failures.append({"check": "tax",
                         "code": "missing_tax_configuration",
                         "message": "default_tax_id not set and items have no tax_amount"})

    # 4) Payment method mapping
    pm_native = dto_dict.get("payment_method") or dto_dict.get("payment_method_native")
    mapping = settings.get("payment_method_mapping") or []
    pm_mapped = None
    for m in mapping:
        if (m.get("salla_method") or "").lower() == (pm_native or "").lower():
            pm_mapped = m.get("qoyod_account_id")
            break
    if not pm_native:
        failures.append({"check": "payment_method",
                         "code": "missing_payment_method",
                         "message": "order has no payment_method on the DTO"})
    elif not pm_mapped:
        failures.append({"check": "payment_method",
                         "code": "payment_method_mapping_missing",
                         "message": f"no Qoyod account mapped for payment_method={pm_native!r}"})

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

    return PreflightResult(passed=not failures, failures=failures)
