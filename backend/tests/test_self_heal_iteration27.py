"""Iteration 27 — Self-healing summary + manual recompute button.

Acceptance covered:
- /summary auto-heals today's orders that have total_product_cost=None
  by re-running attach_cost_to_order_doc, then reports the count in
  `stale_today_healed`.
- The same self-heal logic runs inside GET /api/dashboard for any
  order in the filtered range with null total_product_cost.
- /recompute endpoint remains intact (manual fallback for older envs).

Reproduces the bug: "تكلفة منتجات الطلبات حق تاريخ اليوم كامله لم يتم احتسبها"
(today's orders never had product cost computed).

Run:
  pytest /app/backend/tests/test_self_heal_iteration27.py -v
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
    email = f"i27-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "I27", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"], r.json()["id"]


def _hdr(t): return {"Authorization": f"Bearer {t}"}


def _wh(t):
    r = requests.get(f"{API}/webhook/settings", headers=_hdr(t), timeout=15)
    return r.json()["token"]


def _force_null_tpc(uid: str, order_number: str):
    """Simulate a stale order (e.g. one that was ingested before the
    cost was added) by setting total_product_cost to None directly."""
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        await db.unified_orders.update_one(
            {"user_id": uid, "order_number": order_number},
            {"$unset": {"total_product_cost": "",
                        "cost_items": "",
                        "missing_product_cost_lines": "",
                        "profit_status": ""}},
        )
        c.close()
    asyncio.run(_do())


def _get_order_field(uid: str, order_number: str, field: str):
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        doc = await db.unified_orders.find_one(
            {"user_id": uid, "order_number": order_number},
            {"_id": 0, field: 1},
        )
        c.close()
        return doc
    return asyncio.run(_do())


# ──────────────────────────────────────────────────────────────────────────
class TestSummarySelfHeal:

    def test_summary_heals_today_orders_with_null_tpc(self):
        """The headline iteration-27 fix:
        Today's order with NO cost field → /summary auto-heals it →
        today_total reflects the correct cost on first call."""
        token, uid = _register()
        wh = _wh(token)
        # Seed a cost first.
        requests.post(
            f"{API}/product-costs/",
            json={"product_id": "HEAL-1", "product_name": "منتج",
                  "cost_price": 25.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        # Order arrives for that product.
        order_no = f"O-HEAL-{uuid.uuid4().hex[:6]}"
        today = datetime.now(timezone.utc).date().isoformat()
        requests.post(
            f"{API}/webhook/make/{wh}",
            json={"order_number": order_no, "total": 100, "total_amount": 100,
                  "order_date": today,
                  "products": [{"product_id": "HEAL-1", "name": "منتج",
                                "quantity": 2, "price": 50}]},
            timeout=15,
        )
        # Now corrupt the order — simulate stale state (no cost fields).
        _force_null_tpc(uid, order_no)
        before = _get_order_field(uid, order_no, "total_product_cost")
        assert before.get("total_product_cost") is None
        # Call /summary → must heal automatically.
        r = requests.get(f"{API}/product-costs/summary", headers=_hdr(token),
                         timeout=15)
        body = r.json()
        assert body["stale_today_healed"] >= 1
        # today_total now reflects the real cost (2 * 25 = 50)
        assert body["today_total"] == 50.0
        # And the order doc itself was patched in-place.
        after = _get_order_field(uid, order_no, "total_product_cost")
        assert after["total_product_cost"] == 50.0

    def test_summary_no_heal_when_data_is_clean(self):
        """Healthy data → stale_today_healed = 0 (no extra work)."""
        token, uid = _register()
        wh = _wh(token)
        requests.post(
            f"{API}/product-costs/",
            json={"product_id": "CLEAN-1", "product_name": "نظيف",
                  "cost_price": 10.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        order_no = f"O-CLEAN-{uuid.uuid4().hex[:6]}"
        today = datetime.now(timezone.utc).date().isoformat()
        requests.post(
            f"{API}/webhook/make/{wh}",
            json={"order_number": order_no, "total": 50,
                  "order_date": today,
                  "products": [{"product_id": "CLEAN-1", "name": "نظيف",
                                "quantity": 1, "price": 50}]},
            timeout=15,
        )
        # Order is already enriched by webhook.
        r = requests.get(f"{API}/product-costs/summary", headers=_hdr(token),
                         timeout=15)
        body = r.json()
        assert body["stale_today_healed"] == 0
        assert body["today_total"] == 10.0


# ──────────────────────────────────────────────────────────────────────────
class TestDashboardSelfHeal:

    def test_dashboard_heals_orders_with_null_tpc(self):
        """Same self-heal applied in /api/dashboard so the merchant's
        primary view always reflects up-to-date cost data."""
        token, uid = _register()
        wh = _wh(token)
        requests.post(
            f"{API}/product-costs/",
            json={"product_id": "DH-1", "product_name": "للوحة",
                  "cost_price": 15.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        order_no = f"O-DH-{uuid.uuid4().hex[:6]}"
        today = datetime.now(timezone.utc).date().isoformat()
        requests.post(
            f"{API}/webhook/make/{wh}",
            json={"order_number": order_no, "total": 80,
                  "order_date": today,
                  "products": [{"product_id": "DH-1", "name": "للوحة",
                                "quantity": 2, "price": 40}]},
            timeout=15,
        )
        _force_null_tpc(uid, order_no)
        # Now fetch dashboard — it should heal as a side-effect.
        r = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=20)
        assert r.status_code == 200, r.text
        # After dashboard call, order should be enriched in DB.
        after = _get_order_field(uid, order_no, "total_product_cost")
        assert after["total_product_cost"] == 30.0  # 15 * 2


# ──────────────────────────────────────────────────────────────────────────
class TestManualRecompute:

    def test_recompute_endpoint_still_works(self):
        """The pre-iteration-27 manual /recompute path is unchanged."""
        token, uid = _register()
        r = requests.post(f"{API}/product-costs/recompute?days=2",
                          headers=_hdr(token), timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "orders_updated" in body
        assert "window_days" in body
