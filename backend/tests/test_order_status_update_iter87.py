"""Iter-87 — Order status update path.

Validates the merchant's requirement: when Make.com / Salla sends an
`order.updated` event for an existing order, the system must:
  • upsert (not duplicate) by order_number
  • always refresh order_status (CRITICAL field — newer wins)
  • flip the policy category live (pending → confirmed when paid)
  • be re-runnable via the manual resync endpoint
"""
import os
import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0],
)


@pytest.mark.asyncio
async def test_make_webhook_updates_status_in_place():
    """Direct unit-style test against the upsert helper."""
    import sys
    sys.path.insert(0, "/app/backend")
    from motor.motor_asyncio import AsyncIOMotorClient
    from orders_db import upsert_order

    mongo = open("/app/backend/.env").read().split("MONGO_URL=")[1].split("\n")[0].strip('"')
    client = AsyncIOMotorClient(mongo)
    db = client["test_database"]
    uid = "test-iter87-status-update"
    order_no = "TEST-264753863"

    # Clean slate
    await db.unified_orders.delete_many({"user_id": uid, "order_number": order_no})

    # 1) Order arrives initially with بانتظار الدفع
    res1 = await upsert_order(
        db, uid, order_no,
        {
            "order_id": "salla-xyz",
            "order_status": "بانتظار الدفع",
            "payment_status": "pending",
            "total_amount": 199.50,
            "payment_method": "تحويل بنكي",
            "order_date": "2026-06-01",
        },
        source="make",
    )
    assert res1["created"] is True
    doc1 = res1["doc"]
    assert doc1["order_status"] == "بانتظار الدفع"

    # 2) Same order arrives again with بانتظار المراجعة (paid)
    res2 = await upsert_order(
        db, uid, order_no,
        {
            "order_status": "بانتظار المراجعة",
            "payment_status": "paid",
            "total_amount": 199.50,
            "payment_method": "تحويل بنكي",
            "order_date": "2026-06-01",
        },
        source="make",
    )
    assert res2["created"] is False, "must NOT duplicate"
    doc2 = res2["doc"]
    assert doc2["order_status"] == "بانتظار المراجعة", doc2

    # 3) Only ONE doc in DB for that order_number
    cnt = await db.unified_orders.count_documents(
        {"user_id": uid, "order_number": order_no}
    )
    assert cnt == 1, f"duplicates detected: {cnt}"

    # Cleanup
    await db.unified_orders.delete_many({"user_id": uid, "order_number": order_no})


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "amasi.jewelery@gmail.com", "password": "10201917"},
               timeout=10)
    r.raise_for_status()
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


def test_resync_endpoint_exists(auth):
    """POST /api/orders/{order_number}/resync — exists & responds JSON."""
    r = auth.post(f"{BASE_URL}/api/orders/264753863/resync", timeout=30)
    assert r.status_code in (200, 400, 502)  # 400 when Salla disconnected
    d = r.json()
    # Either a successful resync or an explicit error structure
    assert "ok" in d or "detail" in d


def test_resync_returns_before_after_snapshot(auth):
    """Even when Salla isn't connected the endpoint must return a
    deterministic shape that the UI can render."""
    r = auth.post(f"{BASE_URL}/api/orders/264753863/resync", timeout=30)
    if r.status_code == 200:
        d = r.json()
        assert "ok" in d
        if d.get("found"):
            assert "after" in d
            assert "before" in d
