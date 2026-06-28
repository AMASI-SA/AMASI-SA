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


def _to_int_or_none(v: Any) -> Optional[int]:
    """Coerce any id-like value to ``int`` or ``None``.

    Iter-290c — Qoyod's invoice validator strictly rejects string ids
    (treats them as missing/invalid). All Qoyod ids on the invoice
    payload (`contact_id`, `product_id`, `inventory_id`, `branch_id`)
    MUST be JSON numbers. Mezan persists ids as strings in MongoDB so
    we coerce at the payload boundary.

    Returns ``None`` for blank / non-numeric inputs so the caller can
    omit the field cleanly (preflight blocks bad rows upstream).
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v == int(v) else None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


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

    Iter-290c — Qoyod docs-aligned payload (BREAKING the previous
    customer_first tax-inclusive trick)
    ───────────────────────────────────────────────────────────────
    Per the official Qoyod apidoc invoice example, the canonical
    payload uses:

        invoice.status        = "Approved"            (required)
        invoice.inventory_id  = <int>                  (ROOT, not per line)
        line.product_id       = <int>
        line.quantity         = <number>
        line.unit_price       = <NET, exclusive of tax>
        line.discount         = <amount or percent>
        line.discount_type    = "amount" | "percentage"
        line.tax_percent      = <number, e.g. 15>     (per line, NOT tax_id)

    Mezan sends Salla's raw net unit_price + 15% tax_percent. The
    customer-paid total in Salla may diverge slightly from Qoyod's
    computed invoice total (Salla's effective rate is not always 15%)
    — the receipt amount remains the customer-paid amount, so any
    discrepancy surfaces as an outstanding invoice balance for the
    operator to reconcile. This is the trade-off the operator chose
    when picking the Qoyod-canonical payload shape.

    Branch handling
    ───────────────
    `branch_id` is OPTIONAL. Some Qoyod accounts are single-branch
    and reject explicit branch_id values. When not configured we
    OMIT the field entirely so Qoyod falls back to the default branch.
    """
    tax_mode = _get_tax_mode(settings)
    res_by_sku = {r["sku"]: r["qoyod_product_id"]
                  for r in product_resolutions if r.get("qoyod_product_id")}
    # Iter-290c — inventory_id at root (int).
    inventory_id = _to_int_or_none(settings.get("default_inventory_id"))
    # Iter-290e — Qoyod tax percent (default 15%). Operator can override
    # via `settings.qoyod_tax_percent` (preferred new name) or the older
    # `tax_percentage` key. The legal Saudi VAT rate.
    try:
        tax_percent = float(
            settings.get("qoyod_tax_percent")
            or settings.get("tax_percentage")
            or 15)
    except (TypeError, ValueError):
        tax_percent = 15.0
    tax_factor = 1.0 + tax_percent / 100.0  # e.g. 1.15

    # ── Iter-290e — invoice_total_policy = "match_salla_total" ──────
    #
    # Why this policy exists
    # ──────────────────────
    # Salla's effective per-line tax (~8% empirically on the test
    # orders) DIFFERS from Qoyod's tax_percent (15% Saudi VAT). If we
    # send Salla's net unit_price + Qoyod's 15% tax, the resulting
    # invoice total is INFLATED (310 vs 291 SAR on order 268756329).
    # Customers paid Salla's total, so we must reverse-engineer a
    # discount that lands Qoyod's GROSS line total back on Salla's
    # `item.total`.
    #
    # Per-line math
    # ─────────────
    #   target_gross  = item.total                       # what Salla shows
    #   target_net    = target_gross / (1 + tp/100)      # Qoyod will mark up by tp
    #   original_base = item.unit_price * item.quantity  # Salla's line base
    #   qoyod_discount = original_base - target_net
    #
    # We then send Qoyod:
    #   unit_price = item.unit_price (verbatim — auditable to Salla)
    #   discount   = qoyod_discount  (the math glue)
    #   tax_percent = tp             (15)
    #
    # Edge cases
    # ──────────
    # * item.total == 0 (fully discounted): target_net=0, discount=base
    # * qoyod_discount < 0 (Salla price < target_net — rare/unexpected):
    #   fallback: shrink unit_price to target_net/qty, discount=0.
    policy = (settings.get("invoice_total_policy")
              or "match_salla_total").lower()

    def _line_for_match_salla_total(it: dict) -> dict:
        qty = _f(it.get("quantity"), 1.0) or 1.0
        unit_price = _f(it.get("unit_price"))
        target_gross = _f(it.get("total"))
        target_net = round(target_gross / tax_factor, 4)
        original_base = round(unit_price * qty, 4)
        qoyod_discount = round(original_base - target_net, 4)
        if qoyod_discount < 0:
            # Fallback: use target_net as the unit_price, no discount.
            adj_unit_price = round(target_net / qty, 4) if qty else target_net
            return {
                "unit_price":    adj_unit_price,
                "discount":      0.0,
                "discount_type": "amount",
                "tax_percent":   tax_percent,
                "_pricing_fallback": True,
            }
        return {
            "unit_price":    unit_price,
            "discount":      qoyod_discount,
            "discount_type": "amount",
            "tax_percent":   tax_percent,
            "_pricing_fallback": False,
        }

    lines = []
    line_diagnostics: list[dict] = []
    for it in dto_dict.get("items", []):
        pid = _to_int_or_none(res_by_sku.get(it.get("sku")))
        if policy == "match_salla_total":
            shape = _line_for_match_salla_total(it)
        else:
            # Legacy passthrough — kept for compatibility with old
            # tests / non-Saudi scenarios.
            shape = {
                "unit_price":    _f(it.get("unit_price")),
                "discount":      _f(it.get("discount_amount")),
                "discount_type": "amount",
                "tax_percent":   tax_percent,
                "_pricing_fallback": False,
            }
        line: dict = {
            "product_id":    pid,
            "description":   it.get("name"),
            "quantity":      it.get("quantity"),
            "unit_price":    shape["unit_price"],
            "discount":      shape["discount"],
            "discount_type": shape["discount_type"],
            "tax_percent":   shape["tax_percent"],
        }
        lines.append(line)
        # Per-line gross Qoyod will compute server-side — used for
        # the pre-POST guard and the diagnostics panel.
        computed_gross = round(
            (line["unit_price"] * _f(line.get("quantity"), 1.0)
             - line["discount"]) * tax_factor, 2)
        line_diagnostics.append({
            "sku":          it.get("sku"),
            "salla_total":  _f(it.get("total")),
            "computed_qoyod_gross": computed_gross,
            "fallback_used": shape["_pricing_fallback"],
        })

    # ── Iter-290f — shipping as an additional invoice line ──────────
    # Salla emits shipping at the order level (`shipping_amount`). When
    # present, Qoyod won't know about it unless we add it as a line.
    # We use the same match_salla_total math so the total still lands
    # on Salla's `total_amount`.
    shipping_amount = round(_f(dto_dict.get("shipping_amount")), 2)
    if shipping_amount > 0 and policy == "match_salla_total":
        # Operator must configure a shipping product in Qoyod and bind
        # its id here. Without it we cannot add a Qoyod-valid line.
        shipping_product_id = _to_int_or_none(
            settings.get("default_shipping_product_id"))
        # Derive the customer-paid shipping (incl Salla's effective tax).
        # Salla's `total_amount` = sum(items.total) + shipping_paid.
        items_total_sum = sum(_f(it.get("total"))
                              for it in dto_dict.get("items", []))
        shipping_target_gross = round(
            _f(dto_dict.get("total_amount")) - items_total_sum, 2)
        if shipping_target_gross > 0 and shipping_product_id is not None:
            shipping_target_net = round(shipping_target_gross / tax_factor, 4)
            shipping_unit_price = round(shipping_amount, 4)
            shipping_discount = round(shipping_unit_price - shipping_target_net, 4)
            shipping_fallback = False
            if shipping_discount < 0:
                shipping_unit_price = shipping_target_net
                shipping_discount = 0.0
                shipping_fallback = True
            lines.append({
                "product_id":    shipping_product_id,
                "description":   "شحن (Shipping)",
                "quantity":      1,
                "unit_price":    shipping_unit_price,
                "discount":      shipping_discount,
                "discount_type": "amount",
                "tax_percent":   tax_percent,
            })
            line_diagnostics.append({
                "sku":         "_SHIPPING_",
                "salla_total": shipping_target_gross,
                "computed_qoyod_gross": round(
                    (shipping_unit_price - shipping_discount) * tax_factor, 2),
                "fallback_used": shipping_fallback,
            })
        elif shipping_target_gross > 0 and shipping_product_id is None:
            # Surface in diagnostics so the pre-POST guard catches it.
            line_diagnostics.append({
                "sku":         "_SHIPPING_MISSING_PRODUCT_ID_",
                "salla_total": shipping_target_gross,
                "computed_qoyod_gross": 0.0,
                "fallback_used": False,
            })

    invoice: dict = {
        "contact_id":     _to_int_or_none(qoyod_customer_id),
        "issue_date":     invoice_date.date().isoformat() if invoice_date else None,
        "due_date":       invoice_date.date().isoformat() if invoice_date else None,
        "reference":      dto_dict.get("order_number") or dto_dict.get("order_id"),
        # Iter-290c — Qoyod requires `status: "Approved"` to materialise
        # the invoice in the books. Without it the invoice stays as a
        # draft and the receipt POST fails.
        "status":         "Approved",
        # Iter-290h.7 — Invoice-header `payment_method` is the ZATCA
        # e-invoicing payment-means code. It's a DISPLAY-ONLY field
        # (does NOT create a receipt or move any account) and was
        # left empty in production until this iteration, causing the
        # "طريقة الدفع" column in the قيود invoice list to show as
        # blank. Policy per user decision (2026-06-29): always send
        # `"10"` (Cash / نقدي) regardless of the upstream Salla
        # payment method. The actual settlement still happens via
        # POST /invoice_payments with the operator-mapped account_id,
        # so books reconcile correctly. ZATCA code reference:
        #   "1"  = Not defined
        #   "10" = Cash / نقدي              ← what we send
        #   "30" = Credit / آجل
        #   "42" = Bank account payment
        #   "48" = Bank card
        "payment_method": "10",
        "currency_code":  dto_dict.get("currency") or "SAR",
        "line_items":     lines,
        # Iter-290g — operator-facing audit string. Carries:
        #   • pricing_mode  → the actual invoice math policy in effect
        #                     (Iter-290e renamed from tax_mode which now
        #                     just routes customer-create logic).
        #   • tax_mode      → legacy customer-creation routing tag, kept
        #                     for backward-compatibility filtering in
        #                     existing dashboards/reports.
        "notes":          f"Mezan · Salla order {dto_dict.get('order_id')} · "
                          f"pricing_mode={policy} · tax_mode={tax_mode}",
        # Provenance — operator can find the source order from Qoyod.
        "external_reference": dto_dict.get("order_id"),
    }
    # Iter-290c — inventory_id at INVOICE ROOT, not per line.
    if inventory_id is not None:
        invoice["inventory_id"] = inventory_id
    # Only include branch_id when the operator has configured one.
    branch_id = _to_int_or_none(settings.get("default_branch_id"))
    if branch_id is not None:
        invoice["branch_id"] = branch_id

    # ── Iter-290e — diagnostics block for traceability ───────────────
    # IMPORTANT: kept OUTSIDE `invoice` so Qoyod never receives it.
    # The pipeline lifts this into `qoyod_payloads.invoice_diagnostics`.
    salla_total = round(_f(dto_dict.get("total_amount")), 2)
    expected_qoyod_total = round(
        sum(d["computed_qoyod_gross"] for d in line_diagnostics), 2)
    # Detect Salla's effective tax rate (informational only).
    salla_net_sum = sum(_f(it.get("total")) - _f(it.get("tax_amount"))
                       for it in dto_dict.get("items", []))
    salla_tax_sum = sum(_f(it.get("tax_amount"))
                       for it in dto_dict.get("items", []))
    salla_tax_percent_detected = (
        round(salla_tax_sum / salla_net_sum * 100, 2)
        if salla_net_sum > 0 else 0.0)
    diagnostics = {
        "pricing_mode":               policy,
        "salla_total":                salla_total,
        "expected_qoyod_total":       expected_qoyod_total,
        "difference":                 round(expected_qoyod_total - salla_total, 2),
        "salla_tax_percent_detected": salla_tax_percent_detected,
        "qoyod_tax_percent_used":     tax_percent,
        "line_diagnostics":           line_diagnostics,
    }
    return {"invoice": invoice, "_diagnostics": diagnostics}


