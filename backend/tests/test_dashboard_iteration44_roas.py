"""Iter-44 — Cross-platform ROAS + Average Cost Per Order KPIs.

Two new dashboard cards land in the `marketing` group:
  - overall_roas       = total_sales / total_ads_cost   (× ratio, null if no spend)
  - avg_cost_per_order = total_ads_cost / total_orders  (SAR, null if 0 orders)

Both are exposed under `dashboard.totals.*` so the existing config-driven
KPI grid can pick them up without any hard-coded refs.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register() -> tuple[str, str]:
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "iter44 Tester",
              "email": f"iter44-{uuid.uuid4().hex[:10]}@example.com",
              "password": "Test1234!"},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _cleanup(uid: str) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _do():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            db = client[os.environ["DB_NAME"]]
            for coll in (
                "users", "unified_orders", "daily_costs",
                "tiktok_ads_daily", "meta_ads_daily",
                "settings", "analyses",
            ):
                await db[coll].delete_many(
                    {"$or": [{"user_id": uid}, {"id": uid}, {"_id": uid}]},
                )
        finally:
            client.close()

    asyncio.run(_do())


async def _seed_orders_and_ads(uid: str, sales_total: float,
                                order_count: int, ads_total: float):
    """Create N orders summing to `sales_total` + a single daily_costs row
    with `ads_total` Snapchat spend dated today."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = client[os.environ["DB_NAME"]]
        today_iso = datetime.now(timezone.utc).date().isoformat()
        per = round(sales_total / max(order_count, 1), 2)
        docs = []
        for i in range(order_count):
            docs.append({
                "user_id": uid,
                "order_id": f"O-{uid[:6]}-{i}",
                "order_number": f"TEST-{i}",
                "order_date": today_iso,
                "total_amount": per,
                "status": "completed",
                "data_source": "make",
                "shipping_company": "iMile",
                "payment_method": "تحويل بنكي",
                "total_shipping_cost": 0,
                "payment_fees": 0,
                "vat_amount": 0,
            })
        if docs:
            await db.unified_orders.insert_many(docs)
        if ads_total > 0:
            await db.daily_costs.insert_one({
                "user_id": uid,
                "date": today_iso,
                "snapchat_ads": ads_total,
                "snapchat_ads_2": 0,
                "instagram_ads": 0,
                "google_ads": 0,
                "tiktok_ads": 0,
                "product_costs": 0,
            })
    finally:
        client.close()


def _get_dashboard(token: str) -> dict:
    r = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=15)
    r.raise_for_status()
    return r.json()


# ── 1. Happy path — both KPIs computed correctly ────────────────────────
def test_overall_roas_and_cpa_when_ads_present():
    token, uid = _register()
    try:
        # 10 orders × 100 SAR = 1000 SAR sales, 200 SAR ads
        # ROAS = 1000/200 = 5.00; CPA = 200/10 = 20.00
        asyncio.run(_seed_orders_and_ads(uid, sales_total=1000.0, order_count=10, ads_total=200.0))

        body = _get_dashboard(token)
        totals = body["totals"]
        assert totals["total_orders"] == 10
        assert totals["total_sales"] == pytest.approx(1000.0, abs=0.5)
        assert totals["total_ads_cost"] == pytest.approx(200.0, abs=0.5)
        # ROAS = 1000 / 200 = 5.00
        assert totals["overall_roas"] == pytest.approx(5.0, abs=0.05)
        # Avg cost / order = 200 / 10 = 20.00
        assert totals["avg_cost_per_order"] == pytest.approx(20.0, abs=0.05)
    finally:
        _cleanup(uid)


# ── 2. Zero ad spend → both KPIs are null (UI shows "—") ───────────────
def test_kpis_are_null_when_no_ad_spend():
    token, uid = _register()
    try:
        asyncio.run(_seed_orders_and_ads(uid, sales_total=500.0, order_count=5, ads_total=0.0))

        body = _get_dashboard(token)
        totals = body["totals"]
        assert totals["total_ads_cost"] == 0
        # Both KPIs must be None (JSON null) so UI shows "—" not "0" or "Infinity"
        assert totals["overall_roas"] is None
        assert totals["avg_cost_per_order"] is None
    finally:
        _cleanup(uid)


# ── 3. Zero orders → CPA null even if ad spend exists ──────────────────
def test_cpa_null_when_no_orders():
    token, uid = _register()
    try:
        # Only ads, no orders.
        asyncio.run(_seed_orders_and_ads(uid, sales_total=0.0, order_count=0, ads_total=150.0))

        body = _get_dashboard(token)
        totals = body["totals"]
        assert totals["total_orders"] == 0
        assert totals["total_ads_cost"] == pytest.approx(150.0, abs=0.5)
        # ROAS = 0/150 = 0.00 — present and FINITE (not None).
        assert totals["overall_roas"] == pytest.approx(0.0, abs=0.01)
        # CPA needs orders; null when total_orders == 0.
        assert totals["avg_cost_per_order"] is None
    finally:
        _cleanup(uid)


# ── 4. Mixed scenario — verifies the ratio uses cross-platform ads ─────
def test_roas_uses_all_ad_platforms_combined():
    """Add ads across multiple platforms and confirm ROAS aggregates them."""
    token, uid = _register()
    try:
        asyncio.run(_seed_orders_and_ads(uid, sales_total=2000.0, order_count=4, ads_total=0.0))
        # Seed multi-platform ad spend manually.
        async def _seed_multi():
            from motor.motor_asyncio import AsyncIOMotorClient
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            try:
                db = client[os.environ["DB_NAME"]]
                today = datetime.now(timezone.utc).date().isoformat()
                await db.daily_costs.insert_one({
                    "user_id": uid, "date": today,
                    "snapchat_ads": 100.0,
                    "instagram_ads": 50.0,
                    "google_ads": 50.0,
                    "tiktok_ads": 0,
                    "product_costs": 0,
                })
            finally:
                client.close()
        asyncio.run(_seed_multi())

        body = _get_dashboard(token)
        totals = body["totals"]
        # Total = 100+50+50 = 200 SAR
        assert totals["total_ads_cost"] == pytest.approx(200.0, abs=0.5)
        # ROAS = 2000 / 200 = 10
        assert totals["overall_roas"] == pytest.approx(10.0, abs=0.05)
        # CPA = 200 / 4 = 50
        assert totals["avg_cost_per_order"] == pytest.approx(50.0, abs=0.05)
    finally:
        _cleanup(uid)
