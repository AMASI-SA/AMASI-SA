import pytest

import payment_gateway_metrics
from accounts_routes import _order_expected_amount_sar
from order_engine.mapper import map_salla_order
from order_currency import (
    currency_code,
    hydrate_order_currency_fields,
    order_amount_to_sar,
    order_total_sar,
    salla_order_currency_fields,
    summarize_orders_sar,
)
from orders_db import _merge_into, orders_to_parsed
from reconciliation_routes import _central_expected_for_account


def _salla_order(*, total, currency, rate=None):
    order = {
        "reference_id": "286153601",
        "amounts": {
            "total": {"amount": total, "currency": currency},
        },
    }
    if rate is not None:
        order["exchange_rate"] = {
            "base_currency": "SAR",
            "exchange_currency": currency,
            "rate": rate,
        }
    return order


@pytest.mark.parametrize(
    ("total", "currency", "rate", "expected_sar"),
    [
        ("302.20", "QAR", "1.02914116", 311.01),
        ("558.01", "AED", "1.02150472", 570.01),
        ("28.84", "KWD", "12.17048414", 351.00),
        ("340.88", "QAR", "1.02968160", 351.00),
    ],
)
def test_salla_gcc_orders_use_the_order_exchange_rate(
    total,
    currency,
    rate,
    expected_sar,
):
    fields = salla_order_currency_fields(
        _salla_order(total=total, currency=currency, rate=rate)
    )

    assert fields["original_total_amount"] == float(total)
    assert fields["original_currency"] == currency
    assert fields["exchange_rate_to_sar"] == rate
    assert fields["total_amount_sar"] == expected_sar
    assert fields["accounting_currency"] == "SAR"
    assert fields["currency_conversion_status"] == "verified"


def test_mezan2_order_mapper_keeps_native_currency_and_adds_sar_total():
    raw = _salla_order(
        total="302.20",
        currency={"code": "QAR"},
        rate="1.02914116",
    )
    raw.update({
        "id": 286153601,
        "date": "2026-09-15T12:00:00+03:00",
    })
    raw["exchange_rate"]["exchange_currency"] = {"code": "QAR"}

    order = map_salla_order(raw)

    assert order.totals.currency == "QAR"
    assert order.totals.total == 302.20
    assert order.totals.total_sar == 311.01
    assert order.totals.exchange_rate_to_sar == "1.02914116"
    assert order.totals.conversion_status == "verified"


def test_sar_order_is_not_converted_twice():
    order = {
        "currency": "SAR",
        "total_amount": 166.40,
        "total_amount_sar": 166.40,
        "exchange_rate_to_sar": "9.99",
        "currency_conversion_status": "native_sar",
    }

    assert order_total_sar(order) == 166.40
    assert order_amount_to_sar(25, order) == 25.00
    assert currency_code("ر.س") == "SAR"


def test_promoted_sar_total_wins_without_double_conversion():
    order = {
        "currency": "AED",
        "total_amount": 558.01,
        "total_amount_sar": 570.01,
        "exchange_rate_to_sar": "1.02150472",
        "accounting_currency": "SAR",
        "currency_conversion_status": "verified",
    }

    assert order_total_sar(order) == 570.01


def test_sparse_salla_update_cannot_erase_verified_foreign_currency_fields():
    existing = {
        "currency": "QAR",
        "original_total_amount": 302.20,
        "original_currency": "QAR",
        "exchange_rate_to_sar": "1.02914116",
        "total_amount_sar": 311.01,
        "accounting_currency": "SAR",
        "currency_conversion_status": "verified",
        "currency_conversion_source": "salla_order.exchange_rate",
        "last_make_update_at": "2026-09-15T12:00:00Z",
    }
    sparse = {
        "currency": "SAR",
        "original_currency": "SAR",
        "accounting_currency": "SAR",
        "currency_conversion_status": "missing_total",
        "currency_conversion_source": None,
        "total_amount_sar": None,
    }

    merged = _merge_into(existing, sparse, "salla_direct")

    assert merged["currency"] == "QAR"
    assert merged["original_currency"] == "QAR"
    assert merged["exchange_rate_to_sar"] == "1.02914116"
    assert merged["total_amount_sar"] == 311.01
    assert merged["currency_conversion_status"] == "verified"


def test_unverified_foreign_order_fails_closed():
    order = _salla_order(total="302.20", currency="QAR")
    fields = salla_order_currency_fields(order)

    assert fields["total_amount_sar"] is None
    assert fields["currency_conversion_status"] == "unverified_rate"
    assert order_total_sar({"currency": "QAR", "total_amount": 302.20}) is None


def test_historical_order_is_hydrated_from_narrow_salla_projection():
    orders = [{
        "order_number": "286153601",
        "currency": "QAR",
        "total_amount": 302.20,
    }]
    projected = [{
        "order_number": "286153601",
        "raw_by_source": {
            "salla_direct": _salla_order(
                total="302.20",
                currency="QAR",
                rate="1.02914116",
            ),
        },
    }]

    hydrate_order_currency_fields(orders, projected)

    assert orders[0]["total_amount"] == 302.20
    assert orders[0]["currency"] == "QAR"
    assert orders[0]["total_amount_sar"] == 311.01
    assert "raw_by_source" not in orders[0]


