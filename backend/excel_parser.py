"""Excel parser for Salla platform exports.

The parser is forgiving: it auto-detects the relevant columns by checking common
Arabic and English header names that Salla uses in its exported sheets.
"""
from __future__ import annotations

import io
import re
from typing import Optional

import openpyxl


# Candidate column-name patterns (case-insensitive substring match)
TOTAL_COLS = [
    "إجمالي الطلب", "اجمالي الطلب", "إجمالي السلة", "اجمالي السلة",
    "المبلغ الإجمالي", "المبلغ الاجمالي", "الإجمالي", "الاجمالي",
    "total", "grand total", "order total", "amount",
]
PAYMENT_COLS = [
    "طريقة الدفع", "وسيلة الدفع", "بوابة الدفع",
    "payment method", "payment", "gateway",
]
SHIPPING_COLS = [
    "شركة الشحن", "وسيلة الشحن", "موفر الشحن", "مزود الشحن",
    "shipping company", "shipping", "courier", "carrier",
]
SOURCE_COLS = [
    "مصدر الطلب", "المصدر", "مصدر", "قناة البيع", "القناة",
    "مصدر الزيارة", "مصدر العميل", "نوع المصدر",
    "order source", "source", "channel", "platform", "referrer",
]

# Salla exports place the order source at column BA (index 52, 0-indexed) by default.
SALLA_SOURCE_COL_INDEX = 52  # Excel column "BA"
ORDER_ID_COLS = ["رقم الطلب", "order id", "order number", "id", "#"]
STATUS_COLS = ["حالة الطلب", "الحالة", "status"]
DATE_COLS = ["تاريخ الطلب", "التاريخ", "date", "created at"]


def _norm(s: object) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _find_header_row(rows: list[list]) -> int:
    """Return index of the row that looks like the header. Looks at first 10 rows."""
    best_idx = 0
    best_hits = -1
    keywords = TOTAL_COLS + PAYMENT_COLS + SHIPPING_COLS + ORDER_ID_COLS
    keywords_norm = [_norm(k) for k in keywords]
    for i, row in enumerate(rows[:15]):
        hits = 0
        for cell in row:
            v = _norm(cell)
            if not v:
                continue
            for kw in keywords_norm:
                if kw and kw in v:
                    hits += 1
                    break
        if hits > best_hits:
            best_hits = hits
            best_idx = i
    return best_idx


def _match_col(headers_norm: list[str], candidates: list[str]) -> Optional[int]:
    cand_norm = [_norm(c) for c in candidates]
    # exact-ish first
    for i, h in enumerate(headers_norm):
        for c in cand_norm:
            if c == h:
                return i
    # substring
    for i, h in enumerate(headers_norm):
        for c in cand_norm:
            if c and (c in h or h in c):
                return i
    return None


