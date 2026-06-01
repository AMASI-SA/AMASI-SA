"""Iteration 24 — Profit Status + Auto-reprocess on cost create/update.

Acceptance criteria covered (in user's words):
  • Make.com order with full products[] → profit_status = "complete".
  • Make.com order with SOME unmatched products → profit_status = "incomplete_missing_cost".
  • Excel-style order without products[] → profit_status = "incomplete_no_products"
    AND total_product_cost stays 0 (not assumed as full cost).
  • When merchant adds a product_cost entry, ALL past orders that referenced
    that SKU/product_id as missing get auto-reprocessed.
  • `/product-costs/missing` returns image_url + last_order_number + last_order_date.

Run:
  pytest /app/backend/tests/test_profit_status_iteration24.py -v
"""
from __future__ import annotations

import os
import uuid
import asyncio
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"profit-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "Profit Status Test", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _get_token(user_token: str) -> str:
    r = requests.get(f"{API}/webhook/settings",
                     headers={"Authorization": f"Bearer {user_token}"}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def _post_make(token: str, payload: dict):
    r = requests.post(f"{API}/webhook/make/{token}", json=payload, timeout=15)
    return r


def _get_order(uid: str, order_number: str) -> dict | None:
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        doc = await db.unified_orders.find_one(
            {"user_id": uid, "order_number": order_number}, {"_id": 0},
        )
        c.close()
        return doc
    return asyncio.run(_do())


def _seed_cost(token: str, sku: str, name: str, price: float,
               product_id: str = "", image_url: str = ""):
    r = requests.post(
        f"{API}/product-costs/",
        json={"sku": sku, "product_name": name, "cost_price": price,
              "product_id": product_id, "image_url": image_url},
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ──────────────────────────────────────────────────────────────────────────
class TestProfitStatus:

    def test_complete_when_all_products_matched(self):
        token, uid = _register()
        wh = _get_token(token)
        _seed_cost(token, "SKU-OK-1", "منتج", 10.0)
        order_no = f"O-COMPLETE-{uuid.uuid4().hex[:6]}"
        r = _post_make(wh, {
            "order_number": order_no,
            "total": 100,
            "products": [{"sku": "SKU-OK-1", "name": "منتج",
                          "quantity": 2, "price": 50}],
        })
        assert r.status_code == 200, r.text
        doc = _get_order(uid, order_no)
        assert doc is not None
        assert doc["profit_status"] == "complete"
        assert doc["products_total_lines"] == 1
        assert doc["products_matched_lines"] == 1
        assert doc["total_product_cost"] == 20.0  # 10 * 2

    def test_incomplete_missing_cost_when_no_match(self):
        token, uid = _register()
        wh = _get_token(token)
        # No cost seeded for SKU-NOCOST-1.
        order_no = f"O-NOCOST-{uuid.uuid4().hex[:6]}"
        r = _post_make(wh, {
            "order_number": order_no,
            "total": 80,
            "products": [{"sku": "SKU-NOCOST-1", "name": "غير معروف",
                          "quantity": 1, "price": 80,
                          "image_url": "https://cdn.x/p.jpg"}],
        })
        assert r.status_code == 200, r.text
        doc = _get_order(uid, order_no)
        assert doc["profit_status"] == "incomplete_missing_cost"
        # Per merchant rule: missing cost is NOT assumed to be 0 — the
        # ORDER is flagged, while total_product_cost reflects the
        # (partial) matched sum (0 here since nothing matched).
        assert doc["total_product_cost"] == 0.0
        assert doc["products_matched_lines"] == 0
        # missing line carries image_url from webhook payload
        ml = doc["missing_product_cost_lines"][0]
        assert ml["image_url"] == "https://cdn.x/p.jpg"

    def test_incomplete_no_products_for_empty_array(self):
        token, uid = _register()
        wh = _get_token(token)
        order_no = f"O-NOPRD-{uuid.uuid4().hex[:6]}"
        r = _post_make(wh, {
            "order_number": order_no,
            "total": 50,
            "products": [],  # simulating an Excel-style order
        })
        assert r.status_code == 200, r.text
        doc = _get_order(uid, order_no)
        assert doc["profit_status"] == "incomplete_no_products"
        assert doc["total_product_cost"] == 0.0
        assert doc["products_total_lines"] == 0

    def test_partial_match_keeps_incomplete_status(self):
        """1 of 2 lines matched — order stays incomplete_missing_cost."""
        token, uid = _register()
        wh = _get_token(token)
        _seed_cost(token, "SKU-HALF", "نصف معروف", 15.0)
        order_no = f"O-HALF-{uuid.uuid4().hex[:6]}"
        r = _post_make(wh, {
            "order_number": order_no,
            "total": 200,
            "products": [
                {"sku": "SKU-HALF", "name": "نصف معروف", "quantity": 2, "price": 60},
                {"sku": "SKU-MISS", "name": "غير معروف", "quantity": 1, "price": 80},
            ],
        })
        assert r.status_code == 200
        doc = _get_order(uid, order_no)
        assert doc["profit_status"] == "incomplete_missing_cost"
        assert doc["products_total_lines"] == 2
        assert doc["products_matched_lines"] == 1
        # Partial cost: only the matched line (15 * 2) contributes.
        assert doc["total_product_cost"] == 30.0
        assert len(doc["missing_product_cost_lines"]) == 1
        assert doc["missing_product_cost_lines"][0]["sku"].upper() == "SKU-MISS"


# ──────────────────────────────────────────────────────────────────────────
class TestAutoReprocess:

    def test_create_cost_relinks_past_orders(self):
        """The headline iteration-24 flow:
        1. Order arrives with unknown SKU → marked incomplete_missing_cost.
        2. Merchant adds the cost via POST /product-costs/.
        3. Past order auto-flips to profit_status = "complete".
        """
        token, uid = _register()
        wh = _get_token(token)
        order_no = f"O-RELINK-{uuid.uuid4().hex[:6]}"
        # Step 1 — order without cost.
        _post_make(wh, {
            "order_number": order_no,
            "total": 100,
            "products": [{"sku": "RELINK-1", "name": "منتج", "quantity": 2, "price": 50}],
        })
        doc = _get_order(uid, order_no)
        assert doc["profit_status"] == "incomplete_missing_cost"
        # Step 2 — merchant adds the cost.
        r = requests.post(
            f"{API}/product-costs/",
            json={"sku": "RELINK-1", "product_name": "منتج", "cost_price": 12.0},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        # The endpoint must report how many orders were re-linked.
        body = r.json()
        assert body.get("reprocessed_orders", 0) >= 1
        # Step 3 — past order is now complete.
        doc2 = _get_order(uid, order_no)
        assert doc2["profit_status"] == "complete"
        assert doc2["total_product_cost"] == 24.0  # 12 * 2
        assert doc2["products_matched_lines"] == 1
        assert doc2["missing_product_cost_lines"] == []

    def test_update_cost_price_reflows_to_orders(self):
        """Editing cost_price re-runs cost calc on past orders."""
        token, uid = _register()
        wh = _get_token(token)
        seeded = _seed_cost(token, "EDIT-1", "منتج معدّل", 10.0)
        order_no = f"O-EDIT-{uuid.uuid4().hex[:6]}"
        _post_make(wh, {
            "order_number": order_no,
            "total": 100,
            "products": [{"sku": "EDIT-1", "name": "منتج", "quantity": 3, "price": 30}],
        })
        doc = _get_order(uid, order_no)
        assert doc["total_product_cost"] == 30.0  # 10 * 3
        # Update cost to 17.5
        r = requests.put(
            f"{API}/product-costs/{seeded['id']}",
            json={"cost_price": 17.5},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200
        # Past order now reflects the new cost.
        doc2 = _get_order(uid, order_no)
        assert doc2["total_product_cost"] == 52.5  # 17.5 * 3


# ──────────────────────────────────────────────────────────────────────────
class TestMissingEndpoint:

    def test_missing_returns_image_and_last_order(self):
        token, uid = _register()
        wh = _get_token(token)
        order_no = f"O-MISS-{uuid.uuid4().hex[:6]}"
        _post_make(wh, {
            "order_number": order_no,
            "total": 50,
            "order_date": "2026-05-30",
            "products": [{"sku": "UNCOSTED-A",
                          "product_id": "98765",
                          "name": "منتج بدون تكلفة",
                          "image_url": "https://cdn.x/missing.jpg",
                          "quantity": 1, "price": 50}],
        })
        r = requests.get(f"{API}/product-costs/missing",
                         headers={"Authorization": f"Bearer {token}"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] >= 1
        m = next((it for it in body["items"]
                  if (it.get("sku") or "").upper() == "UNCOSTED-A"), None)
        assert m is not None, body
        assert m["image_url"] == "https://cdn.x/missing.jpg"
        assert m["product_id"] == "98765"
        assert m["last_order_number"] == order_no
        assert m["last_order_date"] == "2026-05-30"
        assert m["occurrences"] >= 1

    def test_missing_endpoint_includes_excel_no_products_count(self):
        """Excel-style orders without products[] are counted separately."""
        token, uid = _register()
        wh = _get_token(token)
        # 2 orders without products[]
        for i in range(2):
            _post_make(wh, {
                "order_number": f"EX-NOPRD-{uuid.uuid4().hex[:6]}",
                "total": 50 + i,
                "products": [],
            })
        r = requests.get(f"{API}/product-costs/missing",
                         headers={"Authorization": f"Bearer {token}"}, timeout=15)
        assert r.status_code == 200
        body = r.json()
        # Field always present even if 0
        assert "excel_no_products_count" in body
        # Make.com without products[] won't show up here as "excel"
        # because data_source = 'make'. But test that the field is exposed.
        assert isinstance(body["excel_no_products_count"], int)


# ──────────────────────────────────────────────────────────────────────────
class TestDashboardSurface:

    def test_dashboard_exposes_incomplete_counts(self):
        token, uid = _register()
        wh = _get_token(token)
        # Order with uncosted product.
        _post_make(wh, {
            "order_number": f"DBM-{uuid.uuid4().hex[:6]}",
            "total": 60,
            "products": [{"sku": "DASH-MISS", "name": "غير معروف",
                          "quantity": 1, "price": 60}],
        })
        # Order without products[].
        _post_make(wh, {
            "order_number": f"DBN-{uuid.uuid4().hex[:6]}",
            "total": 30,
            "products": [],
        })
        r = requests.get(f"{API}/dashboard",
                         headers={"Authorization": f"Bearer {token}"}, timeout=15)
        assert r.status_code == 200, r.text
        t = r.json().get("totals") or {}
        # New iteration-24 fields must be present.
        assert "incomplete_profit_orders_count" in t
        assert "no_products_orders_count" in t
        assert "excel_no_products_count" in t
        # We seeded 2 incomplete orders.
        assert t["incomplete_profit_orders_count"] >= 2
        assert t["no_products_orders_count"] >= 1
