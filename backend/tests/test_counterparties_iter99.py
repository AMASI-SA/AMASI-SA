"""Iter-99 — Counterparties collection + linkage to liabilities.

Covers:
  • CRUD basics (create / list / update / delete)
  • Fuzzy duplicate WARNING (does NOT auto-merge)
  • `force=true` bypasses the warning (creates a second account)
  • Exact-dup blocking (409 with kind="duplicate")
  • Liability creation with counterparty_id pulls the name from the
    counterparties table for both supplier and ad_account kinds.
  • Delete is refused when the counterparty has any unpaid liability.
"""
import os
import uuid

import requests


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _new_user():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter99-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#99test", "name": "CP"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#99test"},
        timeout=10,
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── CRUD ────────────────────────────────────────────────────────────
def test_counterparty_crud_basic():
    h = _new_user()
    # List empty
    r = requests.get(f"{BASE_URL}/api/counterparties", headers=h, timeout=10)
    assert r.status_code == 200
    assert r.json()["total"] == 0

    # Create supplier
    r = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "supplier", "name": "شركة الكرتون"},
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["name"] == "شركة الكرتون"
    assert r.json()["name_lower"] == "شركة الكرتون"

    # Create ad_account
    r = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "ad_account", "ad_provider": "snapchat", "name": "Snapchat Account 1"},
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert r.json()["ad_provider"] == "snapchat"

    # List, expect 2
    r = requests.get(f"{BASE_URL}/api/counterparties", headers=h, timeout=10)
    assert r.json()["total"] == 2

    # Filter by kind=supplier
    r = requests.get(f"{BASE_URL}/api/counterparties?kind=supplier", headers=h, timeout=10)
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["id"] == sid

    # Update notes
    r = requests.put(
        f"{BASE_URL}/api/counterparties/{sid}",
        json={"notes": "مورد رئيسي"},
        headers=h, timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "مورد رئيسي"

    # Delete ad_account (no liability yet)
    r = requests.delete(f"{BASE_URL}/api/counterparties/{aid}", headers=h, timeout=10)
    assert r.status_code == 200


# ── Exact duplicates blocked ─────────────────────────────────────────
def test_exact_duplicate_blocked():
    h = _new_user()
    requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "supplier", "name": "مورد ألف"},
        headers=h, timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "supplier", "name": "مورد ألف"},
        headers=h, timeout=10,
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["message"] == "duplicate"


# ── Fuzzy is WARNING only, NOT auto-merge ───────────────────────────
def test_fuzzy_warning_is_not_auto_merge():
    """Snapchat Account 1 vs Snapchat Account 2 are similar (>0.82).
    The API must return 409 with `similar_name_exists`, but with
    `force=true` it MUST create a SECOND distinct row.
    """
    h = _new_user()
    r = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "ad_account", "ad_provider": "snapchat", "name": "Snapchat Account 1"},
        headers=h, timeout=10,
    )
    assert r.status_code == 200
    first_id = r.json()["id"]

    # Attempt 2 without force → 409 similar_name_exists
    r = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "ad_account", "ad_provider": "snapchat", "name": "Snapchat Account 2"},
        headers=h, timeout=10,
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["message"] == "similar_name_exists"
    assert detail["suggestion"]["id"] == first_id

    # Now with force → creates SEPARATE row
    r = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "ad_account", "ad_provider": "snapchat",
              "name": "Snapchat Account 2", "force": True},
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    second_id = r.json()["id"]
    assert second_id != first_id

    # Both rows exist independently
    r = requests.get(
        f"{BASE_URL}/api/counterparties?kind=ad_account",
        headers=h, timeout=10,
    )
    ids = {x["id"] for x in r.json()["items"]}
    assert {first_id, second_id} <= ids


# ── check-duplicate preview endpoint ────────────────────────────────
def test_check_duplicate_endpoint():
    h = _new_user()
    requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "supplier", "name": "شركة التغليف الذهبي"},
        headers=h, timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/counterparties/check-duplicate",
        json={"kind": "supplier", "name": "شركة التغليف الذهبية"},  # close
        headers=h, timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["suggestion"] is not None
    assert body["suggestion"]["name"] == "شركة التغليف الذهبي"

    # Completely different name → no suggestion
    r = requests.post(
        f"{BASE_URL}/api/counterparties/check-duplicate",
        json={"kind": "supplier", "name": "أكاديمية لوكال"},
        headers=h, timeout=10,
    )
    assert r.json()["suggestion"] is None


# ── Liability creation with counterparty_id ─────────────────────────
def test_liability_supplier_with_counterparty_id_sources_name():
    h = _new_user()
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "supplier", "name": "شركة التعبئة المتقدمة"},
        headers=h, timeout=10,
    ).json()

    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "supplier",
            "counterparty_id": cp["id"],   # NOTE: no supplier_name supplied
            "expected_amount": 1200,
            "due_date": "2026-07-01",
            "description": "فاتورة كرتون",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["supplier_name"] == "شركة التعبئة المتقدمة"
    assert body["counterparty_id"] == cp["id"]
    assert body["remaining_amount"] == 1200


def test_liability_ad_account_with_counterparty_id_sources_name():
    h = _new_user()
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "ad_account", "ad_provider": "snapchat",
              "name": "Snapchat Account 1"},
        headers=h, timeout=10,
    ).json()

    r = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "ad_account",
            "ad_provider": "snapchat",
            "counterparty_id": cp["id"],
            "expected_amount": 3000,
            "due_date": "2026-07-01",
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ad_account_label"] == "Snapchat Account 1"
    assert body["counterparty_id"] == cp["id"]


# ── Delete refusal when in use ──────────────────────────────────────
def test_delete_refused_if_unpaid_liability_exists():
    h = _new_user()
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"kind": "supplier", "name": "مورد قيد الاستخدام"},
        headers=h, timeout=10,
    ).json()
    requests.post(
        f"{BASE_URL}/api/liabilities",
        json={
            "kind": "supplier",
            "counterparty_id": cp["id"],
            "expected_amount": 500,
            "due_date": "2026-07-01",
        },
        headers=h, timeout=10,
    )
    r = requests.delete(
        f"{BASE_URL}/api/counterparties/{cp['id']}",
        headers=h, timeout=10,
    )
    assert r.status_code == 400
    assert "مرتبط" in r.json()["detail"]
