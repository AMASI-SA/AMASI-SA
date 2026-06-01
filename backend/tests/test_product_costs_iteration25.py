"""Iteration 25 — Product ID primary key + Optional cost + Bulk auto-reprocess.

Acceptance covered:
1. Excel without SKU (only product_id) imports cleanly.
2. Re-importing the same product_id with NEW SKU does NOT create a duplicate
   (upsert key is product_id, not sku_normalized).
3. Rows with empty cost_price are imported with cost_pending=True
   (NOT treated as 0).
4. compute_order_cost skips cost_pending=True entries (treated as missing).
5. Bulk import triggers ONE targeted reprocess pass at the end.
6. /missing endpoint includes catalogue-pending entries (so merchant sees
   them even before any order arrives).
7. Manual create allows empty SKU when product_id is provided.
8. Manual create rejects when BOTH sku and product_id are empty.

Run:
  pytest /app/backend/tests/test_product_costs_iteration25.py -v
"""
from __future__ import annotations

import io
import os
import uuid
import asyncio
import requests
from dotenv import load_dotenv
from openpyxl import Workbook
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"i25-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "I25", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _wh_token(user_token: str) -> str:
    r = requests.get(f"{API}/webhook/settings",
                     headers={"Authorization": f"Bearer {user_token}"}, timeout=15)
    r.raise_for_status()
    return r.json()["token"]


def _post_make(wh: str, payload: dict):
    return requests.post(f"{API}/webhook/make/{wh}", json=payload, timeout=15)


