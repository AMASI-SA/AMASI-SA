"""Iter-157 — Financial Input Hub recent entries endpoint tests."""
import os
import uuid
import datetime as dt

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
    email = f"i157-{suffix}@example.com"
    pwd = "T#157abcD"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I157"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    bank = requests.post(f"{BASE_URL}/api/accounts",
                         json={"name": "Bank", "account_type": "bank",
                               "opening_balance": 50000.0},
                         headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "bank_id": bank["id"]}


def test_recent_empty(ctx):
    r = requests.get(
        f"{BASE_URL}/api/financial-input-hub/recent",
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["total_pages"] == 1
    assert body["page"] == 1


def test_recent_lists_created_liability(ctx):
    # Create a supplier liability
    r = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"name": "مورد تجريبي", "kind": "supplier"},
        headers=ctx["hdr"], timeout=10,
    )
    cp_id = r.json()["id"]
    requests.post(
        f"{BASE_URL}/api/liabilities",
        json={"kind": "supplier", "counterparty_id": cp_id,
              "expected_amount": 1500.0,
              "due_date": dt.date.today().isoformat(),
              "description": "فاتورة"},
        headers=ctx["hdr"], timeout=10,
    )
    r2 = requests.get(
        f"{BASE_URL}/api/financial-input-hub/recent?page=1&page_size=10",
        headers=ctx["hdr"], timeout=10,
    )
    body = r2.json()
    assert body["total"] >= 1
    op = body["items"][0]
    assert op["amount"] == 1500.0
    assert "إنشاء التزام" in op["operation"]
    assert op["editable"] is True
    assert op["party_open_balance"] is not None


def test_recent_pagination(ctx):
    """Create 15 supplier liabilities; verify page 1 returns 10 and
    page 2 returns 5."""
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"name": "مورد متعدد", "kind": "supplier"},
        headers=ctx["hdr"], timeout=10,
    ).json()
    for i in range(15):
        requests.post(
            f"{BASE_URL}/api/liabilities",
            json={"kind": "supplier", "counterparty_id": cp["id"],
                  "expected_amount": 100.0 + i,
                  "due_date": dt.date.today().isoformat(),
                  "description": f"فاتورة {i}"},
            headers=ctx["hdr"], timeout=10,
        )
    p1 = requests.get(
        f"{BASE_URL}/api/financial-input-hub/recent?page=1&page_size=10",
        headers=ctx["hdr"], timeout=10,
    ).json()
    assert len(p1["items"]) == 10
    assert p1["total_pages"] == 2
    p2 = requests.get(
        f"{BASE_URL}/api/financial-input-hub/recent?page=2&page_size=10",
        headers=ctx["hdr"], timeout=10,
    ).json()
    assert len(p2["items"]) >= 5
    # Verify no duplicate IDs across pages
    ids1 = {i["id"] for i in p1["items"]}
    ids2 = {i["id"] for i in p2["items"]}
    assert ids1.isdisjoint(ids2)


def test_amount_edit_via_existing_endpoint(ctx):
    """The frontend edits via PUT /liabilities/{id} which already
    exists. Verify it works for amount adjustment after the recent
    endpoint surfaces the entry."""
    cp = requests.post(
        f"{BASE_URL}/api/counterparties",
        json={"name": "مورد قابل للتعديل", "kind": "supplier"},
        headers=ctx["hdr"], timeout=10,
    ).json()
    liab = requests.post(
        f"{BASE_URL}/api/liabilities",
        json={"kind": "supplier", "counterparty_id": cp["id"],
              "expected_amount": 100.0,
              "due_date": dt.date.today().isoformat(),
              "description": "ابتدائي"},
        headers=ctx["hdr"], timeout=10,
    ).json()
    # Edit amount
    r = requests.put(
        f"{BASE_URL}/api/liabilities/{liab['id']}",
        json={"expected_amount": 250.0},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text
    # Verify the recent endpoint reflects the new amount
    rec = requests.get(
        f"{BASE_URL}/api/financial-input-hub/recent",
        headers=ctx["hdr"], timeout=10,
    ).json()
    target = next((i for i in rec["items"] if i["ref_id"] == liab["id"]), None)
    assert target is not None
    assert target["amount"] == 250.0
