"""Tests for Excel date detection (column Q fallback + alt header keywords)
and dashboard aggregation filtering by per-order order_date (not analysis date).
"""
import io
import os
import time
import uuid

import openpyxl
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"u{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(f"{API}/auth/register",
                      json={"name": "T", "email": email, "password": "test12345"})
    r.raise_for_status()
    return r.json()["access_token"]


def _make_xlsx_with_date_col_q(orders, header_label="تاريخ إنشاء الطلب"):
    """Build a Salla-style XLSX where the order-creation date sits at column Q (index 16)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    # Minimal header set required by parser
    headers = [""] * 60
    headers[0] = "رقم الطلب"          # A
    headers[1] = "حالة الطلب"         # B
    headers[2] = "إجمالي الطلب"        # C — total amount
    headers[3] = "طريقة الدفع"        # D
    headers[4] = "شركة الشحن"          # E
    headers[16] = header_label        # Q — date column
    ws.append(headers)
    for o in orders:
        row = [""] * 60
        row[0] = o["order_number"]
        row[1] = "completed"
        row[2] = o["amount"]
        row[3] = "مدى"
        row[4] = "سمسا"
        row[16] = o["date"]            # column Q
        row[52] = "تطبيق سلة"            # BA — source
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_date_column_q_detected():
    """Uploading XLSX with date at column Q (header 'تاريخ إنشاء الطلب') should
    populate order_date on the unified_orders entries."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}

    content = _make_xlsx_with_date_col_q([
        {"order_number": "9001", "amount": 100, "date": "2026-01-15"},
        {"order_number": "9002", "amount": 200, "date": "2026-02-20"},
    ])
    r = requests.post(f"{API}/analyses",
                      headers=h,
                      files={"file": ("orders.xlsx", content,
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                      params={"name": "T1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["orders_imported"] == 2

    # Filter Jan only via dashboard → should yield 1 order, sales=100
    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-01-01", "to_date": "2026-01-31"},
                     headers=h)
    assert r.status_code == 200
    t = r.json()["totals"]
    assert t["total_orders"] == 1, f"expected 1 January order, got {t}"
    assert t["total_sales"] == 100.0

    # Filter Feb only
    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-02-01", "to_date": "2026-02-28"},
                     headers=h)
    t = r.json()["totals"]
    assert t["total_orders"] == 1, f"expected 1 February order, got {t}"
    assert t["total_sales"] == 200.0


def test_excel_date_fallback_when_header_missing():
    """If no date header is detected at all but column Q has a date, the parser
    still falls back to column Q."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}

    # Use a non-matching header label so substring match fails; parser should
    # fall back to SALLA_DATE_COL_INDEX (16).
    content = _make_xlsx_with_date_col_q([
        {"order_number": "8001", "amount": 50, "date": "2025-12-15"},
        {"order_number": "8002", "amount": 75, "date": "2026-03-10"},
    ], header_label="xyz_unknown_header")
    r = requests.post(f"{API}/analyses",
                      headers=h,
                      files={"file": ("orders.xlsx", content,
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                      params={"name": "T2"})
    assert r.status_code == 200, r.text

    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-03-01", "to_date": "2026-03-31"},
                     headers=h)
    t = r.json()["totals"]
    assert t["total_orders"] == 1
    assert t["total_sales"] == 75.0


def test_dashboard_uses_order_date_not_upload_date():
    """Regression: a single Excel upload tagged with one date can still contain
    orders spanning multiple months — and the dashboard MUST split them by
    each order's own creation date, not by the analysis upload date."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}

    # Upload one Excel with orders across THREE months but tag analysis date
    # at today (e.g. 2026-05-01).
    content = _make_xlsx_with_date_col_q([
        {"order_number": "7001", "amount": 100, "date": "2026-01-10"},
        {"order_number": "7002", "amount": 200, "date": "2026-02-10"},
        {"order_number": "7003", "amount": 300, "date": "2026-03-10"},
    ])
    r = requests.post(f"{API}/analyses",
                      headers=h,
                      files={"file": ("orders.xlsx", content,
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                      params={"name": "T3", "date": "2026-05-01"})
    assert r.status_code == 200, r.text

    # Filter Feb only — only order #7002 should match (sales=200)
    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-02-01", "to_date": "2026-02-28"},
                     headers=h)
    t = r.json()["totals"]
    assert t["total_orders"] == 1
    assert t["total_sales"] == 200.0

    # Filter the upload day itself — must NOT return orders (they're not dated today)
    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-05-01", "to_date": "2026-05-01"},
                     headers=h)
    t = r.json()["totals"]
    assert t["total_orders"] == 0
    assert t["total_sales"] == 0.0
