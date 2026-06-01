"""Iteration 19 — webhook ingestion enrichment + SKU-first precedence (E2E).

Verifies the merchant's two explicit acceptance requirements:
  1) POST /api/webhook/make/{token} with a Salla-shaped payload enriches
     unified_orders.total_product_cost + cost_items via attach_cost_to_order_doc.
  2) SKU match takes precedence over product_id match when both could match.
"""
from __future__ import annotations

import os
import uuid
import asyncio
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"pcw-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "Webhook PC Test", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_order_doc(uid: str, order_number: str) -> dict | None:
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        doc = await db.unified_orders.find_one(
            {"user_id": uid, "order_number": order_number}, {"_id": 0},
        )
        c.close()
        return doc
    return asyncio.run(_do())


class TestWebhookEnrichment:
    def test_webhook_attaches_total_product_cost(self):
        token, uid = _register()

        # 1) Seed a product cost for SKU 'WHTEST'
        r = requests.post(f"{API}/product-costs/", headers=_h(token),
                          json={"sku": "WHTEST", "product_name": "Hook Test Product",
                                "cost_price": 12.5},
                          timeout=10)
        assert r.status_code == 200, r.text

        # 2) Fetch webhook token for this user
        r = requests.get(f"{API}/webhook/settings", headers=_h(token), timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        wtoken = body.get("token") or body.get("webhook_token")
        assert wtoken, body

        # 3) Post a Salla-shaped order through the public webhook
        order_no = f"WHO-{uuid.uuid4().hex[:8]}"
        payload = {
            "order_number": order_no,
            "reference_id": order_no,
            "id": order_no,
            "status": "delivered",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "amounts": {"total": {"amount": 100.0}},
            "products": [
                {"sku": "WHTEST", "quantity": 2, "price": 50.0, "name": "Hook Test Product"},
            ],
            "customer": {"name": "T", "mobile": "0500000000"},
            "shipping": {"company": "Aramex"},
            "payment_method": "cod",
        }
        r = requests.post(f"{API}/webhook/make/{wtoken}", json=payload, timeout=15)
        assert r.status_code in (200, 201), r.text

        # 4) Verify the order doc has total_product_cost = 12.5 * 2 = 25
        doc = _get_order_doc(uid, order_no)
        assert doc is not None, "order not persisted"
        assert doc.get("total_product_cost") == 25.0, doc
        assert isinstance(doc.get("cost_items"), list) and len(doc["cost_items"]) == 1
        assert doc["cost_items"][0]["matched_by"] == "sku"
        assert doc["cost_items"][0]["unit_cost"] == 12.5
        # No missing
        assert doc.get("missing_product_cost_lines") == []


class TestSkuFirstPrecedence:
    """Merchant explicitly required SKU-first lookup. Verify by creating:
       cost1: sku='X1' (no product_id)
       cost2: sku='X2' + product_id='PID2'
       Order line 1: {sku:'x1', qty:2}            → matches by sku=X1, cost=10
       Order line 2: {product_id:'PID2', qty:1}   → matches by product_id=PID2, cost=30
    """

    def _seed_via_api(self, token: str):
        for body in [
            {"sku": "X1", "product_name": "Only SKU", "cost_price": 10.0},
            {"sku": "X2", "product_id": "PID2", "product_name": "SKU+PID", "cost_price": 30.0},
        ]:
            r = requests.post(f"{API}/product-costs/", headers=_h(token), json=body, timeout=10)
            assert r.status_code == 200, r.text

    def _seed_order(self, uid: str, order_number: str):
        async def _do():
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            today = datetime.now(timezone.utc).date().isoformat()
            await db.unified_orders.update_one(
                {"user_id": uid, "order_number": order_number},
                {"$set": {
                    "user_id": uid,
                    "order_number": order_number,
                    "order_date": today,
                    "products": [
                        {"sku": "x1", "quantity": 2, "price": 100, "name": "Only SKU"},
                        {"product_id": "PID2", "quantity": 1, "price": 200, "name": "SKU+PID"},
                    ],
                    "total_amount": 400.0,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
            c.close()
        asyncio.run(_do())

    def test_sku_takes_precedence_over_product_id(self):
        token, uid = _register()
        self._seed_via_api(token)
        order_no = f"SP-{uuid.uuid4().hex[:8]}"
        self._seed_order(uid, order_no)

        r = requests.post(f"{API}/product-costs/recompute", headers=_h(token), timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["orders_updated"] >= 1

        doc = _get_order_doc(uid, order_no)
        assert doc is not None
        items = doc["cost_items"]
        assert len(items) == 2
        # Line 1: sku='x1' → matched by SKU
        line1 = next(i for i in items if (i.get("sku") or "").upper() == "X1")
        assert line1["matched_by"] == "sku"
        assert line1["unit_cost"] == 10.0
        assert line1["line_cost"] == 20.0
        # Line 2: only product_id → matched by product_id
        line2 = next(i for i in items if i.get("product_id") == "PID2")
        assert line2["matched_by"] == "product_id"
        assert line2["unit_cost"] == 30.0
        assert line2["line_cost"] == 30.0

        # Total = 20 + 30 = 50
        assert doc["total_product_cost"] == 50.0
        assert doc.get("missing_product_cost_lines") == []
