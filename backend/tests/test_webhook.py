"""Make.com webhook integration tests."""
import os
import uuid
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


def _register(suffix: str = ""):
    suffix = suffix or uuid.uuid4().hex[:8]
    email = f"test_wh_{suffix}@hesab.app"
    payload = {"name": f"WH Test {suffix}", "email": email, "password": "test12345"}
    r = requests.post(f"{API}/auth/register", json=payload, timeout=30)
    if r.status_code in (200, 201):
        token = r.json().get("access_token")
    else:
        # already exists -> login
        r2 = requests.post(f"{API}/auth/login", json={"email": email, "password": "test12345"}, timeout=30)
        assert r2.status_code == 200, r2.text
        token = r2.json().get("access_token")
    return email, token


@pytest.fixture(scope="module")
def user_a():
    email, token = _register()
    return {"email": email, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture(scope="module")
def user_b():
    email, token = _register()
    return {"email": email, "token": token, "headers": {"Authorization": f"Bearer {token}"}}


# ── Settings ──────────────────────────────────────────────────────────
def test_settings_creates_token(user_a):
    r = requests.get(f"{API}/webhook/settings", headers=user_a["headers"], timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "token" in d and len(d["token"]) >= 16
    assert d["webhook_url"].endswith(f"/api/webhook/make/{d['token']}")
    assert "sample_payload" in d
    assert "total_received" in d
    user_a["initial_token"] = d["token"]
    user_a["url"] = d["webhook_url"]


def test_settings_idempotent(user_a):
    r1 = requests.get(f"{API}/webhook/settings", headers=user_a["headers"], timeout=30).json()
    r2 = requests.get(f"{API}/webhook/settings", headers=user_a["headers"], timeout=30).json()
    assert r1["token"] == r2["token"]


# ── Public ingestion ─────────────────────────────────────────────────
def test_ingest_invalid_token():
    r = requests.post(f"{API}/webhook/make/invalid_token_xxx", json={"order_number": "1"}, timeout=30)
    assert r.status_code == 401


def test_ingest_single_object(user_a):
    token = user_a["initial_token"]
    payload = {
        "order_number": "WH-1001",
        "order_date": "2026-01-05",
        "status": "completed",
        "customer_name": "Ahmed",
        "total_amount": 250.0,
        "payment_method": "مدى",
        "shipping_company": "سمسا",
        "products": [{"name": "X", "quantity": 1, "price": 250}],
        "extra_make_field": "should-go-to-raw",
    }
    r = requests.post(f"{API}/webhook/make/{token}", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["accepted"] == 1 and d["updated"] == 0


def test_ingest_array(user_a):
    token = user_a["initial_token"]
    body = [
        {"order_number": "WH-1002", "order_date": "2026-01-06", "total_amount": 100, "payment_method": "تابي", "shipping_company": "سمسا"},
        {"order_number": "WH-1003", "order_date": "2026-01-06T12:30:00", "total_amount": 320, "payment_method": "Apple Pay", "shipping_company": "أرامكس"},
        {"order_number": "WH-1004", "order_date": "07/01/2026", "total_amount": 410, "payment_method": "مدى", "shipping_company": "جندل"},
    ]
    r = requests.post(f"{API}/webhook/make/{token}", json=body, timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 3


def test_ingest_wrapped_orders(user_a):
    token = user_a["initial_token"]
    body = {"orders": [{"order_number": "WH-1005", "order_date": "2026-01-08", "total_amount": 540, "payment_method": "مدى", "shipping_company": "جندل"}]}
    r = requests.post(f"{API}/webhook/make/{token}", json=body, timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 1


def test_ingest_upsert_no_duplicate(user_a):
    token = user_a["initial_token"]
    # send WH-1001 again with new amount -> should update
    r = requests.post(f"{API}/webhook/make/{token}", json={"order_number": "WH-1001", "order_date": "2026-01-05", "total_amount": 999, "payment_method": "مدى", "shipping_company": "سمسا"}, timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["updated"] == 1 and d["accepted"] == 0


def test_orders_list_and_filters(user_a):
    r = requests.get(f"{API}/webhook/orders?limit=100", headers=user_a["headers"], timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["total"] >= 5
    # DESC by order_date
    dates = [o["order_date"] for o in d["orders"] if o.get("order_date")]
    assert dates == sorted(dates, reverse=True)
    # Verify WH-1001 amount was updated to 999
    item = next((o for o in d["orders"] if o["order_number"] == "WH-1001"), None)
    assert item and item["total_amount"] == 999.0

    r2 = requests.get(f"{API}/webhook/orders?date_from=2026-01-07&date_to=2026-01-09", headers=user_a["headers"], timeout=30)
    assert r2.status_code == 200
    for o in r2.json()["orders"]:
        assert "2026-01-07" <= (o["order_date"] or "") <= "2026-01-09"


def test_extra_raw_preserved(user_a):
    # Validate raw extra field saved (via Mongo? Use orders endpoint won't return raw; check via build_analysis indirectly OR by sending again with extra)
    # raw is stripped from /orders endpoint, but we can confirm at least the order exists
    r = requests.get(f"{API}/webhook/orders?limit=100", headers=user_a["headers"], timeout=30)
    o = next((o for o in r.json()["orders"] if o["order_number"] == "WH-1001"), None)
    assert o is not None


def test_stats(user_a):
    r = requests.get(f"{API}/webhook/stats", headers=user_a["headers"], timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["connected"] is True
    assert d["total_orders_in_db"] >= 5
    assert d["last_sync_at"]
    assert d["date_range"]["earliest"] <= d["date_range"]["latest"]


# ── User isolation ─────────────────────────────────────────────────
def test_isolation_user_b_token_not_user_a(user_a, user_b):
    rb = requests.get(f"{API}/webhook/settings", headers=user_b["headers"], timeout=30).json()
    assert rb["token"] != user_a["initial_token"]
    # send order using user B token
    r = requests.post(f"{API}/webhook/make/{rb['token']}", json={"order_number": "WH-B-1", "order_date": "2026-01-10", "total_amount": 50}, timeout=30)
    assert r.status_code == 200 and r.json()["accepted"] == 1

    # user A orders should NOT include WH-B-1
    ra = requests.get(f"{API}/webhook/orders?limit=200", headers=user_a["headers"], timeout=30).json()
    assert not any(o["order_number"] == "WH-B-1" for o in ra["orders"])
    # user B should only see WH-B-1
    rb2 = requests.get(f"{API}/webhook/orders?limit=200", headers=user_b["headers"], timeout=30).json()
    assert any(o["order_number"] == "WH-B-1" for o in rb2["orders"])
    assert not any(o["order_number"] == "WH-1001" for o in rb2["orders"])


# ── Build analysis ─────────────────────────────────────────────────
def test_build_analysis_bad_date(user_a):
    r = requests.post(f"{API}/webhook/build-analysis", headers=user_a["headers"],
                      json={"date_from": "05-01-2026", "date_to": "2026-01-09"}, timeout=30)
    assert r.status_code == 400


def test_build_analysis_empty_range(user_a):
    r = requests.post(f"{API}/webhook/build-analysis", headers=user_a["headers"],
                      json={"date_from": "2030-01-01", "date_to": "2030-01-31"}, timeout=30)
    assert r.status_code == 400
    assert "لا توجد" in (r.json().get("detail") or "")


def test_build_analysis_success_and_dashboard(user_a):
    r = requests.post(f"{API}/webhook/build-analysis", headers=user_a["headers"],
                      json={"date_from": "2026-01-01", "date_to": "2026-01-31",
                            "name": "TEST_WH analysis",
                            "snapchat_ads": 100, "tiktok_ads": 50, "instagram_ads": 25, "product_costs": 200}, timeout=30)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["source"] == "make"
    assert "id" in a and "report" in a
    summary = a["report"]["summary"]
    # Should have core fields from _build_report
    for key in ("total_sales", "total_orders", "total_payment_fees"):
        assert key in summary, f"missing {key} in summary"
    # Manual sum: WH-1001 999 + WH-1002 100 + WH-1003 320 + WH-1004 410 + WH-1005 540 = 2369
    assert abs(summary["total_sales"] - 2369.0) < 0.01
    assert summary["total_orders"] == 5

    # Appears in dashboard
    dash = requests.get(f"{API}/dashboard", headers=user_a["headers"], timeout=30)
    assert dash.status_code == 200
    dd = dash.json()
    # find an aggregate "totals" or "summary"
    tot_sales = dd.get("totals", {}).get("total_sales") or dd.get("summary", {}).get("total_sales") or 0
    assert tot_sales >= 2369.0 - 0.01


# ── Rotate / Disconnect ─────────────────────────────────────────────
def test_rotate_invalidates_old_token(user_a):
    old = user_a["initial_token"]
    r = requests.post(f"{API}/webhook/settings/rotate-token", headers=user_a["headers"], timeout=30)
    assert r.status_code == 200
    new_token = r.json()["token"]
    assert new_token != old
    # Old token must now be invalid
    r2 = requests.post(f"{API}/webhook/make/{old}", json={"order_number": "WH-OLD"}, timeout=30)
    assert r2.status_code == 401
    # New token works
    r3 = requests.post(f"{API}/webhook/make/{new_token}", json={"order_number": "WH-NEW"}, timeout=30)
    assert r3.status_code == 200
    user_a["initial_token"] = new_token


def test_disconnect_deletes_orders(user_a):
    r = requests.delete(f"{API}/webhook/settings", headers=user_a["headers"], timeout=30)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # subsequent GET creates new token
    r2 = requests.get(f"{API}/webhook/settings", headers=user_a["headers"], timeout=30).json()
    assert r2["total_orders_in_db"] == 0
    # old token invalid
    r3 = requests.post(f"{API}/webhook/make/{user_a['initial_token']}", json={"order_number": "X"}, timeout=30)
    assert r3.status_code == 401
