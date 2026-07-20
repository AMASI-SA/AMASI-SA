"""COD fee flow from Orders V2 into the manual Qoyod invoice."""
from __future__ import annotations

import pytest

from integrations.qoyod_manual.send import (
    _build_invoice_payload,
    _overlay_order_engine_facts,
)
from order_engine.mapper import map_salla_order


def _salla_order(*, amounts_extra: dict | None = None, total: float = 315.88,
                 payment: dict | None = None) -> dict:
    amounts = {
        "sub_total": {"amount": 268.59, "currency": "SAR"},
        "tax": {"percent": 8, "amount": {"amount": 23.40, "currency": "SAR"}},
        "shipping": {"amount": 0, "currency": "SAR"},
        "total": {"amount": total, "currency": "SAR"},
    }
    amounts.update(amounts_extra or {})
    return {
        "id": 273187928,
        "reference_id": 273187928,
        "date": "2026-07-20T12:00:00+03:00",
        "status": "completed",
        "payment_method": "cod",
        "payment": payment or {},
        "amounts": amounts,
        "items": [
            {
                "id": 1,
                "name": "Item A",
                "sku": "AMS13006",
                "quantity": 1,
                "amounts": {
                    "price": {"amount": 135.00},
                    "total": {"amount": 145.80},
                },
            },
            {
                "id": 2,
                "name": "Item B",
                "sku": "AMS11237",
                "quantity": 1,
                "amounts": {
                    "price": {"amount": 151.00},
                    "total": {"amount": 163.08},
                },
            },
        ],
    }


@pytest.mark.parametrize(
    ("field", "expected_source"),
    [
        ("cash_on_delivery", "amounts.cash_on_delivery"),
        ("cod_fee", "amounts.cod_fee"),
        ("payment_fee", "amounts.payment_fee"),
    ],
)
def test_orders_v2_preserves_explicit_cod_fee(field: str, expected_source: str):
    order = map_salla_order(
        _salla_order(amounts_extra={field: {"amount": 6.48, "currency": "SAR"}})
    )

    assert order.totals.cod_fee == 6.48
    assert order.totals.cod_fee_tax == 0.52
    assert order.totals.cod_fee_total == 7.0
    assert order.totals.cod_fee_source == expected_source


def test_orders_v2_reads_cod_fee_from_nested_payment_shape():
    order = map_salla_order(
        _salla_order(payment={
            "cash_on_delivery": {"amount": 6.48, "currency": "SAR"},
        })
    )

    assert order.totals.cod_fee == 6.48
    assert order.totals.cod_fee_tax == 0.52
    assert order.totals.cod_fee_total == 7.0
    assert order.totals.cod_fee_source == "payment.cash_on_delivery"


def test_orders_v2_does_not_double_count_product_choice_price_as_order_options():
    raw_order = _salla_order()
    raw_order["items"][0]["options"] = [
        {
            "name": "الاسم",
            "values": [{"name": "أحمد", "price": {"amount": 15}}],
        }
    ]

    order = map_salla_order(raw_order)

    assert order.totals.options == 0.0


def test_orders_v2_never_infers_cod_fee_from_total_residual():
    order = map_salla_order(_salla_order(total=315.88))

    assert order.totals.cod_fee == 0.0
    assert order.totals.cod_fee_source is None


def test_cod_fee_reaches_qoyod_as_explicit_separate_line():
    canon = {
        "total_amount": 315.88,
        "shipping_amount": 0.0,
        "cod_fee_amount": 0.0,
        "items": [
            {
                "sku": "AMS13006",
                "name": "Item A",
                "quantity": 1,
                "unit_price": 135.00,
                "total": 145.80,
            },
            {
                "sku": "AMS11237",
                "name": "Item B",
                "quantity": 1,
                "unit_price": 151.00,
                "total": 163.08,
            },
        ],
    }
    enriched = _overlay_order_engine_facts(
        canon,
        {
            "payment_method": "cod",
            "cod_fee_amount": 7.0,
            "cod_fee_source": "amounts.cash_on_delivery",
        },
    )

    payload, expected_total, breakdown = _build_invoice_payload(
        canon=enriched,
        contact_id=1,
        line_resolutions={"AMS13006": 13006, "AMS11237": 11237},
        settings={
            "qoyod_tax_percent": 15,
            "default_cod_fee_product_id": 700,
        },
        send_date_iso="2026-07-20",
    )

    assert expected_total == 315.88
    assert breakdown["cod_fee"]["included"] is True
    assert breakdown["cod_fee"]["salla_declared_amount"] == 7.0
    assert len(payload["invoice"]["line_items"]) == 3
    assert payload["invoice"]["line_items"][-1]["product_id"] == 700


def test_overlay_accepts_trusted_order_engine_fee_without_legacy_audit_path():
    canon = {"cod_fee_amount": 0.0}
    enriched = _overlay_order_engine_facts(
        canon,
        {
            "payment_method": "cod",
            "cod_fee_amount": 7.0,
            "cod_fee_source": None,
            "cod_fee_is_explicit": True,
        },
    )

    assert enriched["cod_fee_amount"] == 7.0
    assert enriched["cod_fee_source_path"] == (
        "order_engine.totals.cod_fee_total"
    )


def test_overlay_refuses_untrusted_fee_without_source_or_explicit_flag():
    canon = {"cod_fee_amount": 0.0}
    enriched = _overlay_order_engine_facts(
        canon,
        {
            "payment_method": "cod",
            "cod_fee_amount": 7.0,
            "cod_fee_source": None,
            "cod_fee_is_explicit": False,
        },
    )

    assert enriched["cod_fee_amount"] == 0.0


def test_overlay_does_not_treat_non_cod_payment_fee_as_cod_fee():
    canon = {"cod_fee_amount": 0.0}
    enriched = _overlay_order_engine_facts(
        canon,
        {
            "payment_method": "credit_card",
            "cod_fee_amount": 7.0,
            "cod_fee_source": "amounts.payment_fee",
        },
    )

    assert enriched["cod_fee_amount"] == 0.0
