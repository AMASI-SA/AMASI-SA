"""Iter-001k+ — Read-Only Order Totals Breakdown Diagnostic.

Purpose
────────
When Salla's `total_amount` doesn't reconcile against the naive
reconstruction `items_sum + shipping + tax` used by `_check_totals`,
the operator needs a full breakdown to identify the missing pieces:
coupons, promotions, wallet, gift cards, reward points, manual
discounts, tax-inclusive item pricing, etc.

Contract (STRICT):
    • Read-Only. Zero DB writes.
    • Zero Qoyod API calls.
    • Zero policy changes / gate flips.
    • Zero send attempts.
    • No mutation of `_check_totals` / policy formulas.
    • Emits ONLY numeric fields + provenance (`field_paths_used`).
    • Raw payload is included ONLY when `include_raw_debug=True`.

The returned breakdown surfaces EVERY known Salla amount field so
we can decide together which adjustment the current reconstruction
is missing. No premature formula fix.
"""
from __future__ import annotations

from typing import Any, Optional


# ── Small numeric helpers (local, no I/O) ───────────────────────────
def _to_float(v: Any) -> Optional[float]:
    """Best-effort numeric coercion; None on failure or unknown shape."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except (ValueError, TypeError):
            return None
    if isinstance(v, dict):
        # Salla's canonical {amount, currency} envelope.
        if "amount" in v:
            return _to_float(v["amount"])
    return None


def _r2(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v), 2)


def _pick(node: dict, *keys: str) -> tuple[Any, Optional[str]]:
    """Return the first (value, key) pair present in `node`.
    (None, None) if nothing matches."""
    if not isinstance(node, dict):
        return (None, None)
    for k in keys:
        if k in node and node[k] is not None:
            return (node[k], k)
    return (None, None)


def _sum_line_discounts(items: list) -> tuple[float, list[dict]]:
    """Sum per-line `discount_amount` values found in canonical items.
    Returns (total, per_line_breakdown)."""
    total = 0.0
    per_line: list[dict] = []
    for idx, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        raw = it.get("discount_amount")
        val = _to_float(raw) or 0.0
        if val:
            per_line.append({
                "index":  idx,
                "sku":    (it.get("sku") or "")[:64],
                "amount": _r2(val),
            })
            total += val
    return (_r2(total) or 0.0, per_line)


def _sum_line_totals(items: list) -> float:
    """Iter-284-style `Σ(qty × unit_price)` used by the current
    `_check_totals` reconstruction."""
    total = 0.0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        qty = _to_float(it.get("quantity")) or 1.0
        unit = _to_float(it.get("unit_price")) \
            or _to_float(it.get("price")) or 0.0
        total += qty * unit
    return round(total, 2)


# ── Field paths (source-of-truth attribution) ───────────────────────
# When populated, these strings tell the auditor EXACTLY which JSON
# path fed each output number. Empty when the field wasn't found.
_PATHS = {
    "salla_official_total":         "raw_payload.data.amounts.total.amount",
    "subtotal":                     "raw_payload.data.amounts.sub_total.amount",
    "shipping_amount":              "raw_payload.data.amounts.shipping.amount",
    "tax_amount":                   "raw_payload.data.amounts.tax.amount",
    "order_level_discount_amount":  ("raw_payload.data.amounts.discount.amount "
                                     "OR .discounts.amount"),
    "coupon_discount_amount":       "raw_payload.data.coupon.amount",
    "promotion_discount_amount":    "raw_payload.data.promotion.amount",
    "wallet_amount":                "raw_payload.data.wallet.amount",
    "gift_card_amount":             ("raw_payload.data.gift_card.amount OR "
                                     ".gift_certificate.amount"),
    "reward_points_amount":         "raw_payload.data.reward_points.amount",
    "manual_discount_amount":       "raw_payload.data.manual_discount.amount",
    "options_amount":               "raw_payload.data.amounts.options.amount",
    "line_item_discount_sum":       "sum(canonical_payload.items[].discount_amount)",
    "items_sum_from_canonical":     "Σ(qty × unit_price) from canonical_payload.items",
}


def build_order_totals_breakdown(
    *,
    inbox_row: dict,
    include_raw_debug: bool = False,
) -> dict:
    """Pure, deterministic totals breakdown. Feed it one integration_inbox
    row (with canonical + raw payloads); returns the diagnostic dict.

    NEVER writes to DB. NEVER calls Qoyod. NEVER sends.
    """
    canonical = inbox_row.get("canonical_payload") or {}
    raw       = inbox_row.get("raw_payload") or {}
    raw_data  = (raw.get("data") if isinstance(raw, dict) else {}) or {}
    raw_amt   = (raw_data.get("amounts") if isinstance(raw_data, dict)
                 else {}) or {}

    # ── Salla-side numbers ─────────────────────────────────────
    used: dict[str, str] = {}

    def _record(field_name: str, value: Any, path: str) -> Optional[float]:
        num = _to_float(value)
        if num is not None:
            used[field_name] = path
        return num

    salla_official_total = _record(
        "salla_official_total",
        raw_amt.get("total"),
        _PATHS["salla_official_total"])
    if salla_official_total is None:
        # Canonical fallback.
        salla_official_total = _to_float(canonical.get("total_amount"))
        if salla_official_total is not None:
            used["salla_official_total"] = "canonical_payload.total_amount"

    subtotal_raw = _record(
        "subtotal_from_raw",
        raw_amt.get("sub_total") or raw_amt.get("subtotal"),
        _PATHS["subtotal"])
    subtotal_canonical = _to_float(canonical.get("subtotal"))
    subtotal = subtotal_raw if subtotal_raw is not None \
        else subtotal_canonical
    if subtotal_raw is None and subtotal_canonical is not None:
        used["subtotal_from_canonical"] = "canonical_payload.subtotal"
    # Which subtotal source is authoritative for this row?
    subtotal_source = "raw.data.amounts.sub_total" if subtotal_raw is not None \
        else ("canonical_payload.subtotal"
              if subtotal_canonical is not None else None)

    shipping_raw = _record(
        "shipping_from_raw",
        raw_amt.get("shipping") or raw_amt.get("shipping_cost"),
        _PATHS["shipping_amount"])
    shipping_canonical = _to_float(canonical.get("shipping_amount"))
    shipping_amount = shipping_raw if shipping_raw is not None \
        else shipping_canonical
    if shipping_raw is None and shipping_canonical is not None:
        used["shipping_from_canonical"] = "canonical_payload.shipping_amount"
    shipping_source = "raw.data.amounts.shipping" if shipping_raw is not None \
        else ("canonical_payload.shipping_amount"
              if shipping_canonical is not None else None)

    tax_raw = _record(
        "tax_from_raw",
        raw_amt.get("tax"),
        _PATHS["tax_amount"])
    tax_canonical = _to_float(canonical.get("tax_amount"))
    tax_amount = tax_raw if tax_raw is not None else tax_canonical
    if tax_raw is None and tax_canonical is not None:
        used["tax_from_canonical"] = "canonical_payload.tax_amount"
    tax_source = "raw.data.amounts.tax" if tax_raw is not None \
        else ("canonical_payload.tax_amount"
              if tax_canonical is not None else None)

    # ── Discounts (multi-source probe) ─────────────────────────
    order_level_discount = _record(
        "order_level_discount_amount",
        raw_amt.get("discount") or raw_amt.get("discounts"),
        _PATHS["order_level_discount_amount"])
    if order_level_discount is None:
        _c = _to_float(canonical.get("discount_amount"))
        if _c is not None:
            order_level_discount = _c
            used["order_level_discount_amount"] = \
                "canonical_payload.discount_amount"

    coupon_val, coupon_key = _pick(raw_data, "coupon", "coupons")
    coupon_discount = _to_float(coupon_val)
    if coupon_discount is not None:
        used["coupon_discount_amount"] = \
            f"raw_payload.data.{coupon_key}.amount"

    promotion_val, promo_key = _pick(
        raw_data, "promotion", "promotions", "special_offer",
        "special_offers")
    promotion_discount = _to_float(promotion_val)
    if promotion_discount is not None:
        used["promotion_discount_amount"] = \
            f"raw_payload.data.{promo_key}.amount"

    wallet_val, wallet_key = _pick(raw_data, "wallet", "store_credit")
    wallet_amount = _to_float(wallet_val)
    if wallet_amount is not None:
        used["wallet_amount"] = f"raw_payload.data.{wallet_key}.amount"

    gift_val, gift_key = _pick(
        raw_data, "gift_card", "gift_certificate", "gift")
    gift_card_amount = _to_float(gift_val)
    if gift_card_amount is not None:
        used["gift_card_amount"] = f"raw_payload.data.{gift_key}.amount"

    reward_val, reward_key = _pick(
        raw_data, "reward_points", "loyalty_points", "points")
    reward_points_amount = _to_float(reward_val)
    if reward_points_amount is not None:
        used["reward_points_amount"] = \
            f"raw_payload.data.{reward_key}.amount"

    manual_val, manual_key = _pick(raw_data, "manual_discount",
                                   "custom_discount", "extra_discount")
    manual_discount_amount = _to_float(manual_val)
    if manual_discount_amount is not None:
        used["manual_discount_amount"] = \
            f"raw_payload.data.{manual_key}.amount"

    options_amount = _to_float(raw_amt.get("options"))
    if options_amount is not None:
        used["options_amount"] = _PATHS["options_amount"]

    # ── Canonical items → sums ─────────────────────────────────
    items = canonical.get("items") or []
    items_sum_from_canonical = _sum_line_totals(items)
    line_item_discount_sum, per_line_discount_breakdown = \
        _sum_line_discounts(items)

    # ── Current reconstruction (mirrors `_check_totals` EXACTLY) ─
    # `_check_totals` in `eligible_orders.py` reads ONLY canonical
    # fields, ignoring raw. Reproducing that behaviour here lets the
    # diagnostic show what the current buggy formula computes.
    _shipping_canonical = shipping_canonical or 0.0
    _tax_canonical = tax_canonical or 0.0
    current_expected = round(
        items_sum_from_canonical
        + _shipping_canonical
        + _tax_canonical,
        2,
    )
    current_diff = _r2(
        (salla_official_total or 0.0) - current_expected)

    # ── Adjustment-aware reconstruction ────────────────────────
    # Priority:
    #   1. Use subtotal from Salla if present (single source of truth).
    #      Otherwise fall back to items_sum_from_canonical.
    #   2. Subtract every discount source that was populated.
    #   3. Subtract wallet / gift / reward / manual.
    #   4. Add shipping + tax.
    reconstruction_base = subtotal if subtotal is not None \
        else items_sum_from_canonical
    adjustments_applied: list[dict] = []
    total_adjust = 0.0

    def _apply(label: str, val: Optional[float], sign: int, path: str):
        nonlocal total_adjust
        if val is not None and val != 0:
            signed = sign * val
            total_adjust += signed
            adjustments_applied.append({
                "label": label,
                "value": _r2(val),
                "sign":  "+" if sign > 0 else "-",
                "source_path": path,
            })

    _apply("coupon_discount",     coupon_discount,     -1,
           used.get("coupon_discount_amount", ""))
    _apply("promotion_discount",  promotion_discount,  -1,
           used.get("promotion_discount_amount", ""))
    _apply("order_level_discount", order_level_discount, -1,
           used.get("order_level_discount_amount", ""))
    _apply("wallet",              wallet_amount,       -1,
           used.get("wallet_amount", ""))
    _apply("gift_card",           gift_card_amount,    -1,
           used.get("gift_card_amount", ""))
    _apply("reward_points",       reward_points_amount, -1,
           used.get("reward_points_amount", ""))
    _apply("manual_discount",     manual_discount_amount, -1,
           used.get("manual_discount_amount", ""))
    _apply("options",             options_amount,      +1,
           used.get("options_amount", ""))
    # De-duplicate: order_level_discount often OVERLAPS with the sum
    # of coupon+promotion in Salla payloads. Compute BOTH candidate
    # reconstructions so the auditor can pick the correct one.
    reconstructed_with_all = round(
        reconstruction_base
        + total_adjust
        + (shipping_amount or 0.0)
        + (tax_amount or 0.0),
        2,
    )
    # Alternate: if coupon+promotion are populated AND
    # order_level_discount ≈ coupon+promotion, drop the aggregate
    # to avoid double-subtraction.
    coupon_plus_promo = round(
        (coupon_discount or 0.0) + (promotion_discount or 0.0), 2)
    dedup_note = None
    if (order_level_discount is not None
            and coupon_plus_promo > 0
            and abs(order_level_discount - coupon_plus_promo) <= 0.01):
        # Prefer the itemized (coupon + promo) breakdown.
        alt_adjust = total_adjust + (order_level_discount)  # cancel it
        reconstructed_dedup = round(
            reconstruction_base
            + alt_adjust
            + (shipping_amount or 0.0)
            + (tax_amount or 0.0),
            2,
        )
        dedup_note = ("order_level_discount ≈ coupon+promotion — dropped "
                      "aggregate to prevent double-subtraction. See "
                      "`reconstructed_with_adjustments`.")
        reconstructed_with_adjustments = reconstructed_dedup
    else:
        reconstructed_with_adjustments = reconstructed_with_all

    diff_after_adjustments = _r2(
        (salla_official_total or 0.0) - reconstructed_with_adjustments)
    residual_unexplained = diff_after_adjustments

    # ── Formula notes (operator-facing) ────────────────────────
    formula_notes = [
        "current_expected = Σ(qty×unit_price) + shipping + tax "
        "(NO discount subtraction — this is the current bug).",
        ("reconstructed_with_adjustments = subtotal − coupon − "
         "promotion − order_level_discount − wallet − gift_card − "
         "reward_points − manual_discount + options + shipping + "
         "tax."),
        ("Adjustments applied ONLY when Salla payload contains a "
         "non-zero value for that field. Missing fields are treated "
         "as 0."),
        ("If |diff_after_adjustments| > 0.01 the residual is UNKNOWN "
         "and needs raw payload inspection (use "
         "?include_raw_debug=true)."),
    ]
    if dedup_note:
        formula_notes.append(dedup_note)

    out = {
        "order_number":
            str((canonical.get("order_number")
                 or raw_data.get("reference_id")
                 or raw_data.get("id") or "")),
        "salla_order_id":
            str(canonical.get("order_id")
                or inbox_row.get("salla_order_id") or ""),
        "trace_id": inbox_row.get("trace_id"),
        # Salla-side inputs.
        "salla_official_total":  _r2(salla_official_total),
        "subtotal":              _r2(subtotal),
        "subtotal_source":       subtotal_source,
        "shipping_amount":       _r2(shipping_amount),
        "shipping_source":       shipping_source,
        "tax_amount":            _r2(tax_amount),
        "tax_source":            tax_source,
        # Discounts (any source may be null when Salla didn't send it).
        "order_level_discount_amount":  _r2(order_level_discount),
        "coupon_discount_amount":       _r2(coupon_discount),
        "promotion_discount_amount":    _r2(promotion_discount),
        "line_item_discount_sum":       _r2(line_item_discount_sum),
        "wallet_amount":                _r2(wallet_amount),
        "manual_discount_amount":       _r2(manual_discount_amount),
        "gift_card_amount":             _r2(gift_card_amount),
        "reward_points_amount":         _r2(reward_points_amount),
        "options_amount":               _r2(options_amount),
        # Canonical items rollup.
        "items_sum_from_canonical":     _r2(items_sum_from_canonical),
        "items_count":                  len(items),
        "per_line_discount_breakdown":  per_line_discount_breakdown,
        # Current (buggy) reconstruction.
        "current_expected":  current_expected,
        "current_diff":      current_diff,
        # Adjustment-aware reconstruction.
        "adjustments_applied":            adjustments_applied,
        "reconstructed_with_adjustments":
            _r2(reconstructed_with_adjustments),
        "diff_after_adjustments":         diff_after_adjustments,
        "residual_unexplained":           residual_unexplained,
        # Diagnostics.
        "would_pass_totals_guard": (
            diff_after_adjustments is not None
            and abs(diff_after_adjustments) <= 0.01),
        "would_pass_current_check": (
            current_diff is not None and abs(current_diff) <= 0.01),
        "formula_notes":            formula_notes,
        "field_paths_used":         used,
        "read_only":                True,
        "no_qoyod_api_calls":       True,
        "no_db_writes":             True,
    }

    if include_raw_debug:
        # Trimmed to the amounts subtree + top-level discount keys —
        # never dump customer PII or full payload by default.
        raw_debug = {
            "raw_amounts_subtree": raw_amt,
            "raw_top_level_discount_keys": {
                k: raw_data.get(k) for k in (
                    "coupon", "coupons", "promotion", "promotions",
                    "special_offer", "special_offers",
                    "wallet", "store_credit",
                    "gift_card", "gift_certificate", "gift",
                    "reward_points", "loyalty_points", "points",
                    "manual_discount", "custom_discount",
                    "extra_discount",
                ) if raw_data.get(k) is not None},
            "canonical_extra_charges":
                canonical.get("extra_charges") or {},
            "canonical_cod_fee_amount":
                _to_float(canonical.get("cod_fee_amount")),
        }
        out["raw_debug"] = raw_debug

    return out


async def fetch_order_totals_breakdown(
    db,
    *,
    user_id: str,
    order_number: str,
    include_raw_debug: bool = False,
) -> dict:
    """Look up the LATEST inbox row for `order_number` and return the
    breakdown. Read-Only end-to-end."""
    q = {
        "user_id": user_id,
        "$or": [
            {"salla_order_number": order_number},
            {"salla_order_number": _to_float(order_number)},
            {"canonical_payload.order_number": order_number},
        ],
    }
    # Prefer the most recent trace if multiple exist.
    rows = await db.integration_inbox.find(
        q, {"_id": 0}).sort(
        [("received_at", -1), ("pipeline_started_at", -1)]).to_list(
        length=5)
    if not rows:
        return {
            "order_number": order_number,
            "found": False,
            "read_only": True,
            "no_qoyod_api_calls": True,
            "no_db_writes": True,
            "note": ("No integration_inbox row found for this "
                     "order_number in this tenant."),
        }
    breakdown = build_order_totals_breakdown(
        inbox_row=rows[0], include_raw_debug=include_raw_debug)
    breakdown["found"] = True
    breakdown["traces_available"] = len(rows)
    return breakdown
