"""Tests for shipping companies discovery + auto-add + deferred-cost fallback.

Issues fixed:
(b) shipping-accounts page showed 0 owed when settings.cost was missing/None,
    despite orders carrying a real shipping_cost. Fixed by preferring the
    order's actual shipping_cost over the settings value.
(c) Shipping companies present in orders but not configured in settings were
    silently ignored. Fixed by adding /shipping-companies/discover + autodiscover.
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
    r = requests.post(f"{API}/auth/register",
                      json={"name": "T", "email": email, "password": "test12345"})
    r.raise_for_status()
    return r.json()["access_token"]


def _make_xlsx(orders):
    wb = openpyxl.Workbook(); ws = wb.active
    headers = [""] * 60
    headers[0] = "رقم الطلب"; headers[1] = "حالة الطلب"; headers[2] = "إجمالي الطلب"
    headers[3] = "طريقة الدفع"; headers[4] = "شركة الشحن"
    headers[16] = "تاريخ إنشاء الطلب"
    # column F (index 5) commonly holds shipping_cost in Salla exports — try multiple
    # places; the parser uses keyword match — add explicit header
    headers[5] = "تكلفة الشحن"
    ws.append(headers)
    for o in orders:
        row = [""] * 60
        row[0] = o["order_number"]; row[1] = o.get("status", "تم التوصيل")
        row[2] = o["amount"]; row[3] = "مدى"
        row[4] = o["shipping_company"]; row[5] = o.get("shipping_cost", 0)
        row[16] = "2026-05-15"
        ws.append(row)
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def _upload(h, orders, name="t"):
    content = _make_xlsx(orders)
    r = requests.post(f"{API}/analyses", headers=h,
                      files={"file": ("o.xlsx", content,
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                      params={"name": name})
    r.raise_for_status()
    return r.json()


def test_discover_endpoint_returns_three_lists():
    token = _register(); h = {"Authorization": f"Bearer {token}"}
    # Seed: orders with two shipping companies, one of which is NOT in default settings
    _upload(h, [
        {"order_number": "D1", "amount": 200, "shipping_company": "سمسا", "shipping_cost": 15},
        {"order_number": "D2", "amount": 300, "shipping_company": "iMile للتوصيل", "shipping_cost": 25},
        {"order_number": "D3", "amount": 250, "shipping_company": "iMile للتوصيل", "shipping_cost": 25},
    ])
    r = requests.get(f"{API}/shipping-companies/discover", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "configured" in d and "observed" in d and "unconfigured" in d
    obs_names = {o["name"] for o in d["observed"]}
    assert "سمسا" in obs_names
    assert "iMile للتوصيل" in obs_names
    # iMile is not in DEFAULT_SHIPPING_COMPANIES → unconfigured
    unconf_names = {u["name"] for u in d["unconfigured"]}
    assert "iMile للتوصيل" in unconf_names
    # Each observed item carries orders_count + avg_shipping_cost
    imile = next(o for o in d["observed"] if o["name"] == "iMile للتوصيل")
    assert imile["orders_count"] == 2
    assert imile["avg_shipping_cost"] == 25.0


def test_autodiscover_adds_unconfigured_to_settings():
    token = _register(); h = {"Authorization": f"Bearer {token}"}
    _upload(h, [
        {"order_number": "A1", "amount": 100, "shipping_company": "iMile للتوصيل", "shipping_cost": 30},
        {"order_number": "A2", "amount": 200, "shipping_company": "iMile للتوصيل", "shipping_cost": 40},
    ])
    r = requests.post(f"{API}/shipping-companies/autodiscover", headers=h, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["added"][0]["name"] == "iMile للتوصيل"
    assert body["added"][0]["cost"] == 35.0  # avg of 30 and 40
    # Settings now contains it
    s = requests.get(f"{API}/settings", headers=h).json()
    names = [sc["name"] for sc in s["shipping_companies"]]
    assert "iMile للتوصيل" in names


def test_autodiscover_with_names_filter():
    """Only the whitelisted names are added."""
    token = _register(); h = {"Authorization": f"Bearer {token}"}
    _upload(h, [
        {"order_number": "X1", "amount": 100, "shipping_company": "X-Logistics", "shipping_cost": 10},
        {"order_number": "Y1", "amount": 100, "shipping_company": "Y-Express", "shipping_cost": 20},
    ])
    r = requests.post(f"{API}/shipping-companies/autodiscover", headers=h,
                      json={"names": ["X-Logistics"]})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["added"][0]["name"] == "X-Logistics"


def test_balances_use_order_shipping_cost_when_settings_cost_missing():
    """Regression for bug (b): deferred shipping owed must use the actual
    shipping_cost from each order when settings doesn't have a cost set.
    """
    token = _register(); h = {"Authorization": f"Bearer {token}"}
    # Upload orders → settings auto-provisions companies WITHOUT cost values
    _upload(h, [
        {"order_number": "B1", "amount": 100, "shipping_company": "سمسا", "shipping_cost": 12},
        {"order_number": "B2", "amount": 100, "shipping_company": "سمسا", "shipping_cost": 15},
    ])
    # Mark سمسا as deferred (default behaviour for that brand) but DON'T set cost
    s = requests.get(f"{API}/settings", headers=h).json()
    for sc in s["shipping_companies"]:
        if sc["name"] == "سمسا":
            sc["is_deferred"] = True
            sc["cost"] = 0  # explicit zero — simulating the bug condition
            sc["vat_rate"] = 0
    requests.put(f"{API}/settings", json={
        "payment_methods": s["payment_methods"],
        "shipping_companies": s["shipping_companies"],
    }, headers=h)

    # Now hit /shipping-accounts which uses the deferred-cost calculation
    r = requests.get(f"{API}/shipping-accounts", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    samsa = next(c for c in body["accounts"] if c["name"] == "سمسا")
    # Owed should be 12 + 15 = 27 (NOT zero — order shipping_cost wins)
    assert samsa["total_owed"] == 27.0, samsa
    assert samsa["orders_count"] == 2