def test_accounting_parser_and_summary_use_sar_not_native_numbers():
    orders = [
        {
            "order_number": "286153601",
            "currency": "QAR",
            "total_amount": 302.20,
            "total_amount_sar": 311.01,
            "exchange_rate_to_sar": "1.02914116",
            "accounting_currency": "SAR",
            "currency_conversion_status": "verified",
            "payment_method": "credit_card",
        },
        {
            "order_number": "286153452",
            "currency": "AED",
            "total_amount": 558.01,
            "total_amount_sar": 570.01,
            "exchange_rate_to_sar": "1.02150472",
            "accounting_currency": "SAR",
            "currency_conversion_status": "verified",
            "payment_method": "credit_card",
        },
    ]

    summary = summarize_orders_sar(orders)
    parsed = orders_to_parsed(orders)

    assert summary["total_sar"] == 881.02
    assert summary["conversion_complete"] is True
    assert parsed["total_sales"] == 881.02
    assert parsed["payment_methods"][0]["total_sales"] == 881.02
    assert parsed["orders_individual"][0]["total_amount"] == 311.01
    assert parsed["accounting_currency"] == "SAR"
    assert parsed["currency_conversion"]["complete"] is True


def test_accounting_summary_reports_an_unverified_foreign_order():
    orders = [{
        "order_number": "missing-rate",
        "currency": "BHD",
        "total_amount": 10,
        "payment_method": "credit_card",
    }]

    summary = summarize_orders_sar(orders)
    parsed = orders_to_parsed(orders)

    assert summary["total_sar"] is None
    assert summary["known_total_sar"] == 0
    assert summary["missing_order_numbers"] == ["missing-rate"]
    assert parsed["currency_conversion"]["complete"] is False
    assert parsed["currency_conversion"]["missing_order_numbers"] == [
        "missing-rate"
    ]


def test_account_balance_uses_sar_order_total_or_actual_sar_settlement():
    converted_order = {
        "currency": "AED",
        "total_amount": 558.01,
        "total_amount_sar": 570.01,
        "exchange_rate_to_sar": "1.02150472",
        "accounting_currency": "SAR",
        "currency_conversion_status": "verified",
    }
    actual_settlement = {
        **converted_order,
        "payment_fee_status": "actual",
        "actual_net_amount": 551.44,
    }

    assert _order_expected_amount_sar(converted_order) == 570.01
    assert _order_expected_amount_sar(actual_settlement) == 551.44
    assert _order_expected_amount_sar({
        "currency": "KWD",
        "total_amount": 28.84,
    }) is None


def test_reconciliation_does_not_replace_unknown_sar_expected_with_zero():
    expected, orders, actual = _central_expected_for_account([{
        "key": "mada",
        "net": None,
        "orders_count": 1,
        "actual_orders_count": 0,
        "currency_conversion_complete": False,
    }], "salla")

    assert expected is None
    assert orders == 1
    assert actual == 0


class _AsyncRows:
    def __init__(self, rows):
        self._rows = iter(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._rows)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _GatewayOrders:
    def __init__(self, rows):
        self._rows = rows

    def aggregate(self, _pipeline):
        return _AsyncRows(self._rows)


@pytest.mark.asyncio
async def test_payment_gateway_metrics_estimate_from_exact_sar_total(monkeypatch):
    raw = _salla_order(
        total="302.20",
        currency="QAR",
        rate="1.02914116",
    )
    db = type("Db", (), {
        "unified_orders": _GatewayOrders([{
            "order_number": "286153601",
            "order_status": "completed",
            "total_amount": 302.20,
            "currency": "QAR",
            "payment_method": "mada",
            "payment_fee_status": "estimated",
            "raw_by_source": {"salla_direct": raw},
        }]),
    })()

    async def fake_settings(_db, _user_id):
        return {"report_included_statuses": []}

    async def fake_policy(_db, _user_id):
        return {"completed": "confirmed"}

    monkeypatch.setattr(
        payment_gateway_metrics,
        "ensure_user_settings",
        fake_settings,
    )
    import order_status_policy
    monkeypatch.setattr(order_status_policy, "get_policy_map", fake_policy)

    result = await payment_gateway_metrics.compute_metrics(db, "merchant")
    mada = next(row for row in result["rows"] if row["key"] == "mada")

    assert mada["gross"] == 311.01
    assert result["totals"]["gross"] == 311.01
    assert result["totals"]["salla_reference_gross"] == 311.01
    assert result["totals"]["accounting_currency"] == "SAR"
    assert result["totals"]["currency_conversion_complete"] is True


@pytest.mark.asyncio
async def test_payment_gateway_metrics_do_not_zero_an_unverified_rate(monkeypatch):
    db = type("Db", (), {
        "unified_orders": _GatewayOrders([{
            "order_number": "missing-rate",
            "order_status": "completed",
            "total_amount": 10,
            "currency": "BHD",
            "payment_method": "mada",
            "payment_fee_status": "estimated",
        }]),
    })()

    async def fake_settings(_db, _user_id):
        return {"report_included_statuses": []}

    async def fake_policy(_db, _user_id):
        return {"completed": "confirmed"}

    monkeypatch.setattr(
        payment_gateway_metrics,
        "ensure_user_settings",
        fake_settings,
    )
    import order_status_policy
    monkeypatch.setattr(order_status_policy, "get_policy_map", fake_policy)

    result = await payment_gateway_metrics.compute_metrics(db, "merchant")
    mada = next(row for row in result["rows"] if row["key"] == "mada")

    assert mada["gross"] is None
    assert mada["net"] is None
    assert mada["currency_conversion_complete"] is False
    assert result["totals"]["gross"] is None
    assert result["totals"]["currency_conversion_complete"] is False
    assert result["totals"]["unverified_order_numbers"] == ["missing-rate"]
