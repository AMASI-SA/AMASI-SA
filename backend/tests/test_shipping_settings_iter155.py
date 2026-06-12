"""Iter-155 — Shipping company settings round-trip tests.

User feedback: "عند حفظ إعدادات شركات الشحن لا يتم حفظ المعلومات المضافه".

Root cause: the backend's `ShippingCompany` Pydantic model was missing
the `cod_fee_percent` and `cod_fee_fixed_per_order` fields, so they
were silently stripped on PUT /api/settings.  Also: the UI couldn't
ADD new shipping companies.

These tests verify (a) the new fields survive PUT→GET, (b) new
companies can be added and persist, (c) existing fields still work.
"""
import os
import uuid

import pytest
import requests


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


@pytest.fixture
def ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"i155-{suffix}@example.com"
    pwd = "T#155abcD"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I155"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    yield {"hdr": {"Authorization": f"Bearer {token}"}}


def test_get_settings_returns_shipping_companies(ctx):
    r = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200
    s = r.json()
    assert isinstance(s.get("shipping_companies"), list)
    assert len(s["shipping_companies"]) > 0


def test_cod_fee_fields_persist_round_trip(ctx):
    """Save COD fee fields; GET back; values must match."""
    r = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    s = r.json()
    # Modify the first company to add COD fees
    s["shipping_companies"][0]["cod_fee_percent"] = 0.025
    s["shipping_companies"][0]["cod_fee_fixed_per_order"] = 7.5
    s["shipping_companies"][0]["is_deferred"] = True
    r2 = requests.put(f"{BASE_URL}/api/settings",
                      json=s, headers=ctx["hdr"], timeout=10)
    assert r2.status_code == 200, r2.text
    # Read back
    r3 = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    s_after = r3.json()
    first = s_after["shipping_companies"][0]
    assert first["cod_fee_percent"] == 0.025
    assert first["cod_fee_fixed_per_order"] == 7.5
    assert first["is_deferred"] is True


def test_can_add_new_shipping_company(ctx):
    """Append a new company to shipping_companies; verify it persists."""
    r = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    s = r.json()
    initial_count = len(s["shipping_companies"])
    new_company = {
        "name": "شركة شحن تجريبية",
        "cost_per_order": 25.0,
        "vat_percent": 15.0,
        "is_deferred": True,
        "cod_fee_percent": 0.015,
        "cod_fee_fixed_per_order": 5.0,
    }
    s["shipping_companies"].append(new_company)
    r2 = requests.put(f"{BASE_URL}/api/settings",
                      json=s, headers=ctx["hdr"], timeout=10)
    assert r2.status_code == 200, r2.text
    # Read back
    r3 = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    s_after = r3.json()
    assert len(s_after["shipping_companies"]) == initial_count + 1
    added = next((c for c in s_after["shipping_companies"]
                  if c["name"] == "شركة شحن تجريبية"), None)
    assert added is not None
    assert added["cost_per_order"] == 25.0
    assert added["cod_fee_percent"] == 0.015
    assert added["cod_fee_fixed_per_order"] == 5.0


def test_can_remove_shipping_company(ctx):
    """Remove a company from the list; verify it's gone."""
    r = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    s = r.json()
    if len(s["shipping_companies"]) < 2:
        pytest.skip("Need at least 2 default companies")
    removed_name = s["shipping_companies"][0]["name"]
    s["shipping_companies"] = s["shipping_companies"][1:]
    r2 = requests.put(f"{BASE_URL}/api/settings",
                      json=s, headers=ctx["hdr"], timeout=10)
    assert r2.status_code == 200
    r3 = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    names_after = [c["name"] for c in r3.json()["shipping_companies"]]
    assert removed_name not in names_after


def test_legacy_fields_still_work(ctx):
    """Ensure changes to existing fields (cost, vat, is_deferred) still
    persist alongside the new COD fields."""
    r = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    s = r.json()
    target = s["shipping_companies"][0]
    target["cost_per_order"] = 99.99
    target["vat_percent"] = 5.0
    target["is_deferred"] = False
    r2 = requests.put(f"{BASE_URL}/api/settings",
                      json=s, headers=ctx["hdr"], timeout=10)
    assert r2.status_code == 200
    r3 = requests.get(f"{BASE_URL}/api/settings", headers=ctx["hdr"], timeout=10)
    after = r3.json()["shipping_companies"][0]
    assert after["cost_per_order"] == 99.99
    assert after["vat_percent"] == 5.0
    assert after["is_deferred"] is False
