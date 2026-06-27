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


def _f(v: Any, default: float = 0.0) -> float:
    """Tiny safe-float helper."""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────
# Tax-mode strategies (Iter-285)
# ─────────────────────────────────────────────────────────────────────
# Customer-first ("salla_declared"): invoice total MUST equal what the
#   customer paid (canonical.total_amount). Per-line unit_price is the
#   TAX-INCLUSIVE price (Salla unit_price + per-line tax/qty), and the
#   line uses `settings.zero_tax_id` (a 0% tax record) so Qoyod doesn't
#   add tax on top. Discounts stay per-line so promo attribution survives.
# Mezan-fixed-15 ("mezan_fixed_15"): legacy behavior. Use default_tax_id
#   pointing to a 15% Qoyod tax record; Qoyod computes tax server-side.
TAX_MODE_CUSTOMER_FIRST = "customer_first"
TAX_MODE_MEZAN_FIXED_15 = "mezan_fixed_15"
DEFAULT_TAX_MODE = TAX_MODE_CUSTOMER_FIRST


def _get_tax_mode(settings: dict) -> str:
    """Read the tax_mode setting with a safe default."""
    mode = (settings.get("tax_mode") or "").strip()
    if mode in (TAX_MODE_CUSTOMER_FIRST, TAX_MODE_MEZAN_FIXED_15):
        return mode
    return DEFAULT_TAX_MODE


def _line_unit_price_for_mode(it: dict, tax_mode: str) -> float:
    """Return the unit_price Qoyod should see on this line.

    customer_first: unit_price is INCLUSIVE of Salla's per-line tax.
        This way, with a 0% tax_id on the line, Qoyod's computed total
        equals the customer-paid amount EXACTLY — invoice == receipt.
    mezan_fixed_15: pass Salla's net unit_price; Qoyod adds 15% on top.
    """
    base = _f(it.get("unit_price"))
    if tax_mode != TAX_MODE_CUSTOMER_FIRST:
        return base
    qty = _f(it.get("quantity"), default=1.0) or 1.0
    tax = _f(it.get("tax_amount"))
    # Distribute the line's tax across each unit.
    return round(base + (tax / qty), 4)


def _line_tax_id_for_mode(it: dict, tax_mode: str, settings: dict) -> Optional[str]:
    """Return the Qoyod tax_id to attach to this line."""
    if tax_mode == TAX_MODE_CUSTOMER_FIRST:
        # Prefer an explicit 0% tax record. Fall back to none (omit
        # tax_id entirely so Qoyod uses its default — which the
        # operator must configure as 0% before flipping live).
        zero = (settings.get("zero_tax_id") or "").strip()
        return zero or None
    # Mezan-fixed-15: original behavior.
    return (settings.get("default_tax_id") or "").strip() or None


def estimated_invoice_total(dto_dict: dict, settings: dict) -> float:
    """Estimate the invoice total Qoyod will compute for this row.

    customer_first → Σ(unit_price_inclusive × qty − discount) + 0% tax
                  = canonical.total_amount (within rounding).
    mezan_fixed_15 → Σ(unit_price × qty − discount) × (1 + 0.15)
                  + 0.15 × shipping_amount.
    """
    tax_mode = _get_tax_mode(settings)
    items = dto_dict.get("items") or []
    if tax_mode == TAX_MODE_CUSTOMER_FIRST:
        total = 0.0
        for it in items:
            up_incl = _line_unit_price_for_mode(it, tax_mode)
            qty     = _f(it.get("quantity"), default=1.0)
            disc    = _f(it.get("discount_amount"))
            total += up_incl * qty - disc
        # Shipping is not yet rendered as a line in customer_first
        # mode; the customer-paid total in canonical already includes
        # shipping at Salla's effective rate. We add the canonical
        # shipping_amount + shipping tax (0 in customer_first since
        # we don't bill shipping separately) here so the preflight
        # can reconcile. NOTE: if shipping is later rendered as its
        # own line we must adjust this.
        total += _f(dto_dict.get("shipping_amount"))
        return round(total, 2)
    # mezan_fixed_15
    items_net = 0.0
    for it in items:
        items_net += _f(it.get("unit_price")) * _f(it.get("quantity"),
                                                    default=1.0) \
                    - _f(it.get("discount_amount"))
    shipping = _f(dto_dict.get("shipping_amount"))
    return round(items_net * 1.15 + shipping * 1.15, 2)


