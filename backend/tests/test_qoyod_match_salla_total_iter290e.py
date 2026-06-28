"""Iter-290e — Qoyod 15% Match Salla Total.

Business requirement
────────────────────
Production orders 268756329 and 268833109 were technically COMPLETED
in Qoyod but the invoice TOTAL didn't match what the customer paid
on Salla:

    268756329:  Salla 290.63  vs  Qoyod 309.47   (Δ +18.84)
    268833109:  Salla  96.23  vs  Qoyod 102.47   (Δ  +6.24)

Root cause: Salla's effective per-line tax (~8%) differs from Qoyod's
standard tax_percent (15%). Sending Salla's net + 15% over-taxes.

Fix (`invoice_builder.py` — `invoice_total_policy=match_salla_total`):
  For each line:
    target_gross  = item.total            (what Salla shows)
    target_net    = target_gross / 1.15   (Qoyod will mark up by 15%)
    discount      = item.unit_price*qty - target_net
  Send to Qoyod:
    unit_price  = item.unit_price   (auditable to Salla, verbatim)
    discount    = <computed>
    tax_percent = 15
  Edge cases:
    * item.total == 0  →  discount = full base
    * discount < 0     →  fallback to shrunk unit_price, discount=0

Coverage
────────
1. Order 268833109 (current Salla=96.23) → Qoyod invoice computes to 96.23.
2. Order 268756329 (current Salla=290.63) → Qoyod invoice computes to 290.63.
3. Fully-discounted line (item.total=0) handled cleanly.
4. Negative discount fallback engages with anomalous Salla price.
5. Diagnostics block carries salla_total / expected / difference /
   salla_tax_percent_detected / qoyod_tax_percent_used.
6. Diagnostics are returned OUTSIDE the `invoice` dict so they never
   reach Qoyod.
7. Legacy policy (non-match_salla_total) still works unchanged.
"""
from __future__ import annotations

from integrations.qoyod.invoice_builder import build_invoice_payload


def _resolutions(skus):
    return [{"sku": s, "qoyod_product_id": str(i + 1)}
            for i, s in enumerate(skus)]


def _settings():
    return {
        "default_branch_id":     "1",
        "default_inventory_id":  "1",
        "invoice_total_policy":  "match_salla_total",
        "qoyod_tax_percent":     15,
    }


# ── Order 268833109 (Salla=96.23) ────────────────────────────────────
_DTO_268833109 = {
    "order_id":       "268833109",
    "order_number":   "268833109",
    "currency":       "SAR",
    "total_amount":   96.23,
    "subtotal":       104.0,
    "tax_amount":     7.13,
    "discount_amount": 14.9,
    "items": [
        {"sku": "AMS11961", "name": "تغليف", "quantity": 1,
         "unit_price": 5,  "tax_amount": 0,    "discount_amount": 5,
         "total": 0},
        {"sku": "AMS11841", "name": "اسواره", "quantity": 1,
         "unit_price": 99, "tax_amount": 7.13, "discount_amount": 9.9,
         "total": 96.23},
    ],
}


def test_order_268833109_qoyod_total_matches_salla_total_within_10_cents():
    pl = build_invoice_payload(
        dto_dict=_DTO_268833109, qoyod_customer_id="109",
        product_resolutions=_resolutions(["AMS11961", "AMS11841"]),
        invoice_date=None, settings=_settings(),
    )
    d = pl["_diagnostics"]
    assert d["pricing_mode"] == "match_salla_total"
    assert d["qoyod_tax_percent_used"] == 15
    assert d["salla_total"] == 96.23
    assert d["expected_qoyod_total"] == 96.23, (
        f"expected Qoyod to compute exactly 96.23; got {d['expected_qoyod_total']}. "
        f"diagnostics={d}")
    assert abs(d["difference"]) <= 0.10


def test_order_268833109_lines_have_correct_discount_math():
    pl = build_invoice_payload(
        dto_dict=_DTO_268833109, qoyod_customer_id="109",
        product_resolutions=_resolutions(["AMS11961", "AMS11841"]),
        invoice_date=None, settings=_settings(),
    )
    lines = pl["invoice"]["line_items"]
    # Line 1 — fully discounted: discount equals full base (5).
    assert lines[0]["unit_price"] == 5.0
    assert lines[0]["discount"] == 5.0
    assert lines[0]["tax_percent"] == 15
    assert lines[0]["discount_type"] == "amount"
    # Line 2: target_net = 96.23/1.15 ≈ 83.6783; discount ≈ 99 - 83.6783 = 15.3217
    assert lines[1]["unit_price"] == 99.0
    assert abs(lines[1]["discount"] - 15.3217) < 0.01


# ── Order 268756329 (Salla=290.63, 3 lines) ──────────────────────────
_DTO_268756329 = {
    "order_id":       "268756329",
    "order_number":   "268756329",
    "currency":       "SAR",
    "total_amount":   290.63,
    "subtotal":       304,
    "tax_amount":     0,
    "discount_amount": 34.9,
    "items": [
        {"sku": "AMS11961", "name": "تغليف", "quantity": 1,
         "unit_price": 5,   "tax_amount": 0,    "discount_amount": 5,
         "total": 0},
        {"sku": "AMS11738", "name": "طقم",   "quantity": 1,
         "unit_price": 199, "tax_amount": 14.33, "discount_amount": 19.9,
         "total": 193.43},
        {"sku": "AMS10553", "name": "بروش",  "quantity": 1,
         "unit_price": 100, "tax_amount": 7.2,   "discount_amount": 10,
         "total": 97.2},
    ],
}


