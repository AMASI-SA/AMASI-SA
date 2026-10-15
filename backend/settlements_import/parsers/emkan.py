"""Emkan BNPL settlement-report parser.

Verified merchant export layout (2026-07/08):

* rows 1-3 contain merchant/date/opening/closing/settlement totals;
* row 4 contains 23 detail columns;
* each detail row carries the provider Order ID, order creation date,
  original/refunded/net bill amounts, commission, fixed fee, VAT, deduction,
  settlement net, and PO reference.

The provider workbook stores monetary calculations at more than two decimal
places but displays them at two decimals.  The bank settlement is the rounded
net, so a one-halalah residual can remain when displayed fee and VAT columns
are rounded independently.  We preserve the reported source values and put
that residual into the fee leg, keeping every imported order arithmetically
balanced at SAR precision.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import openpyxl

from ..utils import to_float, to_str


HEADER_NEEDLE = "Total deduction for EMKAN"
MONEY = Decimal("0.01")


def _money(value: Any) -> float:
    return float(
        Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)
    )


def _reset_broken_dimension(ws) -> None:
    """Emkan exports declare ``dimension=A1`` despite containing A1:Wn."""
    try:
        dimension = str(ws.calculate_dimension() or "")
    except Exception:
        dimension = ""
    if dimension in {"A1", "A1:A1"} and hasattr(ws, "reset_dimensions"):
        ws.reset_dimensions()


def _parse_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return raw[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw[:10]) else None


def _find_header_row(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    for idx, row in enumerate(rows):
        lowered = [str(cell).strip().lower() for cell in row if cell is not None]
        if any(HEADER_NEEDLE.lower() in cell for cell in lowered):
            return idx, _map_columns(row)
    raise ValueError("لم يتم العثور على رأس جدول التفاصيل في ملف إمكان.")


def _map_columns(header: list[Any]) -> dict[str, int]:
    needles = {
        "merchant_name": "Merchant name",
        "merchant_code": "Merchant Code",
        "provider_order_id": "Order ID",
        "order_date": "Order creation date",
        "original_amount": "Original bill Amount",
        "refund_status": "REFUND STATUS",
        "refund_amount": "Refunded amount",
        "net_bill_amount": "Net bill amount",
        "commission_rate": "Commission rate",
        "commission_amount": "Commission Amount",
        "refundable_rate": "Refundable commission Rate",
        "refundable_amount": "Refundable commission Amount",
        "non_refundable_rate": "Non-Refundable commission rate",
        "non_refundable_amount": "Non-Refundable commission Amount",
        "fixed_fee": "Fixed Fee",
        "total_fee": "Total Fee",
        "vat_rate": "VAT rate",
        "vat_amount": "VAT amount",
        "total_deduction": "Total deduction for EMKAN",
        "settlement_amount": "Settelment:",
        "po_reference": "PO:",
    }
    normalized = [str(cell).strip().lower() if cell is not None else ""
                  for cell in header]
    out: dict[str, int] = {}
    for field, needle in needles.items():
        target = needle.lower()
        for index, value in enumerate(normalized):
            if value == target:
                out[field] = index
                break
        if field in out:
            continue
        for index, value in enumerate(normalized):
            if target in value:
                out[field] = index
                break
    return out


def _next_value(row: list[Any], label_index: int) -> Any:
    for cell in row[label_index + 1:]:
        if cell not in (None, ""):
            return cell
    return None


def _read_preamble(rows: list[list[Any]], stop: int) -> dict:
    info: dict[str, Any] = {}
    labels = {
        "merchant name:": "merchant_name",
        "total settlement today:": "total_settlement_today",
        "date:": "statement_date",
        "closing balance:": "closing_balance",
        "opening balance:": "opening_balance",
        "settlement fees:": "settlement_fee",
    }
    for row in rows[:stop]:
        for index, cell in enumerate(row):
            key = labels.get(str(cell).strip().lower()) if cell is not None else None
            if not key:
                continue
            value = _next_value(row, index)
            if key == "statement_date":
                info[key] = _parse_date(value)
            elif key in {
                "total_settlement_today", "closing_balance",
                "opening_balance", "settlement_fee",
            }:
                info[key] = to_float(value)
            else:
                info[key] = to_str(value)
    return info


def _cell(row: list[Any], columns: dict[str, int], field: str) -> Any:
    index = columns.get(field)
    return row[index] if index is not None and index < len(row) else None


def parse(workbook: openpyxl.Workbook) -> dict:
    ws = workbook.active
    _reset_broken_dimension(ws)
    rows = [list(row) for row in ws.iter_rows(values_only=True)]
    header_index, columns = _find_header_row(rows)
    preamble = _read_preamble(rows, header_index)

    required = {
        "provider_order_id", "original_amount", "refund_status",
        "refund_amount",
        "net_bill_amount", "total_fee", "vat_amount",
        "settlement_amount",
    }
    missing = sorted(required - set(columns))
    if missing:
        raise ValueError(f"ملف إمكان يفتقد أعمدة: {missing}.")

    entries: list[dict] = []
    sales_count = 0
    refund_count = 0
    full_refund_count = 0
    partial_refund_count = 0
    gross_total = Decimal("0")
    refund_full_total = Decimal("0")
    refund_partial_total = Decimal("0")
    balanced_fee_total = Decimal("0")
    reported_fee_total = Decimal("0")
    vat_total = Decimal("0")
    deduction_total = Decimal("0")
    rows_net_total = Decimal("0")

    statement_date = preamble.get("statement_date")
    for row in rows[header_index + 1:]:
        provider_order_id = to_str(_cell(row, columns, "provider_order_id"))
        if not provider_order_id:
            continue

        original = Decimal(str(to_float(_cell(row, columns, "original_amount"))))
        refunded = Decimal(str(abs(to_float(_cell(row, columns, "refund_amount")))))
        net_bill = Decimal(str(to_float(_cell(row, columns, "net_bill_amount"))))
        source_total_fee = Decimal(str(to_float(_cell(row, columns, "total_fee"))))
        source_vat = Decimal(str(to_float(_cell(row, columns, "vat_amount"))))
        source_deduction = Decimal(str(to_float(
            _cell(row, columns, "total_deduction")
        )))
        source_settlement = Decimal(str(to_float(
            _cell(row, columns, "settlement_amount")
        )))
        if original == 0 and net_bill == 0 and source_settlement == 0:
            continue

        refund_status = to_str(_cell(row, columns, "refund_status")).lower()
        is_refund = refunded > 0 or (
            "refund" in refund_status and "no refund" not in refund_status
        )
        is_full_refund = is_refund and original > 0 and refunded >= original
        is_partial_refund = is_refund and not is_full_refund

        gross = _money(original)
        refund_amount = _money(refunded)
        vat = _money(source_vat)
        net = _money(source_settlement)
        reported_fee = _money(source_total_fee)
        # At SAR precision: net bill = bank net + fee + fee VAT.
        # This intentionally absorbs the provider workbook's sub-halalah
        # residual into the commission line rather than leaving receivables
        # off by SAR 0.01.
        balanced_fee = _money(net_bill - Decimal(str(net)) - Decimal(str(vat)))
        rounding_adjustment = _money(
            Decimal(str(balanced_fee)) - Decimal(str(reported_fee))
        )

        if is_refund:
            refund_count += 1
            if is_full_refund:
                full_refund_count += 1
                refund_full_total += Decimal(str(refund_amount))
            else:
                partial_refund_count += 1
                refund_partial_total += Decimal(str(refund_amount))
        else:
            sales_count += 1

        po_reference = to_str(_cell(row, columns, "po_reference"))
        settlement_reference = po_reference or (
            f"emkan:{statement_date or 'unknown'}:{provider_order_id}"
        )
        event_type = (
            "sale_with_full_refund" if is_full_refund
            else "sale_with_partial_refund" if is_partial_refund
            else "sale"
        )
        entries.append({
            "provider": "emkan",
            # Emkan's report exposes its own UUID, not Salla's order number.
            # The importer resolves this through payment_transactions.provider_id
            # before matching unified_orders.
            "order_number": provider_order_id,
            "provider_order_id": provider_order_id,
            "actual_payment_method": "emkan",
            "actual_gross_amount": gross,
            "actual_payment_fee": balanced_fee,
            "actual_payment_vat": vat,
            "actual_net_amount": net,
            "actual_fee_rate": to_float(_cell(row, columns, "commission_rate")),
            "actual_refund_amount": refund_amount if is_full_refund else 0.0,
            "actual_partial_refund_amount": (
                refund_amount if is_partial_refund else 0.0
            ),
            "event_type": event_type,
            "source_refund_status": refund_status,
            "source_order_date": _parse_date(_cell(row, columns, "order_date")),
            "source_net_bill_amount": _money(net_bill),
            "source_commission_amount": to_float(
                _cell(row, columns, "commission_amount")
            ),
            "source_refundable_commission_rate": to_float(
                _cell(row, columns, "refundable_rate")
            ),
            "source_refundable_commission_amount": to_float(
                _cell(row, columns, "refundable_amount")
            ),
            "source_non_refundable_commission_rate": to_float(
                _cell(row, columns, "non_refundable_rate")
            ),
            "source_non_refundable_commission_amount": to_float(
                _cell(row, columns, "non_refundable_amount")
            ),
            "source_fixed_fee": to_float(_cell(row, columns, "fixed_fee")),
            "source_total_fee": float(source_total_fee),
            "source_total_deduction": float(source_deduction),
            "source_vat_rate": to_float(_cell(row, columns, "vat_rate")),
            "statement_rounding_adjustment": rounding_adjustment,
            "settlement_reference": settlement_reference,
            "settlement_date": statement_date,
            "po_reference": po_reference,
            "merchant_name": to_str(_cell(row, columns, "merchant_name")),
            "merchant_code": to_str(_cell(row, columns, "merchant_code")),
        })

        gross_total += Decimal(str(gross))
        balanced_fee_total += Decimal(str(balanced_fee))
        reported_fee_total += source_total_fee
        vat_total += source_vat
        deduction_total += source_deduction
        rows_net_total += source_settlement

    statement_id = ""
    if entries:
        statement_id = (
            f"emkan:{statement_date or 'unknown'}:"
            f"{entries[0]['provider_order_id']}"
        )

    settlement_fee = _money(preamble.get("settlement_fee") or 0)
    if settlement_fee:
        entries.append({
            "provider": "emkan",
            "order_number": f"settlement-fee:{statement_id}",
            "actual_payment_method": "emkan",
            "actual_gross_amount": 0.0,
            "actual_payment_fee": 0.0,
            "actual_payment_vat": 0.0,
            "actual_net_amount": -settlement_fee,
            "actual_fee_rate": 0.0,
            "actual_refund_amount": 0.0,
            "actual_partial_refund_amount": 0.0,
            "event_type": "settlement_fee",
            "settlement_reference": statement_id,
            "settlement_date": statement_date,
            "review_required": True,
            "review_reason": "emkan_settlement_fee_vat_not_separately_identified",
        })

    row_net = _money(rows_net_total)
    header_net = _money(preamble.get("total_settlement_today") or row_net)
    reported_fees = _money(reported_fee_total)
    balanced_fees = _money(balanced_fee_total)
    return {
        "provider": "emkan",
        "header": {
            **preamble,
            "statement_id": statement_id,
            "sheet_title": ws.title,
            "currency": "SAR",
            "settlement_date": statement_date,
            "fee_evidence_version": "emkan-statements-2026-08-v1",
            "refund_policy_verified": refund_count > 0,
            "settlement_cycle_verified": False,
        },
        "entries": entries,
        "totals": {
            "rows": sales_count + refund_count,
            "transactions_count": sales_count + refund_count,
            "refunds_count": refund_count,
            "full_refunds_count": full_refund_count,
            "partial_refunds_count": partial_refund_count,
            "gross": _money(gross_total),
            "fees": balanced_fees,
            "reported_fees": reported_fees,
            "fees_vat": _money(vat_total),
            "total_deduction": _money(deduction_total),
            "net": header_net,
            "rows_net": row_net,
            "statement_net_difference": _money(
                Decimal(str(header_net)) - Decimal(str(row_net))
            ),
            "refund_full": _money(refund_full_total),
            "refund_partial": _money(refund_partial_total),
            "settlement_fee": settlement_fee,
            "settlement_fee_vat": 0.0,
            "rounding_adjustment": _money(
                Decimal(str(balanced_fees)) - Decimal(str(reported_fees))
            ),
            "opening_balance": _money(preamble.get("opening_balance") or 0),
            "closing_balance": _money(preamble.get("closing_balance") or 0),
        },
    }