def _to_float(v: object) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    # strip currency words and any non numeric except dot and minus
    s = re.sub(r"[^\d\.\-]", "", s.replace(",", "."))
    if not s or s in (".", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_salla_excel(file_bytes: bytes) -> dict:
    """Parse a Salla-style Excel export and return aggregated raw data.

    Returns:
        {
          "total_sales": float,
          "total_orders": int,
          "payment_methods": [{"name": str, "orders_count": int, "total_sales": float}, ...],
          "shipping_companies": [{"name": str, "orders_count": int}, ...],
          "orders_sample": [...],
          "detected_columns": {...}
        }
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    ws = wb.active

    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    if not rows:
        raise ValueError("الملف فارغ")

    header_idx = _find_header_row(rows)
    headers = rows[header_idx]
    headers_norm = [_norm(h) for h in headers]

    col_total = _match_col(headers_norm, TOTAL_COLS)
    col_payment = _match_col(headers_norm, PAYMENT_COLS)
    col_shipping = _match_col(headers_norm, SHIPPING_COLS)
    col_source = _match_col(headers_norm, SOURCE_COLS)
    col_order = _match_col(headers_norm, ORDER_ID_COLS)
    col_status = _match_col(headers_norm, STATUS_COLS)
    col_date = _match_col(headers_norm, DATE_COLS)

    # Fallback: Salla exports place order source at column BA (index 52)
    if col_source is None and len(headers) > SALLA_SOURCE_COL_INDEX:
        col_source = SALLA_SOURCE_COL_INDEX

    if col_total is None:
        raise ValueError(
            "لم نتمكن من العثور على عمود إجمالي المبلغ في الملف. تأكد من أنه ملف طلبات سلة."
        )

    data_rows = rows[header_idx + 1 :]

    total_sales = 0.0
    total_orders = 0
    payments: dict[str, dict] = {}
    shippings: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    sample_orders: list[dict] = []

    for row in data_rows:
        # skip totally empty rows
        if not any(cell not in (None, "") for cell in row):
            continue
        amount = _to_float(row[col_total] if col_total < len(row) else 0)
        if amount <= 0:
            # try detecting a row as valid even if amount is 0 only if there is order id
            if col_order is not None and col_order < len(row) and row[col_order]:
                pass
            else:
                continue

        payment_name = (
            str(row[col_payment]).strip()
            if col_payment is not None and col_payment < len(row) and row[col_payment]
            else "غير محدد"
        )
        shipping_name = (
            str(row[col_shipping]).strip()
            if col_shipping is not None and col_shipping < len(row) and row[col_shipping]
            else "غير محدد"
        )
        source_name = (
            str(row[col_source]).strip()
            if col_source is not None and col_source < len(row) and row[col_source]
            else "غير محدد"
        )

        total_sales += amount
        total_orders += 1

        p = payments.setdefault(payment_name, {"name": payment_name, "orders_count": 0, "total_sales": 0.0})
        p["orders_count"] += 1
        p["total_sales"] += amount

        s = shippings.setdefault(shipping_name, {"name": shipping_name, "orders_count": 0})
        s["orders_count"] += 1

        src = sources.setdefault(source_name, {"name": source_name, "orders_count": 0, "total_sales": 0.0})
        src["orders_count"] += 1
        src["total_sales"] += amount

        if len(sample_orders) < 10:
            sample_orders.append({
                "order_id": str(row[col_order]) if col_order is not None and col_order < len(row) and row[col_order] else "",
                "amount": amount,
                "payment_method": payment_name,
                "shipping_company": shipping_name,
                "status": str(row[col_status]) if col_status is not None and col_status < len(row) and row[col_status] else "",
                "date": str(row[col_date]) if col_date is not None and col_date < len(row) and row[col_date] else "",
            })

    if total_orders == 0:
        raise ValueError("لم نعثر على أي طلبات صالحة في الملف.")

    return {
        "total_sales": round(total_sales, 2),
        "total_orders": total_orders,
        "payment_methods": [
            {**v, "total_sales": round(v["total_sales"], 2)}
            for v in sorted(payments.values(), key=lambda x: -x["total_sales"])
        ],
        "shipping_companies": [
            v for v in sorted(shippings.values(), key=lambda x: -x["orders_count"])
        ],
        "order_sources": [
            {**v, "total_sales": round(v["total_sales"], 2)}
            for v in sorted(sources.values(), key=lambda x: -x["orders_count"])
        ],
        "orders_sample": sample_orders,
        "detected_columns": {
            "total": headers[col_total] if col_total is not None else None,
            "payment": headers[col_payment] if col_payment is not None else None,
            "shipping": headers[col_shipping] if col_shipping is not None else None,
            "source": headers[col_source] if col_source is not None else None,
            "order_id": headers[col_order] if col_order is not None else None,
        },
    }


def normalize_name(name: str) -> str:
    """Lower-cased and stripped name for fuzzy matching settings vs file values."""
    if name is None:
        return ""
    n = str(name).strip().lower()
    n = re.sub(r"\s+", " ", n)
    # Remove Arabic diacritics
    n = re.sub(r"[\u064B-\u0652]", "", n)
    return n


def match_settings(
    parsed: dict,
    payment_settings: list[dict],
    shipping_settings: list[dict],
) -> dict:
    """Cross-reference parsed totals with user-provided commission rates and shipping costs.

    Payment fee formula:
        base_commission = (total_sales * commission_percent / 100)
                        + (orders_count * fixed_fee)
        vat_amount      = base_commission * vat_percent / 100
        fee_amount      = base_commission + vat_amount

    Unmatched names get 0 / 0 / 0 but are still returned.
    """
    # Build lookup: normalized name -> full config dict
    payment_map = {
        normalize_name(p["name"]): {
            "commission_percent": float(p.get("commission_percent", 0) or 0),
            "fixed_fee": float(p.get("fixed_fee", 0) or 0),
            "vat_percent": float(p.get("vat_percent", 0) or 0),
        }
        for p in payment_settings
    }
    shipping_map = {
        normalize_name(s["name"]): {
            "cost_per_order": float(s.get("cost_per_order", 0) or 0),
            "vat_percent": float(s.get("vat_percent", 0) or 0),
        }
        for s in shipping_settings
    }

    payment_breakdown = []
    total_payment_fees = 0.0
    for pm in parsed["payment_methods"]:
        key = normalize_name(pm["name"])
        cfg = payment_map.get(key)
        # fuzzy contains match if exact key not found
        if cfg is None:
            for k, v in payment_map.items():
                if k and (k in key or key in k):
                    cfg = v
                    break
        matched = cfg is not None
        cfg = cfg or {"commission_percent": 0.0, "fixed_fee": 0.0, "vat_percent": 0.0}

        pct = cfg["commission_percent"]
        fixed = cfg["fixed_fee"]
        vat_pct = cfg["vat_percent"]

        base_commission = round(pm["total_sales"] * pct / 100.0 + pm["orders_count"] * fixed, 2)
        vat_amount = round(base_commission * vat_pct / 100.0, 2)
        fee_amount = round(base_commission + vat_amount, 2)
        total_payment_fees += fee_amount

        payment_breakdown.append({
            "name": pm["name"],
            "orders_count": pm["orders_count"],
            "total_sales": pm["total_sales"],
            "commission_percent": pct,
            "fixed_fee": fixed,
            "vat_percent": vat_pct,
            "base_commission": base_commission,
            "vat_amount": vat_amount,
            "fee_amount": fee_amount,
            "net_amount": round(pm["total_sales"] - fee_amount, 2),
            "matched": matched,
        })

    shipping_breakdown = []
    total_shipping_cost = 0.0
    for sc in parsed["shipping_companies"]:
        key = normalize_name(sc["name"])
        cfg = shipping_map.get(key)
        if cfg is None:
            for k, v in shipping_map.items():
                if k and (k in key or key in k):
                    cfg = v
                    break
        matched = cfg is not None
        cfg = cfg or {"cost_per_order": 0.0, "vat_percent": 0.0}

        cost = cfg["cost_per_order"]
        vat_pct = cfg["vat_percent"]
        base_cost = round(cost * sc["orders_count"], 2)
        vat_amount = round(base_cost * vat_pct / 100.0, 2)
        total = round(base_cost + vat_amount, 2)
        total_shipping_cost += total
        shipping_breakdown.append({
            "name": sc["name"],
            "orders_count": sc["orders_count"],
            "cost_per_order": cost,
            "base_cost": base_cost,
            "vat_percent": vat_pct,
            "vat_amount": vat_amount,
            "total_cost": total,
            "matched": matched,
        })

    return {
        "payment_breakdown": payment_breakdown,
        "shipping_breakdown": shipping_breakdown,
        "total_payment_fees": round(total_payment_fees, 2),
        "total_shipping_cost": round(total_shipping_cost, 2),
    }
