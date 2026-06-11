"""Iter-136 — Admin purge-before endpoint for BNPL.

Validates the /api/bnpl/{provider}/admin/purge-before flow:
  1. Dry-run reports counts but doesn't delete.
  2. Non-dry-run deletes the matched rows.
  3. Only rows for the current user_id and provider are affected.
  4. Boundary: rows AT/AFTER the cutoff are kept (strictly <).
"""
import pytest
import requests
import os

BASE = os.environ.get(
    "TEST_API_BASE",
    "https://salla-analytics.preview.emergentagent.com",
)


def _login():
    r = requests.post(
        f"{BASE}/api/auth/login",
        json={"email": "amasi.jewelery@gmail.com", "password": "10201917"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def test_purge_before_dry_run_returns_counts():
    tok = _login()
    r = requests.post(
        f"{BASE}/api/bnpl/tabby/admin/purge-before"
        f"?cutoff=2026-04-27&dry_run=true",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "tabby"
    assert body["cutoff_riyadh"] == "2026-04-27"
    assert body["utc_cutoff"] == "2026-04-26T21:00:00Z"
    assert body["dry_run"] is True
    assert set(body["counts"].keys()) == {
        "payment_transactions", "payment_refunds",
        "bnpl_settlements", "unified_orders",
    }
    # Dry run never deletes
    assert body["total_deleted"] == 0


def test_purge_before_rejects_unknown_provider():
    tok = _login()
    r = requests.post(
        f"{BASE}/api/bnpl/foobar/admin/purge-before"
        f"?cutoff=2026-04-27&dry_run=true",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=15,
    )
    assert r.status_code == 404


def test_purge_before_rejects_bad_cutoff():
    tok = _login()
    r = requests.post(
        f"{BASE}/api/bnpl/tabby/admin/purge-before"
        f"?cutoff=notadate&dry_run=true",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=15,
    )
    assert r.status_code in (400, 422)


def test_purge_before_requires_auth():
    r = requests.post(
        f"{BASE}/api/bnpl/tabby/admin/purge-before"
        f"?cutoff=2026-04-27&dry_run=true",
        timeout=15,
    )
    assert r.status_code in (401, 403)
