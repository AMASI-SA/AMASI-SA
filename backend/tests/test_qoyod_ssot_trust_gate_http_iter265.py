"""HTTP smoke tests for SSOT Trust Gate (iter-265).

Covers:
  - POST /api/integrations/qoyod/products/adopt happy + idempotent + 400.
  - GET  /api/integrations/qoyod/diagnostics/identity returns sample
    with name_ar / name_en standalone keys when present.
  - Regression: /dead-letter/preview still responds with auth.
"""
import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "admin@hesab.app"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
               timeout=15)
    if r.status_code != 200:
        pytest.skip(f"admin login failed: {r.status_code} {r.text[:200]}")
    token = r.json().get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


# ──────────────────────────── Adoption ──────────────────────────────
def test_adopt_rejects_missing_fields(auth_session):
    r = auth_session.post(
        f"{BASE}/api/integrations/qoyod/products/adopt",
        json={"sku": "", "qoyod_product_id": "1"})
    assert r.status_code == 400, r.text
    body = r.json()
    detail = body.get("detail") or body
    code = (detail or {}).get("code") if isinstance(detail, dict) else None
    assert code == "sku_and_qoyod_product_id_required", body


def test_adopt_happy_path_and_idempotent(auth_session):
    sku = f"TEST_ADOPT_{uuid.uuid4().hex[:8]}"
    payload = {"sku": sku, "qoyod_product_id": "9999",
               "qoyod_product_name": "أقمشة متنوعة (HTTP test)",
               "note": "iter-265 http smoke"}
    r1 = auth_session.post(
        f"{BASE}/api/integrations/qoyod/products/adopt", json=payload)
    assert r1.status_code == 200, r1.text
    assert r1.json().get("ok") is True

    # Idempotent re-adopt with updated note
    payload2 = dict(payload, note="iter-265 updated note")
    r2 = auth_session.post(
        f"{BASE}/api/integrations/qoyod/products/adopt", json=payload2)
    assert r2.status_code == 200, r2.text
    assert r2.json().get("ok") is True


# ──────────────────────────── Diagnostics ───────────────────────────
def test_identity_diagnostics_responds(auth_session):
    """Endpoint must be reachable for an authed admin; if Qoyod creds
    are absent the endpoint still must respond gracefully (not 500)."""
    r = auth_session.get(
        f"{BASE}/api/integrations/qoyod/diagnostics/identity")
    # Either 200 with structure, or 400 'no_credentials' for tenant
    # without Qoyod connection — both are acceptable here.
    assert r.status_code in (200, 400), r.text
    if r.status_code == 200:
        body = r.json()
        # Sample shape must be a dict; products.sample is a list
        prods = (body.get("products") or {}).get("sample") or []
        assert isinstance(prods, list)
        # If samples present, each MUST expose name_ar / name_en keys
        # (may be None) so the frontend fallback chain works.
        for p in prods[:5]:
            assert "name_ar" in p, f"missing name_ar in sample: {p}"
            assert "name_en" in p, f"missing name_en in sample: {p}"


# ──────────────────────────── Dead-letter Regression ────────────────
def test_dead_letter_preview_regression(auth_session):
    r = auth_session.get(
        f"{BASE}/api/integrations/qoyod/dead-letter/preview")
    # Endpoint must respond (200) for authed admin.
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ok" in body or "preview" in body or "items" in body or \
           "candidates" in body, body
