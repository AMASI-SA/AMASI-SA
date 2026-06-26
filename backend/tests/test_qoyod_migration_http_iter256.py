"""HTTP smoke for Qoyod Migration routes (iter256).

Verifies that the 6 new `/api/integrations/qoyod/migration/*` routes
respond correctly through the public REACT_APP_BACKEND_URL with admin
auth. Live Qoyod calls are NOT exercised — the `run` endpoint should
either succeed (if a key happens to be configured) or return 400
`credentials_missing`, but MUST NOT 5xx.
"""
from __future__ import annotations

import os
import io
import csv
import uuid
import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL", "https://salla-analytics.preview.emergentagent.com"
).rstrip("/")

ADMIN_EMAIL = "admin@hesab.app"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="module")
def token() -> str:
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    tok = body.get("access_token") or body.get("token")
    assert tok, f"no token in login response: {body}"
    return tok


@pytest.fixture(scope="module")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_run_returns_400_or_2xx_never_5xx(auth):
    r = requests.post(
        f"{BASE_URL}/api/integrations/qoyod/migration/run",
        headers=auth, timeout=120,
    )
    assert r.status_code < 500, f"5xx from /run: {r.status_code} {r.text[:300]}"
    assert r.status_code in (200, 400, 502), \
        f"unexpected status: {r.status_code} {r.text[:300]}"
    if r.status_code == 400:
        detail = r.json().get("detail")
        if isinstance(detail, dict):
            assert detail.get("code") == "credentials_missing"


def test_status_endpoint_shape(auth):
    r = requests.get(
        f"{BASE_URL}/api/integrations/qoyod/migration/status",
        headers=auth, timeout=30,
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body.get("ok") is True
    assert "run" in body  # may be null if never run


def test_report_endpoint_shape(auth):
    r = requests.get(
        f"{BASE_URL}/api/integrations/qoyod/migration/report",
        headers=auth, timeout=30,
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body.get("ok") is True
    report = body.get("report")
    if report is not None:
        # When a run exists, schema must carry all 13 expected fields.
        expected_keys = {
            "products_mapped", "products_mapped_with_warning",
            "products_candidate", "products_unmapped",
            "products_sku_mismatch_warnings", "customers_mapped",
            "customers_candidate", "customers_unmapped",
            "needs_manual_review", "qoyod_products_imported",
            "qoyod_customers_imported", "mezan_products_distinct",
            "mezan_customers_distinct",
        }
        missing = expected_keys - set(report.keys())
        assert not missing, f"report missing keys: {missing}"


def test_products_list_pagination_and_filter(auth):
    r = requests.get(
        f"{BASE_URL}/api/integrations/qoyod/migration/products",
        headers=auth,
        params={"page": 1, "page_size": 50,
                "status": "auto_mapped", "search": "SKU"},
        timeout=30,
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body.get("ok") is True
    assert body.get("page") == 1
    assert body.get("page_size") == 50
    assert isinstance(body.get("rows"), list)
    assert isinstance(body.get("total"), int)


def test_customers_list_pagination_and_filter(auth):
    r = requests.get(
        f"{BASE_URL}/api/integrations/qoyod/migration/customers",
        headers=auth,
        params={"page": 1, "page_size": 50},
        timeout=30,
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body.get("ok") is True
    assert isinstance(body.get("rows"), list)


def test_confirm_validation_400(auth):
    r = requests.post(
        f"{BASE_URL}/api/integrations/qoyod/migration/products/confirm",
        headers=auth, json={}, timeout=30,
    )
    assert r.status_code == 400, r.text[:200]


def test_confirm_invalid_kind_404(auth):
    r = requests.post(
        f"{BASE_URL}/api/integrations/qoyod/migration/widgets/confirm",
        headers=auth, json={"mezan_key": "x", "qoyod_id": "1"}, timeout=30,
    )
    # The /{kind} segment is constrained: confirm_candidate raises on
    # unknown kind → 400.
    assert r.status_code in (400, 404)


def test_products_export_csv(auth):
    r = requests.get(
        f"{BASE_URL}/api/integrations/qoyod/migration/products/export.csv",
        headers=auth, timeout=60,
    )
    assert r.status_code == 200, r.text[:200]
    ct = r.headers.get("content-type", "")
    assert "text/csv" in ct, f"unexpected content-type: {ct}"
    # Header row must be the first line.
    first_line = r.text.splitlines()[0] if r.text else ""
    assert "mezan_sku" in first_line, f"unexpected header: {first_line}"


def test_customers_export_csv(auth):
    r = requests.get(
        f"{BASE_URL}/api/integrations/qoyod/migration/customers/export.csv",
        headers=auth, timeout=60,
    )
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
    first = r.text.splitlines()[0] if r.text else ""
    assert "mezan_name" in first


def test_openapi_lists_six_new_paths(auth):
    # Public ingress also serves openapi.json under the FastAPI default.
    # Hit backend directly via the public URL.
    r = requests.get(f"{BASE_URL}/openapi.json", timeout=30)
    if r.status_code != 200:
        pytest.skip("openapi.json not exposed through ingress")
    try:
        data = r.json()
    except Exception:
        pytest.skip("openapi.json not JSON via ingress (frontend catch-all)")
    paths = data.get("paths", {})
    needles = [
        "/api/integrations/qoyod/migration/run",
        "/api/integrations/qoyod/migration/status",
        "/api/integrations/qoyod/migration/report",
        "/api/integrations/qoyod/migration/{kind}",
        "/api/integrations/qoyod/migration/{kind}/confirm",
        "/api/integrations/qoyod/migration/{kind}/export.csv",
    ]
    missing = [n for n in needles if n not in paths]
    assert not missing, f"missing paths in openapi: {missing}"
