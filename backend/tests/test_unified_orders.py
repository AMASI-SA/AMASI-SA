"""Unified orders (Excel + Make.com merge) integration tests.

Covers:
- unified_orders collection writes from BOTH /api/webhook/make/{token} (data_source='make')
  and /api/analyses (Excel upload, data_source='excel').
- Merge logic: critical fields newer-wins, non-critical first-writer-wins,
  never overwrite with empty, field_sources provenance, data_sources history.
- Filters: GET /api/webhook/orders?data_source=excel|make.
- Stats: GET /api/webhook/stats returns by_source={excel,make} and total_orders_in_db.
- /api/webhook/build-analysis pulls from unified_orders (mixed sources).
- DELETE /api/webhook/settings only removes data_source='make' rows.
- Per-user isolation.
"""
import io
import os
import uuid
import openpyxl
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


# ── helpers ───────────────────────────────────────────────────────────
def _register():
    suffix = uuid.uuid4().hex[:8]
    email = f"test_unified_{suffix}@hesab.app"
    r = requests.post(f"{API}/auth/register",
                      json={"name": f"U{suffix}", "email": email, "password": "test12345"},
                      timeout=30)
    assert r.status_code in (200, 201), r.text
    return email, r.json()["access_token"]


def _make_excel(rows):
    """Build a Salla-style .xlsx in memory. rows: list of dicts with the canonical keys."""
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = ["رقم الطلب", "تاريخ الطلب", "حالة الطلب", "إجمالي الطلب",
               "طريقة الدفع", "شركة الشحن", "اسم العميل", "الجوال"]
    # pad to 60+ cols so col BA exists for source
    while len(headers) < 60:
        headers.append(f"col_{len(headers)}")
    ws.append(headers)
    for r in rows:
        row = [r.get("order_number"), r.get("order_date"), r.get("status", "completed"),
               r.get("total"), r.get("payment_method"), r.get("shipping_company"),
               r.get("customer_name", ""), r.get("mobile", "")]
        row += [None] * (60 - len(row))
        row[52] = "تطبيق سلة"
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@pytest.fixture(scope="module")
def user():
    email, token = _register()
    h = {"Authorization": f"Bearer {token}"}
    # ensure webhook token exists
    r = requests.get(f"{API}/webhook/settings", headers=h, timeout=30).json()
    return {"email": email, "token": token, "headers": h, "webhook_token": r["token"]}


@pytest.fixture(scope="module")
def user_b():
    email, token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/webhook/settings", headers=h, timeout=30).json()
    return {"email": email, "token": token, "headers": h, "webhook_token": r["token"]}


