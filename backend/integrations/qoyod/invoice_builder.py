"""Invoice + Receipt builders, payload snapshots, and DryRun client.

Day 5 building blocks:
    • `build_invoice_payload`   — canonical SalesOrderDTO + resolved
                                  customer/products + settings → Qoyod
                                  POST /invoices body.
    • `build_receipt_payload`   — invoice id + payment info → Qoyod
                                  POST /receipts body.
    • `DryRunQoyodClient`       — drop-in replacement for QoyodAPIClient.
                                  Records every "POST" it WOULD make
                                  and returns deterministic fake ids,
                                  but never makes an HTTP call.

All builders are pure functions. The orchestrator stores the result
under `row.qoyod_payloads.invoice` / `.receipt` before any POST, so
even live-mode runs keep an auditable trail.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from integrations.qoyod.payment_methods import resolve_payment_account


# ─────────────────────────────────────────────────────────────────────
# Pure payload builders
# ─────────────────────────────────────────────────────────────────────
def _resolve_payment_account(settings: dict, payment_method: Optional[str]) -> Optional[str]:
    # Delegates to the alias-aware resolver so variants like
    # `tamara_installment` fall back to the `tamara` mapping
    # automatically (Iter 2026-02-26).
    return resolve_payment_account(settings, payment_method)


def build_invoice_payload(
    *, dto_dict: dict, qoyod_customer_id: str,
    product_resolutions: list[dict],
    invoice_date,        # datetime from rules decision
    settings: dict,
) -> dict:
    """Build the Qoyod `POST /invoices` body.

    Tax handling
    ────────────
    `default_tax_id` MUST be a Qoyod Tax ID (e.g. `"1"`) — NOT a tax
    rate. Qoyod resolves the rate from the tax record server-side. If
    no `default_tax_id` is set in settings, the line-item `tax_id` is
    omitted; the operator must ensure each item carries its own tax.

    Branch handling
    ───────────────
    `branch_id` is OPTIONAL. Some Qoyod accounts are single-branch
    and reject explicit branch_id values. When not configured we
    OMIT the field entirely so Qoyod falls back to the default branch.
    """
    res_by_sku = {r["sku"]: r["qoyod_product_id"]
                  for r in product_resolutions if r.get("qoyod_product_id")}
    default_tax_id = (settings.get("default_tax_id") or "").strip() or None
    lines = []
    for it in dto_dict.get("items", []):
        pid = res_by_sku.get(it.get("sku"))
        line: dict = {
            "product_id":  pid,
            "description": it.get("name"),
            "quantity":    it.get("quantity"),
            "unit_price":  it.get("unit_price"),
            "discount":    0,
        }
        if default_tax_id:
            line["tax_id"] = default_tax_id
        lines.append(line)

    invoice: dict = {
        "contact_id":     qoyod_customer_id,
        "issue_date":     invoice_date.date().isoformat() if invoice_date else None,
        "due_date":       invoice_date.date().isoformat() if invoice_date else None,
        "reference":      dto_dict.get("order_number") or dto_dict.get("order_id"),
        "currency_code":  dto_dict.get("currency") or "SAR",
        "line_items":     lines,
        "notes":          f"Mezan · Salla order {dto_dict.get('order_id')}",
        # Provenance — operator can find the source order from Qoyod.
        "external_reference": dto_dict.get("order_id"),
    }
    # Only include branch_id when the operator has configured one.
    branch_id = (settings.get("default_branch_id") or "").strip()
    if branch_id:
        invoice["branch_id"] = branch_id

    return {"invoice": invoice}


def build_receipt_payload(
    *, qoyod_invoice_id: str, dto_dict: dict,
    invoice_date, settings: dict,
) -> dict:
    """Build the Qoyod `POST /receipts` body."""
    pm_native = dto_dict.get("payment_method") or dto_dict.get("payment_method_native")
    account_id = _resolve_payment_account(settings, pm_native)
    return {
        "receipt": {
            "invoice_id":   qoyod_invoice_id,
            "date":         invoice_date.date().isoformat() if invoice_date else None,
            "amount":       dto_dict.get("total_amount"),
            "currency":     dto_dict.get("currency") or "SAR",
            "account_id":   account_id,
            "payment_method": pm_native,
            "notes":        f"Mezan · Salla order {dto_dict.get('order_id')}",
            "external_reference": dto_dict.get("order_id"),
        }
    }


# ─────────────────────────────────────────────────────────────────────
# DryRunQoyodClient — interface-compatible with QoyodAPIClient
# ─────────────────────────────────────────────────────────────────────
class DryRunQoyodClient:
    """Mocks the surface of `QoyodAPIClient` without making HTTP calls.

    Returns deterministic ids of the form `DRY:<entity>:<sha8>` so the
    pipeline can still build downstream payloads (invoice → receipt).

    Records every call in `self.calls` for audit / test introspection.
    """
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def _fake(self, kind: str, payload: dict) -> str:
        # Stable hash of the payload so the same input → same fake id.
        h = hashlib.sha1(repr(sorted(payload.items())).encode("utf-8")).hexdigest()[:8]
        return f"DRY:{kind}:{h}"

    async def create_contact(self, payload, *, idem):
        # Accept both "customer" (preferred legacy.qoyod.com) and
        # "contact" (older alias) so existing tests keep passing.
        body = payload.get("customer") or payload.get("contact") or payload
        cid = self._fake("contact", body)
        self.calls.append({"endpoint": "POST /customers", "idem": idem,
                           "payload": payload, "returned_id": cid})
        return {"customer": {"id": cid}}

    async def create_product(self, payload, *, idem):
        pid = self._fake("product", payload.get("product") or payload)
        self.calls.append({"endpoint": "POST /products", "idem": idem,
                           "payload": payload, "returned_id": pid})
        return {"product": {"id": pid}}

    async def find_product_by_sku(self, sku):
        """Dry-run: no Qoyod state exists, so the trust gate sees no
        legacy products and always proceeds to create. Recorded for
        audit so tests can assert the gate WAS consulted."""
        self.calls.append({"endpoint": "GET /products?q[sku_eq]",
                           "idem": None, "payload": {"sku": sku},
                           "returned_id": None})
        return None

    async def create_invoice(self, payload, *, idem):
        iid = self._fake("invoice", payload.get("invoice") or payload)
        self.calls.append({"endpoint": "POST /invoices", "idem": idem,
                           "payload": payload, "returned_id": iid})
        return {"invoice": {"id": iid, "number": f"INV-{iid[-6:]}"}}

    async def create_receipt(self, payload, *, idem):
        rid = self._fake("receipt", payload.get("receipt") or payload)
        self.calls.append({"endpoint": "POST /receipts", "idem": idem,
                           "payload": payload, "returned_id": rid})
        return {"receipt": {"id": rid}}


def is_dry_run_mode(settings: dict) -> bool:
    return bool(settings.get("dry_run_mode", False))
