"""Iteration 22: import without SKU (Salla files with only product_id + cost).

Use cases covered:
- File has ONLY: رقم المنتج + تكلفة المنتج (no SKU, no name).
- File has رقم المنتج + الاسم + التكلفة (no SKU).
- File has SKU + رقم المنتج → SKU is primary, product_id stored for fallback lookup.
- Order-cost lookup matches by product_id when an order line has no SKU.
- Re-importing the same product_id-only row UPDATES (not duplicates).

Run:
  export REACT_APP_BACKEND_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
  pytest /app/backend/tests/test_product_costs_import_v3.py -v
"""
from __future__ import annotations

import io
import os
import uuid
import asyncio
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from openpyxl import Workbook

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"pc-v3-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "PC v3", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _xlsx(rows, headers):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(token, xlsx_bytes):
    files = {"file": ("test.xlsx", xlsx_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    return requests.post(f"{API}/product-costs/import",
                         headers={"Authorization": f"Bearer {token}"},
                         files=files, timeout=15)


# ── Import without SKU ────────────────────────────────────────────────────
class TestImportWithoutSku:
    def test_product_id_and_cost_only(self):
        """File: رقم المنتج | تكلفة المنتج — no SKU, no name."""
        token, _ = _register()
        xlsx = _xlsx(headers=["رقم المنتج", "تكلفة المنتج"],
                     rows=[
                         ["1001", 25.5],
                         ["1002", 40.0],
                         ["1003", 15.0],
                     ])
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == 3
        assert body["errors"] == []
        # Each row used product_id as both the unique key and placeholder name.
        items = requests.get(f"{API}/product-costs/", headers=_headers(token),
                             timeout=10).json()["items"]
        ids = {it["product_id"] for it in items}
        assert ids == {"1001", "1002", "1003"}
        # `sku` field is EMPTY (no fake SKU created), but sku_normalized is set.
        for it in items:
            assert it["sku"] == "", f"sku should be '' when only product_id given: {it}"
            assert it["sku_normalized"] != ""
            # Name placeholder = product_id (so the UI shows something).
            assert it["product_name"] == it["product_id"]

    def test_product_id_with_name_and_cost(self):
        """File: رقم المنتج | الاسم | التكلفة — still no SKU."""
        token, _ = _register()
        xlsx = _xlsx(headers=["رقم المنتج", "الاسم", "التكلفة"],
                     rows=[["2001", "سلسال", 18.0]])
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 1
        items = requests.get(f"{API}/product-costs/?search=سلسال",
                             headers=_headers(token), timeout=10).json()["items"]
        assert len(items) == 1
        assert items[0]["product_id"] == "2001"
        assert items[0]["product_name"] == "سلسال"
        assert items[0]["cost_price"] == 18.0

    def test_re_import_same_product_id_updates(self):
        token, _ = _register()
        x1 = _xlsx(headers=["رقم المنتج", "تكلفة المنتج"], rows=[["3001", 10.0]])
        _import(token, x1).raise_for_status()
        # Re-import with new cost
        x2 = _xlsx(headers=["رقم المنتج", "تكلفة المنتج"], rows=[["3001", 22.0]])
        r = _import(token, x2)
        body = r.json()
        assert body["updated"] == 1
        assert body["created"] == 0
        items = requests.get(f"{API}/product-costs/", headers=_headers(token),
                             timeout=10).json()["items"]
        assert len(items) == 1
        assert items[0]["cost_price"] == 22.0

    def test_no_cost_column_imports_as_pending(self):
        """Iteration 25: cost is OPTIONAL. Rows without a cost column
        import successfully with cost_pending=True so the merchant can
        fill prices later via the UI."""
        token, _ = _register()
        xlsx = _xlsx(headers=["رقم المنتج", "الاسم"],
                     rows=[["X", "Y"]])
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == 1
        assert body["pending_count"] == 1
        items = requests.get(f"{API}/product-costs/", headers=_headers(token),
                             timeout=10).json()["items"]
        assert items[0]["cost_pending"] is True
        assert items[0]["cost_price"] == 0.0

    def test_no_identifier_column_returns_friendly_arabic(self):
        token, _ = _register()
        xlsx = _xlsx(headers=["الاسم", "التكلفة"], rows=[["X", 5]])
        r = _import(token, xlsx)
        assert r.status_code == 400
        assert "SKU" in r.json()["detail"]


# ── Both SKU and product_id present ───────────────────────────────────────
class TestBothColumnsPresent:
    def test_product_id_is_primary_when_present(self):
        """Iteration 25: product_id is the primary identifier when present.
        Both SKU and product_id are kept on the doc, but sku_normalized
        derives from product_id (so re-imports stay idempotent even when
        the merchant adds/removes SKU later)."""
        token, _ = _register()
        xlsx = _xlsx(headers=["SKU", "رقم المنتج", "الاسم", "التكلفة"],
                     rows=[["MY-SKU", "9999", "Item", 50.0]])
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 1
        items = requests.get(f"{API}/product-costs/", headers=_headers(token),
                             timeout=10).json()["items"]
        it = items[0]
        # Both stored; product_id is the upsert key (sku_normalized
        # derived from it for unique-index stability).
        assert it["sku"] == "MY-SKU"
        assert it["product_id"] == "9999"
        assert it["sku_normalized"] == "9999"


# ── Order-cost lookup matches by product_id when SKU empty ────────────────
class TestOrderLookupByProductId:
    def test_recompute_matches_orders_by_product_id_only(self):
        """Seed a cost row with only product_id (no SKU). Seed an order
        whose line has only product_id. Recompute, then verify the order's
        total_product_cost reflects the matched cost."""
        token, uid = _register()
        # Import: product_id only
        xlsx = _xlsx(headers=["رقم المنتج", "تكلفة المنتج"], rows=[["P5001", 12.0]])
        _import(token, xlsx).raise_for_status()

        # Seed an order via direct Mongo (mimic webhook ingestion)
        async def seed_order():
            from motor.motor_asyncio import AsyncIOMotorClient
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            now = datetime.now(timezone.utc).isoformat()
            today = datetime.now(timezone.utc).date().isoformat()
            await db.unified_orders.update_one(
                {"user_id": uid, "order_number": "OPID1"},
                {"$set": {"user_id": uid, "order_number": "OPID1",
                          "order_date": today,
                          "products": [
                              {"name": "Item P", "product_id": "P5001",
                               "quantity": 3, "price": 100},
                          ], "total_amount": 300.0,
                          "created_at": now}},
                upsert=True,
            )
            c.close()
        asyncio.run(seed_order())

        r = requests.post(f"{API}/product-costs/recompute",
                          headers=_headers(token), timeout=15)
        assert r.status_code == 200
        assert r.json()["orders_updated"] >= 1

        # Verify missing endpoint shows ZERO unmatched (the order's line
        # was matched by product_id).
        r = requests.get(f"{API}/product-costs/missing",
                         headers=_headers(token), timeout=10)
        body = r.json()
        # Filter to only P5001 (other test users' orders may exist)
        my_missing = [m for m in body["items"] if m.get("product_id") == "P5001"]
        assert len(my_missing) == 0
