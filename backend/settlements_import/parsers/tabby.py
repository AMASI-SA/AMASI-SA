"""Tabby settlement-report parser.

Real sample layout (verified — 2026-06):
    Rows 1-8: Preamble  (Date, Statement#, Company Name)
    Row 11: Detail header
        col1: Order Number               ← matches unified_orders.order_number
        col2: Sale/Refund Date
        col3: Merchant Name
        col4: Merchant Code
        col5: Product Type (Installments: X Months)
        col6: Type (sale | refund)
        col7: Currency
        col8: Order Amount
        col9: Commission Rate (%)
        col10: Refundable Commission
        col11: Non Refundable Commission
        col12: Fixed Fee
        col13: Total Fee
        col14: VAT Amount
        col15: VAT Rate
        col16: Total Deduction
        col17: Transferred amount
        col18: Transfer Date
    Rows 12..M: data
    Trailing rows: "Payout fee" line + grand-total row. Skipped.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import openpyxl

from ..utils import to_float, to_str


HEADER_NEEDLE = "Refundable Commission"


def _find_header_row(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    for idx, row in enumerate(rows):
        for cell in row or []:
            if cell and HEADER_NEEDLE.lower() in str(cell).lower():
                return idx, _map_columns(row)
    raise ValueError("لم يتم العثور على رأس جدول التفاصيل في ملف تابي.")


def _map_columns(header_row: list[Any]) -> dict[str, int]:
    needles = {
        "order_number": "Order Number",
        "event_date": "Sale/Refund Date",
        "merchant_name": "Merchant Name",
        "merchant_code": "Merchant Code",
        "product_type": "Product Type",
        "event_type": "Type",
        "currency": "Currency",
        "order_amount": "Order Amount",
        "commission_rate": "Commission Rate",
        "refundable_commission": "Refundable Commission",
        "non_refundable_commission": "Non Refundable Commission",
        "fixed_fee": "Fixed Fee",
        "total_fee": "Total Fee",
        "vat_amount": "VAT Amount",
        "vat_rate": "VAT Rate",
        "total_deduction": "Total Deduction",
        "transferred_amount": "Transferred amount",
        "transfer_date": "Transfer Date",
    }
    out: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        s = str(cell).strip().lower()
        for field, needle in needles.items():
            if needle.lower() == s or needle.lower() in s:
                out.setdefault(field, i)
    return out


def _read_preamble(rows: list[list[Any]], stop: int) -> dict:
    info: dict = {}
    for r in rows[:stop]:
        labels = [str(c).strip() if c is not None else "" for c in (r or [])]
        for j, cell in enumerate(labels):
            cl = cell.lower()
            if cl == "statement #":
                info["statement_id"] = next((labels[k] for k in range(j + 1, len(labels)) if labels[k]), "")
            elif cl == "date":
                info["statement_date_raw"] = next((labels[k] for k in range(j + 1, len(labels)) if labels[k]), "")
            elif cl == "company name":
                info["company_name"] = next((labels[k] for k in range(j + 1, len(labels)) if labels[k]), "")
    return info


def _parse_dt(v: Any) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse(workbook: openpyxl.Workbook) -> dict:
    ws = workbook.active
    title = ws.title or ""
    rows = [list(r) for r in ws.iter_rows(values_only=True)]

    header_idx, cols = _find_header_row(rows)
    preamble = _read_preamble(rows, header_idx)
    statement_ref = preamble.get("statement_id") or title

    required = ["order_number", "order_amount", "total_fee", "transferred_amount"]
    missing = [k for k in required if k not in cols]
    if missing:
        raise ValueError(f"ملف تابي يفتقد أعمدة: {missing}.")

    entries: list[dict] = []
    total_gross = 0.0
    total_fees = 0.0
    total_vat = 0.0
    total_net = 0.0
    total_refund_full = 0.0
    total_refund_partial = 0.0

    for r in rows[header_idx + 1:]:
        if not r:
            continue
        order_no = to_str(r[cols["order_number"]] if cols["order_number"] < len(r) else "")
        if not order_no:
            continue
        order_no = re.sub(r"\.0+$", "", order_no)

        # Skip totals/summary trailing rows (Payout fee or grand totals)
        if not order_no.isdigit():
            continue

        ev_type = to_str(r[cols.get("event_type", -1)]).lower() if cols.get("event_type") is not None else "sale"
        gross = to_float(r[cols["order_amount"]])
        fees = to_float(r[cols["total_fee"]])
        vat = to_float(r[cols.get("vat_amount", -1)]) if cols.get("vat_amount") is not None else 0.0
        net = to_float(r[cols["transferred_amount"]])
        product_type = to_str(r[cols.get("product_type", -1)]) if cols.get("product_type") is not None else ""
        event_date = _parse_dt(r[cols.get("event_date", -1)] if cols.get("event_date") is not None else None)
        transfer_date = _parse_dt(r[cols.get("transfer_date", -1)] if cols.get("transfer_date") is not None else None)
        commission_rate = to_float(r[cols.get("commission_rate", -1)]) if cols.get("commission_rate") is not None else 0.0

        if gross == 0 and net == 0:
            continue

        is_refund = (ev_type == "refund") or (net < 0)
        # Tabby reports refunds as separate rows with Type=refund. The
        # file itself doesn't carry full vs partial flag — we infer:
        #   • |net| == gross  → full refund
        #   • |net| < gross   → partial refund
        refund_full = 0.0
        refund_partial = 0.0
        if is_refund:
            if abs(round(abs(net) - gross, 2)) < 0.51:
                refund_full = abs(net)
            else:
                refund_partial = abs(net)

        fee_rate = commission_rate  # Tabby already reports the rate (%) directly

        entries.append({
            "provider": "tabby",
            "order_number": order_no,
            "actual_payment_method": "tabby",
            "actual_gross_amount": gross,
            "actual_payment_fee": fees if not is_refund else 0.0,
            "actual_payment_vat": vat if not is_refund else 0.0,
            "actual_net_amount": net,
            "actual_fee_rate": fee_rate,
            "actual_refund_amount": refund_full,
            "actual_partial_refund_amount": refund_partial,
            "event_type": "refund" if is_refund else "sale",
            "settlement_reference": statement_ref,
            "settlement_date": transfer_date or event_date,
            "raw_product_type": product_type,
        })

        if is_refund:
            total_refund_full += refund_full
            total_refund_partial += refund_partial
            total_net += net
        else:
            total_gross += gross
            total_fees += fees
            total_vat += vat
            total_net += net

    return {
        "provider": "tabby",
        "header": {
            **preamble,
            "sheet_title": title,
            "currency": "SAR",
        },
        "entries": entries,
        "totals": {
            "rows": len(entries),
            "gross": round(total_gross, 2),
            "fees": round(total_fees, 2),
            "fees_vat": round(total_vat, 2),
            "net": round(total_net, 2),
            "refund_full": round(total_refund_full, 2),
            "refund_partial": round(total_refund_partial, 2),
        },
    }