def build_receipt_payload(
    *, qoyod_invoice_id: str, qoyod_customer_id: Optional[str] = None,
    dto_dict: dict,
    invoice_date, settings: dict,
) -> dict:
    """Build the Qoyod `POST /receipts` body.

    DEPRECATED in Iter-290h — kept only for legacy code paths / tests.
    New pipeline uses `build_invoice_payment_payload` + `POST
    /invoice_payments` so the payment is REGISTERED ON the invoice
    (closing balance) instead of creating a standalone receipt.

    Iter-290d — Qoyod's `/receipts` validator requires `contact_id`
    on the receipt root (otherwise: 422 `{'contact': ["Can't be blank"]}`).
    Mirror Iter-290c's id-coercion rules: every Qoyod id is sent as
    an integer.
    """
    pm_native = dto_dict.get("payment_method") or dto_dict.get("payment_method_native")
    account_id = _resolve_payment_account(settings, pm_native)
    receipt: dict = {
        "invoice_id":   _to_int_or_none(qoyod_invoice_id),
        "contact_id":   _to_int_or_none(qoyod_customer_id),
        "date":         invoice_date.date().isoformat() if invoice_date else None,
        "amount":       dto_dict.get("total_amount"),
        "currency":     dto_dict.get("currency") or "SAR",
        "account_id":   _to_int_or_none(account_id),
        "payment_method": pm_native,
        "notes":        f"Mezan · Salla order {dto_dict.get('order_id')}",
        "external_reference": dto_dict.get("order_id"),
    }
    return {"receipt": receipt}


