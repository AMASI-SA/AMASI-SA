"""Regression coverage for Tabby statement-level payout fees."""
import os
import sys

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from settlements_import.parsers.tabby import parse  # noqa: E402


HEADERS = [
    "Order Number", "Sale/Refund Date", "Merchant Name", "Merchant Code",
    "Product Type", "Type", "Currency", "Order Amount", "Commission Rate",
    "Refundable Commission", "Non Refundable Commission", "Fixed Fee",
    "Total Fee", "VAT Amount", "VAT Rate", "Total Deduction",
    "Transferred amount", "Transfer Date",
]


def _tabby_workbook_with_payout_fee():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SR"
    ws.append(["Settlement Report"])
    ws.append(["Date", "14/09/2026"])
    ws.append(["Statement #", "Tabby20260914SAR"])
    while ws.max_row < 10:
        ws.append([])
    ws.append(HEADERS)
    ws.append([
        "1001", "2026-09-11", "Merchant", "default",
        "Installments: 3 Months", "sale", "SAR", 100.0, 6.99,
        7.0, 2.0, 1.0, 10.0, 1.5, 0.15, 11.5, 88.5, "2026-09-14",
    ])
    ws.append([
        "1001", "2026-09-12", "Merchant", "default",
        "Installments: 3 Months", "partial refund", "SAR", -20.0, 6.99,
        -2.0, 0.0, 0.0, -2.0, -0.3, 0.15, -2.3, -17.7, "2026-09-14",
    ])
    ws.append([
        None, None, None, None, "Payout fee", None, None, None, None,
        None, None, None, 6.0, 0.9, 0.15, None, -6.9, None,
    ])
    ws.append([
        None, None, None, None, None, None, None, 80.0, None,
        None, None, None, 8.0, 1.2, None, 9.2, 63.9, None,
    ])
    return wb


def test_tabby_payout_fee_is_in_statement_totals_not_order_entries():
    wb = _tabby_workbook_with_payout_fee()
    result = parse(wb)
    totals = result["totals"]

    assert totals["rows"] == 2
    assert totals["gross"] == 100.0
    assert totals["refund_partial"] == 20.0
    assert totals["fees"] == 8.0
    assert totals["fees_vat"] == 1.2
    assert totals["settlement_fee"] == 6.0
    assert totals["settlement_fee_vat"] == 0.9
    assert totals["net"] == 63.9
    assert all(entry["order_number"].isdigit() for entry in result["entries"])
