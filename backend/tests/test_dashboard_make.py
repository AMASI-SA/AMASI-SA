"""Dashboard ↔ Make.com live orders aggregation tests.

These verify the fix that streams unified_orders (data_source='make') directly
into GET /api/dashboard totals so users see their webhook-ingested orders
without needing to click "Build analysis".
"""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


# ── helpers ───────────────────────────────────────────────────────────────────
def _register():
    suffix = uuid.uuid4().hex[:10]
    email = f"test_dashmake_{suffix}@hesab.app"
    payload = {"name": f"Dash Make {suffix}", "email": email, "password": "test12345"}
    r = requests.post(f"{API}/auth/register", json=payload, timeout=30)
    assert r.status_code in (200, 201), r.text
    token = r.json()["access_token"]
    return email, token


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _get_webhook_token(headers):
    r = requests.get(f"{API}/webhook/settings", headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _post_orders(wh_token, orders):
    r = requests.post(f"{API}/webhook/make/{wh_token}", json=orders, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def user():
    email, token = _register()
    headers = _headers(token)
    wh_token = _get_webhook_token(headers)
    return {"email": email, "token": token, "headers": headers, "wh_token": wh_token}


@pytest.fixture(scope="module")
def user_b():
    email, token = _register()
    headers = _headers(token)
    wh_token = _get_webhook_token(headers)
    return {"email": email, "token": token, "headers": headers, "wh_token": wh_token}


# 5 Make orders covering varied payment methods + shipping companies.
# Values calibrated to the defaults from auth.DEFAULT_PAYMENT_METHODS &
# DEFAULT_SHIPPING_COMPANIES so we can sanity-check derived numbers.
MAKE_ORDERS = [
    # مدى → fee = (100 * 1% + 1) * 1.15 = 2.30 ; سمسا shipping = 23 * 1.15 = 26.45
    {"order_number": "DM-1", "order_date": "2026-01-15", "total_amount": 100.0,
     "payment_method": "مدى", "shipping_company": "سمسا", "order_status": "completed"},
    # تمارا → fee = 200 * 6.99% * 1.15 = 16.077 ; أرامكس = 27 * 1.15 = 31.05
    {"order_number": "DM-2", "order_date": "2026-01-15", "total_amount": 200.0,
     "payment_method": "تمارا", "shipping_company": "أرامكس", "order_status": "completed"},
    # تابي → fee = 300 * 5% * 1.15 = 17.25 ; سمسا = 26.45
    {"order_number": "DM-3", "order_date": "2026-01-16", "total_amount": 300.0,
     "payment_method": "تابي", "shipping_company": "سمسا", "order_status": "completed"},
    # إمكان → fee = 150 * 5% * 1.15 = 8.625 ; جندل = 19 * 1.15 = 21.85
    {"order_number": "DM-4", "order_date": "2026-01-16", "total_amount": 150.0,
     "payment_method": "إمكان", "shipping_company": "جندل", "order_status": "completed"},
    # Apple Pay → generic-card fallback = (250 * 2.2% + 1) * 1.15 = 7.475 ; سمسا = 26.45
    {"order_number": "DM-5", "order_date": "2026-01-17", "total_amount": 250.0,
     "payment_method": "Apple Pay", "shipping_company": "سمسا", "order_status": "completed"},
]
TOTAL_MAKE_SALES = sum(o["total_amount"] for o in MAKE_ORDERS)  # 1000.0
TOTAL_MAKE_ORDERS = len(MAKE_ORDERS)  # 5

# Expected fee approximations (with 15% VAT)
EXP_FEES_MADA = (100 * 0.01 + 1) * 1.15           # 2.30
EXP_FEES_TAMARA = (200 * 0.0699) * 1.15            # 16.077
EXP_FEES_TABBY = (300 * 0.05) * 1.15               # 17.25
EXP_FEES_EMKAN = (150 * 0.05) * 1.15               # 8.625
EXP_FEES_APPLEPAY = (250 * 0.022 + 1) * 1.15       # 7.475
EXP_TOTAL_FEES = (
    EXP_FEES_MADA + EXP_FEES_TAMARA + EXP_FEES_TABBY + EXP_FEES_EMKAN + EXP_FEES_APPLEPAY
)

# Shipping (3 سمسا + 1 أرامكس + 1 جندل) with 15% VAT
EXP_TOTAL_SHIPPING = (3 * 23 + 27 + 19) * 1.15  # 132.25


# ── baseline ──────────────────────────────────────────────────────────────────
def test_baseline_dashboard_no_make_orders(user):
    """Before any orders are posted, make_orders_count must be 0."""
    r = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "totals" in d
    assert "make_orders_count" in d["totals"], "missing make_orders_count field"
    assert d["totals"]["make_orders_count"] == 0
    assert d["totals"]["total_sales"] == 0
    assert d["totals"]["total_orders"] == 0


# ── ingestion ────────────────────────────────────────────────────────────────
def test_inject_make_orders(user):
    """Inject 5 Make.com orders via webhook."""
    res = _post_orders(user["wh_token"], MAKE_ORDERS)
    # webhook_routes returns counts in this shape
    assert res.get("ok") is True or res.get("received") or res.get("inserted") is not None, res

    # Verify they landed in unified_orders
    r = requests.get(f"{API}/webhook/orders?limit=100", headers=user["headers"], timeout=30)
    assert r.status_code == 200
    orders = r.json().get("orders", r.json())
    if isinstance(orders, dict):
        orders = orders.get("orders", [])
    nums = {o["order_number"] for o in orders}
    for o in MAKE_ORDERS:
        assert o["order_number"] in nums, f"missing {o['order_number']} in unified_orders"


# ── core: dashboard rolls Make orders in ─────────────────────────────────────
def test_dashboard_includes_make_total_sales_and_orders(user):
    r = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30)
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    # 5 Make orders, 1000 SAR in sales
    assert t["make_orders_count"] == TOTAL_MAKE_ORDERS, t
    assert abs(t["total_sales"] - TOTAL_MAKE_SALES) < 0.01, t
    assert t["total_orders"] == TOTAL_MAKE_ORDERS, t


def test_dashboard_payment_fees_for_make_orders(user):
    r = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30)
    t = r.json()["totals"]
    # ±0.5 SAR rounding tolerance
    assert abs(t["total_payment_fees"] - EXP_TOTAL_FEES) < 0.5, (
        t["total_payment_fees"], EXP_TOTAL_FEES,
    )


