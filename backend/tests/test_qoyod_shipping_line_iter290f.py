"""Iter-290f — Shipping line + preflight reconciliation skip.

Production order 268860160 (Salla=131.92 = items 106.92 + shipping 25.00)
was rejected by the old `invoice_receipt_reconciliation` preflight check
because:
  • The estimator didn't account for `shipping_amount`.
  • The new `match_salla_total` policy has its own pre-POST guard.

Fix:
  1. Preflight skips the old estimator when `policy == "match_salla_total"`.
  2. `build_invoice_payload` appends a shipping line (using
     `default_shipping_product_id`) so the invoice total includes shipping.
  3. Preflight refuses the row when shipping > 0 AND
     `default_shipping_product_id` is missing.
"""
from __future__ import annotations

from integrations.qoyod.invoice_builder import build_invoice_payload
from integrations.qoyod.preflight import run as preflight_run


_DTO_268860160 = {
    "order_id":       "268860160",
    "order_number":   "268860160",
    "order_status":   "completed",
    "currency":       "SAR",
    "total_amount":   131.92,
    "subtotal":       104,
    "tax_amount":     0,
    "shipping_amount": 23.15,
    "discount_amount": 5,
    "items_count":    2,
    "payment_method": "mada",
    "items": [
        {"sku": "AMS11961", "name": "تغليف", "quantity": 1,
         "unit_price": 5,  "tax_amount": 0,    "discount_amount": 5,
         "total": 0},
        {"sku": "AMS11841", "name": "اسواره", "quantity": 1,
         "unit_price": 99, "tax_amount": 7.92, "discount_amount": 0,
         "total": 106.92},
    ],
}

_SETTINGS_OK = {
    "tax_mode":              "customer_first",
    "invoice_total_policy":  "match_salla_total",
    "qoyod_tax_percent":     15,
    "default_branch_id":     "1",
    "default_inventory_id":  "1",
    "default_shipping_product_id": "999",
    "default_tax_id":        "1",
    "zero_tax_id":           "1",
    "invoice_trigger_statuses": ["completed"],
    "payment_method_mapping": [
        {"salla_method": "mada", "qoyod_account_id": "94"},
    ],
}

_RESOLUTIONS = [
    {"sku": "AMS11961", "qoyod_product_id": "39"},
    {"sku": "AMS11841", "qoyod_product_id": "40"},
]


# ─── Shipping line appended with correct math ───────────────────────
def test_shipping_line_added_when_amount_positive():
    pl = build_invoice_payload(
        dto_dict=_DTO_268860160, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_SETTINGS_OK,
    )
    lines = pl["invoice"]["line_items"]
    # 2 product lines + 1 shipping line
    assert len(lines) == 3
    shipping = lines[-1]
    assert shipping["product_id"] == 999
    assert shipping["description"] == "شحن (Shipping)"
    assert shipping["tax_percent"] == 15
    assert shipping["discount_type"] == "amount"


def test_qoyod_invoice_total_matches_salla_131_92():
    pl = build_invoice_payload(
        dto_dict=_DTO_268860160, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_SETTINGS_OK,
    )
    d = pl["_diagnostics"]
    assert d["salla_total"] == 131.92
    assert d["expected_qoyod_total"] == 131.92, (
        f"expected 131.92; got {d['expected_qoyod_total']}. diag={d}")
    assert abs(d["difference"]) <= 0.10


def test_shipping_line_omitted_when_amount_zero():
    dto = {**_DTO_268860160, "shipping_amount": 0,
           "total_amount": 106.92}  # no shipping → Salla=items only
    pl = build_invoice_payload(
        dto_dict=dto, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_SETTINGS_OK,
    )
    lines = pl["invoice"]["line_items"]
    assert len(lines) == 2  # only the two product lines


# ─── Preflight refuses missing shipping_product_id ──────────────────
def test_preflight_refuses_when_shipping_positive_but_setting_missing():
    settings = {k: v for k, v in _SETTINGS_OK.items()
                if k != "default_shipping_product_id"}
    res = preflight_run(
        dto_dict=_DTO_268860160, settings=settings,
        qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
    )
    codes = [f["code"] for f in res.failures]
    assert "missing_default_shipping_product_id" in codes


def test_preflight_passes_when_shipping_zero_even_without_product_id():
    settings = {k: v for k, v in _SETTINGS_OK.items()
                if k != "default_shipping_product_id"}
    dto = {**_DTO_268860160, "shipping_amount": 0, "total_amount": 106.92}
    res = preflight_run(
        dto_dict=dto, settings=settings,
        qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
    )
    codes = [f["code"] for f in res.failures]
    assert "missing_default_shipping_product_id" not in codes


# ─── Preflight: old customer_first estimator SKIPPED under new policy ─
def test_preflight_skips_legacy_reconciliation_under_match_salla_policy():
    """The old `invoice_receipt_reconciliation` check (Iter-285 estimator)
    did not account for shipping → would false-positive on order 268860160.
    Iter-290f skips it when match_salla_total is active (Iter-290e has its
    own shipping-aware guard)."""
    res = preflight_run(
        dto_dict=_DTO_268860160, settings=_SETTINGS_OK,
        qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
    )
    codes = [f["code"] for f in res.failures]
    assert "invoice_total_mismatch_with_receipt" not in codes
