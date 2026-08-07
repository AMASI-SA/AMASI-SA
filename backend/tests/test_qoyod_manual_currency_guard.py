import pytest

from integrations.qoyod_manual.send import (
    ManualSendRefused,
    _assert_sar_currency,
    _build_invoice_payload,
    _prepare_sar_invoice_canon,
)


@pytest.mark.parametrize("value", ["SAR", "sar", None, ""])
def test_assert_sar_currency_accepts_sar_and_missing_default(value):
    canon = {} if value is None else {"currency": value}
    assert _assert_sar_currency(canon) == "SAR"


@pytest.mark.parametrize("value", ["AED", "QAR", {"code": "AED"}])
def test_assert_sar_currency_blocks_unverified_currency(value):
    with pytest.raises(ManualSendRefused) as exc_info:
        _assert_sar_currency({"currency": value})

    exc = exc_info.value
    assert exc.code == "unsupported_invoice_currency"
    assert exc.extra["qoyod_write_performed"] is False
    assert exc.extra["currency"] in {"AED", "QAR"}



def _foreign_row(*, currency, rate, tax_percent, total, subtotal, shipping=0):
    return {
        "raw_payload": {
            "event": "order.updated",
            "data": {
                "reference_id": 275590587,
                "exchange_rate": {
                    "base_currency": "SAR",
                    "exchange_currency": currency,
                    "rate": rate,
                },
                "amounts": {
                    "sub_total": {"amount": subtotal, "currency": currency},
                    "shipping_cost": {
                        "amount": shipping, "currency": currency
                    },
                    "tax": {
                        "percent": str(tax_percent),
                        "amount": {"amount": 0, "currency": currency},
                    },
                    "total": {"amount": total, "currency": currency},
                },
            },
        },
    }


def test_aed_zero_tax_order_uses_salla_rate_and_stays_zero_tax():
    canon = {
        "order_number": "275590587",
        "order_id": "802684702",
        "currency": "AED",
        "total_amount": 264.76,
        "subtotal": 213.77,
        "tax_amount": 0,
        "shipping_amount": 0,
        "items": [{
            "sku": "AMS13031",
            "name": "عباية",
            "quantity": 1,
            "unit_price": 213.77,
            "tax_amount": 0,
            "discount_amount": 0,
            "total": 213.77,
        }],
    }
    row = _foreign_row(
        currency="AED",
        rate="1.01978901",
        tax_percent="0.00",
        total=264.76,
        subtotal=213.77,
        shipping=50.99,
    )

    prepared = _prepare_sar_invoice_canon(canon=canon, row=row)

    assert prepared["currency"] == "SAR"
    assert prepared["total_amount"] == 270.00
    assert prepared["subtotal"] == 218.00
    assert prepared["shipping_amount"] == 52.00
    assert prepared["items"][0]["unit_price"] == 218.00
    assert prepared["_qoyod_tax_percent"] == 0.0
    assert prepared["_qoyod_fx"]["original_total"] == 264.76
    assert prepared["_qoyod_fx"]["rate"] == "1.01978901"

    payload, expected_total, breakdown = _build_invoice_payload(
        canon=prepared,
        contact_id=1,
        line_resolutions={"AMS13031": 10},
        settings={
            "qoyod_tax_percent": 15,
            "default_shipping_product_id": 20,
        },
        send_date_iso="2026-08-08",
    )

    assert expected_total == 270.00
    assert payload["invoice"]["currency_code"] == "SAR"
    assert all(
        line["tax_percent"] == 0.0
        for line in payload["invoice"]["line_items"]
    )
    assert breakdown["tax_percent"] == 0.0
    assert "original=264.76 AED" in payload["invoice"]["notes"]
    assert "converted=270.0 SAR" in payload["invoice"]["notes"]


def test_foreign_order_preserves_explicit_fifteen_percent_tax():
    canon = {
        "order_number": "275590587",
        "currency": "QAR",
        "total_amount": 115,
        "items": [{
            "sku": "QAR15",
            "name": "منتج",
            "quantity": 1,
            "unit_price": 100,
            "tax_amount": 15,
            "discount_amount": 0,
            "total": 115,
        }],
    }
    row = _foreign_row(
        currency="QAR",
        rate="1.03",
        tax_percent="15.00",
        total=115,
        subtotal=100,
    )

    prepared = _prepare_sar_invoice_canon(canon=canon, row=row)
    payload, expected_total, breakdown = _build_invoice_payload(
        canon=prepared,
        contact_id=1,
        line_resolutions={"QAR15": 10},
        settings={"qoyod_tax_percent": 0},
        send_date_iso="2026-08-08",
    )

    assert prepared["total_amount"] == 118.45
    assert expected_total == 118.45
    assert payload["invoice"]["line_items"][0]["tax_percent"] == 15.0
    assert breakdown["tax_percent"] == 15.0


def test_foreign_order_without_salla_rate_is_blocked_before_qoyod_write():
    canon = {
        "order_number": "275590587",
        "currency": "AED",
        "total_amount": 264.76,
        "items": [],
    }
    row = _foreign_row(
        currency="AED",
        rate="1.01978901",
        tax_percent="0.00",
        total=264.76,
        subtotal=264.76,
    )
    del row["raw_payload"]["data"]["exchange_rate"]

    with pytest.raises(ManualSendRefused) as exc_info:
        _prepare_sar_invoice_canon(canon=canon, row=row)

    assert exc_info.value.code == "foreign_currency_exchange_rate_unverified"
    assert exc_info.value.extra["qoyod_write_performed"] is False


@pytest.mark.parametrize("tax_percent", [None, "5.00"])
def test_foreign_order_requires_explicit_zero_or_fifteen_tax(tax_percent):
    canon = {
        "order_number": "275590587",
        "currency": "AED",
        "total_amount": 100,
        "items": [],
    }
    row = _foreign_row(
        currency="AED",
        rate="1.00",
        tax_percent=tax_percent,
        total=100,
        subtotal=100,
    )

    with pytest.raises(ManualSendRefused) as exc_info:
        _prepare_sar_invoice_canon(canon=canon, row=row)

    assert exc_info.value.code == "foreign_currency_tax_unverified"
    assert exc_info.value.extra["qoyod_write_performed"] is False