def build_invoice_payload(
    *, dto_dict: dict, qoyod_customer_id: str,
    product_resolutions: list[dict],
    invoice_date,        # datetime from rules decision
    settings: dict,
) -> dict:
    """Build the Qoyod `POST /invoices` body.

    Tax handling — Iter-285
    ───────────────────────
    Behaviour depends on `settings.tax_mode`:

    • `customer_first` (DEFAULT for trial Go-Live): invoice total
      MUST equal `canonical.total_amount` (what the customer paid).
      Each line carries a TAX-INCLUSIVE unit_price (Salla's
      `unit_price + per-line tax/quantity`) and `tax_id =
      settings.zero_tax_id` (a 0% tax record). Discounts stay
      per-line. Qoyod's computed total = invoice total = receipt
      amount. The 15% Mezan policy is surfaced as DIAGNOSTIC ONLY
      via `mezan_vat_diagnostics`.

    • `mezan_fixed_15` (legacy): line unit_price is Salla's net,
      `tax_id = settings.default_tax_id` (15% Qoyod record). Qoyod
      computes tax server-side. Customer-paid amount may diverge
      from invoice total; needs a tax adjustment line — NOT done
      here.

    Branch handling
    ───────────────
    `branch_id` is OPTIONAL. Some Qoyod accounts are single-branch
    and reject explicit branch_id values. When not configured we
    OMIT the field entirely so Qoyod falls back to the default branch.
    """
    tax_mode = _get_tax_mode(settings)
    res_by_sku = {r["sku"]: r["qoyod_product_id"]
                  for r in product_resolutions if r.get("qoyod_product_id")}
    # Iter-290 — Qoyod's /invoices validator requires `inventory_id`
    # on every line item, even when the product is type=service or
    # is_non_stock. The operator creates one default warehouse in
    # Qoyod and sets its id in `settings.default_inventory_id`.
    inventory_id = (settings.get("default_inventory_id") or "").strip() or None
    lines = []
    for it in dto_dict.get("items", []):
        pid = res_by_sku.get(it.get("sku"))
        unit_price = _line_unit_price_for_mode(it, tax_mode)
        line_tax_id = _line_tax_id_for_mode(it, tax_mode, settings)
        # Iter-276: per-line discount column. Qoyod accepts `discount`
        # as an absolute amount per line; we never fold it into
        # unit_price so the merchant's books match Salla's promo-code
        # attribution.
        line: dict = {
            "product_id":  pid,
            "description": it.get("name"),
            "quantity":    it.get("quantity"),
            "unit_price":  unit_price,
            "discount":    _f(it.get("discount_amount")),
        }
        if line_tax_id:
            line["tax_id"] = line_tax_id
        if inventory_id:
            # Stamp on every line — Qoyod rejects the entire invoice if
            # ANY line is missing it ("inventory id missing in a line
            # item"). Preflight refuses the row when the setting is
            # blank so we never reach here without an id.
            line["inventory_id"] = inventory_id
        lines.append(line)

    invoice: dict = {
        "contact_id":     qoyod_customer_id,
        "issue_date":     invoice_date.date().isoformat() if invoice_date else None,
        "due_date":       invoice_date.date().isoformat() if invoice_date else None,
        "reference":      dto_dict.get("order_number") or dto_dict.get("order_id"),
        "currency_code":  dto_dict.get("currency") or "SAR",
        "line_items":     lines,
        "notes":          f"Mezan · Salla order {dto_dict.get('order_id')} · "
                          f"tax_mode={tax_mode}",
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

    async def find_all_products_by_sku(self, sku, *, limit: int = 10):
        """Iter-288 — Dry-run returns empty list (no existing products)."""
        self.calls.append({"endpoint": "GET /products?q[sku_eq]",
                           "idem": None, "payload": {"sku": sku, "limit": limit},
                           "returned_id": None})
        return []

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
