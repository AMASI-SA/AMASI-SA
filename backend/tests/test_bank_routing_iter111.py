"""Iter-111 — Bank-transfer routing.

Verifies:
  • GET /accounts/bank-transfer-routing/options returns the known bank
    sub-keys (rajhi, inma, ahli, …) with display names.
  • PUT /accounts/{id} with `bank_transfer_aliases` stores the routing.
  • Conflict detection: two banks can't claim the same sub-key.
  • Routing only allowed on account_type='bank'.
  • After routing is set, `sync-payment-methods` diverts the matching
    order revenue from the rollup to the routed bank account.
  • GET /accounts/{id}/breakdown returns the expected components.
"""
import os
import uuid

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _mdb():
    load_dotenv("/app/backend/.env")
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
def ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"route111-{suffix}@example.com"
    pwd = "T#111a"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "R"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb()}


def _create_bank(ctx, name):
    r = requests.post(f"{BASE_URL}/api/accounts",
                      json={"name": name, "account_type": "bank",
                            "opening_balance": 0},
                      headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_options_endpoint_lists_known_banks(ctx):
    r = requests.get(f"{BASE_URL}/api/accounts/bank-transfer-routing/options",
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    sub_keys = {o["sub_key"] for o in r.json()["options"]}
    # Must include at least the 3 majors
    assert "bank_rajhi" in sub_keys
    assert "bank_inma" in sub_keys
    assert "bank_ahli" in sub_keys
    # Must NOT contain the generic catch-all (only specific banks)
    assert "bank_transfer" not in sub_keys


def test_put_sets_aliases_on_bank(ctx):
    bank_id = _create_bank(ctx, "بنك الراجحي")
    r = requests.put(f"{BASE_URL}/api/accounts/{bank_id}",
                     json={"bank_transfer_aliases": ["bank_rajhi"]},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["bank_transfer_aliases"] == ["bank_rajhi"]


def test_put_rejects_routing_on_non_bank(ctx):
    pp = requests.post(f"{BASE_URL}/api/accounts",
                       json={"name": "Test PP", "account_type": "payment_platform",
                             "opening_balance": 0},
                       headers=ctx["hdr"], timeout=10).json()
    r = requests.put(f"{BASE_URL}/api/accounts/{pp['id']}",
                     json={"bank_transfer_aliases": ["bank_rajhi"]},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 400
    assert "bank" in r.json()["detail"]


def test_put_rejects_unknown_sub_key(ctx):
    bank_id = _create_bank(ctx, "Bank X")
    r = requests.put(f"{BASE_URL}/api/accounts/{bank_id}",
                     json={"bank_transfer_aliases": ["bank_unknown_xyz"]},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 400


def test_put_rejects_duplicate_alias_across_banks(ctx):
    a = _create_bank(ctx, "Bank A")
    b = _create_bank(ctx, "Bank B")
    requests.put(f"{BASE_URL}/api/accounts/{a}",
                 json={"bank_transfer_aliases": ["bank_rajhi"]},
                 headers=ctx["hdr"], timeout=10)
    r = requests.put(f"{BASE_URL}/api/accounts/{b}",
                     json={"bank_transfer_aliases": ["bank_rajhi"]},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 400
    assert "Bank A" in r.json()["detail"]


def test_routing_redirects_order_revenue_to_routed_bank(ctx):
    """Core integration test — set routing, post some orders with the
    matching payment method, run sync, verify the bank's
    expected_orders_balance picked up the amount and rollup didn't."""
    bank = _create_bank(ctx, "Rajhi Test")
    requests.put(f"{BASE_URL}/api/accounts/{bank}",
                 json={"bank_transfer_aliases": ["bank_rajhi"]},
                 headers=ctx["hdr"], timeout=10)

    # Seed 2 unified_orders with payment_method that resolves to bank_rajhi
    now = "2026-06-08T00:00:00+00:00"
    ctx["db"].unified_orders.insert_many([
        {"id": str(uuid.uuid4()), "user_id": ctx["uid"], "order_number": "R1",
         "payment_method": "حوالة بنكية مصرف الراجحي", "total_amount": 250.0,
         "order_status": "completed", "order_date": "2026-06-01",
         "created_at": now},
        {"id": str(uuid.uuid4()), "user_id": ctx["uid"], "order_number": "R2",
         "payment_method": "الراجحي", "total_amount": 150.0,
         "order_status": "completed", "order_date": "2026-06-02",
         "created_at": now},
        # Different bank — must NOT bleed into routed bank
        {"id": str(uuid.uuid4()), "user_id": ctx["uid"], "order_number": "I1",
         "payment_method": "حوالة بنكية مصرف الإنماء", "total_amount": 99.0,
         "order_status": "completed", "order_date": "2026-06-03",
         "created_at": now},
    ])
    r = requests.post(f"{BASE_URL}/api/accounts/sync-payment-methods",
                      headers=ctx["hdr"], timeout=20)
    assert r.status_code == 200, r.text
    routed = r.json().get("routed_banks", [])
    rajhi = next((b for b in routed if b["id"] == bank), None)
    assert rajhi is not None, f"Routed bank not in response: {routed}"
    assert rajhi["expected_orders_balance"] == 400.0  # 250 + 150
    assert rajhi["orders_count"] == 2

    # The rollup "تحويل بنكي" should now hold ONLY the Inma row (99.0)
    # because Inma is NOT routed in this test.
    accs = requests.get(f"{BASE_URL}/api/accounts?account_type=payment_platform",
                       headers=ctx["hdr"], timeout=10).json()
    bt_rollup = next((a for a in accs
                      if a.get("normalized_payment_method") == "bank_transfer"), None)
    assert bt_rollup is not None
    assert bt_rollup["expected_orders_balance"] == 99.0


def test_routing_change_recomputes_balance(ctx):
    """Removing the routing must reset the bank's balance to 0 and
    return the revenue to the rollup."""
    bank = _create_bank(ctx, "Rajhi Toggle")
    requests.put(f"{BASE_URL}/api/accounts/{bank}",
                 json={"bank_transfer_aliases": ["bank_rajhi"]},
                 headers=ctx["hdr"], timeout=10)
    ctx["db"].unified_orders.insert_one({
        "id": str(uuid.uuid4()), "user_id": ctx["uid"],
        "order_number": "T1", "payment_method": "الراجحي",
        "total_amount": 500.0, "order_status": "completed",
        "order_date": "2026-06-01",
    })
    r1 = requests.post(f"{BASE_URL}/api/accounts/sync-payment-methods",
                       headers=ctx["hdr"], timeout=20)
    routed = r1.json()["routed_banks"]
    assert routed[0]["expected_orders_balance"] == 500.0

    # Remove the routing
    requests.put(f"{BASE_URL}/api/accounts/{bank}",
                 json={"bank_transfer_aliases": []},
                 headers=ctx["hdr"], timeout=10)
    r2 = requests.post(f"{BASE_URL}/api/accounts/sync-payment-methods",
                       headers=ctx["hdr"], timeout=20)
    # Routed banks should be empty now
    assert r2.json()["routed_banks"] == []
    # And the bank's balance should be back to 0
    acc = requests.get(f"{BASE_URL}/api/accounts/{bank}",
                       headers=ctx["hdr"], timeout=10).json()
    assert acc["expected_orders_balance"] == 0.0
    assert acc["current_balance"] == 0.0


def test_breakdown_endpoint(ctx):
    bank = _create_bank(ctx, "Breakdown Bank")
    requests.put(f"{BASE_URL}/api/accounts/{bank}",
                 json={"bank_transfer_aliases": ["bank_rajhi"]},
                 headers=ctx["hdr"], timeout=10)
    ctx["db"].unified_orders.insert_one({
        "id": str(uuid.uuid4()), "user_id": ctx["uid"],
        "order_number": "B1", "payment_method": "الراجحي",
        "total_amount": 1000.0, "order_status": "completed",
        "order_date": "2026-06-01",
    })
    requests.post(f"{BASE_URL}/api/accounts/sync-payment-methods",
                  headers=ctx["hdr"], timeout=20)

    r = requests.get(f"{BASE_URL}/api/accounts/{bank}/breakdown",
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["incoming_from_customer_bank_transfers"] == 1000.0
    assert d["orders_count"] == 1
    assert d["bank_transfer_aliases"] == ["bank_rajhi"]
    assert d["final_balance"] == 1000.0
    assert d["recorded_balance"] == 1000.0
    assert d["discrepancy"] == 0.0
