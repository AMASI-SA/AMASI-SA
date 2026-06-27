"""Mezan VAT — fixed 15% (SSOT for tax math).

User directive (2026-02-27, Iter-282)
─────────────────────────────────────
The VAT rate used for invoice math is OWNED BY MEZAN, not by Salla
and not by Qoyod. Salla's `tax.percent` / `tax.amount` and Qoyod's
server-side tax records are treated as DIAGNOSTIC only — never the
source of truth.

Rationale
─────────
1. Salla's tax math is shaped by storefront promo configuration
   (Tamara BNPL fees, free-shipping coupons, partial-tax SKUs, …)
   and historically reports inconsistent `tax.percent` values
   (e.g. order 268746039 showed `8.00` while the VAT period rate
   in KSA is 15%).
2. Qoyod's tax records mirror the merchant's manual configuration.
   When operators change the Qoyod tax_id, the same Mezan invoice
   could end up with a different rate — silently.
3. The merchant's books must always reflect the prevailing legal VAT
   rate (15% for KSA, post-2020). Anchoring that here makes the rate
   reviewable in code and unit-tested.

Single source of truth
──────────────────────
    VAT_RATE = 0.15

Helpers
───────
• `compute_mezan_totals(canonical)` — returns the expected per-line
  and order-level totals using Mezan's fixed 15%. Surfaces side-by-side
  `salla_*` vs `mezan_*` figures and the difference. NEVER mutates
  the input.
"""
from __future__ import annotations

from typing import Any


# ── Canonical VAT rate (Mezan SSOT) ─────────────────────────────────
VAT_RATE: float = 0.15
TAX_SOURCE_LABEL: str = "mezan_fixed_15"


def _f(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _r(x: float) -> float:
    """Round to 2 decimals for audit readability."""
    return round(x, 2)


def compute_mezan_totals(canonical: dict) -> dict:
    """Compute the Mezan-VAT-15% view of a canonical SalesOrderDTO dict.

    Output shape (also embedded as `mezan_vat_diagnostics` in the
    inbox row's totals_guard details + the canonical DTO metadata):
      {
        "tax_source":             "mezan_fixed_15",
        "vat_rate":               0.15,
        "items":                  [
          {sku, quantity, unit_price, discount_amount,
           net_line, mezan_tax_line, salla_tax_line,
           tax_difference_line},
          ...
        ],
        "net_items_total":        — sum of (unit_price*qty − discount).
        "mezan_items_tax":        — net_items_total × 0.15
        "salla_items_tax":        — sum of item.tax_amount from Salla
        "shipping_amount":        — order-level shipping
        "mezan_shipping_tax":     — shipping × 0.15
        "salla_shipping_tax":     — currently 0 (Salla doesn't separate)
        "mezan_expected_total":   — net_items + mezan_items_tax
                                    + shipping + mezan_shipping_tax
        "salla_declared_total":   — canonical.total_amount
        "tax_difference":         — salla_declared_total
                                    − mezan_expected_total
                                    (NEGATIVE means Salla under-taxed
                                     vs Mezan's policy)
        "salla_items_tax_amount": — header-level Salla tax_amount
      }

    Notes
    -----
    • This function NEVER raises. Missing/malformed fields default to 0.
    • Discount math: per-line `discount_amount` is treated as POSITIVE
      money the merchant gave away. `net_line = unit_price*qty − discount`.
    • Shipping math: shipping is taxable at the same Mezan rate as items.
      Adjust here if Mezan policy changes (out-of-scope for Iter-282).
    """
    items_raw = canonical.get("items") or []
    items_out: list[dict] = []
    net_items_total = 0.0
    salla_items_tax = 0.0

    for it in items_raw:
        unit_price = _f(it.get("unit_price"))
        qty        = _f(it.get("quantity"))
        disc       = _f(it.get("discount_amount"))
        salla_tax  = _f(it.get("tax_amount"))
        net_line   = unit_price * qty - disc
        mezan_tax_line = net_line * VAT_RATE
        net_items_total += net_line
        salla_items_tax += salla_tax
        items_out.append({
            "sku":              it.get("sku"),
            "quantity":         qty,
            "unit_price":       unit_price,
            "discount_amount":  _r(disc),
            "net_line":         _r(net_line),
            "mezan_tax_line":   _r(mezan_tax_line),
            "salla_tax_line":   _r(salla_tax),
            "tax_difference_line": _r(salla_tax - mezan_tax_line),
        })

    shipping_amount = _f(canonical.get("shipping_amount"))
    mezan_items_tax = net_items_total * VAT_RATE
    mezan_shipping_tax = shipping_amount * VAT_RATE
    mezan_expected_total = (net_items_total + mezan_items_tax
                            + shipping_amount + mezan_shipping_tax)
    salla_declared_total = _f(canonical.get("total_amount"))
    salla_header_tax = _f(canonical.get("tax_amount"))

    return {
        "tax_source":             TAX_SOURCE_LABEL,
        "vat_rate":               VAT_RATE,
        "items":                  items_out,
        "net_items_total":        _r(net_items_total),
        "mezan_items_tax":        _r(mezan_items_tax),
        "salla_items_tax":        _r(salla_items_tax),
        "shipping_amount":        _r(shipping_amount),
        "mezan_shipping_tax":     _r(mezan_shipping_tax),
        "salla_shipping_tax":     0.0,
        "mezan_expected_total":   _r(mezan_expected_total),
        "salla_declared_total":   _r(salla_declared_total),
        "tax_difference":         _r(salla_declared_total - mezan_expected_total),
        "salla_items_tax_amount": _r(salla_header_tax),
    }


def expected_line_tax(unit_price: Any, quantity: Any,
                      discount_amount: Any = 0) -> float:
    """Compute Mezan's expected per-line tax. Used by the invoice
    builder to override Salla's reported tax."""
    net = _f(unit_price) * _f(quantity) - _f(discount_amount)
    return _r(net * VAT_RATE)
