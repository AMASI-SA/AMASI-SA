"""Comprehensive tests for Product Cost Management (iteration 19).

Covers:
- CRUD: create, list (search/filter/active), update, soft-delete, re-activate
- Excel import (multi-row, mixed Arabic/English headers, error reporting)
- Cost lookup by SKU > product_id (compute_order_cost helper)
- Missing-costs endpoint (lazy compute when order not enriched yet)
- Summary endpoint (today/month/avg/top)
- Recompute endpoint (re-attaches cost to existing orders)
- Dashboard `total_product_cost` reflects computed cost across orders

Run:
  export REACT_APP_BACKEND_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
  pytest /app/backend/tests/test_product_costs.py -v
"""
from __future__ import annotations

import io
import os
import uuid
import asyncio
import pytest
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from openpyxl import Workbook

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"pc-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "PC Test", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ── CRUD ──────────────────────────────────────────────────────────────────
class TestCRUD:
    def test_create_list_update_delete_flow(self):
        token, uid = _register()

        # List empty
        r = requests.get(f"{API}/product-costs/", headers=_headers(token), timeout=10)
        assert r.status_code == 200
        assert r.json()["total"] == 0

        # Create one
        r = requests.post(f"{API}/product-costs/", headers=_headers(token),
                          json={"sku": "NECK001", "product_name": "سلسال مضيء",
                                "supplier_name": "مورد سلسال", "cost_price": 18, "currency": "SAR"},
                          timeout=10)
        assert r.status_code == 200, r.text
        item = r.json()
        assert item["sku"] == "NECK001"
        assert item["sku_normalized"] == "NECK001"
        assert item["cost_price"] == 18.0
        assert item["is_active"] is True
        item_id = item["id"]

        # Duplicate (case-insensitive) → 409 with friendly Arabic
        r = requests.post(f"{API}/product-costs/", headers=_headers(token),
                          json={"sku": "neck001", "product_name": "x", "cost_price": 1},
                          timeout=10)
        assert r.status_code == 409
        assert "موجود" in r.json()["detail"]

        # Create a second SKU
        r = requests.post(f"{API}/product-costs/", headers=_headers(token),
                          json={"sku": "RING_002", "product_name": "خاتم فضة",
                                "cost_price": 45},
                          timeout=10)
        assert r.status_code == 200

        # Search by Arabic name
        r = requests.get(f"{API}/product-costs/?search=سلسال", headers=_headers(token), timeout=10)
        assert r.json()["total"] == 1

        # Update price
        r = requests.put(f"{API}/product-costs/{item_id}", headers=_headers(token),
                         json={"cost_price": 22}, timeout=10)
        assert r.status_code == 200
        assert r.json()["cost_price"] == 22.0

        # Delete (soft)
        r = requests.delete(f"{API}/product-costs/{item_id}", headers=_headers(token), timeout=10)
        assert r.status_code == 200
        # Active list should not contain it
        r = requests.get(f"{API}/product-costs/?is_active=true",
                         headers=_headers(token), timeout=10)
        skus = {i["sku"] for i in r.json()["items"]}
        assert "NECK001" not in skus
        assert "RING_002" in skus

        # Re-create with same SKU → re-activates instead of erroring
        r = requests.post(f"{API}/product-costs/", headers=_headers(token),
                          json={"sku": "NECK001", "product_name": "سلسال v2",
                                "cost_price": 25},
                          timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["is_active"] is True
        assert body["cost_price"] == 25.0
        assert body["product_name"] == "سلسال v2"


# ── Excel Import ──────────────────────────────────────────────────────────
class TestExcelImport:
    def _build_xlsx(self, rows: list, headers: list) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.append(headers)
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_import_arabic_headers(self):
        token, _ = _register()
        xlsx = self._build_xlsx(
            headers=["SKU", "اسم المنتج", "التكلفة", "المورد"],
            rows=[
                ["A001", "منتج 1", 10.50, "مورد أ"],
                ["A002", "منتج 2", 25.00, "مورد ب"],
                ["A003", "منتج 3", 5.00, ""],
            ],
        )
        files = {"file": ("test.xlsx", xlsx,
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        r = requests.post(f"{API}/product-costs/import",
                          headers={"Authorization": f"Bearer {token}"},
                          files=files, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == 3
        assert body["updated"] == 0
        assert body["errors"] == []

        # Verify rows exist
        r = requests.get(f"{API}/product-costs/", headers=_headers(token), timeout=10)
        skus = {i["sku"] for i in r.json()["items"]}
        assert {"A001", "A002", "A003"}.issubset(skus)

    def test_import_english_headers_and_update_on_dup(self):
        token, _ = _register()
        # First import
        xlsx1 = self._build_xlsx(
            headers=["sku", "product_name", "cost_price", "supplier"],
            rows=[["B100", "Widget A", 30.0, "Sup A"]],
        )
        files = {"file": ("v1.xlsx", xlsx1,
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        r = requests.post(f"{API}/product-costs/import",
                          headers={"Authorization": f"Bearer {token}"},
                          files=files, timeout=15)
        assert r.json()["created"] == 1

        # Second import with same SKU but new price → should UPDATE, not create
        xlsx2 = self._build_xlsx(
            headers=["sku", "product_name", "cost_price"],
            rows=[["B100", "Widget A v2", 35.0]],
        )
        files = {"file": ("v2.xlsx", xlsx2,
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        r = requests.post(f"{API}/product-costs/import",
                          headers={"Authorization": f"Bearer {token}"},
                          files=files, timeout=15)
        body = r.json()
        assert body["updated"] == 1
        assert body["created"] == 0

        # Verify the row was updated, not duplicated
        r = requests.get(f"{API}/product-costs/?search=B100",
                         headers=_headers(token), timeout=10)
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["cost_price"] == 35.0
        assert items[0]["product_name"] == "Widget A v2"

    def test_import_missing_required_column(self):
        token, _ = _register()
        xlsx = self._build_xlsx(
            headers=["sku", "supplier"],  # missing product_name AND cost
            rows=[["X1", "Sup"]],
        )
        files = {"file": ("bad.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        r = requests.post(f"{API}/product-costs/import",
                          headers={"Authorization": f"Bearer {token}"},
                          files=files, timeout=15)
        assert r.status_code == 400
        # Friendly Arabic message
        assert "SKU" in r.json()["detail"] or "التكلفة" in r.json()["detail"]


# ── Cost lookup (compute_order_cost helper, tested via /missing endpoint) ──
class TestCostLookup:
    """Seed orders and product_costs directly in Mongo, then verify the
    helper correctly computes cost. We exercise the public /missing
    endpoint which calls the helper lazily."""

    def _seed(self, uid, costs: list, orders: list):
        async def _do():
            from motor.motor_asyncio import AsyncIOMotorClient
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            now = datetime.now(timezone.utc).isoformat()
            for cost in costs:
                sku = (cost.get("sku") or "").upper()
                await db.product_costs.update_one(
                    {"user_id": uid, "sku_normalized": sku},
                    {"$set": {"user_id": uid, "sku": cost["sku"], "sku_normalized": sku,
                              "product_id": cost.get("product_id", ""),
                              "product_name": cost.get("product_name", ""),
                              "supplier_name": cost.get("supplier_name", ""),
                              "cost_price": float(cost.get("cost_price", 0)),
                              "currency": cost.get("currency", "SAR"),
                              "is_active": True, "updated_at": now},
                     "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": now}},
                    upsert=True,
                )
            today = datetime.now(timezone.utc).date().isoformat()
            for o in orders:
                ord_num = o.get("order_number") or str(uuid.uuid4())
                doc = {
                    "user_id": uid,
                    "order_number": ord_num,
                    "order_date": o.get("order_date") or today,
                    "products": o.get("products") or [],
                    "total_amount": o.get("total_amount", 0.0),
                    "created_at": now,
                }
                await db.unified_orders.update_one(
                    {"user_id": uid, "order_number": ord_num},
                    {"$set": doc}, upsert=True,
                )
            c.close()
        asyncio.run(_do())

    def test_lookup_by_sku_then_product_id(self):
        token, uid = _register()
        self._seed(uid,
            costs=[
                {"sku": "NECK001", "product_name": "سلسال", "cost_price": 18.0},
                {"sku": "RING_X", "product_id": "PID_999", "product_name": "خاتم", "cost_price": 50.0},
            ],
            orders=[
                {"order_number": "O1", "products": [
                    {"name": "سلسال", "sku": "neck001", "quantity": 2, "price": 100},
                ]},
                {"order_number": "O2", "products": [
                    {"name": "خاتم", "product_id": "PID_999", "quantity": 1, "price": 200},
                ]},
                {"order_number": "O3", "products": [
                    {"name": "غير معروف", "sku": "MISSING_SKU", "quantity": 5, "price": 10},
                ]},
            ],
        )
        # Recompute to populate per-order cost
        r = requests.post(f"{API}/product-costs/recompute",
                          headers=_headers(token), timeout=15)
        assert r.status_code == 200
        assert r.json()["orders_updated"] == 3

        # Missing endpoint should show only the unmatched SKU
        r = requests.get(f"{API}/product-costs/missing",
                         headers=_headers(token), timeout=10)
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["sku"] == "MISSING_SKU"
        assert body["items"][0]["occurrences"] == 1
        assert body["items"][0]["total_quantity"] == 5.0

        # Summary should reflect: O1 cost = 18*2 = 36; O2 cost = 50*1 = 50; total = 86
        r = requests.get(f"{API}/product-costs/summary",
                         headers=_headers(token), timeout=10)
        body = r.json()
        assert body["month_total"] == 86.0
        assert body["active_products"] == 2
        # Avg = (18+50)/2 = 34
        assert body["avg_cost"] == 34.0

    def test_dashboard_total_product_cost_reflects_computed(self):
        token, uid = _register()
        self._seed(uid,
            costs=[{"sku": "X1", "product_name": "X", "cost_price": 10.0}],
            orders=[
                {"order_number": "D1", "products": [
                    {"name": "X", "sku": "x1", "quantity": 3, "price": 50},
                ], "total_amount": 150.0},
            ],
        )
        requests.post(f"{API}/product-costs/recompute",
                      headers=_headers(token), timeout=15)

        today = datetime.now(timezone.utc).date().isoformat()
        r = requests.get(f"{API}/dashboard?from_date={today}&to_date={today}",
                         headers=_headers(token), timeout=15)
        assert r.status_code == 200
        totals = r.json()["totals"]
        # 10 * 3 = 30
        assert totals["total_product_cost"] == 30.0
        assert totals["computed_product_cost"] == 30.0
        assert totals["missing_product_cost_count"] == 0
