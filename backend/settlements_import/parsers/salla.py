"""Salla payment-invoice parser.

Real sample column layout (verified — 2026-06):
    Sheet title:  "Invoice # 6320306"     ← we read invoice number from here
    Row 1 (header):
        col0: رقم الطلب
        col1: إجمالي الطلب (ر.س)
        col2: طريقة الدفع
        col3: الرسوم (ر.س)
        col4: المُستحق قبل الضريبة (ر.س)
        col5: الضريبة
        col6: المُستحق بعد الضريبة (ر.س)
    Rows 2..N: data rows.

This file is the merchant's settlement of payment-gateway fees taken
by Salla on prepaid orders (mada, credit card, Apple Pay handled by
Salla Pay). It does NOT include refunds — refunds for Salla orders
are handled through a separate refund flow (out of scope for this
parser).
"""
from __future__ import annotations

import re
from typing import Any

import openpyxl

from ..utils import to_float, to_str, normalize_payment_method


SALLA_AR_HEADERS = {
    "order_number": "رقم الطلب",
    "gross_amount": "إجمالي الطلب",
    "payment_method": "طريقة الدفع",
    "payment_fee": "الرسوم",
    "net_before_vat": "المستحق قبل",
    "payment_vat": "الضريبة",
    "net_amount": "المستحق بعد",
}


def _strip(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip())


# Arabic diacritics (tashkeel) — must be stripped before comparison
# because Salla's invoice headers contain a damma (ـُ) inside "المُستحق"
# that breaks literal substring matching.
_TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def _normalize_ar(s: str) -> str:
    return _TASHKEEL_RE.sub("", s)


def _find_column_indices(header_row: list[Any]) -> dict[str, int]:
    """Return {logical_field: column_index_0based}.

    Algorithm: for each logical field, scan all cells and pick the one
    whose normalized text is closest to the field's needle. Closest =
    cells that *start with* the needle beat cells that merely *contain*
    it. This avoids "الضريبة" (the VAT needle) being incorrectly mapped
    to a cell like "المستحق قبل الضريبة" where it appears as a suffix.
    """
    cells_norm = []
    for idx, cell in enumerate(header_row):
        if cell is None:
            cells_norm.append((idx, ""))
            continue
        cells_norm.append((idx, _normalize_ar(_strip(cell))))

    out: dict[str, int] = {}
    used: set[int] = set()
    for field, needle in SALLA_AR_HEADERS.items():
        n = _normalize_ar(needle)
        # 1st pass: exact equality
        match = next((i for i, t in cells_norm if i not in used and t == n), None)
        # 2nd pass: cell starts with needle
        if match is None:
            match = next((i for i, t in cells_norm if i not in used and t.startswith(n)), None)
        # 3rd pass: needle is a token at the START of the cell text
        if match is None:
            match = next((i for i, t in cells_norm if i not in used and (n + " ") in (t + " ")), None)
        if match is not None:
            out[field] = match
            used.add(match)
    return out


def _extract_invoice_number(sheet_title: str) -> str:
    """'Invoice # 6320306' → '6320306'."""
    m = re.search(r"(\d+)", sheet_title or "")
    return m.group(1) if m else ""


def parse(workbook: openpyxl.Workbook) -> dict:
    ws = workbook.active
    title = ws.title or ""
    invoice_number = _extract_invoice_number(title)

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("ملف Salla فارغ.")

    header_row = list(rows[0])
    cols = _find_column_indices(header_row)
    required = ["order_number", "gross_amount", "payment_method", "payment_fee", "net_amount"]
    missing = [r for r in required if r not in cols]
    if missing:
        raise ValueError(
            f"ملف Salla يفتقد الأعمدة: {missing}. تم العثور على: {list(cols.keys())}"
        )

    entries: list[dict] = []
    total_gross = 0.0
    total_fees = 0.0
    total_vat = 0.0
    total_net = 0.0

    for r in rows[1:]:
        if not r:
            continue
        order_no = to_str(r[cols["order_number"]] if cols.get("order_number") is not None else "")
        if not order_no or order_no.lower() in ("total", "الإجمالي", "المجموع"):
            continue
        # Salla treats order numbers as integers — strip ".0" if openpyxl
        # parsed a numeric cell.
        order_no = re.sub(r"\.0+$", "", order_no)

        gross = to_float(r[cols["gross_amount"]])
        fee = to_float(r[cols["payment_fee"]])
        vat = to_float(r[cols.get("payment_vat", -1)]) if cols.get("payment_vat") is not None else 0.0
        net = to_float(r[cols["net_amount"]])
        method = to_str(r[cols["payment_method"]])

        if gross <= 0 and net <= 0 and fee <= 0:
            continue  # blank row

        fee_rate = round((fee / gross) * 100, 4) if gross > 0 else 0.0

        entries.append({
            "provider": "salla",
            "order_number": order_no,
            "actual_payment_method": normalize_payment_method(method),
            "actual_gross_amount": gross,
            "actual_payment_fee": fee,
            "actual_payment_vat": vat,
            "actual_net_amount": net,
            "actual_fee_rate": fee_rate,
            "actual_refund_amount": 0.0,
            "actual_partial_refund_amount": 0.0,
            "event_type": "sale",
            "settlement_reference": invoice_number,
            "settlement_date": None,  # not present in Salla invoice header
            "raw_payment_method": method,
        })
        total_gross += gross
        total_fees += fee
        total_vat += vat
        total_net += net

    return {
        "provider": "salla",
        "header": {
            "invoice_number": invoice_number,
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
            "refund_full": 0.0,
            "refund_partial": 0.0,
        },
    }
