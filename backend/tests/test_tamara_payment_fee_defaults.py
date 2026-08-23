"""Regression coverage for statement-derived Tamara fees (2026-08)."""
from __future__ import annotations

import openpyxl
import pytest

from bnpl.settlements_service import _capture_fee_components
import payment_gateway_metrics as gateway_metrics
from payment_methods import (
    DEFAULT_PAYMENT_METHODS,
    PAYMENT_FEE_DEFAULTS_VERSION,
    migrate_payment_method_defaults,
)
from settlements_import.parsers.tamara import parse


def _default(name: str) -> dict:
    return next(row for row in DEFAULT_PAYMENT_METHODS if row["name"] == name)


def test_tamara_unified_default_matches_verified_statements():
    assert _default("تمارا") == {
        "name": "تمارا",
        "commission_percent": 6.99,
        "fixed_fee": 1.5,
        "vat_percent": 15.0,
    }
    assert PAYMENT_FEE_DEFAULTS_VERSION == "salla-tamara-tabby-statements-2026-08-v3"


def test_migration_upgrades_only_untouched_tamara_default():
    current = [
        {"name": "تمارا", "commission_percent": 6.99, "fixed_fee": 0.0, "vat_percent": 15.0},
        # An explicit merchant edit must survive the version upgrade.
        {"name": "تابي", "commission_percent": 4.25, "fixed_fee": 0.75, "vat_percent": 15.0},
    ]

    migrated, changed = migrate_payment_method_defaults(
        current,
        current_version="salla-invoices-2026-08-v1",
    )
    by_name = {row["name"]: row for row in migrated}

    assert changed is True
    assert by_name["تمارا"]["fixed_fee"] == 1.5
    assert by_name["تابي"]["commission_percent"] == 4.25
    assert by_name["تابي"]["fixed_fee"] == 0.75


@pytest.mark.parametrize(
    ("amount", "expected_fee", "expected_vat"),
    [
        (316.52, 23.62, 3.54),
        (251.72, 19.10, 2.87),
        (186.92, 14.57, 2.19),
        (137.32, 11.10, 1.67),
    ],
)
def test_tamara_rounds_fee_then_vat_per_capture(
    amount: float,
    expected_fee: float,
    expected_vat: float,
):
    fee, vat = _capture_fee_components(
        "tamara", amount, 0.0699, 1.50, 0.15,
    )
    assert fee == expected_fee
    assert vat == expected_vat


def _statement_workbook() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "20260822"
    ws.append([
        "Transaction Date DD/MM/YYYY",
        "Tamara Order ID",
        "Merchant Order ID",
        "Refund Reason",
        "Payment Type",
        "Order Status",
        "Currency",
        "Order Amount",
        "Event",
        "Event Amount",
        "Event Date DD/MM/YYYY",
        "Tamara Fixed Fees",
        "Tamara Variable Fees %",
        "Tamara Variable Fees",
        "Total Fees",
        "VAT Collected by Tamara",
        "Total Payable to Merchant",
        "Installments",
    ])
    ws.append([
        "15/08/2026", "CAP-1", "ORDER-1", None,
        "PAY_BY_INSTALMENTS", "fully_captured", "SAR",
        251.72, "Captured", 251.72, "15/08/2026",
        1.50, 0.0699, 17.60, 19.10, 2.87, 229.75, 4,
    ])
    ws.append([
        "01/08/2026", "REF-1", "ORDER-2", "partial refund",
        "PAY_BY_INSTALMENTS", "partially_refunded", "SAR",
        197.72, "Refunded", -100.00, "16/08/2026",
        None, None, None, None, None, -100.00, 4,
    ])
    ws.append([
        "05/08/2026", "CAN-1", "ORDER-3", None,
        "PAY_BY_INSTALMENTS", "canceled", "SAR",
        220.32, "Canceled", 220.32, "16/08/2026",
        1.50, 0.0, 0.0, 1.50, 0.23, -1.73, 4,
    ])
    return wb


def test_tamara_parser_keeps_cancellation_fee_out_of_captured_gross():
    wb = _statement_workbook()
    try:
        result = parse(wb)
    finally:
        wb.close()

    entries = {row["order_number"]: row for row in result["entries"]}
    assert entries["ORDER-1"]["event_type"] == "sale"
    assert entries["ORDER-2"]["event_type"] == "refund"
    assert entries["ORDER-3"]["event_type"] == "canceled_fee"
    assert entries["ORDER-3"]["actual_gross_amount"] == 0.0
    assert entries["ORDER-3"]["actual_payment_fee"] == 1.5
    assert entries["ORDER-3"]["actual_payment_vat"] == 0.23

    assert result["totals"] == {
        "rows": 3,
        "gross": 251.72,
        "fees": 20.60,
        "fees_vat": 3.10,
        "net": 128.02,
        "refund_full": 0.0,
        "refund_partial": 100.0,
        "canceled_count": 1,
        "canceled_amount": 220.32,
        "canceled_fees": 1.5,
        "canceled_fees_vat": 0.23,
    }


@pytest.mark.asyncio
async def test_gateway_metrics_keep_tamara_refund_and_cancellation_fees(monkeypatch):
    class AsyncRows:
        def __init__(self, rows):
            self.rows = iter(rows)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.rows)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class UnifiedOrders:
        def aggregate(self, _pipeline):
            return AsyncRows([
                {
                    "order_status": "completed", "total_amount": 251.72,
                    "payment_method": "تمارا", "actual_payment_method": "",
                    "payment_fee_status": "estimated",
                },
                {
                    "order_status": "refunded", "total_amount": 186.92,
                    "payment_method": "تمارا", "actual_payment_method": "",
                    "payment_fee_status": "estimated",
                },
                {
                    "order_status": "cancelled", "total_amount": 220.32,
                    "payment_method": "تمارا", "actual_payment_method": "",
                    "payment_fee_status": "estimated",
                },
            ])

    class FakeDb:
        unified_orders = UnifiedOrders()

    async def fake_settings(_db, _user_id):
        return {
            "payment_methods": DEFAULT_PAYMENT_METHODS,
            "report_included_statuses": [],
        }

    async def fake_policy(_db, _user_id):
        return {
            "completed": "confirmed",
            "refunded": "refunded",
            "cancelled": "cancelled",
        }

    monkeypatch.setattr(gateway_metrics, "ensure_user_settings", fake_settings)
    import order_status_policy
    monkeypatch.setattr(order_status_policy, "get_policy_map", fake_policy)

    result = await gateway_metrics.compute_metrics(FakeDb(), "merchant")
    tamara = next(row for row in result["rows"] if row["key"] == "tamara")

    # Confirmed 19.10 + refunded capture 14.57 + cancellation fixed 1.50.
    assert tamara["fees"] == 35.17
    # 2.87 + 2.19 + 0.23.
    assert tamara["fees_vat"] == 5.29
    assert tamara["refund_full"] == 186.92
    assert tamara["cancelled_orders_count"] == 1
