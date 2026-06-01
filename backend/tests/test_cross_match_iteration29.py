"""Iteration 29 — Cross-match SKU↔Product ID.

Reproduces the real merchant bug:
"إلى الآن مافي اي بيانات تكلفة المنتجات ماتظهر خالص — مرتبط 2,123 منتج"

Root cause: merchant imported a Salla products Excel where the column
labeled `رقم المنتج` (product_id) ended up in the catalogue's `sku`
field, leaving `product_id` empty. Orders arriving from Make.com carry
`product_id` only → no match → total_product_cost stays 0 forever.

Acceptance covered:
- Catalogue row with SKU="ABC123" and product_id=""
  matches an order line with product_id="ABC123" (sku empty).
- Catalogue row with product_id="999" and SKU=""
  matches an order line with sku="999" (product_id empty).
- Reprocess after adding catalogue cost flips past orders too,
  even when the identifier swap happened.

Run:
  pytest /app/backend/tests/test_cross_match_iteration29.py -v
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
    email = f"i29-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "I29", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"], r.json()["id"]


def _hdr(t): return {"Authorization": f"Bearer {t}"}


def _wh(t):
    r = requests.get(f"{API}/webhook/settings", headers=_hdr(t), timeout=15)
    return r.json()["token"]


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


# ──────────────────────────────────────────────────────────────────────────
class TestCrossMatch:

    def test_sku_in_catalogue_matches_order_product_id(self):
        """The HEADLINE bug: catalogue has identifier in SKU column,
        order arrives with the same value in product_id field → MUST
        match via the new cross-lookup."""
        token, uid = _register()
        wh = _wh(token)
        # Seed catalogue: identifier is in SKU column, product_id empty.
        # This mirrors how Salla product Excel imports leave the data
        # for the affected merchant.
        r = requests.post(
            f"{API}/product-costs/",
            json={"sku": "1573005664", "product_name": "خاتم",
                  "cost_price": 22.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        # Order arrives with the value in product_id field.
        order_no = f"O-CROSS-{uuid.uuid4().hex[:6]}"
        today = datetime.now(timezone.utc).date().isoformat()
        requests.post(
            f"{API}/webhook/make/{wh}",
            json={"order_number": order_no, "total": 60,
                  "order_date": today,
                  "products": [{"product_id": "1573005664",
                                "name": "خاتم",
                                "quantity": 2, "price": 30}]},
            timeout=15,
        )
        doc = _get_order(uid, order_no)
        assert doc is not None
        # Before iter 29 → profit_status='incomplete_missing_cost' and tpc=0.
        # After iter 29 → profit_status='complete' and tpc=44 (22 * 2).
        assert doc["profit_status"] == "complete", f"Expected complete, got {doc['profit_status']}"
        assert doc["total_product_cost"] == 44.0
        # The matched_by reflects the cross-match path.
        assert doc["cost_items"][0]["matched_by"] in (
            "product_id_as_sku", "sku", "product_id", "sku_as_product_id"
        )

    def test_product_id_in_catalogue_matches_order_sku(self):
        """Reverse cross-match: catalogue has identifier in product_id
        field, order arrives with the same value in sku field."""
        token, uid = _register()
        wh = _wh(token)
        r = requests.post(
            f"{API}/product-costs/",
            json={"product_id": "9999", "product_name": "سلسال",
                  "cost_price": 15.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200
        order_no = f"O-REV-{uuid.uuid4().hex[:6]}"
        today = datetime.now(timezone.utc).date().isoformat()
        requests.post(
            f"{API}/webhook/make/{wh}",
            json={"order_number": order_no, "total": 30,
                  "order_date": today,
                  "products": [{"sku": "9999", "name": "سلسال",
                                "quantity": 1, "price": 30}]},
            timeout=15,
        )
        doc = _get_order(uid, order_no)
        assert doc["profit_status"] == "complete"
        assert doc["total_product_cost"] == 15.0

    def test_canonical_match_still_preferred_over_cross(self):
        """When both canonical AND cross-match are possible (same
        identifier exists in BOTH sku and product_id of different
        catalogue rows), canonical (sku→sku, product_id→product_id)
        must win."""
        token, uid = _register()
        wh = _wh(token)
        # Catalogue: 2 rows. Row A has SKU=AAA (cost=10). Row B has
        # product_id=AAA (cost=999). Order arrives with SKU=AAA.
        # Expected: canonical sku→sku match → Row A (cost 10), NOT Row B.
        requests.post(
            f"{API}/product-costs/",
            json={"sku": "AAA-canonical", "product_name": "A", "cost_price": 10.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        requests.post(
            f"{API}/product-costs/",
            json={"product_id": "AAA-canonical", "product_name": "B",
                  "cost_price": 999.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        order_no = f"O-CAN-{uuid.uuid4().hex[:6]}"
        today = datetime.now(timezone.utc).date().isoformat()
        requests.post(
            f"{API}/webhook/make/{wh}",
            json={"order_number": order_no, "total": 50, "order_date": today,
                  "products": [{"sku": "AAA-canonical", "name": "A",
                                "quantity": 1, "price": 50}]},
            timeout=15,
        )
        doc = _get_order(uid, order_no)
        # Canonical sku-to-sku match wins (cost 10, not 999)
        assert doc["total_product_cost"] == 10.0
        assert doc["cost_items"][0]["matched_by"] == "sku"

    def test_reprocess_after_recompute_picks_up_cross_match(self):
        """Existing past order with no cost → after /recompute,
        cross-match kicks in and the order flips to complete."""
        token, uid = _register()
        wh = _wh(token)
        # Order arrives BEFORE any catalogue entry.
        order_no = f"O-LATE-{uuid.uuid4().hex[:6]}"
        today = datetime.now(timezone.utc).date().isoformat()
        requests.post(
            f"{API}/webhook/make/{wh}",
            json={"order_number": order_no, "total": 100,
                  "order_date": today,
                  "products": [{"product_id": "LATE-1", "name": "x",
                                "quantity": 1, "price": 100}]},
            timeout=15,
        )
        doc = _get_order(uid, order_no)
        assert doc["profit_status"] == "incomplete_missing_cost"
        # Now catalogue is created with the identifier in SKU column (bug case).
        requests.post(
            f"{API}/product-costs/",
            json={"sku": "LATE-1", "product_name": "x", "cost_price": 25.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        # Recompute → order must flip via cross-match.
        r = requests.post(f"{API}/product-costs/recompute?days=30",
                          headers=_hdr(token), timeout=20)
        assert r.json()["complete_orders"] >= 1
        doc2 = _get_order(uid, order_no)
        assert doc2["profit_status"] == "complete"
        assert doc2["total_product_cost"] == 25.0
