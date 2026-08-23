"""Tabby settlement-report parser.

Real sample layout (verified — 2026-06):
    Rows 1-8: Preamble  (Date, Statement#, Company Name)
    Row 11: Detail header
        col1: Order Number               ← matches unified_orders.order_number
        col2: Sale/Refund Date
        col3: Merchant Name
        col4: Merchant Code
        col5: Product Type (Installments: X Months)
        col6: Type (sale | refund | partial refund)
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
    Trailing rows: optional "Payout fee" line + note/grand-total rows.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
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
    normalized = [str(cell).strip().lower() if cell is not None else ""
                  for cell in header_row]
    # Exact headers first. This is essential for `Type`: a substring-first
    # scan incorrectly binds it to the earlier `Product Type` column.
    for field, needle in needles.items():
        needle_lc = needle.lower()
        for i, value in enumerate(normalized):
            if value == needle_lc:
                out[field] = i
                break
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        s = str(cell).strip().lower()
        for field, needle in needles.items():
            if field not in out and needle.lower() in s:
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
    statement_date = _parse_dt(preamble.get("statement_date_raw"))
    period_start = None
    period_end = None
    if statement_date:
        issue_date = datetime.fromisoformat(statement_date).date()
        # Four adjacent merchant reports prove a Monday issue/transfer for
        # the preceding Monday→Sunday activity cycle.
        period_end = (issue_date - timedelta(days=1)).isoformat()
        period_start = (issue_date - timedelta(days=7)).isoformat()

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
    total_refunded_fees = 0.0
    total_refunded_vat = 0.0
    settlement_fee = 0.0
    settlement_fee_vat = 0.0
    sales_count = 0
    refund_count = 0
    partial_refund_count = 0

    for r in rows[header_idx + 1:]:
        if not r:
            continue
        order_no = to_str(r[cols["order_number"]] if cols["order_number"] < len(r) else "")
        if not order_no:
            continue
        order_no = re.sub(r"\.0+$", "", order_no)

        order_label = order_no.strip().lower()
        if order_label.startswith("payout fee"):
            fee = abs(to_float(
                r[cols["total_fee"]] if cols["total_fee"] < len(r) else 0,
            ))
            vat = abs(to_float(
                r[cols.get("vat_amount", -1)]
                if cols.get("vat_amount") is not None else 0,
            ))
            net = to_float(
                r[cols["transferred_amount"]]
                if cols["transferred_amount"] < len(r) else 0,
            )
            if not net and (fee or vat):
                net = -(fee + vat)
            transfer_date = _parse_dt(
                r[cols.get("transfer_date", -1)]
                if cols.get("transfer_date") is not None else None,
            ) or statement_date
            settlement_fee += fee
            settlement_fee_vat += vat
            total_net += net
            entries.append({
                "provider": "tabby",
                "order_number": f"payout-fee:{statement_ref}",
                "actual_payment_method": "tabby",
                "actual_gross_amount": 0.0,
                "actual_payment_fee": fee,
                "actual_payment_vat": vat,
                "actual_net_amount": round(net, 2),
                "actual_fee_rate": 0.0,
                "actual_refund_amount": 0.0,
                "actual_partial_refund_amount": 0.0,
                "event_type": "settlement_fee",
                "settlement_reference": statement_ref,
                "settlement_date": transfer_date,
                "event_date": transfer_date,
                "raw_product_type": "",
            })
            continue

        # Skip note/grand-total rows. Transaction order numbers are numeric.
        if not order_no.isdigit():
            continue

        ev_type = to_str(r[cols.get("event_type", -1)]).lower() if cols.get("event_type") is not None else "sale"
        order_amount = to_float(r[cols["order_amount"]])
        fees = to_float(r[cols["total_fee"]])
        vat = to_float(r[cols.get("vat_amount", -1)]) if cols.get("vat_amount") is not None else 0.0
        net = to_float(r[cols["transferred_amount"]])
        product_type = to_str(r[cols.get("product_type", -1)]) if cols.get("product_type") is not None else ""
        event_date = _parse_dt(r[cols.get("event_date", -1)] if cols.get("event_date") is not None else None)
        transfer_date = _parse_dt(r[cols.get("transfer_date", -1)] if cols.get("transfer_date") is not None else None)
        commission_rate = to_float(r[cols.get("commission_rate", -1)]) if cols.get("commission_rate") is not None else 0.0
        refundable_commission = to_float(
            r[cols.get("refundable_commission", -1)]
            if cols.get("refundable_commission") is not None else 0.0,
        )
        non_refundable_commission = to_float(
            r[cols.get("non_refundable_commission", -1)]
            if cols.get("non_refundable_commission") is not None else 0.0,
        )
        fixed_fee = to_float(
            r[cols.get("fixed_fee", -1)]
            if cols.get("fixed_fee") is not None else 0.0,
        )
        total_deduction = to_float(
            r[cols.get("total_deduction", -1)]
            if cols.get("total_deduction") is not None else 0.0,
        )

        if order_amount == 0 and net == 0:
            continue

        # The Type column is authoritative.  "Transferred amount" is the
        # refund gross less Tabby's 4.99% commission/VAT rebate, so comparing
        # it with Order Amount misclassifies every full refund as partial.
        is_partial_refund = "partial refund" in ev_type
        is_refund = is_partial_refund or ("refund" in ev_type) or (net < 0)
        refund_full = 0.0
        refund_partial = 0.0
        if is_refund:
            refund_amount = abs(order_amount)
            if is_partial_refund:
                refund_partial = refund_amount
                partial_refund_count += 1
            else:
                refund_full = refund_amount
                refund_count += 1
        else:
            sales_count += 1

        fee_rate = commission_rate  # Tabby already reports the rate (%) directly

        entries.append({
            "provider": "tabby",
            "order_number": order_no,
            "actual_payment_method": "tabby",
            "actual_gross_amount": max(order_amount, 0.0) if not is_refund else 0.0,
            # Refund rows are negative commission rebates. Keeping their sign
            # lets consolidation retain the non-refundable 2% + fixed fee.
            "actual_payment_fee": fees,
            "actual_payment_vat": vat,
            "actual_net_amount": net,
            "actual_fee_rate": fee_rate if not is_refund else 0.0,
            "actual_refund_amount": refund_full,
            "actual_partial_refund_amount": refund_partial,
            "event_type": "refund" if is_refund else "sale",
            "source_event_type": ev_type,
            "source_commission_rate": fee_rate,
            "settlement_reference": statement_ref,
            "settlement_date": transfer_date or event_date,
            "event_date": event_date,
            "raw_product_type": product_type,
            "actual_refundable_commission": refundable_commission,
            "actual_non_refundable_commission": non_refundable_commission,
            "actual_fixed_fee": fixed_fee,
            "actual_total_deduction": total_deduction,
        })

        if is_refund:
            total_refund_full += refund_full
            total_refund_partial += refund_partial
            total_fees += fees
            total_vat += vat
            total_refunded_fees += abs(fees)
            total_refunded_vat += abs(vat)
            total_net += net
        else:
            total_gross += order_amount
            total_fees += fees
            total_vat += vat
            total_net += net

    return {
        "provider": "tabby",
        "header": {
            **preamble,
            "statement_date": statement_date,
            "period_start": period_start,
            "period_end": period_end,
            "sheet_title": title,
            "currency": "SAR",
        },
        "entries": entries,
        "totals": {
            "rows": len(entries),
            "transactions_count": sales_count,
            "refunds_count": refund_count + partial_refund_count,
            "full_refunds_count": refund_count,
            "partial_refunds_count": partial_refund_count,
            "gross": round(total_gross, 2),
            "fees": round(total_fees, 2),
            "fees_vat": round(total_vat, 2),
            "net": round(total_net, 2),
            "refund_full": round(total_refund_full, 2),
            "refund_partial": round(total_refund_partial, 2),
            "refunded_fees": round(total_refunded_fees, 2),
            "refunded_fees_vat": round(total_refunded_vat, 2),
            "settlement_fee": round(settlement_fee, 2),
            "settlement_fee_vat": round(settlement_fee_vat, 2),
        },
    }
