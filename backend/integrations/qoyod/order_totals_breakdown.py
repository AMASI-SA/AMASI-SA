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

from typing import Any, Iterable, Optional


# ── Tax Source Finder constants ─────────────────────────────────────
# Keys that STRONGLY indicate a tax-related numeric.
_TAX_KEY_NEEDLES: tuple[str, ...] = (
    "tax", "vat", "ضريبة",
)

# Paths we ALWAYS probe first — even when the walker doesn't reach
# them (e.g. because a parent node is empty).
_CANONICAL_TAX_PATHS: tuple[str, ...] = (
    "canonical_payload.tax_amount",
    "canonical_payload.order.tax_amount",
    "raw_payload.data.amounts.tax",
    "raw_payload.data.amounts.tax.amount",
    "raw_payload.data.amounts.vat",
    "raw_payload.data.amounts.vat.amount",
    "raw_payload.data.tax",
    "raw_payload.data.vat",
    "raw_payload.data.order.amounts.tax",
    "raw_payload.data.order.amounts.vat",
    "raw_payload.data.total.tax",
    "raw_payload.data.amounts.total.tax",
)

# Keys under integration_inbox that are LIKELY to hold a raw/snapshot
# copy of the Salla payload (varies by pipeline stage).
_SNAPSHOT_KEYS: tuple[str, ...] = (
    "raw_payload",
    "canonical_payload",
    "trace",
    "snapshot",
    "request_body",
    "webhook_body",
    "salla_webhook_payload",
    "normalized_order",
    "pre_dispatch_snapshot",
    "eligible_orders_snapshot",
)


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


# ── Tax Source Finder (Read-Only walker) ────────────────────────────
def _walk_tax_candidates(
    node: Any,
    path: str,
    out: list,
    *,
    max_depth: int = 12,
    max_candidates: int = 60,
    _depth: int = 0,
) -> None:
    """Recursively walk `node` collecting every (path, value) whose
    KEY name contains one of the tax needles. Emits ONLY numeric
    values or {amount: numeric} envelopes. Never emits strings,
    lists of scalars, or nested structures verbatim.

    Depth-limited and count-limited to keep the response small and
    to guarantee we NEVER surface PII or large payload fragments.
    """
    if _depth > max_depth or len(out) >= max_candidates:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            child_path = f"{path}.{k}" if path else str(k)
            key_lower = str(k).lower()
            key_matches_tax = any(
                needle in key_lower for needle in _TAX_KEY_NEEDLES
            ) or ("ضريبة" in str(k))
            if key_matches_tax:
                # Emit if the value is a scalar OR a {amount:...} envelope.
                num = _to_float(v)
                if num is not None:
                    out.append({
                        "path":  child_path,
                        "value": _r2(num),
                        "key":   str(k)[:64],
                        "shape": "scalar" if not isinstance(v, dict)
                                 else "envelope",
                    })
                elif isinstance(v, dict):
                    # Envelope without an `amount` key — record the
                    # keys present (numeric only) so the auditor can
                    # decide. NO string values are ever exposed.
                    numeric_children = {
                        ck: _r2(_to_float(cv))
                        for ck, cv in v.items()
                        if _to_float(cv) is not None
                    }
                    if numeric_children:
                        out.append({
                            "path": child_path,
                            "value": None,
                            "key":  str(k)[:64],
                            "shape": "nested_numeric",
                            "numeric_children": numeric_children,
                        })
            # Recurse into containers regardless of key match.
            if isinstance(v, (dict, list)):
                _walk_tax_candidates(
                    v, child_path, out,
                    max_depth=max_depth,
                    max_candidates=max_candidates,
                    _depth=_depth + 1)
    elif isinstance(node, list):
        # Only descend a shallow amount into lists (items[] etc).
        for i, item in enumerate(node[:20]):   # cap at 20 elements
            _walk_tax_candidates(
                item, f"{path}[{i}]", out,
                max_depth=max_depth,
                max_candidates=max_candidates,
                _depth=_depth + 1)


def _rate_confidence(
    candidate: dict,
    *,
    derived_tax: Optional[float],
) -> tuple[str, str]:
    """Assign (confidence, reason) for a tax candidate.

        HIGH   — value matches derived_tax (within 0.01) AND key is
                 an exact tax marker.
        MEDIUM — key contains tax/vat but value does not match
                 derived_tax OR shape is envelope with no direct value.
        LOW    — key contains tax/vat but value is None / nested only.
    """
    key_lower = candidate.get("key", "").lower()
    exact_key = key_lower in {"tax", "vat", "tax_amount", "vat_amount"} \
        or candidate.get("key") == "ضريبة"
    val = candidate.get("value")
    if (derived_tax is not None and val is not None
            and abs(val - derived_tax) <= 0.01
            and exact_key):
        return ("high",
                "key is a canonical tax marker AND value matches "
                "derived_tax within 0.01.")
    if derived_tax is not None and val is not None \
            and abs(val - derived_tax) <= 0.01:
        return ("high",
                "value matches derived_tax within 0.01 "
                "(non-canonical key name).")
    if val is None and candidate.get("shape") == "nested_numeric":
        return ("low",
                "key mentions tax/vat but the node is a nested "
                "structure with no direct scalar value.")
    if val is not None and exact_key:
        return ("medium",
                "key is a canonical tax marker but its value does "
                "not match derived_tax.")
    return ("low",
            "key mentions tax/vat but value provenance is unclear.")


