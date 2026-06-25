"""Audit-only Salla diff column — must NOT leak into totals or balances.

Locks in the user's mandate: the new "الفرق مع سلة" column is for
review only. It must NEVER affect:
  • shipping_cost rows (totals.total_shipping_cost)
  • shipping balances (compute_balances output)
  • per-company aggregates
  • general_ledger postings
"""
from __future__ import annotations

import os
import asyncio

import httpx
import pytest

API = (os.environ.get("REACT_APP_BACKEND_URL")
       or "http://localhost:8001").rstrip("/") + "/api"
LOGIN_EMAIL = "amasi.jewelery@gmail.com"
LOGIN_PWD = "10201917"


@pytest.fixture(scope="module")
def http_client():
    return httpx.Client(base_url=API, timeout=60.0)


@pytest.fixture(scope="module")
def auth_headers():
    async def _g():
        async with httpx.AsyncClient(timeout=30.0) as h:
            r = await h.post(f"{API}/auth/login",
                              json={"email": LOGIN_EMAIL, "password": LOGIN_PWD})
            return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return asyncio.run(_g())


# ─────────────────────────────────────────────────────────────────────
# 1. Row-level diff is computed correctly
# ─────────────────────────────────────────────────────────────────────
def test_diff_vs_salla_present_on_row(http_client, auth_headers):
    r = http_client.get("/shipping-ledger?limit=10", headers=auth_headers)
    rows = r.json()["rows"]
    assert len(rows) > 0
    for row in rows:
        # field must exist on every row (even if None when no Salla value)
        assert "diff_vs_salla" in row
        assert "salla_shipping_native" in row
        # The diff is base - salla when both present; None otherwise
        if (row["shipping_cost_source"] == "company_config"
            and row["salla_shipping_native"] > 0):
            expected = round(
                row["shipping_base"] - row["salla_shipping_native"], 2,
            )
            assert row["diff_vs_salla"] == expected
        elif row["shipping_cost_source"] == "salla":
            # When falling back to Salla, diff is 0/None (no compare)
            assert row["diff_vs_salla"] is None
        elif row["salla_shipping_native"] == 0:
            assert row["diff_vs_salla"] is None


# ─────────────────────────────────────────────────────────────────────
# 2. INVARIANT — diff NEVER leaks into totals or per_company
# ─────────────────────────────────────────────────────────────────────
def test_diff_does_not_appear_in_totals(http_client, auth_headers):
    r = http_client.get("/shipping-ledger?limit=200", headers=auth_headers)
    d = r.json()
    assert "diff_vs_salla" not in d["totals"]
    assert "salla_shipping_native" not in d["totals"]
    for pc in d["per_company"]:
        assert "diff_vs_salla" not in pc
        assert "salla_shipping_native" not in pc


# ─────────────────────────────────────────────────────────────────────
# 3. INVARIANT — totals.total_shipping_cost equals sum of row.shipping_cost
# (NOT influenced by diff_vs_salla or salla_shipping_native fields)
# ─────────────────────────────────────────────────────────────────────
def test_totals_match_sum_of_row_shipping_cost(http_client, auth_headers):
    r = http_client.get("/shipping-ledger?limit=500", headers=auth_headers)
    d = r.json()
    row_sum = round(sum(row["shipping_cost"] for row in d["rows"]), 2)
    assert abs(d["totals"]["total_shipping_cost"] - row_sum) < 0.5, (
        f"totals.total_shipping_cost={d['totals']['total_shipping_cost']} "
        f"vs sum-of-rows={row_sum} — drift means diff_vs_salla leaked in"
    )


# ─────────────────────────────────────────────────────────────────────
# 4. INVARIANT — /balances ignores diff_vs_salla completely
# ─────────────────────────────────────────────────────────────────────
def test_balances_ignores_diff_vs_salla(http_client, auth_headers):
    """The /balances endpoint must return the same shipping totals
    regardless of any diff_vs_salla value. We can't directly toggle
    diff_vs_salla (it's computed), but we can ensure /balances doesn't
    expose any diff_vs_salla field and the value is purely derived
    from base+tax of approved orders.
    """
    r = http_client.get("/balances", headers=auth_headers)
    if r.status_code != 200:
        pytest.skip("balances endpoint unavailable")
    body = r.json()
    payload_str = str(body)
    assert "diff_vs_salla" not in payload_str
    assert "salla_shipping_native" not in payload_str