def test_dashboard_shipping_cost_for_make_orders(user):
    r = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30)
    t = r.json()["totals"]
    assert abs(t["total_shipping_cost"] - EXP_TOTAL_SHIPPING) < 0.5, (
        t["total_shipping_cost"], EXP_TOTAL_SHIPPING,
    )


def test_dashboard_bnpl_split(user):
    """tamara_fees + tabby_fees + emkan_fees ⇒ bnpl_fees ; mada/applepay ⇒ electronic_net."""
    r = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30)
    t = r.json()["totals"]
    # Tamara
    assert abs(t["tamara_fees"] - EXP_FEES_TAMARA) < 0.5, t
    # Tabby
    assert abs(t["tabby_fees"] - EXP_FEES_TABBY) < 0.5, t
    # Emkan
    assert abs(t["emkan_fees"] - EXP_FEES_EMKAN) < 0.5, t
    # BNPL aggregate
    assert abs(t["bnpl_fees"] - (EXP_FEES_TAMARA + EXP_FEES_TABBY + EXP_FEES_EMKAN)) < 0.5, t
    # other (electronic) = مدى + Apple Pay
    assert abs(t["other_payment_fees"] - (EXP_FEES_MADA + EXP_FEES_APPLEPAY)) < 0.5, t
    # electronic_net = electronic sales - electronic fees ; electronic sales = 100 + 250 = 350
    assert abs(t["electronic_net"] - (350 - (EXP_FEES_MADA + EXP_FEES_APPLEPAY))) < 0.5, t


def test_dashboard_expected_salla_transfer_formula(user):
    r = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30)
    t = r.json()["totals"]
    expected = t["total_sales"] - t["total_payment_fees"] - t["regular_shipping_cost"]
    assert abs(t["expected_salla_transfer"] - expected) < 0.01, t