def build_invoice_payment_payload(
    *, qoyod_invoice_id: str, dto_dict: dict,
    invoice_date, settings: dict,
) -> tuple[dict, dict]:
    """Iter-290h — Build the Qoyod `POST /invoice_payments` body.

    Returns `(payload, idempotency_fingerprint)`. The fingerprint is a
    structured dict the pipeline uses for its DB-side idempotency
    guard (per user spec — `order_id + invoice_id + payment_method
    + amount`).

    Payload shape (per LIVE Qoyod docs + 2026-02-28 retry on order
    269048975 — see field-name correction below):
        {"invoice_payment": {
            "invoice_id":   <int>,
            "amount":       <decimal>,
            "date":         "YYYY-MM-DD",
            "account_id":   <int>,        # Qoyod Chart-of-Accounts id
            "reference":    "<order #>",
            "description":  "Mezan · Salla order <id>"
        }}

    NOTE on field names — Iter-290h.6 (CORRECTION)
    ──────────────────────────────────────────────
    Iter-290h.3 read the 422 message `{"account":["Can't be blank"]}`
    and wrongly concluded the wire field was `account`. The retry on
    order 269048975 (invoice_id=63) with `"account": 94` still got
    422 with the SAME message — proving Qoyod was not reading the
    field at all. The actual wire name per Qoyod's official docs
    (https://www.qoyod.com/en/knowledge-base/explaining-the-account-
    id-field-in-api-requests-…) is `account_id`. The 422 message
    surfaces the Rails `belongs_to :account` association name, which
    is misleading — the foreign-key column the validator checks is
    `account_id`. Sending `"account_id": <int>` resolves the
    association and clears the validation.

    The `payment_method_id` is resolved from the existing
    `settings.payment_method_accounts` mapping (operator-configured per
    Salla payment method, e.g. "mada" → 17). The mapping ID was
    historically called `qoyod_account_id` and that is exactly what
    Qoyod expects on the wire (`account_id`).

    Returns the payload with `account_id = None` when the mapping is
    missing — the pipeline's pre-POST guard catches this and routes
    the row to `PAYMENT_METHOD_MAPPING_MISSING`.
    """
    pm_native = (dto_dict.get("payment_method")
                 or dto_dict.get("payment_method_native"))
    method_id = _resolve_payment_account(settings, pm_native)
    invoice_id_int = _to_int_or_none(qoyod_invoice_id)
    amount = dto_dict.get("total_amount")
    payment_date_iso = (
        invoice_date.date().isoformat() if invoice_date else None)
    reference = (str(dto_dict.get("order_number") or "").strip()
                 or str(dto_dict.get("order_id") or "").strip()
                 or None)
    body = {
        "invoice_id":         invoice_id_int,
        "amount":             amount,
        # Iter-290h.6 — Live Qoyod evidence (order 269048975, invoice
        # 63, 2026-06-28 retry) proved the canonical field is
        # `account_id` — NOT `account`. Sending `"account": 94`
        # returned 422 `{"account":["Can't be blank"]}` because
        # Qoyod's validator never reads `account` on the wire; it
        # reads `account_id` and surfaces the Rails association name
        # in the error message. Per Qoyod's official docs the field
        # is `account_id` and it references the Chart-of-Accounts id.
        "date":               payment_date_iso,
        "account_id":         _to_int_or_none(method_id),
        "reference":          reference,
        "description":        f"Mezan · Salla order {dto_dict.get('order_id')}",
    }
    # Idempotency fingerprint — exactly per user spec
    # `order_id + invoice_id + payment_method + amount`.
    fingerprint = {
        "order_id":         dto_dict.get("order_id"),
        "qoyod_invoice_id": invoice_id_int,
        "payment_method":   pm_native,
        "payment_method_id": _to_int_or_none(method_id),
        "amount":           amount,
    }
    return ({"invoice_payment": body}, fingerprint)


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

    async def create_invoice_payment(self, payload, *, idem):
        """Iter-290h — Dry-run mirror of `POST /invoice_payments`. Returns
        a deterministic `DRY:invoice_payment:<hash>` id so the pipeline
        can complete the dry-run successfully without contacting Qoyod."""
        body = payload.get("invoice_payment") or payload
        pid = self._fake("invoice_payment", body)
        self.calls.append({"endpoint": "POST /invoice_payments",
                           "idem": idem, "payload": payload,
                           "returned_id": pid})
        return {"invoice_payment": {"id": pid}}


def is_dry_run_mode(settings: dict) -> bool:
    return bool(settings.get("dry_run_mode", False))
