"""Tests for the unified data layer:
- GET /api/order-statuses returns distinct statuses with counts
- PUT /api/settings accepts report_included_statuses
- /api/dashboard applies the report_included_statuses filter
- /api/balances applies the report_included_statuses filter
"""
import os
import uuid

import requests

from tests.test_order_date_filter import _make_xlsx_with_date_col_q

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"u{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(f"{API}/auth/register",
                      json={"name": "T", "email": email, "password": "test12345"})
    r.raise_for_status()
    return r.json()["access_token"]


def _upload(h, orders, name="t"):
    content = _make_xlsx_with_date_col_q(orders)
    r = requests.post(f"{API}/analyses", headers=h,
                      files={"file": ("o.xlsx", content,
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                      params={"name": name})
    r.raise_for_status()
    return r.json()


def _seed_orders_with_statuses(h):
    """Create unified_orders with varying statuses via the webhook."""
    # _upload uses Excel which sets a fixed status. Instead push via Make.com webhook.
    # But webhook requires a token; simplest: upload first, then update statuses via DB? No.
    # We'll just use Excel uploads with different statuses by patching the helper inline.
    import io, openpyxl
    def _make_xlsx(orders):
        wb = openpyxl.Workbook(); ws = wb.active
        headers = [""] * 60
        headers[0] = "رقم الطلب"; headers[1] = "حالة الطلب"; headers[2] = "إجمالي الطلب"
        headers[3] = "طريقة الدفع"; headers[4] = "شركة الشحن"; headers[16] = "تاريخ إنشاء الطلب"
        ws.append(headers)
        for o in orders:
            row = [""] * 60
            row[0] = o["order_number"]; row[1] = o["status"]; row[2] = o["amount"]
            row[3] = "مدى"; row[4] = "سمسا"; row[16] = o["date"]
            ws.append(row)
        buf = io.BytesIO(); wb.save(buf); return buf.getvalue()

    content = _make_xlsx([
        {"order_number": "S1", "status": "تم التوصيل", "amount": 100, "date": "2026-03-01"},
        {"order_number": "S2", "status": "تم التوصيل", "amount": 200, "date": "2026-03-02"},
        {"order_number": "S3", "status": "ملغي",       "amount": 999, "date": "2026-03-03"},
        {"order_number": "S4", "status": "قيد التنفيذ", "amount": 50, "date": "2026-03-04"},
    ])
    r = requests.post(f"{API}/analyses", headers=h,
                      files={"file": ("o.xlsx", content,
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                      params={"name": "seed"})
    r.raise_for_status()


def test_order_statuses_endpoint():
    token = _register(); h = {"Authorization": f"Bearer {token}"}
    _seed_orders_with_statuses(h)
    r = requests.get(f"{API}/order-statuses", headers=h)
    assert r.status_code == 200
    names = {s["name"]: s["count"] for s in r.json()["statuses"]}
    assert names.get("تم التوصيل") == 2
    assert names.get("ملغي") == 1
    assert names.get("قيد التنفيذ") == 1


def test_settings_persist_report_included_statuses():
    token = _register(); h = {"Authorization": f"Bearer {token}"}
    # Read current settings (defaults provisioned on register)
    s = requests.get(f"{API}/settings", headers=h).json()
    assert s.get("report_included_statuses") == []
    # Update
    payload = {
        "payment_methods": s["payment_methods"],
        "shipping_companies": s["shipping_companies"],
        "report_included_statuses": ["تم التوصيل", "delivered"],
    }
    r = requests.put(f"{API}/settings", json=payload, headers=h)
    assert r.status_code == 200, r.text
    # Re-read
    s2 = requests.get(f"{API}/settings", headers=h).json()
    assert s2["report_included_statuses"] == ["تم التوصيل", "delivered"]


def test_dashboard_filters_by_report_included_statuses():
    token = _register(); h = {"Authorization": f"Bearer {token}"}
    _seed_orders_with_statuses(h)

    # No filter → all 4 orders counted (sum = 100+200+999+50 = 1349)
    t = requests.get(f"{API}/dashboard", headers=h).json()["totals"]
    assert t["total_orders"] == 4
    assert t["total_sales"] == 1349.0
    assert t["report_included_statuses_active"] is False

    # Activate the filter → only "تم التوصيل" (orders S1, S2) → 2 orders, sales=300
    s = requests.get(f"{API}/settings", headers=h).json()
    requests.put(f"{API}/settings", headers=h, json={
        "payment_methods": s["payment_methods"],
        "shipping_companies": s["shipping_companies"],
        "report_included_statuses": ["تم التوصيل"],
    })
    t2 = requests.get(f"{API}/dashboard", headers=h).json()["totals"]
    assert t2["total_orders"] == 2
    assert t2["total_sales"] == 300.0
    assert t2["report_included_statuses_active"] is True


def test_balances_respect_report_included_statuses():
    """The balances endpoint should also honour report_included_statuses."""
    token = _register(); h = {"Authorization": f"Bearer {token}"}
    _seed_orders_with_statuses(h)

    # Configure: shipping considered approved when status == "تم التوصيل"
    s = requests.get(f"{API}/settings", headers=h).json()
    requests.put(f"{API}/settings", headers=h, json={
        "payment_methods": s["payment_methods"],
        "shipping_companies": s["shipping_companies"],
        "shipping_approved_statuses": ["تم التوصيل"],
        "cod_approved_statuses": ["تم التوصيل"],
        "report_included_statuses": ["تم التوصيل"],  # exclude others entirely
    })
    bal = requests.get(f"{API}/balances", headers=h).json()
    # Only 2 orders remain in scope → none of S3/S4 should affect balances
    # شحن mada (سمسا default cost from settings) — depends on settings; we just
    # assert no values from excluded orders bleed in.
    assert bal["shipping"]["total_unapproved"] == 0, bal
    assert bal["cod"]["total_unapproved"] == 0, bal
