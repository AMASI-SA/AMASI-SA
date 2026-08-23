"""Tamara merchant-statement parser.

Real sample layout (verified — 2026-06):
    Rows 1-15: Merchant header preamble (statement ID, period, etc.)
    Rows 17-23: Summary block by payment type (skipped — we re-aggregate)
    Row 27: Detail header
        col1: Transaction Date DD/MM/YYYY
        col2: Tamara Order ID                ← UUID
        col3: Merchant Order ID              ← matches unified_orders.order_number
        col4: Refund Reason
        col5: Payment Type (PAY_BY_INSTALMENTS / Pay In Full)
        col6: Order Status (fully_captured / fully_refunded / partially_refunded)
        col7: Currency
        col8: Order Amount
        col9: Event (Captured / Refunded / Canceled)
        col10: Event Amount (NEGATIVE when refunded)
        col11: Event Date DD/MM/YYYY
        col12: Tamara Fixed Fees
        col13: Tamara Variable Fees %
        col14: Tamara Variable Fees
        col15: Total Fees
        col16: VAT Collected by Tamara
        col17: Total Payable to Merchant
        col18: Installments
    Rows 28..N: data

A single Merchant Order ID may appear MULTIPLE times when the order
has both a Captured event and one or more Refund events. We emit one
entry per event row; the importer collapses them when applying to
unified_orders so the final actual_* values reflect:
    net   = captured net + refund deductions + cancellation-fee deductions
    fees  = sum(positive Total Fees on captures and cancellations)
    vat   = sum(positive VAT only)
    refund_full / refund_partial — split by order status

Cancellation rows are fee adjustments, not sales.  In five verified 2026-08
merchant statements every cancellation carried only the SAR 1.50 fixed fee
plus SAR 0.23 VAT; it must reduce the payable without inflating captured gross.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import openpyxl

from ..utils import to_float, to_str


HEADER_NEEDLE = "Merchant Order ID"


def _find_header_row(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    """Locate the detail header row index (1-based-ish in the sheet
    layout but returned as 0-based here)."""
    for idx, row in enumerate(rows):
        for cell in row or []:
            if cell and HEADER_NEEDLE in str(cell):
                return idx, _map_columns(row)
    raise ValueError("لم يتم العثور على رأس جدول التفاصيل في ملف تمارا.")


def _map_columns(header_row: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    needles = {
        "transaction_date": "Transaction Date",
        "tamara_order_id": "Tamara Order ID",
        "merchant_order_id": "Merchant Order ID",
        "refund_reason": "Refund Reason",
        "payment_type": "Payment Type",
        "order_status": "Order Status",
        "currency": "Currency",
        "order_amount": "Order Amount",
        "event": "Event",
        "event_amount": "Event Amount",
        "event_date": "Event Date",
        "fixed_fees": "Tamara Fixed Fees",
        "variable_fees_pct": "Tamara Variable Fees %",
        "variable_fees": "Tamara Variable Fees",
        "total_fees": "Total Fees",
        "vat": "VAT Collected",
        "total_payable": "Total Payable",
        "installments": "Installments",
    }
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        s = str(cell).strip()
        for field, needle in needles.items():
            if needle.lower() in s.lower():
                out.setdefault(field, i)
    return out


def _read_preamble(rows: list[list[Any]], stop: int) -> dict:
    """Extract Statement ID, period, merchant info from the top block."""
    info: dict = {}
    flat: list[str] = []
    for r in rows[:stop]:
        for c in (r or []):
            if c is None:
                continue
            flat.append(str(c).strip())
    # Look for known labels and grab the next non-empty cell from that row
    for r in rows[:stop]:
        labels = [str(c).strip() if c is not None else "" for c in (r or [])]
        for j, cell in enumerate(labels):
            cl = cell.lower()
            if cl == "statement id":
                info["statement_id"] = next((labels[k] for k in range(j + 1, len(labels)) if labels[k]), "")
            elif cl == "statement date":
                info["statement_date_raw"] = next((labels[k] for k in range(j + 1, len(labels)) if labels[k]), "")
            elif cl == "statement period":
                info["statement_period"] = next((labels[k] for k in range(j + 1, len(labels)) if labels[k]), "")
            elif cl == "tamara merchant id":
                info["tamara_merchant_id"] = next((labels[k] for k in range(j + 1, len(labels)) if labels[k]), "")
    return info


def _parse_date(v: Any) -> str | None:
    """DD/MM/YYYY → ISO YYYY-MM-DD."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
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

    entries: list[dict] = []
    total_gross = 0.0
    total_fees = 0.0
    total_vat = 0.0
    total_net = 0.0
    total_refund_full = 0.0
    total_refund_partial = 0.0
    total_canceled_amount = 0.0
    total_canceled_fees = 0.0
    total_canceled_vat = 0.0
    canceled_count = 0

    required = ["merchant_order_id", "order_amount", "event_amount", "total_payable"]
    missing = [k for k in required if k not in cols]
    if missing:
        raise ValueError(f"ملف تمارا يفتقد أعمدة: {missing}.")

    for r in rows[header_idx + 1:]:
        if not r:
            continue
        order_no = to_str(r[cols["merchant_order_id"]] if cols.get("merchant_order_id") is not None and cols["merchant_order_id"] < len(r) else "")
        if not order_no:
            continue
        order_no = re.sub(r"\.0+$", "", order_no)

        # Skip totals/summary rows that may slip past the header search
        if order_no.lower() in ("total", "credit total", "subtotal"):
            continue

        event = to_str(r[cols.get("event", -1)]).lower() if cols.get("event") is not None else ""
        status = to_str(r[cols.get("order_status", -1)]).lower() if cols.get("order_status") is not None else ""

        gross = to_float(r[cols["order_amount"]])
        event_amount = to_float(r[cols["event_amount"]])
        net = to_float(r[cols["total_payable"]])
        fees = to_float(r[cols.get("total_fees", -1)]) if cols.get("total_fees") is not None else 0.0
        vat = to_float(r[cols.get("vat", -1)]) if cols.get("vat") is not None else 0.0
        installments = int(to_float(r[cols.get("installments", -1)]) or 0)
        event_date = _parse_date(r[cols.get("event_date", -1)] if cols.get("event_date") is not None else None)
        txn_date = _parse_date(r[cols.get("transaction_date", -1)] if cols.get("transaction_date") is not None else None)
        payment_type = to_str(r[cols.get("payment_type", -1)]) if cols.get("payment_type") is not None else ""

        if gross == 0 and net == 0 and event_amount == 0:
            continue

        # The Event column is authoritative.  A Captured row may already carry
        # order_status=partially_refunded because the order was refunded later;
        # classifying by status would erase the capture, its fee, and its VAT.
        # Status/net are fallbacks only for legacy files with a blank Event.
        if event:
            is_canceled = "cancel" in event
            is_refund = (not is_canceled) and ("refund" in event)
        else:
            is_canceled = "cancel" in status
            is_refund = (
                not is_canceled
                and ((net < 0) or ("refunded" in status))
            )
        refund_full = abs(event_amount) if (is_refund and "fully_refunded" in status) else 0.0
        refund_partial = abs(event_amount) if (is_refund and "partially_refunded" in status) else 0.0

        fee_rate = (
            round((fees / gross) * 100, 4)
            if gross > 0 and not is_refund and not is_canceled else 0.0
        )
        event_type = (
            "canceled_fee" if is_canceled
            else ("refund" if is_refund else "sale")
        )

        entries.append({
            "provider": "tamara",
            "order_number": order_no,
            "actual_payment_method": "tamara",
            "actual_gross_amount": 0.0 if is_canceled else gross,
            "actual_payment_fee": fees if not is_refund else 0.0,
            "actual_payment_vat": vat if not is_refund else 0.0,
            "actual_net_amount": net,
            "actual_fee_rate": fee_rate,
            "actual_refund_amount": refund_full,
            "actual_partial_refund_amount": refund_partial,
            "actual_canceled_amount": abs(event_amount) if is_canceled else 0.0,
            "event_type": event_type,
            "settlement_reference": statement_ref,
            "settlement_date": event_date or txn_date,
            "installments": installments,
            "raw_payment_type": payment_type,
            "tamara_order_id": to_str(r[cols.get("tamara_order_id", -1)] if cols.get("tamara_order_id") is not None else ""),
        })

        if is_canceled:
            canceled_count += 1
            total_canceled_amount += abs(event_amount)
            total_canceled_fees += fees
            total_canceled_vat += vat
            total_fees += fees
            total_vat += vat
            total_net += net
        elif is_refund:
            total_refund_full += refund_full
            total_refund_partial += refund_partial
            total_net += net  # negative
        else:
            total_gross += gross
            total_fees += fees
            total_vat += vat
            total_net += net

    return {
        "provider": "tamara",
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
            "canceled_count": canceled_count,
            "canceled_amount": round(total_canceled_amount, 2),
            "canceled_fees": round(total_canceled_fees, 2),
            "canceled_fees_vat": round(total_canceled_vat, 2),
        },
    }
