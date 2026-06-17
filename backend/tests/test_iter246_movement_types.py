"""Iter-246 — Movement-type ↔ root-category mapping.

Validates:
  * `movement_types` is persisted on root rows during seed.
  * `?movement_type=<x>` filters categories to those whose root maps
    to `<x>` (and their descendants inherit via the API).
  * Posting a `financial_movement` whose category's root doesn't list
    the movement_type returns HTTP 400.
"""
from __future__ import annotations

import os
import uuid

import pytest
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
    email = f"iter246-{suffix}@x.com"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": email,
                            "password": "pw1234567"})
    assert r.status_code == 200, r.text
    return _h(r.json()["access_token"])


def test_seed_assigns_movement_types(auth_headers):
    h = auth_headers
    r = requests.post(f"{BASE_URL}/api/expense-category-tree/seed-template",
                      headers=h)
    assert r.status_code == 200, r.text

    r = requests.get(f"{BASE_URL}/api/expense-category-tree", headers=h)
    assert r.status_code == 200
    rows = r.json()["items"]

    roots = {r["name"]: r for r in rows if not r.get("parent_id")}
    assert "تكاليف المنتجات" in roots
    assert roots["تكاليف المنتجات"]["movement_types"] == ["supplier_invoice"]
    assert roots["المصروفات التشغيلية"]["movement_types"] == ["general_expense"]
    assert roots["الأصول"]["movement_types"] == ["fixed_asset"]


def test_inheritance_to_children(auth_headers):
    h = auth_headers
    r = requests.get(f"{BASE_URL}/api/expense-category-tree", headers=h)
    rows = r.json()["items"]
    # Find any descendant of "تكاليف المنتجات" and assert inheritance.
    desc = [r for r in rows
            if r.get("path") and r["path"][0] == "تكاليف المنتجات"
            and r["parent_id"]]
    assert desc, "expected descendants"
    for d in desc:
        assert "supplier_invoice" in d["movement_types"]


def test_filter_by_movement_type(auth_headers):
    h = auth_headers
    for mt in ("supplier_invoice", "general_expense", "fixed_asset"):
        r = requests.get(
            f"{BASE_URL}/api/expense-category-tree",
            params={"movement_type": mt, "include_inactive": "false"},
            headers=h)
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        # Every returned row must include `mt` in movement_types.
        assert items, f"no rows for {mt}"
        for it in items:
            assert mt in (it.get("movement_types") or [])


def test_create_movement_rejects_wrong_category(auth_headers):
    h = auth_headers
    # Pick an "الأصول" leaf and try to use it for supplier_invoice.
    r = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "fixed_asset"}, headers=h)
    rows = r.json()["items"]
    asset_leaf = next(
        (x for x in rows if x.get("parent_id")), rows[0])

    # We don't even need a supplier — the category validation fires
    # before supplier check.
    payload = {
        "movement_type": "supplier_invoice",
        "doc_date": "2026-02-01",
        "category_id": asset_leaf["id"],
        "payment_terms": "credit",
        "total_amount": 100,
        "supplier_id": str(uuid.uuid4()),
    }
    r = requests.post(f"{BASE_URL}/api/financial-movements",
                      headers=h, json=payload)
    # Expect 400 for mismatch (NOT 404 / 422).
    assert r.status_code == 400, r.text
    assert "غير متاح" in r.text or "fixed_asset" in r.text


def test_patch_movement_types_root_only(auth_headers):
    h = auth_headers
    # Pick a leaf and try to patch its movement_types → expect 400.
    r = requests.get(f"{BASE_URL}/api/expense-category-tree", headers=h)
    rows = r.json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))
    r = requests.patch(
        f"{BASE_URL}/api/expense-category-tree/{leaf['id']}",
        headers=h, json={"movement_types": ["fixed_asset"]})
    assert r.status_code == 400, r.text

    # Patch on a root → should succeed.
    root = next(x for x in rows if not x.get("parent_id"))
    r = requests.patch(
        f"{BASE_URL}/api/expense-category-tree/{root['id']}",
        headers=h, json={"movement_types":
                         sorted(set((root.get("movement_types") or [])
                                    + ["general_expense"]))})
    assert r.status_code == 200, r.text
