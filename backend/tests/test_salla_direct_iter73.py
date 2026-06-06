"""Iter-73 — Salla Direct Phase 2: OAuth config + Sync + Sources comparison.

Tests two layers:
  • Pure-Python helpers (no DB): _salla_order_to_doc mapper, is_configured
    cache resolution.
  • HTTP endpoints (live API): /salla/config CRUD, /salla/sync/logs,
    /salla/sources-comparison, plus the orders_db merge rule that
    salla_direct must never overwrite Make data.
"""
import os
import sys
import time

import pytest
import requests
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SALLA_TOKEN_ENC_KEY", "wa7NpuhE5bdIJHz4ExSuzhxgjB2kCN29K7Ea2a8smJI=")

from salla_integration.sync import _salla_order_to_doc  # noqa: E402
from salla_integration.service import is_configured, update_credentials_cache, _CREDS_CACHE  # noqa: E402
from orders_db import _merge_into  # noqa: E402


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL",
                         "https://salla-analytics.preview.emergentagent.com").rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.text}"
    token = r.json().get("access_token")
    assert token
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


# ── 1. Mapper ──────────────────────────────────────────────────────────
def test_salla_order_to_doc_minimal():
    raw = {
        "id": 12345,
        "reference_id": "ORDER-001",
        "amounts": {
            "total": {"amount": 250.0, "currency": "SAR"},
            "sub_total": {"amount": 200.0},
            "shipping_cost": {"amount": 25.0},
            "tax": {"amount": 5.0},
            "discounts": {"amount": 10.0},
        },
        "status": {"name": "تم التنفيذ", "slug": "delivered"},
        "payment_method": "mada",
        "customer": {"full_name": "Ali", "mobile": "+966500000000"},
        "date": "2026-01-15T10:00:00",
        "items": [{"product": {"id": 9, "name": "P1"}, "quantity": 2,
                   "amounts": {"price_without_tax": 50.0, "total": 100.0}}],
        "shipments": [{"courier": {"name": "iMile"}}],
    }
    doc = _salla_order_to_doc(raw)
    assert doc["order_number"] == "ORDER-001"
    assert doc["order_id"] == "12345"
    assert doc["total_amount"] == 250.0
    assert doc["payment_method"] == "mada"
    assert doc["shipping_company"] == "iMile"
    assert doc["customer_name"] == "Ali"
    assert doc["order_date"] == "2026-01-15"
    assert doc["order_status"] == "تم التنفيذ"
    assert len(doc["products"]) == 1


def test_salla_order_to_doc_handles_missing_fields():
    """Real Salla payloads sometimes omit shipments/items/customer."""
    raw = {"id": 1, "reference_id": "MIN-001"}
    doc = _salla_order_to_doc(raw)
    assert doc["order_number"] == "MIN-001"
    assert doc["total_amount"] == 0.0
    assert doc["products"] == []


# ── 2. is_configured() cache ──────────────────────────────────────────
def test_is_configured_picks_up_cache():
    # Reset
    _CREDS_CACHE["client_id"] = ""
    _CREDS_CACHE["client_secret"] = ""
    _CREDS_CACHE["loaded"] = False
    update_credentials_cache("cid_x", "secret_x")
    assert is_configured() is True
    update_credentials_cache("", "")
    # falls back to .env which may or may not be set — just confirm it doesn't crash
    _ = is_configured()


# ── 3. Merge rule: salla_direct never overwrites Make ─────────────────
def test_salla_direct_does_not_overwrite_make_critical_fields():
    existing = {
        "order_number": "X-1",
        "total_amount": 500.0,
        "order_status": "paid",
        "payment_method": "mada",
        "last_make_update_at": "2026-01-01T00:00:00+00:00",
        "data_source": "make",
        "field_sources": {"total_amount": "make", "order_status": "make"},
        "data_sources": [{"source": "make", "at": "2026-01-01"}],
    }
    incoming = {
        "order_number": "X-1",
        "total_amount": 999.0,    # ignored
        "order_status": "refunded",  # ignored
        "payment_method": "tabby",    # ignored
        "customer_name": "New Customer",  # filled (was empty)
    }
    merged = _merge_into(existing, incoming, source="salla_direct")
    assert merged["total_amount"] == 500.0
    assert merged["order_status"] == "paid"
    assert merged["payment_method"] == "mada"
    assert merged["customer_name"] == "New Customer"
    assert merged["data_source"] == "make"  # stays make-authoritative
    assert merged.get("last_salla_direct_sync_at") is not None


def test_salla_direct_can_create_new_orders():
    merged = _merge_into({}, {
        "order_number": "NEW-1",
        "total_amount": 333.33,
        "order_status": "delivered",
    }, source="salla_direct")
    assert merged["total_amount"] == 333.33
    assert merged["data_source"] == "salla_direct"


# ── 4. /api/salla/config CRUD ─────────────────────────────────────────
def test_config_endpoint_roundtrip(auth_session):
    # Save with a junk client_id (won't actually be used until OAuth flow runs)
    r = auth_session.put(f"{BASE_URL}/api/salla/config", json={
        "client_id": "test_cid_iter73",
        "client_secret": "test_secret_iter73",
        "redirect_uri": "",
    }, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True

    r = auth_session.get(f"{BASE_URL}/api/salla/config", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["client_id"] == "test_cid_iter73"
    assert body["has_client_secret"] is True
    assert body["configured"] is True

    # Clean up to avoid interfering with real OAuth setup
    r = auth_session.delete(f"{BASE_URL}/api/salla/config", timeout=10)
    assert r.status_code == 200


# ── 5. /api/salla/sync/logs lists prior logs ──────────────────────────
def test_sync_logs_endpoint(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/salla/sync/logs?limit=10", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert "logs" in body
    assert isinstance(body["logs"], list)


# ── 6. /api/salla/sources-comparison aggregation ──────────────────────
def test_sources_comparison_endpoint(auth_session):
    r = auth_session.get(
        f"{BASE_URL}/api/salla/sources-comparison",
        params={"from_date": "2026-06-01", "to_date": "2026-06-30"},
        timeout=15,
    )
    assert r.status_code == 200
    body = r.json()
    assert "totals" in body
    assert "by_combination" in body
    assert "per_source_totals" in body
    # Shape check
    for k in ("make_only", "excel_only", "salla_only"):
        assert k in body["by_combination"]
    for k in ("make", "excel", "salla_direct"):
        assert k in body["per_source_totals"]


# ── 7. /api/salla/sync/orders refuses when not connected ──────────────
def test_sync_orders_returns_proper_error_when_not_connected(auth_session):
    # Ensure we're disconnected first (no-op if already)
    auth_session.post(f"{BASE_URL}/api/salla/disconnect", timeout=10)
    r = auth_session.post(f"{BASE_URL}/api/salla/sync/orders", json={}, timeout=15)
    # Should fail because no token. 404 / 401 / 503 all acceptable
    assert r.status_code in (400, 401, 404, 503), r.text
    body = r.json()
    # Surface a clear message in either string or {message, needs_reauth}
    detail = body.get("detail") if isinstance(body, dict) else None
    assert detail is not None
