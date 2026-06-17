"""Iter-244 — Foundation tests: Categories tree + Suppliers.

Verifies the contract the merchant relies on:
  - Category tree builds correct path[] / depth.
  - Sibling-uniqueness rejects duplicate names under same parent.
  - Seed template is idempotent.
  - Suppliers enforce per-user uniqueness on company_name,
    contact_person AND phone independently.
  - Supplier ↔ category multi-link works.
  - No DELETE endpoint exists (status toggle only).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
import requests
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def auth_headers():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter244-{suffix}@x.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": email,
                            "password": "pw1234567"})
    assert r.status_code == 200, r.text
    return _h(r.json()["access_token"])


# ── Categories ─────────────────────────────────────────────────────

def test_create_tree_with_correct_path(auth_headers):
    h = auth_headers
    r = requests.post(f"{BASE_URL}/api/expense-category-tree",
                      headers=h, json={"name": "تكاليف"})
    assert r.status_code == 200, r.text
    root = r.json()
    assert root["depth"] == 0
    assert root["path"] == ["تكاليف"]

    r = requests.post(f"{BASE_URL}/api/expense-category-tree",
                      headers=h,
                      json={"name": "منتجات", "parent_id": root["id"]})
    assert r.status_code == 200
    mid = r.json()
    assert mid["depth"] == 1
    assert mid["path"] == ["تكاليف", "منتجات"]

    r = requests.post(f"{BASE_URL}/api/expense-category-tree",
                      headers=h,
                      json={"name": "ساعات", "parent_id": mid["id"]})
    assert r.status_code == 200
    leaf = r.json()
    assert leaf["depth"] == 2
    assert leaf["path"] == ["تكاليف", "منتجات", "ساعات"]
    assert leaf["path_ids"] == [root["id"], mid["id"]]


def test_sibling_uniqueness_rejects_dup_under_same_parent(auth_headers):
    h = auth_headers
    r = requests.post(f"{BASE_URL}/api/expense-category-tree",
                      headers=h, json={"name": "تشغيلية"})
    parent = r.json()
    r1 = requests.post(f"{BASE_URL}/api/expense-category-tree",
                       headers=h,
                       json={"name": "إيجارات",
                             "parent_id": parent["id"]})
    assert r1.status_code == 200
    r2 = requests.post(f"{BASE_URL}/api/expense-category-tree",
                       headers=h,
                       json={"name": "إيجارات",
                             "parent_id": parent["id"]})
    assert r2.status_code == 409, r2.text


def test_status_toggle_no_delete_endpoint(auth_headers):
    h = auth_headers
    r = requests.post(f"{BASE_URL}/api/expense-category-tree",
                      headers=h, json={"name": f"للإيقاف-{uuid.uuid4().hex[:4]}"})
    cid = r.json()["id"]
    # Inactivate via PATCH
    r = requests.patch(f"{BASE_URL}/api/expense-category-tree/{cid}",
                       headers=h, json={"status": "inactive"})
    assert r.status_code == 200
    assert r.json()["status"] == "inactive"
    # Confirm DELETE is NOT registered (405 or 404)
    r = requests.delete(f"{BASE_URL}/api/expense-category-tree/{cid}",
                        headers=h)
    assert r.status_code in (404, 405)


def test_seed_template_is_idempotent(auth_headers):
    h = auth_headers
    r1 = requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template",
        headers=h)
    assert r1.status_code == 200
    inserted_first = r1.json()["inserted"]
    # Second call must skip everything (idempotent).
    r2 = requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template",
        headers=h)
    assert r2.status_code == 200
    assert r2.json()["inserted"] == 0
    assert r2.json()["skipped"] >= inserted_first


# ── Suppliers ──────────────────────────────────────────────────────

def test_supplier_unique_company_contact_phone(auth_headers):
    h = auth_headers
    suffix = uuid.uuid4().hex[:6]
    # Phone must have ≥7 digits — uuid hex has letters, so we build
    # a numeric phone from time + counter to keep digits only.
    import time
    base_phone = f"055{int(time.time()) % 10000000:07d}"
    base = {
        "company_name": f"شركة {suffix}",
        "contact_person": f"شخص {suffix}",
        "phone": base_phone,
    }
    r = requests.post(f"{BASE_URL}/api/suppliers",
                      headers=h, json=base)
    assert r.status_code == 200, r.text

    # Duplicate company_name → reject
    r = requests.post(f"{BASE_URL}/api/suppliers", headers=h,
                      json={**base,
                            "contact_person": f"آخر {suffix}",
                            "phone": "0560000001"})
    assert r.status_code == 409, r.text
    assert "الشركة" in r.json()["detail"]

    # Duplicate contact_person → reject
    r = requests.post(f"{BASE_URL}/api/suppliers", headers=h,
                      json={**base,
                            "company_name": f"شركة جديدة {suffix}",
                            "phone": "0560000002"})
    assert r.status_code == 409, r.text
    assert ("شخص" in r.json()["detail"]
            or "الاتصال" in r.json()["detail"])

    # Duplicate phone → reject
    r = requests.post(f"{BASE_URL}/api/suppliers", headers=h,
                      json={**base,
                            "company_name": f"شركة مختلفة {suffix}",
                            "contact_person": f"آخر آخر {suffix}"})
    assert r.status_code == 409, r.text
    assert "الجوال" in r.json()["detail"]


def test_supplier_multi_category_link(auth_headers):
    h = auth_headers
    # Create 3 categories
    cids = []
    for nm in ["تصنيف-A", "تصنيف-B", "تصنيف-C"]:
        nm_unique = f"{nm}-{uuid.uuid4().hex[:4]}"
        r = requests.post(f"{BASE_URL}/api/expense-category-tree",
                          headers=h, json={"name": nm_unique})
        cids.append(r.json()["id"])

    import time
    suffix = uuid.uuid4().hex[:6]
    phone = f"057{(int(time.time()) + 1) % 10000000:07d}"
    r = requests.post(f"{BASE_URL}/api/suppliers", headers=h, json={
        "company_name": f"شركة متعددة {suffix}",
        "contact_person": f"متعدد {suffix}",
        "phone": phone,
        "category_ids": cids,
    })
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert set(r.json()["category_ids"]) == set(cids)

    # /suggested-categories returns the 3 with full path
    r = requests.get(
        f"{BASE_URL}/api/suppliers/{sid}/suggested-categories",
        headers=h)
    assert r.status_code == 200
    ids_seen = {it["id"] for it in r.json()["items"]}
    assert ids_seen == set(cids)


def test_supplier_no_delete_endpoint(auth_headers):
    h = auth_headers
    import time
    suffix = uuid.uuid4().hex[:6]
    phone = f"058{(int(time.time()) + 2) % 10000000:07d}"
    r = requests.post(f"{BASE_URL}/api/suppliers", headers=h, json={
        "company_name": f"للإيقاف {suffix}",
        "contact_person": f"عضو {suffix}",
        "phone": phone,
    })
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    # DELETE must be absent.
    r = requests.delete(f"{BASE_URL}/api/suppliers/{sid}", headers=h)
    assert r.status_code in (404, 405)
    # Status toggle must work.
    r = requests.patch(f"{BASE_URL}/api/suppliers/{sid}", headers=h,
                       json={"status": "inactive"})
    assert r.status_code == 200
    assert r.json()["status"] == "inactive"
