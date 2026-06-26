"""iter257 — HTTP smoke for the new Last Order Date sort/filter/CSV.

Hits the public REACT_APP_BACKEND_URL with admin credentials and verifies:
  • GET /migration/products?sort=last_order_date&sort_dir=desc → 200
  • GET /migration/products?last_order_after=YYYY-MM-DD → 200, rows filtered
  • Same on /customers
  • CSV exports include 'last_order_date' column in header row
"""
from __future__ import annotations

import csv
import io
import os

import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or os.environ.get("BACKEND_URL")
            or "").rstrip("/")
if not BASE_URL:
    # Fall back to frontend/.env
    fe_env = os.path.join(os.path.dirname(__file__), "..", "..",
                          "frontend", ".env")
    if os.path.isfile(fe_env):
        with open(fe_env) as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break

API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def auth_token():
    r = requests.post(f"{API}/auth/login",
                      json={"email": "admin@hesab.app",
                            "password": "admin123"},
                      timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"no token in {r.json()}"
    return tok


@pytest.fixture(scope="module")
def H(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


# ── /products: sort + filter ─────────────────────────────────────────
def test_products_sort_by_last_order_date_desc(H):
    r = requests.get(f"{API}/integrations/qoyod/migration/products",
                     params={"sort": "last_order_date", "sort_dir": "desc"},
                     headers=H, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "rows" in data
    # Verify sort if there are >=2 rows with a date
    dated = [row.get("last_order_date") for row in data["rows"]
             if row.get("last_order_date")]
    if len(dated) >= 2:
        assert dated == sorted(dated, reverse=True), \
            f"expected desc sort, got {dated}"


def test_products_sort_by_last_order_date_asc(H):
    r = requests.get(f"{API}/integrations/qoyod/migration/products",
                     params={"sort": "last_order_date", "sort_dir": "asc"},
                     headers=H, timeout=30)
    assert r.status_code == 200, r.text


def test_products_filter_last_order_after(H):
    r = requests.get(f"{API}/integrations/qoyod/migration/products",
                     params={"last_order_after": "2025-01-01"},
                     headers=H, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    for row in data["rows"]:
        lod = row.get("last_order_date")
        assert lod is None or lod >= "2025-01-01", \
            f"row leaked through filter: {lod}"


# ── /customers: sort + filter ────────────────────────────────────────
def test_customers_sort_by_last_order_date_desc(H):
    r = requests.get(f"{API}/integrations/qoyod/migration/customers",
                     params={"sort": "last_order_date", "sort_dir": "desc"},
                     headers=H, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    dated = [row.get("last_order_date") for row in data["rows"]
             if row.get("last_order_date")]
    if len(dated) >= 2:
        assert dated == sorted(dated, reverse=True)


def test_customers_filter_last_order_after(H):
    r = requests.get(f"{API}/integrations/qoyod/migration/customers",
                     params={"last_order_after": "2025-01-01"},
                     headers=H, timeout=30)
    assert r.status_code == 200, r.text
    for row in r.json()["rows"]:
        lod = row.get("last_order_date")
        assert lod is None or lod >= "2025-01-01"


# ── CSV exports include the new column ───────────────────────────────
def test_products_csv_includes_last_order_date_header(H):
    r = requests.get(
        f"{API}/integrations/qoyod/migration/products/export.csv",
        headers=H, timeout=30)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    reader = csv.reader(io.StringIO(r.text))
    header = next(reader)
    assert "last_order_date" in header, f"header missing: {header}"


def test_customers_csv_includes_last_order_date_header(H):
    r = requests.get(
        f"{API}/integrations/qoyod/migration/customers/export.csv",
        headers=H, timeout=30)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    reader = csv.reader(io.StringIO(r.text))
    header = next(reader)
    assert "last_order_date" in header, f"header missing: {header}"


# ── Validation: invalid sort key rejected ────────────────────────────
def test_invalid_sort_key_rejected(H):
    r = requests.get(f"{API}/integrations/qoyod/migration/products",
                     params={"sort": "bogus"}, headers=H, timeout=30)
    assert r.status_code in (400, 422), r.text