def _xlsx(rows, headers):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(token, xlsx_bytes, update_existing=True):
    files = {"file": ("t.xlsx", xlsx_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    return requests.post(
        f"{API}/product-costs/import"
        f"?update_existing={'true' if update_existing else 'false'}",
        headers={"Authorization": f"Bearer {token}"},
        files=files, timeout=15,
    )


def _catalogue(token):
    r = requests.get(f"{API}/product-costs/?is_active=true",
                     headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    return r.json()["items"]


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
class TestSallaExcelWithoutSku:

    def test_imports_without_sku_using_product_id(self):
        """Salla product export with only `رقم المنتج` + `تكلفة المنتج`."""
        token, _ = _register()
        xlsx = _xlsx(
            headers=["رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[
                ["100001", "ساعة ذهبية", 75.0],
                ["100002", "خاتم فضي", 30.0],
            ],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == 2
        assert body["pending_count"] == 0
        items = _catalogue(token)
        by_pid = {it["product_id"]: it for it in items}
        assert by_pid["100001"]["cost_price"] == 75.0
        assert by_pid["100001"]["sku"] == ""  # NO SKU in file
        # sku_normalized derived from product_id to keep unique index stable.
        assert by_pid["100001"]["sku_normalized"] == "100001"

    def test_reimport_with_added_sku_does_not_duplicate(self):
        """Headline iter-25 fix: re-import same product_id with a new SKU
        must UPDATE the existing row, not create a duplicate."""
        token, _ = _register()
        # First import: product_id only.
        xlsx1 = _xlsx(
            headers=["رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[["555", "منتج", 20.0]],
        )
        r1 = _import(token, xlsx1)
        assert r1.json()["created"] == 1
        # Second import: SAME product_id, NEW SKU added.
        xlsx2 = _xlsx(
            headers=["SKU", "رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[["ABC-555", "555", "منتج", 22.0]],
        )
        r2 = _import(token, xlsx2)
        body = r2.json()
        assert body["created"] == 0  # no new row
        assert body["updated"] == 1  # existing row updated
        items = _catalogue(token)
        assert len(items) == 1  # NO duplicate
        assert items[0]["sku"] == "ABC-555"
        assert items[0]["product_id"] == "555"
        assert items[0]["cost_price"] == 22.0


# ──────────────────────────────────────────────────────────────────────────
class TestPendingCost:

    def test_empty_cost_row_imports_as_pending(self):
        """Salla export with some rows missing cost — they import as
        cost_pending=True, NOT as cost=0."""
        token, _ = _register()
        xlsx = _xlsx(
            headers=["رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[
                ["A", "بسعر", 50.0],
                ["B", "بدون سعر", None],
                ["C", "بدون سعر ٢", ""],
            ],
        )
        r = _import(token, xlsx)
        body = r.json()
        assert body["created"] == 3
        assert body["pending_count"] == 2
        items = _catalogue(token)
        by_pid = {it["product_id"]: it for it in items}
        assert by_pid["A"]["cost_pending"] is False
        assert by_pid["A"]["cost_price"] == 50.0
        assert by_pid["B"]["cost_pending"] is True
        assert by_pid["B"]["cost_price"] == 0.0  # stored as 0 but flagged pending
        assert by_pid["C"]["cost_pending"] is True

    def test_pending_cost_does_NOT_match_orders(self):
        """An order arrives for a product whose cost is pending → the
        order is marked incomplete_missing_cost (NOT matched as cost=0)."""
        token, uid = _register()
        # Import product with no cost.
        _import(token, _xlsx(
            headers=["رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[["777", "بدون سعر", None]],
        ))
        # Order for that product.
        wh = _wh_token(token)
        order_no = f"O-PEND-{uuid.uuid4().hex[:6]}"
        r = _post_make(wh, {
            "order_number": order_no,
            "total": 100,
            "products": [{"product_id": "777", "name": "بدون سعر",
                          "quantity": 2, "price": 50}],
        })
        assert r.status_code == 200
        doc = _get_order(uid, order_no)
        # Even though catalogue has product 777, cost is pending → treated as missing.
        assert doc["profit_status"] == "incomplete_missing_cost"
        assert doc["total_product_cost"] == 0.0  # partial sum: nothing matched
        assert len(doc["missing_product_cost_lines"]) == 1
        assert doc["missing_product_cost_lines"][0]["product_id"] == "777"

    def test_pending_cost_appears_in_missing_endpoint(self):
        """Catalogue rows with cost_pending=True appear in /missing
        even when NO order has been placed for them yet."""
        token, _ = _register()
        _import(token, _xlsx(
            headers=["رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[["8888", "في انتظار سعر", None]],
        ))
        r = requests.get(f"{API}/product-costs/missing",
                         headers={"Authorization": f"Bearer {token}"}, timeout=15)
        body = r.json()
        match = next((it for it in body["items"] if it["product_id"] == "8888"), None)
        assert match is not None, body
        assert match.get("pending_in_catalogue") is True
        assert match["name"] == "في انتظار سعر"

    def test_update_cost_clears_pending_flag(self):
        """Editing cost_price on a pending product → flag clears, status flips."""
        token, _ = _register()
        _import(token, _xlsx(
            headers=["رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[["555888", "في الانتظار", None]],
        ))
        items = _catalogue(token)
        target = next(it for it in items if it["product_id"] == "555888")
        assert target["cost_pending"] is True
        # Edit cost.
        u = requests.put(
            f"{API}/product-costs/{target['id']}",
            json={"cost_price": 33.5},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert u.status_code == 200, u.text
        assert u.json()["cost_pending"] is False
        assert u.json()["cost_price"] == 33.5


# ──────────────────────────────────────────────────────────────────────────
class TestBulkImportReprocess:

    def test_bulk_import_reprocesses_past_orders(self):
        """Critical iter-25 acceptance: after importing a CSV of costs,
        every past order containing those product_ids auto-flips."""
        token, uid = _register()
        wh = _wh_token(token)
        # 3 orders arrive BEFORE any cost is set.
        for i, pid in enumerate(["P001", "P002", "P003"]):
            _post_make(wh, {
                "order_number": f"O-BULK-{i}-{uuid.uuid4().hex[:4]}",
                "total": 100,
                "products": [{"product_id": pid, "name": f"P{i}",
                              "quantity": 1, "price": 100}],
            })
        # Now merchant uploads a costs file covering 2 of the 3.
        xlsx = _xlsx(
            headers=["رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[
                ["P001", "P0", 60.0],
                ["P002", "P1", 70.0],
            ],
        )
        r = _import(token, xlsx)
        body = r.json()
        assert body["created"] == 2
        # 2 past orders re-linked.
        assert body["reprocessed_orders"] >= 2

    def test_bulk_import_only_real_costs_trigger_reprocess(self):
        """Pending rows (no cost) must NOT count as reprocessable —
        they have no cost to apply."""
        token, uid = _register()
        wh = _wh_token(token)
        # 1 past order
        _post_make(wh, {
            "order_number": f"O-PENDR-{uuid.uuid4().hex[:6]}",
            "total": 50,
            "products": [{"product_id": "PENDX", "name": "x",
                          "quantity": 1, "price": 50}],
        })
        # Import 1 pending + 1 real.
        xlsx = _xlsx(
            headers=["رقم المنتج", "اسم المنتج", "تكلفة المنتج"],
            rows=[
                ["PENDX", "في الانتظار", None],
                ["OTHER", "آخر", 20.0],
            ],
        )
        r = _import(token, xlsx)
        body = r.json()
        # PENDX cost is still pending → no reprocess for it.
        assert body["pending_count"] == 1
        # But the OTHER row had no past order → reprocessed_orders may be 0.
        assert body["reprocessed_orders"] == 0


# ──────────────────────────────────────────────────────────────────────────
class TestManualCreateValidation:

    def test_create_with_only_product_id(self):
        """SKU is optional — product_id alone is valid."""
        token, _ = _register()
        r = requests.post(
            f"{API}/product-costs/",
            json={"product_id": "9999", "product_name": "P",
                  "cost_price": 10.0},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["product_id"] == "9999"
        assert body["sku"] == ""
        assert body["cost_pending"] is False

    def test_create_with_only_sku(self):
        """SKU alone is also valid (legacy non-Salla flow)."""
        token, _ = _register()
        r = requests.post(
            f"{API}/product-costs/",
            json={"sku": "LEGACY-A", "product_name": "P",
                  "cost_price": 10.0},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json()["sku"] == "LEGACY-A"

    def test_create_rejects_when_both_missing(self):
        """No identifier at all → 422 validation error."""
        token, _ = _register()
        r = requests.post(
            f"{API}/product-costs/",
            json={"product_name": "P", "cost_price": 10.0},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 422

    def test_create_with_no_cost_marks_pending(self):
        """Manual create without cost → cost_pending=True."""
        token, _ = _register()
        r = requests.post(
            f"{API}/product-costs/",
            json={"product_id": "AAA", "product_name": "P"},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cost_pending"] is True
        assert body["cost_price"] == 0.0

    def test_duplicate_product_id_returns_409(self):
        """Re-creating same product_id → 409 conflict."""
        token, _ = _register()
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json"}
        requests.post(f"{API}/product-costs/",
                      json={"product_id": "DUP", "product_name": "P",
                            "cost_price": 5.0},
                      headers=headers, timeout=15)
        r2 = requests.post(f"{API}/product-costs/",
                           json={"product_id": "DUP", "product_name": "P2",
                                 "cost_price": 7.0},
                           headers=headers, timeout=15)
        assert r2.status_code == 409
