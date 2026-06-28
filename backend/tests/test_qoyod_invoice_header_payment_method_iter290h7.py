"""Iter-290h.7 — Invoice header carries `payment_method: "10"` (Cash).

Decision (user, 2026-06-29):
Always populate the invoice header's display-only payment-method
field with ZATCA code "10" (Cash / نقدي), regardless of the
upstream Salla payment method. The actual settlement still flows
through `POST /invoice_payments` with the operator-mapped
`account_id`, so books reconcile correctly.

Why a constant, not a mapping per Salla method?
  • The header field is purely cosmetic — it shows up in the قيود
    invoice list under "طريقة الدفع".
  • The user explicitly does NOT want per-method labels (tabby,
    tamara, mada, bank transfer) in this column.
  • Cash ("10") matches their existing manual workflow.

This file pins the field so the next time someone re-derives the
invoice payload, they can't accidentally drop it. It also guards
against the field being mistakenly reused for the `/invoice_payments`
`account_id` flow.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integrations.qoyod.invoice_builder import build_invoice_payload


def _base_dto() -> dict:
    return {
        "order_id":      "269077005",
        "order_number":  "269077005",
        "currency":      "SAR",
        "total_amount":  131.92,
        "subtotal":      114.71,
        "tax_amount":    17.21,
        "shipping_amount": 0.0,
        "discount_amount": 0.0,
        "items": [{
            "sku":           "AMS12116",
            "name":          "اسواره بالاسم حسب الطلب",
            "quantity":      1,
            "unit_price":    114.71,
            "tax_amount":    17.21,
            "discount_amount": 0.0,
            "total":         131.92,
        }],
    }


def _base_settings() -> dict:
    return {
        "default_inventory_id": "1",
        "default_branch_id":    "1",
        "shipping_product_id":  "42",
        "pricing_mode":         "match_salla_total",
        "tax_mode":             "customer_first",
    }


@pytest.fixture
def product_resolutions() -> list:
    return [{"sku": "AMS12116", "qoyod_product_id": "38"}]


@pytest.mark.parametrize("salla_payment_method", [
    "tabby_installment", "tamara_installment", "mada",
    "apple_pay", "credit_card", "bank_transfer", "cash",
    "salla_payments",  None,
])
def test_invoice_header_payment_method_always_cash_code_10(
    salla_payment_method, product_resolutions,
):
    """The قيود invoice header MUST carry payment_method="10" (Cash)
    regardless of the upstream Salla payment method."""
    dto = _base_dto()
    dto["payment_method"] = salla_payment_method

    payload = build_invoice_payload(
        dto_dict=dto,
        qoyod_customer_id="109",
        product_resolutions=product_resolutions,
        invoice_date=datetime(2026, 6, 29, tzinfo=timezone.utc),
        settings=_base_settings(),
    )
    body = payload["invoice"]
    assert body["payment_method"] == "10", (
        "Invoice header payment_method must be '10' (Cash) — see "
        "Iter-290h.7. Sending other ZATCA codes would change the "
        "'طريقة الدفع' column in قيود's invoice list, which the "
        "user explicitly does not want.")


def test_invoice_header_payment_method_field_is_a_string():
    """ZATCA payment-means codes are sent as strings on قيود's
    /invoices endpoint per the documented schema. Sending an int
    has been observed to be silently dropped by some Qoyod
    validator versions."""
    payload = build_invoice_payload(
        dto_dict=_base_dto(),
        qoyod_customer_id="109",
        product_resolutions=[{"sku": "AMS12116", "qoyod_product_id": "38"}],
        invoice_date=datetime(2026, 6, 29, tzinfo=timezone.utc),
        settings=_base_settings(),
    )
    assert isinstance(payload["invoice"]["payment_method"], str)


def test_invoice_payment_method_is_independent_of_account_id():
    """The header `payment_method` is DISPLAY-ONLY. It MUST NOT be
    confused with the `/invoice_payments` body's `account_id` (which
    settles the invoice against a Chart-of-Accounts entry). This
    test guards against any future code that wires them together."""
    payload = build_invoice_payload(
        dto_dict=_base_dto(),
        qoyod_customer_id="109",
        product_resolutions=[{"sku": "AMS12116", "qoyod_product_id": "38"}],
        invoice_date=datetime(2026, 6, 29, tzinfo=timezone.utc),
        settings=_base_settings(),
    )
    body = payload["invoice"]
    assert "account_id" not in body
    assert "account"    not in body
    # And the header payment_method is still "10".
    assert body["payment_method"] == "10"
