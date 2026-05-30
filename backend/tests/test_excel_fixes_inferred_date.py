"""Test: Excel upload corrects inferred dates from Make.com webhooks.

User journey:
1. Make.com webhook arrives without created_at → order saved with
   order_date=today (inferred=True).
2. Merchant later uploads Salla Excel export containing the same order.
3. Excel has the authoritative created_at → order_date is replaced with
   the correct date and order_date_inferred=False.

This is the merchant's primary "fix" path for old missing-date orders.
"""
import io
import os
import uuid

import openpyxl
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"u{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "T", "email": email, "password": "test12345"},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _build_salla_excel(order_number: str, order_date: str, amount: float = 100.0) -> bytes:
    """Construct a minimal Salla-like xlsx blob with the parser's expected columns."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([
        "رقم الطلب",
        "تاريخ إنشاء الطلب",
        "حالة الطلب",
        "اسم العميل",
        "جوال العميل",
        "طريقة الدفع",
        "شركة الشحن",
        "تكلفة الشحن",
        "المجموع الفرعي",
        "الخصم",
        "إجمالي الطلب",
        "العملة",
        "مصدر الطلب",
    ])
    ws.append([
        order_number,
        order_date,
        "تم التوصيل",
        "Test",
        "0500000000",
        "مدى",
        "سمسا",
        25.0,
        amount - 25.0,
        0.0,
        amount,
        "SAR",
        "موقع",
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_upload_replaces_inferred_make_date():
    """End-to-end: Make webhook (no date) → Excel upload (with date) → date fixed."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}

    # Step 1: webhook arrives without created_at → inferred date
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "FIXME-EXCEL-1",
            "total": 500,
            "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    orders = requests.get(f"{API}/webhook/orders?limit=10", headers=h).json()["orders"]
    target = next(o for o in orders if o["order_number"] == "FIXME-EXCEL-1")
    assert target["order_date_inferred"] is True
    inferred_date = target["order_date"]  # =today

    # Stats: inferred = 1
    stats = requests.get(f"{API}/webhook/stats", headers=h).json()
    assert stats["orders_inferred_date"] == 1

    # Step 2: upload Excel with same order_number BUT authoritative March date
    xlsx_bytes = _build_salla_excel(
        order_number="FIXME-EXCEL-1",
        order_date="2026-03-12 10:00:00",
        amount=500.0,
    )
    files = {"file": ("salla.xlsx", xlsx_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r_up = requests.post(
        f"{API}/analyses",
        files=files,
        data={"name": "Test Salla Export"},
        headers=h,
    )
    r_up.raise_for_status()

    # Step 3: verify order_date was replaced with authoritative value
    orders = requests.get(f"{API}/webhook/orders?limit=10", headers=h).json()["orders"]
    target = next(o for o in orders if o["order_number"] == "FIXME-EXCEL-1")
    assert target["order_date"] == "2026-03-12", f"date not corrected: {target.get('order_date')}"
    assert target["order_date"] != inferred_date
    assert target["order_date_inferred"] is False

    # Step 4: stats now show inferred = 0 (no more approximate dates)
    stats = requests.get(f"{API}/webhook/stats", headers=h).json()
    assert stats["orders_inferred_date"] == 0

    # Step 5: order now appears in March, NOT in current month
    r_mar = requests.get(
        f"{API}/dashboard?from_date=2026-03-01&to_date=2026-03-31",
        headers=h,
    ).json()
    assert r_mar["totals"]["total_orders"] == 1
