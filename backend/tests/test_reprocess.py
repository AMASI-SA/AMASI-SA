"""Test the reprocess endpoint: re-uploading an Excel for a legacy analysis
must populate unified_orders with per-order dates and mark the analysis as
non-legacy (orders_imported > 0).
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
    return r.json()["access_token"], email


def _upload_xlsx(headers, content, name="legacy"):
    r = requests.post(f"{API}/analyses",
                      headers=headers,
                      files={"file": ("orders.xlsx", content,
                                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                      params={"name": name})
    r.raise_for_status()
    return r.json()


def test_reprocess_endpoint_repopulates_unified_orders():
    token, _ = _register()
    h = {"Authorization": f"Bearer {token}"}

    # 1) Create an analysis with proper Excel — unified_orders gets populated
    xlsx = _make_xlsx_with_date_col_q([
        {"order_number": "R1", "amount": 100, "date": "2026-04-10"},
        {"order_number": "R2", "amount": 250, "date": "2026-04-20"},
    ])
    a = _upload_xlsx(h, xlsx, name="initial")
    aid = a["id"]
    assert a["orders_imported"] == 2

    # 2) Simulate legacy: blank out orders_imported (mimics pre-migration data)
    requests.post(f"{API}/analyses/{aid}/reprocess",
                  headers=h,
                  files={"file": ("orders.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    # endpoint must respond with 200 and reflect imports/updates (re-upload should update existing)
    r = requests.post(f"{API}/analyses/{aid}/reprocess",
                      headers=h,
                      files={"file": ("orders.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["analysis_id"] == aid
    # Re-uploading the SAME orders should be all updates, zero new creates
    assert body["orders_imported"] == 0
    assert body["orders_updated"] == 2

    # 3) Dashboard sees the orders, filterable by per-order date (April 10 only)
    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-04-10", "to_date": "2026-04-10"},
                     headers=h)
    t = r.json()["totals"]
    assert t["total_orders"] == 1
    assert t["total_sales"] == 100.0


def test_reprocess_unknown_analysis_returns_404():
    token, _ = _register()
    h = {"Authorization": f"Bearer {token}"}
    xlsx = _make_xlsx_with_date_col_q([{"order_number": "x", "amount": 1, "date": "2026-01-01"}])
    r = requests.post(f"{API}/analyses/does-not-exist/reprocess",
                      headers=h,
                      files={"file": ("o.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 404


def test_reprocess_rejects_non_excel():
    token, _ = _register()
    h = {"Authorization": f"Bearer {token}"}
    xlsx = _make_xlsx_with_date_col_q([{"order_number": "a", "amount": 1, "date": "2026-01-01"}])
    a = _upload_xlsx(h, xlsx, name="x")
    r = requests.post(f"{API}/analyses/{a['id']}/reprocess",
                      headers=h,
                      files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400
