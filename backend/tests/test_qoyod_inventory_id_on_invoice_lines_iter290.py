"""Iter-290 — Qoyod /invoices requires `inventory_id` on every line item.

Why this test exists
────────────────────
Production order 268756329 reached PRODUCT_RESOLVED successfully but
failed at FAILED_INVOICE with:

    POST /invoices → 422
    "inventory id missing in a line item"

Qoyod's invoice validator demands `inventory_id` on every line —
even when the product is `type=service` or `is_non_stock=true`.
The operator creates one default warehouse in Qoyod (e.g.
"مستودع افتراضي - ميزان") and Mezan stamps its id on every line
via `settings.default_inventory_id`.

Coverage:
    1) build_invoice_payload stamps inventory_id on every line.
    2) Blank setting → field is OMITTED (preflight refuses upstream).
    3) Preflight fails fast when default_inventory_id is missing.
    4) Inventory id reaches every line — not just the first.
"""
from __future__ import annotations

from integrations.qoyod.invoice_builder import build_invoice_payload
from integrations.qoyod.preflight import run as preflight_run


_DTO = {
    "order_id":       "268756329",
    "order_number":   "268756329",
    "order_status":   "completed",
    "currency":       "SAR",
    "total_amount":   290.63,
    "subtotal":       304,
    "tax_amount":     0,
    "shipping_amount": 0,
    "discount_amount": 34.9,
    "items_count":    3,
    "payment_method": "mada",
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

_RESOLUTIONS = [
    {"sku": "AMS11961", "qoyod_product_id": "P-1"},
    {"sku": "AMS11738", "qoyod_product_id": "P-2"},
    {"sku": "AMS10553", "qoyod_product_id": "P-3"},
]

_BASE_SETTINGS = {
    "tax_mode":              "customer_first",
    "default_branch_id":     "BR-1",
    "default_inventory_id":  "INV-42",
    "default_tax_id":        "1",
    "zero_tax_id":           "1",
    "invoice_trigger_statuses": ["completed"],
    "payment_method_mapping": [
        {"salla_method": "mada", "qoyod_account_id": "ACC-9"},
    ],
}


# ─── Payload builder ────────────────────────────────────────────────
def test_invoice_payload_stamps_inventory_id_on_every_line():
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="C-1",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_BASE_SETTINGS,
    )
    lines = pl["invoice"]["line_items"]
    assert len(lines) == 3
    for ln in lines:
        assert ln["inventory_id"] == "INV-42", (
            f"every line must carry inventory_id; got {ln!r}"
        )


def test_invoice_payload_omits_inventory_id_when_setting_blank():
    """When `default_inventory_id` is empty/whitespace we drop the
    key entirely. Preflight is responsible for refusing the row
    upstream so we never actually POST a bare line."""
    settings = {**_BASE_SETTINGS, "default_inventory_id": "   "}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="C-1",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    for ln in pl["invoice"]["line_items"]:
        assert "inventory_id" not in ln


def test_invoice_payload_omits_inventory_id_when_setting_missing():
    settings = {k: v for k, v in _BASE_SETTINGS.items()
                if k != "default_inventory_id"}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="C-1",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    for ln in pl["invoice"]["line_items"]:
        assert "inventory_id" not in ln


# ─── Preflight refusal ──────────────────────────────────────────────
def test_preflight_refuses_when_default_inventory_id_missing():
    settings = {k: v for k, v in _BASE_SETTINGS.items()
                if k != "default_inventory_id"}
    res = preflight_run(
        dto_dict=_DTO, settings=settings,
        qoyod_customer_id="C-1",
        product_resolutions=_RESOLUTIONS,
    )
    assert res.passed is False
    codes = [f["code"] for f in res.failures]
    assert "missing_default_inventory_id" in codes


def test_preflight_refuses_when_default_inventory_id_blank():
    settings = {**_BASE_SETTINGS, "default_inventory_id": "   "}
    res = preflight_run(
        dto_dict=_DTO, settings=settings,
        qoyod_customer_id="C-1",
        product_resolutions=_RESOLUTIONS,
    )
    codes = [f["code"] for f in res.failures]
    assert "missing_default_inventory_id" in codes


def test_preflight_passes_when_default_inventory_id_set():
    res = preflight_run(
        dto_dict=_DTO, settings=_BASE_SETTINGS,
        qoyod_customer_id="C-1",
        product_resolutions=_RESOLUTIONS,
    )
    codes = [f["code"] for f in res.failures]
    assert "missing_default_inventory_id" not in codes
