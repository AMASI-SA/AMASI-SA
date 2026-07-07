"""Iter-73 Phase-2 review — Salla Direct OAuth + Sync + Sources Comparison.

Covers ALL items requested in the review_request:
  1. /status response shape (no token leakage)
  2. /config GET/PUT/DELETE + Arabic-error 400 on empty client_id
  3. PUT /config without client_secret preserves previous secret
  4. /sync/logs basic shape + ?limit + ?kind filters
  5. /sync/orders + /sync/products error wiring when not connected
  6. /sources-comparison shape + ?from_date+?to_date filters
  7. /disconnect idempotency
  8. /oauth/login 503 when not configured + 200 after PUT /config
  9. Regression: /reconciliation/summary and /accounts/sync-payment-methods
 10. Merge-rule unit test: salla_direct never overwrites Make data
"""
import os
import sys
from urllib.parse import urlparse, parse_qs

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SALLA_TOKEN_ENC_KEY", "wa7NpuhE5bdIJHz4ExSuzhxgjB2kCN29K7Ea2a8smJI=")

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com"
).rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"
JUNK_CID = "test_cid_iter73_v2"
JUNK_SECRET = "test_secret_iter73_v2"


@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.text}"
    token = r.json().get("access_token")
    assert token
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(autouse=True)
def _cleanup_config(auth_session):
    """Make sure each test starts with no DB config row."""
    auth_session.delete(f"{BASE_URL}/api/salla/config", timeout=10)
    yield
    auth_session.delete(f"{BASE_URL}/api/salla/config", timeout=10)