def _extract_probe_paths(inbox_row: dict) -> list[dict]:
    """Explicitly probe the well-known paths from `_CANONICAL_TAX_PATHS`
    even if the walker missed them (e.g. empty parents)."""
    hits: list[dict] = []
    for dotted in _CANONICAL_TAX_PATHS:
        cur: Any = inbox_row
        parts = dotted.split(".")
        ok = True
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok:
            val = _to_float(cur)
            if val is None and isinstance(cur, dict):
                val = _to_float(cur.get("amount"))
            if val is not None:
                hits.append({
                    "path":  dotted,
                    "value": _r2(val),
                    "key":   parts[-1][:64],
                    "shape": "probed",
                })
    return hits


def _find_tax_source_candidates(
    inbox_row: dict,
    *,
    derived_tax: Optional[float],
) -> list[dict]:
    """Read-Only tax provenance finder. Walks integration_inbox row
    plus canonical/raw/snapshot subtrees and returns every candidate
    numeric field whose key name mentions tax/vat/ضريبة.

    NEVER returns raw payload verbatim. NEVER emits string fields
    (PII-safe).
    """
    collected: list[dict] = []
    # 1. Probe well-known paths first (deterministic).
    collected.extend(_extract_probe_paths(inbox_row))
    # 2. Walk every snapshot-shaped subtree.
    for key in _SNAPSHOT_KEYS:
        subtree = inbox_row.get(key)
        if subtree is None:
            continue
        _walk_tax_candidates(
            subtree, f"{key}", collected,
            max_depth=12, max_candidates=60)
    # 3. Also walk the row itself once at shallow depth for tax
    #    markers that live at the top level.
    _walk_tax_candidates(
        {k: v for k, v in inbox_row.items()
         if k not in _SNAPSHOT_KEYS
         and not str(k).startswith("_")},
        "", collected, max_depth=3, max_candidates=20)

    # ── Dedupe by (path, value) ────────────────────────────────
    seen: set[tuple[str, Optional[float]]] = set()
    unique: list[dict] = []
    for c in collected:
        key = (c["path"], c.get("value"))
        if key in seen:
            continue
        seen.add(key)
        conf, reason = _rate_confidence(c, derived_tax=derived_tax)
        c = {**c, "confidence": conf, "reason": reason}
        unique.append(c)
    # ── Sort: high → medium → low, then by path length asc.
    _order = {"high": 0, "medium": 1, "low": 2}
    unique.sort(key=lambda x: (_order.get(x["confidence"], 3),
                               len(x["path"])))
    return unique


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

    # ── Derived Tax Reconciliation ──────────────────────────────
    # Iter-001k+ — Working hypothesis: the residual after subtracting
    # all detected discounts equals the missing tax that the
    # normalizer failed to lift into canonical. We compute the tax
    # value implied by pure arithmetic reconciliation and check
    # whether it matches the observed residual.
    #
    # derived_tax = official_total − (subtotal + shipping + options
    #                                 − order_level_discount)
    _subtotal   = subtotal or items_sum_from_canonical or 0.0
    _shipping   = shipping_amount or 0.0
    _options    = options_amount or 0.0
    _order_disc = order_level_discount or 0.0
    if salla_official_total is not None:
        derived_tax_from_reconciliation = _r2(
            salla_official_total
            - (_subtotal + _shipping + _options - _order_disc))
    else:
        derived_tax_from_reconciliation = None

    derived_tax_matches_residual = (
        derived_tax_from_reconciliation is not None
        and residual_unexplained is not None
        and abs(derived_tax_from_reconciliation
                - residual_unexplained) <= 0.01)

    if derived_tax_from_reconciliation is not None:
        corrected_expected_using_derived_tax = _r2(
            _subtotal + _shipping + _options
            - _order_disc + derived_tax_from_reconciliation)
        diff_using_derived_tax = _r2(
            (salla_official_total or 0.0)
            - corrected_expected_using_derived_tax)
    else:
        corrected_expected_using_derived_tax = None
        diff_using_derived_tax = None

    # ── Tax Source Finder ───────────────────────────────────────
    tax_source_candidates = _find_tax_source_candidates(
        inbox_row, derived_tax=derived_tax_from_reconciliation)

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
        ("derived_tax_from_reconciliation = official_total − "
         "(subtotal + shipping + options − order_level_discount). "
         "This is the arithmetic value the tax MUST have for the "
         "totals to reconcile — regardless of whether the normalizer "
         "captured it."),
        ("tax_source_candidates walks every snapshot in "
         "integration_inbox looking for keys containing "
         "tax/vat/ضريبة. Confidence=high when the value matches "
         "derived_tax within 0.01."),
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
        # ── Derived tax reconciliation ──────────────────────────
        "derived_tax_from_reconciliation":
            derived_tax_from_reconciliation,
        "derived_tax_matches_residual":   bool(
            derived_tax_matches_residual),
        "corrected_expected_using_derived_tax":
            corrected_expected_using_derived_tax,
        "diff_using_derived_tax":         diff_using_derived_tax,
        # ── Tax Source Finder (read-only walker) ────────────────
        "tax_source_candidates":          tax_source_candidates,
        "tax_source_summary": {
            "count":         len(tax_source_candidates),
            "high_conf_count": sum(
                1 for c in tax_source_candidates
                if c.get("confidence") == "high"),
            "any_high_confidence_match": any(
                c.get("confidence") == "high"
                for c in tax_source_candidates),
        },
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