def test_dashboard_monthly_includes_make(user):
    """Monthly trend `sales` line must include Make orders (all in 2026-01)."""
    r = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30)
    monthly = r.json().get("monthly", [])
    jan = next((m for m in monthly if m["month"] == "2026-01"), None)
    assert jan is not None, monthly
    assert abs(jan["sales"] - TOTAL_MAKE_SALES) < 0.01, jan


# ── date filter ───────────────────────────────────────────────────────────────
def test_dashboard_date_filter_applies_to_make(user):
    """from_date/to_date must clamp Make orders too."""
    # Only 2026-01-16 → DM-3 (300) + DM-4 (150) = 450, 2 orders
    r = requests.get(
        f"{API}/dashboard?from_date=2026-01-16&to_date=2026-01-16",
        headers=user["headers"], timeout=30,
    )
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    assert t["make_orders_count"] == 2, t
    assert abs(t["total_sales"] - 450.0) < 0.01, t
    assert t["total_orders"] == 2, t


def test_dashboard_date_filter_excludes_all_make(user):
    """Out-of-range date filter must yield zero Make orders."""
    r = requests.get(
        f"{API}/dashboard?from_date=2030-01-01&to_date=2030-01-31",
        headers=user["headers"], timeout=30,
    )
    t = r.json()["totals"]
    assert t["make_orders_count"] == 0, t
    assert t["total_sales"] == 0, t
    assert t["total_orders"] == 0, t


# ── Excel rows are NOT double-counted via Make aggregation ───────────────────
def test_excel_unified_rows_not_double_counted(user):
    """Insert an Excel-flagged unified_orders row via the same webhook user.

    The dashboard reads data_source='make' only — Excel rows are covered by the
    analyses collection. Manually inserting an order with data_source='excel'
    into unified_orders must NOT show up in dashboard totals from the Make path.

    Since the public API has no way to directly create data_source='excel'
    unified_orders rows without uploading a real Excel file (and creating an
    analysis), we instead assert:
       make_orders_count == count(make-only orders) — i.e. no leakage.
    """
    r_before = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30).json()
    cnt_before = r_before["totals"]["make_orders_count"]

    # Post another Make order
    _post_orders(user["wh_token"], [{
        "order_number": "DM-EX-1", "order_date": "2026-02-01",
        "total_amount": 77.0, "payment_method": "مدى", "shipping_company": "سمسا",
        "order_status": "completed",
    }])

    r_after = requests.get(f"{API}/dashboard", headers=user["headers"], timeout=30).json()
    cnt_after = r_after["totals"]["make_orders_count"]
    assert cnt_after == cnt_before + 1, (cnt_before, cnt_after)
    # Sales delta must equal exactly 77 (no double count)
    delta = r_after["totals"]["total_sales"] - r_before["totals"]["total_sales"]
    assert abs(delta - 77.0) < 0.01, delta


# ── isolation ────────────────────────────────────────────────────────────────
def test_per_user_isolation_dashboard(user, user_b):
    """user_b dashboard must be empty (no leakage from user)."""
    r = requests.get(f"{API}/dashboard", headers=user_b["headers"], timeout=30)
    assert r.status_code == 200
    t = r.json()["totals"]
    assert t["make_orders_count"] == 0, t
    assert t["total_sales"] == 0, t
    assert t["total_orders"] == 0, t


# ── empty dashboard remains stable ───────────────────────────────────────────
def test_empty_dashboard_for_fresh_user_has_no_make_key_pollution(user_b):
    """No Make orders for user_b → dashboard shape must still be intact."""
    r = requests.get(f"{API}/dashboard", headers=user_b["headers"], timeout=30)
    t = r.json()["totals"]
    for k in [
        "total_sales", "total_orders", "total_payment_fees",
        "total_shipping_cost", "bnpl_fees", "tamara_fees", "tabby_fees",
        "emkan_fees", "electronic_net", "expected_salla_transfer",
        "make_orders_count",
    ]:
        assert k in t, f"missing key {k}"
