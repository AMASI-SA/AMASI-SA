"""Iter-290 + Iter-290c — `inventory_id` lives on the invoice ROOT.

History
───────
• Iter-290:  added inventory_id to every line item.
• Iter-290b: coerced inventory_id to int.
• Iter-290c: Qoyod apidoc proved inventory_id belongs on the INVOICE
             ROOT, not per line. Moving it surfaced via production
             rejection of order 268756329 even when every line carried
             a valid integer inventory_id.

These tests pin the current contract:
    invoice.inventory_id   → int  (at root)
    line.inventory_id      → DROPPED
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
    {"sku": "AMS11961", "qoyod_product_id": "39"},
    {"sku": "AMS11738", "qoyod_product_id": "40"},
    {"sku": "AMS10553", "qoyod_product_id": "41"},
]

_BASE_SETTINGS = {
    "tax_mode":              "customer_first",
    "default_branch_id":     "10",
    "default_inventory_id":  "1",
    "default_tax_id":        "1",
    "zero_tax_id":           "1",
    "invoice_trigger_statuses": ["completed"],
    "payment_method_mapping": [
        {"salla_method": "mada", "qoyod_account_id": "9"},
    ],
}


# ─── Iter-290c: inventory_id on ROOT, not lines ─────────────────────
def test_invoice_root_carries_integer_inventory_id():
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_BASE_SETTINGS,
    )
    inv = pl["invoice"]
    assert inv["inventory_id"] == 1
    assert isinstance(inv["inventory_id"], int)


def test_invoice_lines_do_NOT_carry_inventory_id():
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_BASE_SETTINGS,
    )
    for ln in pl["invoice"]["line_items"]:
        assert "inventory_id" not in ln, (
            f"Iter-290c moved inventory_id to root; line still has it: {ln!r}"
        )


def test_invoice_root_omits_inventory_id_when_setting_blank():
    settings = {**_BASE_SETTINGS, "default_inventory_id": "   "}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    assert "inventory_id" not in pl["invoice"]


def test_invoice_root_omits_inventory_id_when_setting_missing():
    settings = {k: v for k, v in _BASE_SETTINGS.items()
                if k != "default_inventory_id"}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    assert "inventory_id" not in pl["invoice"]


# ─── Iter-290c: status = "Approved" required on root ────────────────
def test_invoice_root_has_status_approved():
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_BASE_SETTINGS,
    )
    assert pl["invoice"]["status"] == "Approved"


# ─── Iter-290c: per-line shape per Qoyod docs example ───────────────
def test_invoice_lines_carry_tax_percent_not_tax_id():
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_BASE_SETTINGS,
    )
    for ln in pl["invoice"]["line_items"]:
        assert "tax_id" not in ln
        assert ln["tax_percent"] == 15


def test_invoice_lines_carry_discount_type_amount():
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_BASE_SETTINGS,
    )
    for ln in pl["invoice"]["line_items"]:
        assert ln["discount_type"] == "amount"


def test_invoice_lines_unit_price_is_net_from_salla():
    """Salla emits unit_price as net (excl. tax). Iter-290c sends it
    verbatim — Qoyod's tax_percent=15 produces the line total."""
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_BASE_SETTINGS,
    )
    lines = pl["invoice"]["line_items"]
    assert lines[0]["unit_price"] == 5.0
    assert lines[1]["unit_price"] == 199.0
    assert lines[2]["unit_price"] == 100.0


# ─── Iter-290c: type-safety on all ids ──────────────────────────────
def test_all_ids_on_payload_are_integers_not_strings():
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=_BASE_SETTINGS,
    )
    inv = pl["invoice"]
    assert isinstance(inv["contact_id"], int) and inv["contact_id"] == 109
    assert isinstance(inv["inventory_id"], int) and inv["inventory_id"] == 1
    assert isinstance(inv["branch_id"], int) and inv["branch_id"] == 10
    for ln, expected_pid in zip(inv["line_items"], [39, 40, 41]):
        assert isinstance(ln["product_id"], int), f"product_id must be int: {ln!r}"
        assert ln["product_id"] == expected_pid


def test_branch_id_omitted_when_setting_blank():
    settings = {**_BASE_SETTINGS, "default_branch_id": ""}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    assert "branch_id" not in pl["invoice"]


# ─── Preflight refusal ──────────────────────────────────────────────
def test_preflight_refuses_when_default_inventory_id_missing():
    settings = {k: v for k, v in _BASE_SETTINGS.items()
                if k != "default_inventory_id"}
    res = preflight_run(
        dto_dict=_DTO, settings=settings,
        qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
    )
    assert res.passed is False
    codes = [f["code"] for f in res.failures]
    assert "missing_default_inventory_id" in codes


def test_preflight_refuses_when_default_inventory_id_blank():
    settings = {**_BASE_SETTINGS, "default_inventory_id": "   "}
    res = preflight_run(
        dto_dict=_DTO, settings=settings,
        qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
    )
    codes = [f["code"] for f in res.failures]
    assert "missing_default_inventory_id" in codes


def test_preflight_passes_when_default_inventory_id_set():
    res = preflight_run(
        dto_dict=_DTO, settings=_BASE_SETTINGS,
        qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
    )
    codes = [f["code"] for f in res.failures]
    assert "missing_default_inventory_id" not in codes


# ─── Iter-290b: int coercion of inventory_id setting ────────────────
def test_coerces_string_inventory_id_to_int_at_root():
    settings = {**_BASE_SETTINGS, "default_inventory_id": "10"}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    assert pl["invoice"]["inventory_id"] == 10
    assert isinstance(pl["invoice"]["inventory_id"], int)


def test_coerces_int_inventory_id_unchanged_at_root():
    settings = {**_BASE_SETTINGS, "default_inventory_id": 7}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    assert pl["invoice"]["inventory_id"] == 7


def test_omits_inventory_id_when_value_non_numeric():
    settings = {**_BASE_SETTINGS, "default_inventory_id": "main-warehouse"}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    assert "inventory_id" not in pl["invoice"]


def test_trims_whitespace_around_numeric_string():
    settings = {**_BASE_SETTINGS, "default_inventory_id": "  10  "}
    pl = build_invoice_payload(
        dto_dict=_DTO, qoyod_customer_id="109",
        product_resolutions=_RESOLUTIONS,
        invoice_date=None, settings=settings,
    )
    assert pl["invoice"]["inventory_id"] == 10
