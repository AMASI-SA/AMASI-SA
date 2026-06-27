"""Qoyod Totals Guard — Pre-flight payload completeness check.

Why this exists
───────────────
Production order `268670571` reached `PRODUCT_RESOLVED` carrying a
single-line canonical payload (sku=AMS11961, unit_price=5) while the
order's own `subtotal=105` and `total=131.60`. Make.com's `map()`
step had silently truncated `items[]` to one row, and the partial
invoice almost shipped to Qoyod for the wrong amount.

The Totals Guard runs **before** customer/product resolution. If the
line items don't sum to the declared subtotal (within a small
rounding tolerance), the row is hard-refused with one of:

  • `line_items_incomplete`    — items sum is MUCH smaller than
    subtotal → upstream (Make / Salla) dropped rows.
  • `line_items_total_mismatch` — items sum diverges in either
    direction → caller convention mismatch (tax-inclusive?
    discount applied per-line?).
  • `order_total_mismatch`      — subtotal + tax + shipping − discount
    does NOT equal total_amount → header-level math is broken.

Design choices
──────────────
* **Read-only / pure.** No DB writes. Returns a typed result.
  Caller (`pipeline.process_normalized_row`) owns persistence.
* **Tolerance** is currency-aware. SAR has 2 decimals; we accept
  ±0.05 (one halala rounding margin) per check.
* **No auto-retry.** A totals mismatch is upstream-misconfigured;
  retrying without a Make fix would just fail again. The row goes
  straight to DEAD_LETTER.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 5 halalas ≈ 1.3 USD¢ — covers float rounding without masking real
# discrepancies. Salla & Qoyod both use 2-decimal SAR amounts so any
# difference larger than this is structural, not arithmetic.
DEFAULT_TOLERANCE: float = 0.05


@dataclass
class TotalsGuardResult:
    ok: bool
    code: Optional[str] = None       # error code, None on success
    message: Optional[str] = None
    details: dict = field(default_factory=dict)

    def to_log_dict(self) -> dict:
        return {
            "ok":      self.ok,
            "code":    self.code,
            "message": self.message,
            "details": dict(self.details),
        }


def _safe_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _round2(x: float) -> float:
    """Round to 2 decimals to keep audit output readable."""
    return round(x, 2)


def validate_totals(
    canonical: dict, *, tolerance: float = DEFAULT_TOLERANCE,
) -> TotalsGuardResult:
    """Validate that the canonical sales order's line items sum to
    the declared subtotal AND the header math reconciles to
    `total_amount`.

    Accepts the canonical payload as a plain dict (the same shape we
    store in `integration_inbox.canonical_payload`) so this module
    stays trivially unit-testable without spinning up DTOs.
    """
    items = canonical.get("items") or []
    subtotal        = _safe_float(canonical.get("subtotal"))
    tax_amount      = _safe_float(canonical.get("tax_amount"))
    shipping_amount = _safe_float(canonical.get("shipping_amount"))
    discount_amount = _safe_float(canonical.get("discount_amount"))
    total_amount    = _safe_float(canonical.get("total_amount"))

    # ── 1. Empty items[] is always a hard-refuse ───────────────────
    # If subtotal is non-zero but we have zero rows, the upstream
    # dropped EVERYTHING.
    if not items:
        if subtotal > tolerance or total_amount > tolerance:
            return TotalsGuardResult(
                ok=False,
                code="line_items_incomplete",
                message=("canonical payload has no line items but "
                         f"declares subtotal={_round2(subtotal)} "
                         f"total={_round2(total_amount)}"),
                details={
                    "items_count":  0,
                    "items_sum":    0.0,
                    "subtotal":     _round2(subtotal),
                    "total_amount": _round2(total_amount),
                },
            )
        # Zero-value order with no items — pathological but consistent.
        return TotalsGuardResult(ok=True, details={"items_count": 0})

    # ── 2. Compute items sum (excl-tax baseline) ───────────────────
    # Line-level math (Iter-276):
    #   line_excl  = unit_price × quantity − discount_amount
    #   line_incl  = line_excl + tax_amount
    # The discount is REAL money the merchant gave away; it must
    # subtract from items_sum_excl so the guard reconciles against
    # the order-level `subtotal` (which Salla reports POST-discount).
    items_sum_excl = 0.0
    items_sum_incl = 0.0
    parsed_items = []
    for it in items:
        unit_price = _safe_float(it.get("unit_price"))
        quantity   = _safe_float(it.get("quantity"))
        item_tax   = _safe_float(it.get("tax_amount"))
        item_disc  = _safe_float(it.get("discount_amount"))
        line_excl  = unit_price * quantity - item_disc
        line_incl  = line_excl + item_tax
        items_sum_excl += line_excl
        items_sum_incl += line_incl
        parsed_items.append({
            "sku":              it.get("sku"),
            "quantity":         quantity,
            "unit_price":       unit_price,
            "discount_amount":  item_disc,
            "tax_amount":       item_tax,
            "line_excl":        _round2(line_excl),
            "line_incl":        _round2(line_incl),
        })

    items_sum_excl = _round2(items_sum_excl)
    items_sum_incl = _round2(items_sum_incl)
    subtotal_r     = _round2(subtotal)

    # ── 3. items_sum vs subtotal — the headline check ──────────────
    # Some adapters report subtotal INCLUSIVE of item-level tax (rare),
    # others EXCLUSIVE (Salla default). Accept either convention so
    # we don't reject a legit order on a tax-bookkeeping nit.
    matches_excl = abs(items_sum_excl - subtotal_r) <= tolerance
    matches_incl = abs(items_sum_incl - subtotal_r) <= tolerance
    if not (matches_excl or matches_incl):
        # If items sum is SUBSTANTIALLY less than subtotal, the
        # upstream dropped rows. Otherwise it's a convention
        # divergence (still a refusal, but a different code so the
        # operator knows whether to chase Make.com or normalizer).
        shortfall = subtotal_r - items_sum_excl
        if shortfall > tolerance and items_sum_excl < subtotal_r * 0.5:
            code = "line_items_incomplete"
            msg  = (f"items[] is missing rows: items_sum_excl="
                    f"{items_sum_excl} but subtotal={subtotal_r} "
                    f"(shortfall={_round2(shortfall)} SAR)")
        else:
            code = "line_items_total_mismatch"
            msg  = (f"items_sum_excl={items_sum_excl} / "
                    f"items_sum_incl={items_sum_incl} neither matches "
                    f"subtotal={subtotal_r} within ±{tolerance}")
        return TotalsGuardResult(
            ok=False, code=code, message=msg,
            details={
                "items_count":         len(items),
                "items_sum_excl":      items_sum_excl,
                "items_sum_incl":      items_sum_incl,
                "subtotal":            subtotal_r,
                "shortfall":           _round2(subtotal_r - items_sum_excl),
                "tolerance":           tolerance,
                "parsed_items":        parsed_items,
            },
        )

    # ── 4. Header math: subtotal + tax + ship − disc == total ──────
    # Reconcile against the order-level total. Use the convention
    # that matched the items check (excl/incl) to avoid double-counting
    # item-level tax.
    if matches_excl:
        derived_total = subtotal + tax_amount + shipping_amount - discount_amount
    else:
        derived_total = items_sum_incl + shipping_amount - discount_amount
    derived_total_r = _round2(derived_total)

    if abs(derived_total_r - _round2(total_amount)) > tolerance:
        return TotalsGuardResult(
            ok=False,
            code="order_total_mismatch",
            message=(f"derived_total={derived_total_r} (subtotal "
                     f"{subtotal_r} + tax {_round2(tax_amount)} + "
                     f"ship {_round2(shipping_amount)} - disc "
                     f"{_round2(discount_amount)}) != "
                     f"declared_total={_round2(total_amount)}"),
            details={
                "items_count":     len(items),
                "items_sum_excl":  items_sum_excl,
                "subtotal":        subtotal_r,
                "tax_amount":      _round2(tax_amount),
                "shipping_amount": _round2(shipping_amount),
                "discount_amount": _round2(discount_amount),
                "derived_total":   derived_total_r,
                "declared_total":  _round2(total_amount),
                "tolerance":       tolerance,
            },
        )

    # ── 5. All green ───────────────────────────────────────────────
    return TotalsGuardResult(
        ok=True,
        details={
            "items_count":     len(items),
            "items_sum_excl":  items_sum_excl,
            "items_sum_incl":  items_sum_incl,
            "subtotal":        subtotal_r,
            "matched_convention": "exclusive" if matches_excl else "inclusive",
            "total_amount":    _round2(total_amount),
        },
    )
