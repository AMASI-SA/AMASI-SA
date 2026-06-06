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


# Wallet-recharge / Salla-purchase detection.
#
# In real Salla invoices these rows appear as:
#   payment_method == "order.payment_method." (an untranslated i18n key
#                     Salla emits when the merchant pays from his
#                     Salla wallet — usually for a shipping label)
#   gross / net are NEGATIVE because the amount is taken FROM the
#   merchant's wallet credit, not paid TO him.
#
# We tag these rows separately, exclude them from real sale totals,
# surface them on the UI as "مشتريات سله", and intentionally do NOT
# push actual_* fields to unified_orders for them (the order_number
# can point at a real customer order whose actual_net is unrelated).
_SALLA_WALLET_METHOD_NEEDLES = (
    "order.payment_method.",   # untranslated i18n key (most common)
    "شحن محفظة",                # arabic literal for "wallet recharge"
    "wallet recharge",
    "wallet_recharge",
)


def _is_wallet_recharge(method: str, gross: float, net: float) -> bool:
    """STRICT detection — only flag rows where the payment_method
    explicitly matches a wallet needle. Negative amounts on a *normal*
    method (مدى / البطاقة الائتمانية) are CUSTOMER refunds and must
    remain in the regular sale aggregation."""
    if not method:
        return False
    m = method.strip().lower()
    for n in _SALLA_WALLET_METHOD_NEEDLES:
        if n.lower() in m:
            return True
    return False


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

    # ── Pre-pass: accumulate sum of POSITIVE gross per order_number ──
    # We need this to classify refund rows as full vs partial. A refund
    # is "full" when |refund_gross| >= 99% of the order's total positive
    # payments in this same file. Otherwise it's a partial refund.
    positive_gross_by_order: dict[str, float] = {}
    for r in rows[1:]:
        if not r or cols.get("order_number") is None:
            continue
        ono_raw = r[cols["order_number"]]
        gross_v = r[cols["gross_amount"]] if cols.get("gross_amount") is not None else None
        method_v = r[cols["payment_method"]] if cols.get("payment_method") is not None else ""
        ono = re.sub(r"\.0+$", "", to_str(ono_raw))
        if not ono:
            continue
        g = to_float(gross_v)
        # Exclude wallet-recharge rows from "positive gross" because
        # they're internal balance moves, not customer payments.
        if _is_wallet_recharge(to_str(method_v), g, to_float(r[cols["net_amount"]] if cols.get("net_amount") is not None else 0)):
            continue
        if g > 0:
            positive_gross_by_order[ono] = positive_gross_by_order.get(ono, 0.0) + g

    entries: list[dict] = []
    total_gross = 0.0
    total_fees = 0.0
    total_vat = 0.0
    total_net = 0.0
    total_refund_full = 0.0
    total_refund_partial = 0.0
    salla_purchases_total = 0.0
    salla_purchases_count = 0

    for r in rows[1:]:
        if not r:
            continue
        order_no = to_str(r[cols["order_number"]] if cols.get("order_number") is not None else "")
        if not order_no or order_no.lower() in ("total", "الإجمالي", "المجموع"):
            continue
        order_no = re.sub(r"\.0+$", "", order_no)

        gross = to_float(r[cols["gross_amount"]])
        fee = to_float(r[cols["payment_fee"]])
        vat = to_float(r[cols.get("payment_vat", -1)]) if cols.get("payment_vat") is not None else 0.0
        net = to_float(r[cols["net_amount"]])
        method = to_str(r[cols["payment_method"]])

        # ── Wallet-recharge rows (مشتريات سله) ──────────────────────
        if _is_wallet_recharge(method, gross, net):
            entries.append({
                "provider": "salla",
                "order_number": order_no,
                "actual_payment_method": "wallet_recharge",
                "actual_gross_amount": gross,
                "actual_payment_fee": fee,
                "actual_payment_vat": vat,
                "actual_net_amount": net,
                "actual_fee_rate": 0.0,
                "actual_refund_amount": 0.0,
                "actual_partial_refund_amount": 0.0,
                "event_type": "salla_purchase",
                "settlement_reference": invoice_number,
                "settlement_date": None,
                "raw_payment_method": method,
                "notes": "شحن محفظة (لبوليصة شحن)",
            })
            salla_purchases_total += abs(net or gross)
            salla_purchases_count += 1
            continue

        # ── Customer refund rows ─────────────────────────────────────
        # Negative amount on a normal payment method (مدى / credit card
        # / etc.) means the merchant refunded the customer back via
        # the same gateway. Classify as full vs partial by comparing
        # |refund_gross| to the order's total positive payments in
        # this file.
        if gross < 0:
            refund_amount = abs(gross)
            order_total_paid = positive_gross_by_order.get(order_no, 0.0)
            # Tolerance: 1% (≥99% → full). Falls back to "partial"
            # when the original payment isn't in this file (we'd be
            # comparing against 0).
            is_full = (
                order_total_paid > 0
                and refund_amount >= order_total_paid * 0.99
            )
            refund_full = refund_amount if is_full else 0.0
            refund_partial = refund_amount if not is_full else 0.0
            entries.append({
                "provider": "salla",
                "order_number": order_no,
                "actual_payment_method": normalize_payment_method(method),
                "actual_gross_amount": gross,
                "actual_payment_fee": fee,
                "actual_payment_vat": vat,
                "actual_net_amount": net,
                "actual_fee_rate": 0.0,
                "actual_refund_amount": refund_full,
                "actual_partial_refund_amount": refund_partial,
                "event_type": "refund",
                "settlement_reference": invoice_number,
                "settlement_date": None,
                "raw_payment_method": method,
            })
            if is_full:
                total_refund_full += refund_amount
            else:
                total_refund_partial += refund_amount
            total_net += net  # negative — drags the net total down
            continue

        # Skip rows with no positive gross AND no fee (defensive — true blanks)
        if gross == 0 and fee == 0 and net == 0:
            continue

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
            "settlement_date": None,
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
            "refund_full": round(total_refund_full, 2),
            "refund_partial": round(total_refund_partial, 2),
            "salla_purchases_total": round(salla_purchases_total, 2),
            "salla_purchases_count": salla_purchases_count,
        },
    }
