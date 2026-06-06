"""Iter-74 review: extra tests for review request requirements.

Covers:
  - GET /api/payment-settlements list (totals + matched/unmatched counts)
  - GET /api/payment-settlements/{file_id} detail incl unmatched_orders
  - POST /api/accounts/sync-payment-methods uses actual_net_amount for matched orders
  - payment_adjustments count is unchanged after uploads (NO auto adjustments)
  - GET /api/reconciliation/summary still works after settlements uploaded
"""
import os
import sys

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://salla-analytics.preview.emergentagent.com",
).rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"
SAMPLES = "/tmp/settlements_samples"
MONGO = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB = os.environ.get("DB_NAME", "test_database")


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


@pytest.fixture(scope="module")
def db():
    return MongoClient(MONGO)[DB]


@pytest.fixture(autouse=True)
def _clean(auth):
    r = auth.get(f"{BASE_URL}/api/payment-settlements", timeout=10)
    for f in r.json().get("files", []):
        auth.delete(f"{BASE_URL}/api/payment-settlements/{f['id']}", timeout=10)
    yield
    r = auth.get(f"{BASE_URL}/api/payment-settlements", timeout=10)
    for f in r.json().get("files", []):
        auth.delete(f"{BASE_URL}/api/payment-settlements/{f['id']}", timeout=10)


def _upload(auth, name):
    with open(f"{SAMPLES}/{name}", "rb") as fh:
        return auth.post(
            f"{BASE_URL}/api/payment-settlements/upload",
            files={"file": (name, fh.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=60,
        )


# ── List + detail endpoints ──────────────────────────────────────────
def test_list_endpoint_returns_uploaded_files(auth):
    up = _upload(auth, "salla.xlsx")
    assert up.status_code == 200, up.text

    r = auth.get(f"{BASE_URL}/api/payment-settlements", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "files" in data
    assert len(data["files"]) >= 1
    f = data["files"][0]
    assert f["provider"] == "salla"
    assert f["rows"] == 140
    assert "matched" in f and "unmatched" in f
    assert f["matched"] + f["unmatched"] <= f["rows"] + 1  # consolidated rows
    assert "totals" in f


def test_detail_endpoint_returns_unmatched_truncated(auth):
    up = _upload(auth, "salla.xlsx")
    fid = up.json()["file_id"]
    r = auth.get(f"{BASE_URL}/api/payment-settlements/{fid}", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "unmatched_orders" in data
    assert isinstance(data["unmatched_orders"], list)
    assert len(data["unmatched_orders"]) <= 200


# ── No auto adjustments / transfers side effect ─────────────────────
def test_no_auto_payment_adjustments_created(auth, db):
    before_adj = db.payment_adjustments.count_documents({})
    before_tx = db.transfers.count_documents({}) if "transfers" in db.list_collection_names() else 0

    for f in ("salla.xlsx", "tamara.xlsx", "tabby.xlsx"):
        r = _upload(auth, f)
        assert r.status_code == 200, r.text

    after_adj = db.payment_adjustments.count_documents({})
    after_tx = db.transfers.count_documents({}) if "transfers" in db.list_collection_names() else 0
    assert after_adj == before_adj, f"payment_adjustments grew {before_adj} -> {after_adj}"
    assert after_tx == before_tx, f"transfers grew {before_tx} -> {after_tx}"


# ── Expected balance integration ─────────────────────────────────────
def test_expected_balance_uses_actual_for_tamara(auth, db):
    # Resolve user_id from auth
    me = auth.get(f"{BASE_URL}/api/auth/me", timeout=10).json()
    uid = me["id"]

    # Baseline expected_orders_balance (no uploads)
    sync = auth.post(f"{BASE_URL}/api/accounts/sync-payment-methods", timeout=30)
    assert sync.status_code == 200, sync.text
    tamara_before = db.accounts.find_one({"user_id": uid, "name": "تمارا"}) or {}
    bal_before = float(tamara_before.get("expected_orders_balance", 0) or 0)

    # Upload Tamara settlement
    up = _upload(auth, "tamara.xlsx")
    assert up.status_code == 200, up.text
    matched = up.json().get("matched", 0)

    sync = auth.post(f"{BASE_URL}/api/accounts/sync-payment-methods", timeout=30)
    assert sync.status_code == 200, sync.text

    tamara_after = db.accounts.find_one({"user_id": uid, "name": "تمارا"}) or {}
    bal_after = float(tamara_after.get("expected_orders_balance", 0) or 0)

    # If any orders matched, the balance should change (actual net replaces gross)
    if matched > 0:
        assert bal_after != bal_before, (
            f"expected_orders_balance unchanged after Tamara settlement (matched={matched}): "
            f"{bal_before} -> {bal_after}"
        )


# ── Reconciliation still works ───────────────────────────────────────
def test_reconciliation_summary_still_works_after_uploads(auth):
    for f in ("salla.xlsx", "tamara.xlsx"):
        _upload(auth, f)
    r = auth.get(f"{BASE_URL}/api/reconciliation/summary", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    # Must still return well-formed top-level shape
    assert isinstance(data, dict)


# ── Salla page header invoice_number ─────────────────────────────────
def test_salla_header_invoice_number(auth):
    up = _upload(auth, "salla.xlsx")
    assert up.status_code == 200, up.text
    fid = up.json()["file_id"]
    r = auth.get(f"{BASE_URL}/api/payment-settlements/{fid}", timeout=10)
    assert r.status_code == 200
    header = r.json().get("header", {}) or {}
    assert header.get("invoice_number") == "6320306", header