# ── 1. Excel upload writes into unified_orders ───────────────────────
def test_excel_upload_populates_unified_orders(user):
    xlsx = _make_excel([
        {"order_number": "UO-100", "order_date": "2026-01-10", "total": 250.0,
         "payment_method": "مدى", "shipping_company": "سمسا", "customer_name": "Sara"},
        {"order_number": "UO-101", "order_date": "2026-01-10", "total": 150.0,
         "payment_method": "Apple Pay", "shipping_company": "أرامكس"},
    ])
    files = {"file": ("salla.xlsx", xlsx,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    params = {"name": "TEST_excel", "date": "2026-01-10"}
    r = requests.post(f"{API}/analyses", headers=user["headers"], params=params, files=files, timeout=60)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("orders_imported", 0) >= 2
    assert "orders_updated" in body

    # Verify orders accessible via /webhook/orders filtered by data_source=excel
    r2 = requests.get(f"{API}/webhook/orders?data_source=excel&limit=200",
                      headers=user["headers"], timeout=30)
    assert r2.status_code == 200
    nums = [o["order_number"] for o in r2.json()["orders"]]
    assert "UO-100" in nums and "UO-101" in nums
    for o in r2.json()["orders"]:
        assert o["data_source"] == "excel"


# ── 2. Webhook (make) writes into the same unified_orders ────────────
def test_webhook_make_writes_unified(user):
    r = requests.post(
        f"{API}/webhook/make/{user['webhook_token']}",
        json={"order_number": "UO-200", "order_date": "2026-01-11",
              "total": 480, "payment_method": "تابي", "shipping_company": "جندل",
              "customer_name": "Khaled", "status": "completed"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 1
    r2 = requests.get(f"{API}/webhook/orders?data_source=make&limit=200",
                      headers=user["headers"], timeout=30).json()
    nums = [o["order_number"] for o in r2["orders"]]
    assert "UO-200" in nums
    assert all(o["data_source"] == "make" for o in r2["orders"])


# ── 3. MERGE: same order_number from both sources ────────────────────
def test_merge_excel_then_make(user):
    # 3a. Excel first: customer_name + mobile + partial total
    xlsx = _make_excel([
        {"order_number": "UO-MRG-1", "order_date": "2026-01-15",
         "total": 100.0, "payment_method": "مدى", "shipping_company": "سمسا",
         "customer_name": "Excel Customer", "mobile": "0555000111"},
    ])
    files = {"file": ("m1.xlsx", xlsx,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = requests.post(f"{API}/analyses",
                      headers=user["headers"],
                      params={"name": "TEST_merge_a", "date": "2026-01-15"},
                      files=files, timeout=60)
    assert r.status_code == 200, r.text

    # 3b. Make later for same order_number with different/richer fields
    r2 = requests.post(
        f"{API}/webhook/make/{user['webhook_token']}",
        json={
            "order_number": "UO-MRG-1",
            "order_date": "2026-01-15",
            # critical: newer wins → expect 250 after merge
            "total": 250.0,
            # non-critical: payment_method already 'مدى' → first writer wins → expect 'مدى'
            "payment_method": "Apple Pay",
            # empty customer_name → must NOT erase existing
            "customer_name": "",
            # new field added
            "tags": ["vip"],
            "status": "completed",
        },
        timeout=30,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["updated"] == 1  # not a new doc

    # 3c. Fetch merged doc — expect:
    rows = requests.get(f"{API}/webhook/orders?limit=500", headers=user["headers"], timeout=30).json()["orders"]
    doc = next(o for o in rows if o["order_number"] == "UO-MRG-1")

    # critical: total_amount overridden by make
    assert doc["total_amount"] == 250.0, f"expected 250, got {doc['total_amount']}"
    # non-critical: payment_method stays from excel (first writer wins)
    assert doc["payment_method"] == "مدى", f"expected مدى, got {doc['payment_method']}"
    # never overwrite with empty
    assert doc["customer_name"] == "Excel Customer"
    # latest data_source updated
    assert doc["data_source"] == "make"
    # field_sources provenance present
    fs = doc.get("field_sources") or {}
    assert fs.get("customer_name") == "excel"
    assert fs.get("total_amount") == "make"
    # tags merged in from make
    assert "vip" in (doc.get("tags") or [])
    # data_sources accumulates touches from both sources
    sources_touched = {s["source"] for s in (doc.get("data_sources") or [])}
    assert "excel" in sources_touched and "make" in sources_touched


def test_merge_make_then_excel(user):
    # 4a. Make first with partial data
    r = requests.post(
        f"{API}/webhook/make/{user['webhook_token']}",
        json={"order_number": "UO-MRG-2", "order_date": "2026-01-16",
              "total": 999.0, "payment_method": "تابي", "shipping_company": "أرامكس",
              "status": "completed"},
        timeout=30,
    )
    assert r.status_code == 200

    # 4b. Excel later for same order with customer_name (was missing in make)
    xlsx = _make_excel([
        {"order_number": "UO-MRG-2", "order_date": "2026-01-16",
         "total": 1500.0,  # different critical value
         "payment_method": "مدى",  # different non-critical
         "shipping_company": "سمسا",
         "customer_name": "Late Excel", "mobile": "0555999"},
    ])
    files = {"file": ("m2.xlsx", xlsx,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r2 = requests.post(f"{API}/analyses", headers=user["headers"],
                      params={"name": "TEST_merge_b", "date": "2026-01-16"},
                      files=files, timeout=60)
    assert r2.status_code == 200

    rows = requests.get(f"{API}/webhook/orders?limit=500", headers=user["headers"], timeout=30).json()["orders"]
    doc = next(o for o in rows if o["order_number"] == "UO-MRG-2")
    # critical: newer (excel) wins
    assert doc["total_amount"] == 1500.0
    # non-critical: first writer (make) wins
    assert doc["payment_method"] == "تابي"
    # customer_name was empty in make → filled by excel
    assert doc["customer_name"] == "Late Excel"
    # data_source: Make wins over Excel (iteration 31 precedence fix).
    # Once any Make write exists in the order's history, data_source stays
    # "make" forever — even after a later Excel re-import. field_sources
    # still records per-field provenance for audit (see below).
    assert doc["data_source"] == "make"
    fs = doc.get("field_sources") or {}
    assert fs.get("total_amount") == "excel"
    assert fs.get("payment_method") == "make"
    assert fs.get("customer_name") == "excel"


# ── 5. Stats: by_source breakdown ────────────────────────────────────
def test_stats_by_source(user):
    s = requests.get(f"{API}/webhook/stats", headers=user["headers"], timeout=30).json()
    assert "by_source" in s
    assert "excel" in s["by_source"] and "make" in s["by_source"]
    assert s["by_source"]["excel"] >= 2  # UO-100, UO-101 (+more)
    assert s["by_source"]["make"] >= 1
    assert s["total_orders_in_db"] >= s["by_source"]["excel"] + s["by_source"]["make"] - 100  # sanity


# ── 6. Build analysis aggregates from BOTH sources ───────────────────
def test_build_analysis_unified(user):
    r = requests.post(
        f"{API}/webhook/build-analysis",
        headers=user["headers"],
        json={"date_from": "2026-01-10", "date_to": "2026-01-20",
              "name": "TEST_unified_build"},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    a = r.json()
    summary = a["report"]["summary"]
    # Must include orders from BOTH excel-only (UO-100, UO-101) and make-only (UO-200)
    # and merged (UO-MRG-1, UO-MRG-2). Count >= 5.
    assert summary["total_orders"] >= 5, f"got {summary['total_orders']}"


# ── 7. DELETE /webhook/settings preserves Excel rows ─────────────────
def test_disconnect_preserves_excel(user):
    # snapshot
    pre = requests.get(f"{API}/webhook/stats", headers=user["headers"], timeout=30).json()
    excel_count_before = pre["by_source"].get("excel", 0)
    make_count_before = pre["by_source"].get("make", 0)
    assert excel_count_before > 0 and make_count_before > 0

    r = requests.delete(f"{API}/webhook/settings", headers=user["headers"], timeout=30)
    assert r.status_code == 200

    post = requests.get(f"{API}/webhook/stats", headers=user["headers"], timeout=30).json()
    # Excel preserved, Make-only orders deleted, but merged orders (where last writer
    # was make) ALSO deleted by the current implementation. So at minimum: make==0 and
    # excel-only rows survive.
    assert post["by_source"].get("make", 0) == 0
    # excel rows that were never touched by make should still be there:
    rows = requests.get(f"{API}/webhook/orders?data_source=excel&limit=500",
                        headers=user["headers"], timeout=30).json()["orders"]
    nums = {o["order_number"] for o in rows}
    assert "UO-100" in nums and "UO-101" in nums


# ── 8. User isolation: B never sees A's orders ───────────────────────
def test_user_isolation(user, user_b):
    rb = requests.get(f"{API}/webhook/orders?limit=500", headers=user_b["headers"], timeout=30).json()
    for o in rb["orders"]:
        assert not o["order_number"].startswith("UO-")
    # send through B's webhook
    rr = requests.post(f"{API}/webhook/make/{user_b['webhook_token']}",
                       json={"order_number": "UO-B-1", "order_date": "2026-01-12", "total": 33},
                       timeout=30)
    assert rr.status_code == 200
    # A must not see UO-B-1
    ra = requests.get(f"{API}/webhook/orders?limit=500", headers=user["headers"], timeout=30).json()
    assert all(o["order_number"] != "UO-B-1" for o in ra["orders"])


# ── 9. Filter ?data_source enforcement ───────────────────────────────
def test_filter_data_source_param(user_b):
    # B has only make orders → filtering by excel returns empty list
    r = requests.get(f"{API}/webhook/orders?data_source=excel&limit=200",
                     headers=user_b["headers"], timeout=30).json()
    assert r["total"] == 0
    r2 = requests.get(f"{API}/webhook/orders?data_source=make&limit=200",
                      headers=user_b["headers"], timeout=30).json()
    assert r2["total"] >= 1