# ── 1. /status never leaks tokens ────────────────────────────────────
def test_status_does_not_leak_tokens(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/salla/status", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "connected" in body and "configured" in body and "status" in body
    # token fields must NOT be present
    forbidden = ("access_token", "refresh_token",
                 "access_token_encrypted", "refresh_token_encrypted",
                 "client_secret")
    for k in forbidden:
        assert k not in body, f"/status leaked field: {k}"


# ── 2. /config GET shape (no raw secret) ─────────────────────────────
def test_config_get_shape_no_raw_secret(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/salla/config", timeout=10)
    assert r.status_code == 200
    body = r.json()
    for k in ("client_id", "redirect_uri", "has_client_secret",
              "configured", "env_client_id_present", "env_client_secret_present"):
        assert k in body, f"missing key {k}"
    assert "client_secret" not in body  # raw secret must never be returned


# ── 3. PUT /config CRUD with junk values ─────────────────────────────
def test_config_put_get_delete_roundtrip(auth_session):
    r = auth_session.put(
        f"{BASE_URL}/api/salla/config",
        json={"client_id": JUNK_CID, "client_secret": JUNK_SECRET, "redirect_uri": ""},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True

    r = auth_session.get(f"{BASE_URL}/api/salla/config", timeout=10)
    body = r.json()
    assert body["client_id"] == JUNK_CID
    assert body["has_client_secret"] is True
    assert body["configured"] is True
    assert "client_secret" not in body

    r = auth_session.delete(f"{BASE_URL}/api/salla/config", timeout=10)
    assert r.status_code == 200
    assert r.json().get("ok") is True


# ── 4. PUT /config without client_secret keeps existing secret ───────
def test_config_put_without_secret_preserves_existing(auth_session):
    # 1st save with both
    r = auth_session.put(
        f"{BASE_URL}/api/salla/config",
        json={"client_id": JUNK_CID, "client_secret": JUNK_SECRET, "redirect_uri": ""},
        timeout=10,
    )
    assert r.status_code == 200

    # 2nd save with only client_id (no secret)
    r = auth_session.put(
        f"{BASE_URL}/api/salla/config",
        json={"client_id": JUNK_CID + "_v3", "redirect_uri": "https://x.test/cb"},
        timeout=10,
    )
    assert r.status_code == 200

    body = auth_session.get(f"{BASE_URL}/api/salla/config", timeout=10).json()
    assert body["client_id"] == JUNK_CID + "_v3"
    assert body["has_client_secret"] is True, "previous secret should be preserved"
    assert body["redirect_uri"] == "https://x.test/cb"


# ── 5. PUT /config with empty client_id → 400 + Arabic message ───────
def test_config_put_empty_client_id_returns_arabic_400(auth_session):
    r = auth_session.put(
        f"{BASE_URL}/api/salla/config",
        json={"client_id": "", "client_secret": JUNK_SECRET},
        timeout=10,
    )
    assert r.status_code == 400
    detail = r.json().get("detail", "")
    assert "Client ID" in detail
    # must contain Arabic
    assert any("\u0600" <= ch <= "\u06FF" for ch in detail), \
        f"expected Arabic in error: {detail}"


# ── 6. /sync/logs shape + filters ────────────────────────────────────
def test_sync_logs_basic(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/salla/sync/logs", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert "logs" in body and isinstance(body["logs"], list)


def test_sync_logs_supports_limit_and_kind(auth_session):
    r = auth_session.get(
        f"{BASE_URL}/api/salla/sync/logs",
        params={"limit": 5, "kind": "orders"},
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("logs"), list)
    assert len(body["logs"]) <= 5
    for row in body["logs"]:
        if row.get("kind"):
            assert row["kind"] == "orders"

    r2 = auth_session.get(
        f"{BASE_URL}/api/salla/sync/logs",
        params={"kind": "products"},
        timeout=10,
    )
    assert r2.status_code == 200
    for row in r2.json().get("logs", []):
        if row.get("kind"):
            assert row["kind"] == "products"


# ── 7. /sync/orders + /sync/products without OAuth → proper error ────
def _assert_needs_reauth_shape(r):
    assert r.status_code in (400, 401, 404, 503), f"unexpected {r.status_code}: {r.text}"
    body = r.json()
    detail = body.get("detail")
    assert isinstance(detail, dict), f"detail must be dict, got: {detail!r}"
    assert isinstance(detail.get("message"), str) and detail["message"]
    assert isinstance(detail.get("needs_reauth"), bool)


def test_sync_orders_error_shape_when_not_connected(auth_session):
    auth_session.post(f"{BASE_URL}/api/salla/disconnect", timeout=10)
    r = auth_session.post(f"{BASE_URL}/api/salla/sync/orders", json={}, timeout=15)
    _assert_needs_reauth_shape(r)


def test_sync_products_disabled_rev42(auth_session):
    """rev42 (user directive): products scope is locked OFF in the
    Salla Partners panel — the endpoint must refuse with 409 and
    NEVER call Salla (no needs_reauth path)."""
    r = auth_session.post(f"{BASE_URL}/api/salla/sync/products", timeout=15)
    assert r.status_code == 409, f"unexpected {r.status_code}: {r.text}"
    detail = r.json()["detail"]
    assert detail["disabled_reason"] == "salla_products_scope_unavailable"
    assert detail["needs_reauth"] is False


# ── 8. /sources-comparison shape + filters ───────────────────────────
def test_sources_comparison_full_shape(auth_session):
    r = auth_session.get(
        f"{BASE_URL}/api/salla/sources-comparison",
        params={"from_date": "2026-01-01", "to_date": "2026-01-31"},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for top in ("totals", "by_combination", "per_source_totals",
                "missing_from_make", "missing_from_salla"):
        assert top in body, f"missing top-level: {top}"
    for k in ("make_only", "excel_only", "salla_only",
             "make_and_salla", "excel_and_salla", "make_excel_and_salla",
             "make_and_excel", "unknown"):
        assert k in body["by_combination"], f"by_combination missing {k}"
    for k in ("make", "excel", "salla_direct"):
        assert k in body["per_source_totals"], f"per_source_totals missing {k}"


# ── 9. /disconnect idempotent ────────────────────────────────────────
def test_disconnect_is_idempotent(auth_session):
    r1 = auth_session.post(f"{BASE_URL}/api/salla/disconnect", timeout=10)
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1.get("ok") is True
    assert "removed" in b1

    r2 = auth_session.post(f"{BASE_URL}/api/salla/disconnect", timeout=10)
    assert r2.status_code == 200
    assert r2.json().get("ok") is True


# ── 10. /oauth/login behaviour gated by /config ──────────────────────
def test_oauth_login_503_when_not_configured(auth_session):
    # Ensure DB config + cache cleared
    auth_session.delete(f"{BASE_URL}/api/salla/config", timeout=10)
    # env SALLA_CLIENT_ID/SECRET are intentionally empty per review
    r = auth_session.get(f"{BASE_URL}/api/salla/oauth/login", timeout=10)
    # If env happens to have values this returns 200 — but review says env is empty
    assert r.status_code == 503, (
        f"expected 503 when not configured, got {r.status_code}: {r.text}"
    )
    detail = r.json().get("detail", "")
    assert any("\u0600" <= ch <= "\u06FF" for ch in detail), \
        f"expected Arabic message: {detail}"


def test_oauth_login_200_after_config_saved(auth_session):
    auth_session.put(
        f"{BASE_URL}/api/salla/config",
        json={"client_id": JUNK_CID, "client_secret": JUNK_SECRET},
        timeout=10,
    )
    r = auth_session.get(f"{BASE_URL}/api/salla/oauth/login", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "authorize_url" in body and "redirect_uri" in body
    url = body["authorize_url"]
    assert url.startswith("https://accounts.salla.sa/oauth2/auth?"), url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs.get("client_id") == [JUNK_CID]
    assert qs.get("response_type") == ["code"]
    assert "redirect_uri" in qs
    assert "scope" in qs
    assert "state" in qs


# ── 11. Regression: reconciliation/summary still works ───────────────
def test_regression_reconciliation_summary(auth_session):
    r = auth_session.get(f"{BASE_URL}/api/reconciliation/summary", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "totals" in body
    totals = body["totals"]
    for k in ("expected", "transferred", "pending", "collection_rate"):
        assert k in totals
    # Sanity: expected should be a non-negative number
    assert isinstance(totals["expected"], (int, float))
    assert totals["expected"] >= 0


# ── 12. Regression: /accounts/sync-payment-methods still works ───────
def test_regression_sync_payment_methods(auth_session):
    r = auth_session.post(f"{BASE_URL}/api/accounts/sync-payment-methods", timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    # Endpoint should produce some structure (ok/created/updated/etc.)
    assert isinstance(body, dict)


# ── 13. Merge-rule unit test (covered by file but re-run inline) ─────
def test_merge_rule_unit_does_not_overwrite_make():
    from orders_db import _merge_into

    existing = {
        "order_number": "X-99",
        "total_amount": 500.0,
        "order_status": "paid",
        "payment_method": "mada",
        "last_make_update_at": "2026-01-01T00:00:00+00:00",
        "data_source": "make",
        "field_sources": {"total_amount": "make", "order_status": "make",
                          "payment_method": "make"},
        "data_sources": [{"source": "make", "at": "2026-01-01"}],
    }
    incoming = {
        "order_number": "X-99",
        "total_amount": 999.0,
        "order_status": "refunded",
        "payment_method": "tabby",
        "customer_name": "New",
    }
    merged = _merge_into(existing, incoming, source="salla_direct")
    assert merged["total_amount"] == 500.0
    assert merged["order_status"] == "paid"
    assert merged["payment_method"] == "mada"
    assert merged["customer_name"] == "New"
    assert merged["data_source"] == "make"
    assert merged.get("last_salla_direct_sync_at") is not None
