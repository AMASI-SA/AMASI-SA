import pytest

from integrations.qoyod_manual.send import (
    ManualSendRefused,
    _assert_sar_currency,
    _build_invoice_payload,
    _prepare_sar_invoice_canon,
    _prepare_sar_invoice_canon_from_inbox,
)


@pytest.mark.parametrize("value", ["SAR", "sar", None, ""])
def test_assert_sar_currency_accepts_sar_and_missing_default(value):
    canon = {} if value is None else {"currency": value}
    assert _assert_sar_currency(canon) == "SAR"


@pytest.mark.parametrize("source_tax", [None, "0", "8", "10", "15"])
def test_sar_order_always_extracts_fifteen_percent_from_total(source_tax):
    row = {
        "raw_payload": {
            "data": {
                "reference_id": "12345",
                "amounts": {
                    "tax": {"percent": source_tax},
                    "total": {"amount": 115, "currency": "SAR"},
                },
            },
        },
    }
    prepared = _prepare_sar_invoice_canon(
        canon={
            "order_number": "12345",
            "currency": "SAR",
            "total_amount": 115,
            "items": [],
        },
        row=row,
    )

    assert prepared["total_amount"] == 115
    assert prepared["_qoyod_tax_percent"] == 15.0
    assert prepared["_qoyod_tax_policy"]["policy"] == (
        "all_orders_total_inclusive_15"
    )


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


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield row
        return iterate()


class _FakeInbox:
    def __init__(self, rows):
        self.rows = rows

    def find(self, *_args, **_kwargs):
        return _FakeCursor(list(self.rows))


class _FakeDb:
    def __init__(self, rows, unified=None):
        self.integration_inbox = _FakeInbox(rows)
        self.unified_orders = _FakeUnifiedOrders(unified)


class _FakeUnifiedOrders:
    def __init__(self, row=None):
        self.row = row

    async def find_one(self, *_args, **_kwargs):
        return self.row


@pytest.mark.asyncio
async def test_foreign_order_recovers_fx_and_tax_from_older_trace():
    canon = {
        "order_number": "275590587",
        "currency": "AED",
        "total_amount": 264.76,
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
    newest = _foreign_row(
        currency="AED", rate="1.01978901", tax_percent="0.00",
        total=264.76, subtotal=213.77, shipping=50.99,
    )
    newest["id"] = "newest-status-webhook"
    del newest["raw_payload"]["data"]["exchange_rate"]
    historical = _foreign_row(
        currency="AED", rate="1.01978901", tax_percent="0.00",
        total=264.76, subtotal=213.77, shipping=50.99,
    )
    historical["id"] = "order-created-webhook"

    prepared = await _prepare_sar_invoice_canon_from_inbox(
        _FakeDb([newest, historical]),
        canon=canon,
        representative_row=newest,
        user_id="merchant-1",
        order_number="275590587",
    )

    assert prepared["currency"] == "SAR"
    assert prepared["total_amount"] == 270.00
    assert prepared["_qoyod_tax_percent"] == 15.0
    assert prepared["_qoyod_fx"]["source"] == "salla_order.exchange_rate"


@pytest.mark.asyncio
async def test_foreign_order_recovers_fx_from_unified_order_details():
    canon = {
        "order_number": "275590587",
        "currency": "AED",
        "total_amount": 264.76,
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
    stripped = _foreign_row(
        currency="AED", rate="1.01978901", tax_percent="0.00",
        total=264.76, subtotal=213.77, shipping=50.99,
    )
    del stripped["raw_payload"]["data"]["exchange_rate"]
    authoritative = _foreign_row(
        currency="AED", rate="1.01978901", tax_percent="0.00",
        total=264.76, subtotal=213.77, shipping=50.99,
    )["raw_payload"]["data"]
    db = _FakeDb(
        [stripped],
        unified={"raw_by_source": {"salla_direct": authoritative}},
    )

    prepared = await _prepare_sar_invoice_canon_from_inbox(
        db,
        canon=canon,
        representative_row=stripped,
        user_id="qoyod-tenant",
        orders_user_id="orders-tenant",
        order_number="275590587",
    )

    assert prepared["total_amount"] == 270.00
    assert prepared["_qoyod_tax_percent"] == 15.0
    assert prepared["_qoyod_fx"]["source"].startswith("unified_orders.")


@pytest.mark.asyncio
async def test_foreign_order_rejects_fx_from_representative_other_owner():
    canon = {
        "order_number": "275590587",
        "currency": "AED",
        "total_amount": 264.76,
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
    representative = _foreign_row(
        currency="AED", rate="1.01978901", tax_percent="0.00",
        total=264.76, subtotal=213.77, shipping=50.99,
    )
    representative["user_id"] = "main"

    with pytest.raises(ManualSendRefused) as exc_info:
        await _prepare_sar_invoice_canon_from_inbox(
            _FakeDb([]),
            canon=canon,
            representative_row=representative,
            user_id="qoyod-tenant",
            orders_user_id="orders-tenant",
            order_number="275590587",
        )

    assert exc_info.value.code == "foreign_currency_accounting_facts_missing"
    assert exc_info.value.extra["qoyod_write_performed"] is False

def test_aed_zero_tax_order_becomes_inclusive_fifteen_percent_without_total_change(
):
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
    assert prepared["_qoyod_tax_percent"] == 15.0
    assert prepared["_qoyod_fx"]["original_total"] == 264.76
    assert prepared["_qoyod_fx"]["rate"] == "1.01978901"
    assert prepared["_qoyod_fx"]["source_tax_percent"] == 0.0
    assert prepared["_qoyod_fx"]["tax_policy"] == (
        "all_orders_total_inclusive_15"
    )

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
        line["tax_percent"] == 15.0
        for line in payload["invoice"]["line_items"]
    )
    assert breakdown["tax_percent"] == 15.0
    assert payload["invoice"]["notes"] == "فاتورة للطلب رقم 275590587"
    assert "Mezan" not in payload["invoice"]["notes"]
    assert "Plan-B" not in payload["invoice"]["notes"]
    assert "tax_policy" not in payload["invoice"]["notes"]
    assert breakdown["currency_conversion"]["original_total"] == 264.76
    assert breakdown["currency_conversion"]["converted_total"] == 270.0


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


@pytest.mark.parametrize(
    "tax_percent", [None, "0.00", "5.00", "8.00", "15.00"]
)
def test_foreign_order_always_extracts_fifteen_percent_from_gross(tax_percent):
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

    prepared = _prepare_sar_invoice_canon(canon=canon, row=row)
    assert prepared["total_amount"] == 100.00
    assert prepared["_qoyod_tax_percent"] == 15.0
