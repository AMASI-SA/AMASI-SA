"""Iteration 31 — data_source precedence (Make wins over Excel).

Reproduces the real merchant bug:
"عند رفع ملف اكسل بالطلبات الجديده يتوقف النظام عن احتساب طلبات make
بكل مره ولازم اكلمك عشان تضبطه من جديد".

Root cause: when the same order_number arrived from BOTH Make.com
(via webhook) AND a later Excel re-import, `upsert_order` used to set
`data_source = source` (the LAST writer). Excel re-imports would
demote every Make-originating order to data_source="excel" → the
Dashboard's `orders_make_count` would silently drop to ~0 after every
Excel upload, breaking source-based KPIs (e.g. ad attribution from
Make payloads).

Fix: Make is the AUTHORITATIVE source (richer — has products[],
webhook-fresh). Once any Make write exists in an order's history,
`data_source` stays "make" forever. `data_sources[]` keeps full audit.
Plus: Dashboard self-heals legacy orders whose data_source was already
demoted by re-promoting them in-place.

Run:
  pytest /app/backend/tests/test_data_source_precedence_iteration31.py -v
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
    email = f"i31-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "I31", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"], r.json()["id"]


def _hdr(t): return {"Authorization": f"Bearer {t}"}


def _wh(t):
    r = requests.get(f"{API}/webhook/settings", headers=_hdr(t), timeout=15)
    return r.json()["token"]


def _post_make(wh, payload):
    return requests.post(f"{API}/webhook/make/{wh}", json=payload, timeout=15)


def _get(uid: str, order_number: str) -> dict | None:
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        doc = await db.unified_orders.find_one(
            {"user_id": uid, "order_number": order_number}, {"_id": 0},
        )
        c.close()
        return doc
    return asyncio.run(_do())


def _direct_upsert_excel(uid: str, order_number: str, payload: dict):
    """Simulate Excel-source upsert by calling orders_db.upsert_order
    directly (the same path the Excel /analyze handler uses)."""
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        from orders_db import upsert_order
        res = await upsert_order(db, uid, order_number, payload, source="excel")
        c.close()
        return res
    return asyncio.run(_do())


def _direct_force_data_source(uid: str, order_number: str, ds: str):
    """Helper to simulate a pre-iter-31 corrupted state where
    data_source was already demoted to 'excel' on an order that has
    a Make write in its data_sources[] history."""
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        await db.unified_orders.update_one(
            {"user_id": uid, "order_number": order_number},
            {"$set": {"data_source": ds}},
        )
        c.close()
    asyncio.run(_do())


# ──────────────────────────────────────────────────────────────────────────
class TestPrecedenceOnNewWrites:

    def test_make_then_excel_keeps_make_as_data_source(self):
        """The HEADLINE bug: Make-first order then Excel re-import
        must NOT demote data_source to 'excel'."""
        token, uid = _register()
        wh = _wh(token)
        order_no = f"O-MK-EX-{uuid.uuid4().hex[:6]}"
        # 1) Order from Make first.
        _post_make(wh, {
            "order_number": order_no, "total": 100,
            "order_date": "2026-06-01",
            "products": [{"product_id": "X", "name": "x",
                          "quantity": 1, "price": 100}],
        })
        doc1 = _get(uid, order_no)
        assert doc1["data_source"] == "make"
        # 2) Excel re-imports the same order.
        _direct_upsert_excel(uid, order_no, {
            "order_date": "2026-06-01",
            "total_amount": 100,
            "payment_method": "Mada",
        })
        doc2 = _get(uid, order_no)
        # Iteration 31: stays 'make'.
        assert doc2["data_source"] == "make", \
            f"Expected make, got {doc2['data_source']!r}"
        # history records both
        sources_in_history = {(s or {}).get("source") for s in
                              (doc2.get("data_sources") or [])}
        assert "make" in sources_in_history
        assert "excel" in sources_in_history

    def test_excel_first_then_make_promotes(self):
        """Excel-first order whose later Make write should PROMOTE to 'make'."""
        token, uid = _register()
        wh = _wh(token)
        order_no = f"O-EX-MK-{uuid.uuid4().hex[:6]}"
        # 1) Excel first.
        _direct_upsert_excel(uid, order_no, {
            "order_date": "2026-06-01",
            "total_amount": 50,
            "payment_method": "Mada",
        })
        doc1 = _get(uid, order_no)
        assert doc1["data_source"] == "excel"
        # 2) Make webhook arrives later.
        _post_make(wh, {
            "order_number": order_no, "total": 50,
            "order_date": "2026-06-01",
            "products": [{"product_id": "Y", "name": "y",
                          "quantity": 1, "price": 50}],
        })
        doc2 = _get(uid, order_no)
        assert doc2["data_source"] == "make"

    def test_excel_only_stays_excel(self):
        """No Make ever → stays 'excel'."""
        token, uid = _register()
        order_no = f"O-EX-EX-{uuid.uuid4().hex[:6]}"
        _direct_upsert_excel(uid, order_no, {
            "order_date": "2026-06-01",
            "total_amount": 20,
        })
        _direct_upsert_excel(uid, order_no, {
            "order_date": "2026-06-01",
            "total_amount": 25,  # update
        })
        doc = _get(uid, order_no)
        assert doc["data_source"] == "excel"


# ──────────────────────────────────────────────────────────────────────────
class TestDashboardSelfHeal:

    def test_dashboard_promotes_legacy_excel_with_make_history(self):
        """A pre-iter-31 corrupted order (data_source='excel' but
        data_sources[] contains a Make write) must auto-promote when
        the merchant opens the Dashboard."""
        token, uid = _register()
        wh = _wh(token)
        order_no = f"O-LEG-{uuid.uuid4().hex[:6]}"
        # Real Make write.
        _post_make(wh, {
            "order_number": order_no, "total": 75,
            "order_date": datetime.now(timezone.utc).date().isoformat(),
            "products": [{"product_id": "Z", "name": "z",
                          "quantity": 1, "price": 75}],
        })
        # Manually corrupt: simulate the legacy bug.
        _direct_force_data_source(uid, order_no, "excel")
        bad = _get(uid, order_no)
        assert bad["data_source"] == "excel"  # corrupted state
        # Hit /dashboard — self-heal must promote it back.
        r = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=20)
        assert r.status_code == 200
        fixed = _get(uid, order_no)
        assert fixed["data_source"] == "make", \
            f"Self-heal failed, got {fixed['data_source']!r}"

    def test_dashboard_does_not_promote_pure_excel(self):
        """An Excel-only order (no Make in history) must stay 'excel'
        — self-heal must NOT over-eagerly promote."""
        token, uid = _register()
        order_no = f"O-PURE-EX-{uuid.uuid4().hex[:6]}"
        _direct_upsert_excel(uid, order_no, {
            "order_date": datetime.now(timezone.utc).date().isoformat(),
            "total_amount": 10,
        })
        requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=20)
        doc = _get(uid, order_no)
        assert doc["data_source"] == "excel"


# ──────────────────────────────────────────────────────────────────────────
class TestDashboardCounters:

    def test_make_count_does_not_drop_after_excel_reimport(self):
        """End-to-end acceptance: orders_make_count stays correct even
        after a bulk Excel re-import of Make-originated orders."""
        token, uid = _register()
        wh = _wh(token)
        # Seed 3 Make orders today.
        today = datetime.now(timezone.utc).date().isoformat()
        order_ids = []
        for i in range(3):
            no = f"O-CNT-{uuid.uuid4().hex[:6]}"
            order_ids.append(no)
            _post_make(wh, {"order_number": no, "total": 50,
                            "order_date": today,
                            "products": [{"product_id": str(i), "name": "x",
                                          "quantity": 1, "price": 50}]})
        # Dashboard sees 3 make orders.
        r1 = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=20)
        t1 = (r1.json().get("totals") or {})
        make_before = t1.get("make_orders_count") or t1.get("orders_make_count") or 0
        assert make_before >= 3
        # Now Excel re-imports the same 3 orders.
        for no in order_ids:
            _direct_upsert_excel(uid, no, {
                "order_date": today, "total_amount": 50,
                "payment_method": "Mada",
            })
        # Dashboard should STILL count them as make.
        r2 = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=20)
        t2 = (r2.json().get("totals") or {})
        make_after = t2.get("make_orders_count") or t2.get("orders_make_count") or 0
        assert make_after == make_before, \
            f"Make counter dropped after Excel re-import: {make_before} → {make_after}"