def test_order_268756329_qoyod_total_matches_salla_total_within_10_cents():
    pl = build_invoice_payload(
        dto_dict=_DTO_268756329, qoyod_customer_id="109",
        product_resolutions=_resolutions(["AMS11961","AMS11738","AMS10553"]),
        invoice_date=None, settings=_settings(),
    )
    d = pl["_diagnostics"]
    assert d["salla_total"] == 290.63
    assert d["expected_qoyod_total"] == 290.63, (
        f"expected 290.63; got {d['expected_qoyod_total']}. diag={d}")
    assert abs(d["difference"]) <= 0.10


def test_order_268756329_no_negative_discounts():
    pl = build_invoice_payload(
        dto_dict=_DTO_268756329, qoyod_customer_id="109",
        product_resolutions=_resolutions(["AMS11961","AMS11738","AMS10553"]),
        invoice_date=None, settings=_settings(),
    )
    for li in pl["invoice"]["line_items"]:
        assert li["discount"] >= 0, f"negative discount on line: {li!r}"


# ── Fully-discounted (item.total == 0) ───────────────────────────────
def test_zero_total_line_sets_discount_to_full_base():
    dto = {
        "currency": "SAR", "total_amount": 0,
        "items": [{"sku": "X", "name": "Free", "quantity": 2,
                   "unit_price": 10, "tax_amount": 0,
                   "discount_amount": 20, "total": 0}],
    }
    pl = build_invoice_payload(
        dto_dict=dto, qoyod_customer_id="1",
        product_resolutions=_resolutions(["X"]),
        invoice_date=None, settings=_settings(),
    )
    line = pl["invoice"]["line_items"][0]
    # unit_price=10, qty=2, total=0 → target_net=0, discount=20
    assert line["unit_price"] == 10.0
    assert line["discount"] == 20.0
    assert pl["_diagnostics"]["expected_qoyod_total"] == 0.0


# ── Negative-discount fallback ───────────────────────────────────────
def test_negative_discount_triggers_fallback_to_shrunk_unit_price():
    """Anomalous case: Salla shows total > unit_price*qty*1.15 — this
    shouldn't happen in practice but the fallback must preserve the
    invariant `expected_qoyod_total ≈ salla_total`."""
    dto = {
        "currency": "SAR", "total_amount": 200.0,
        "items": [{"sku": "X", "name": "Anomaly", "quantity": 1,
                   "unit_price": 50, "tax_amount": 0,
                   "discount_amount": 0, "total": 200.0}],
    }
    pl = build_invoice_payload(
        dto_dict=dto, qoyod_customer_id="1",
        product_resolutions=_resolutions(["X"]),
        invoice_date=None, settings=_settings(),
    )
    line = pl["invoice"]["line_items"][0]
    # target_net = 200/1.15 ≈ 173.91; original_base = 50 → discount
    # would be -123.91 < 0 → fallback: unit_price = 173.91, discount=0
    assert line["discount"] == 0.0
    assert abs(line["unit_price"] - 173.9130) < 0.01
    assert abs(pl["_diagnostics"]["expected_qoyod_total"] - 200.0) < 0.10


# ── Diagnostics shape ────────────────────────────────────────────────
def test_diagnostics_block_present_and_outside_invoice_dict():
    pl = build_invoice_payload(
        dto_dict=_DTO_268833109, qoyod_customer_id="109",
        product_resolutions=_resolutions(["AMS11961", "AMS11841"]),
        invoice_date=None, settings=_settings(),
    )
    # Critical: diagnostics MUST NOT live inside `invoice` (otherwise
    # they leak to Qoyod's HTTP body).
    assert "_diagnostics" not in pl["invoice"]
    d = pl["_diagnostics"]
    assert set(d.keys()) >= {
        "pricing_mode", "salla_total", "expected_qoyod_total",
        "difference", "salla_tax_percent_detected",
        "qoyod_tax_percent_used", "line_diagnostics",
    }
    # Salla's effective rate detection on 268833109:
    # net_sum = (0-0) + (96.23-7.13) = 89.1 ; tax = 7.13 → 8.00%
    assert d["salla_tax_percent_detected"] == 8.00


# ── Iter-290c canonical shape preserved ──────────────────────────────
def test_payload_still_carries_iter290c_canonical_shape():
    pl = build_invoice_payload(
        dto_dict=_DTO_268833109, qoyod_customer_id="109",
        product_resolutions=_resolutions(["AMS11961", "AMS11841"]),
        invoice_date=None, settings=_settings(),
    )
    inv = pl["invoice"]
    assert inv["status"] == "Approved"
    assert inv["inventory_id"] == 1
    assert isinstance(inv["contact_id"], int)
    for li in inv["line_items"]:
        assert "inventory_id" not in li
        assert "tax_id" not in li
        assert li["discount_type"] == "amount"
        assert li["tax_percent"] == 15
        assert isinstance(li["product_id"], int)


# ── Legacy policy still works (back-compat) ──────────────────────────
def test_legacy_policy_uses_passthrough_unit_price_and_discount():
    settings = {**_settings(), "invoice_total_policy": "legacy_passthrough"}
    pl = build_invoice_payload(
        dto_dict=_DTO_268833109, qoyod_customer_id="109",
        product_resolutions=_resolutions(["AMS11961", "AMS11841"]),
        invoice_date=None, settings=settings,
    )
    lines = pl["invoice"]["line_items"]
    # Legacy: unit_price = item.unit_price, discount = item.discount_amount
    assert lines[0]["discount"] == 5.0    # from canonical
    assert lines[1]["discount"] == 9.9    # from canonical
    assert pl["_diagnostics"]["pricing_mode"] == "legacy_passthrough"
