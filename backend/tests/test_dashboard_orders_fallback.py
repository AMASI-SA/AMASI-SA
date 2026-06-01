"""Regression tests for iteration-21 bug:
"بطاقات Snap/TikTok/Meta لا تظهر الطلبات وباقي البيانات (orders/revenue)
رغم ظهور الصرف بشكل صحيح".

Root cause: Pixel-reported `purchases` and `revenue` can legitimately be 0
(no Pixel setup, attribution gap, or partial-day reporting), but the
existing summary endpoints did not fall back to `unified_orders` UTM
attribution in that case. As a result, the card showed `orders=0` and
`revenue=0` even when the merchant clearly had matching orders in their
Salla store with `utm_source` set to the platform.

Fix: when Pixel returns `orders=0 AND revenue=0` for a window AND spend>0,
backfill from `unified_orders` where `utm_source` matches the platform's
aliases (snap/snapchat, tiktok, facebook/instagram/fb/ig/meta).

Run:
  export REACT_APP_BACKEND_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
  pytest /app/backend/tests/test_dashboard_orders_fallback.py -v
"""
from __future__ import annotations

import os
import uuid
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"d21-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "Dash 21", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _riyadh_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Riyadh")).date()
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


async def _seed_orders_with_utm(uid: str, orders: list):
    """orders = [{order_date, utm_source, total_amount, ...}]"""
    from motor.motor_asyncio import AsyncIOMotorClient
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    now = datetime.now(timezone.utc).isoformat()
    for o in orders:
        ord_num = o.get("order_number") or str(uuid.uuid4())
        doc = {
            "user_id": uid,
            "order_number": ord_num,
            "order_date": o["order_date"],
            "utm_source": o["utm_source"],
            "total_amount": float(o.get("total_amount", 100.0)),
            "data_source": "make",
            "created_at": now,
        }
        await db.unified_orders.update_one(
            {"user_id": uid, "order_number": ord_num},
            {"$set": doc}, upsert=True,
        )
    c.close()


async def _seed_zero_pixel_spend(uid: str, collection: str, date_str: str, spend: float):
    """Seed `snapchat_daily_stats` / `tiktok_ads_daily` / `meta_ads_daily`
    with spend > 0 but purchases=0 (simulates Pixel returning no
    conversions despite the campaign spending)."""
    from motor.motor_asyncio import AsyncIOMotorClient
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    now = datetime.now(timezone.utc).isoformat()
    if collection == "snapchat_daily_stats":
        await db[collection].update_one(
            {"user_id": uid, "date": date_str},
            {"$set": {"user_id": uid, "date": date_str, "spend": spend,
                      "purchases": 0, "revenue": 0.0, "updated_at": now}},
            upsert=True,
        )
        # The card sums spend from daily_costs.snapchat_ads — write there too
        # so spend > 0 reaches the card.
        await db.daily_costs.update_one(
            {"user_id": uid, "date": date_str},
            {"$set": {"snapchat_ads": spend, "updated_at": now}},
            upsert=True,
        )
    elif collection == "tiktok_ads_daily":
        await db[collection].update_one(
            {"user_id": uid, "date": date_str, "campaign_id": "_test"},
            {"$set": {"user_id": uid, "date": date_str,
                      "campaign_id": "_test", "campaign_name": "T",
                      "platform": "tiktok",
                      "spend": spend, "purchases": 0, "revenue": 0.0,
                      "updated_at": now}},
            upsert=True,
        )
    elif collection == "meta_ads_daily":
        await db[collection].update_one(
            {"user_id": uid, "date": date_str, "campaign_id": "_meta"},
            {"$set": {"user_id": uid, "date": date_str,
                      "campaign_id": "_meta", "campaign_name": "M",
                      "spend": spend, "purchases": 0, "purchase_value": 0.0,
                      "impressions": 0, "clicks": 0,
                      "updated_at": now}},
            upsert=True,
        )
    c.close()


# ── Tests ────────────────────────────────────────────────────────────────────
class TestSnapchatFallback:
    def test_zero_pixel_purchases_falls_back_to_unified_orders(self):
        token, uid = _register()
        today = _riyadh_today().isoformat()
        # Seed: Snap spend 400 SAR today, Pixel reported 0 purchases.
        asyncio.run(_seed_zero_pixel_spend(uid, "snapchat_daily_stats", today, 400.0))
        # Seed: 3 store orders today with utm_source = "snapchat"
        asyncio.run(_seed_orders_with_utm(uid, [
            {"order_date": today, "utm_source": "snapchat", "total_amount": 200.0},
            {"order_date": today, "utm_source": "Snapchat", "total_amount": 150.0},
            {"order_date": today, "utm_source": "snap_ads", "total_amount": 250.0},
            # Unrelated order — must NOT count
            {"order_date": today, "utm_source": "google", "total_amount": 999.0},
        ]))
        r = requests.get(f"{API}/dashboard/snapchat-summary",
                         headers=_headers(token), timeout=15)
        assert r.status_code == 200
        body = r.json()
        # spend stays at 400
        assert body["today"]["spend"] == 400.0
        # orders should now reflect the 3 snap-attributed store orders
        assert body["today"]["orders"] == 3, (
            f"Bug not fixed: today.orders = {body['today']['orders']}, expected 3"
        )
        # revenue = 200 + 150 + 250 = 600
        assert body["today"]["revenue"] == 600.0
        # ROAS = 600/400 = 1.5
        assert body["today"]["roas"] == 1.5

    def test_pixel_data_takes_precedence_when_present(self):
        """When Pixel HAS reported purchases, the fallback must NOT
        override it — Pixel is the source of truth for ad-attributed
        orders."""
        token, uid = _register()
        today = _riyadh_today().isoformat()
        # Seed: Pixel reports 5 purchases @ 1500 SAR revenue
        from motor.motor_asyncio import AsyncIOMotorClient
        async def _s():
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            now = datetime.now(timezone.utc).isoformat()
            await db.snapchat_daily_stats.update_one(
                {"user_id": uid, "date": today},
                {"$set": {"user_id": uid, "date": today, "spend": 400.0,
                          "purchases": 5, "revenue": 1500.0, "updated_at": now}},
                upsert=True,
            )
            await db.daily_costs.update_one(
                {"user_id": uid, "date": today},
                {"$set": {"snapchat_ads": 400.0, "updated_at": now}},
                upsert=True,
            )
            c.close()
        asyncio.run(_s())
        # Also seed 10 store orders — they must NOT inflate the card.
        asyncio.run(_seed_orders_with_utm(uid, [
            {"order_date": today, "utm_source": "snapchat",
             "total_amount": 100.0, "order_number": f"O{i}"}
            for i in range(10)
        ]))
        r = requests.get(f"{API}/dashboard/snapchat-summary",
                         headers=_headers(token), timeout=15)
        body = r.json()
        # Pixel numbers preserved
        assert body["today"]["orders"] == 5
        assert body["today"]["revenue"] == 1500.0


class TestTikTokFallback:
    def test_zero_webhook_purchases_falls_back(self):
        token, uid = _register()
        today = _riyadh_today().isoformat()
        # Seed: TikTok webhook reports spend 500 but purchases=0
        asyncio.run(_seed_zero_pixel_spend(uid, "tiktok_ads_daily", today, 500.0))
        # 2 store orders with utm_source = "tiktok"
        asyncio.run(_seed_orders_with_utm(uid, [
            {"order_date": today, "utm_source": "tiktok", "total_amount": 300.0},
            {"order_date": today, "utm_source": "TIK_TOK", "total_amount": 400.0},
        ]))
        r = requests.get(f"{API}/dashboard/tiktok-summary",
                         headers=_headers(token), timeout=15)
        body = r.json()
        assert body["today"]["spend"] == 500.0
        assert body["today"]["orders"] == 2
        assert body["today"]["revenue"] == 700.0


class TestMetaFallback:
    def test_zero_meta_purchases_falls_back_facebook_or_instagram(self):
        token, uid = _register()
        today = _riyadh_today().isoformat()
        asyncio.run(_seed_zero_pixel_spend(uid, "meta_ads_daily", today, 600.0))
        # Mix of Facebook + Instagram utm sources
        asyncio.run(_seed_orders_with_utm(uid, [
            {"order_date": today, "utm_source": "facebook", "total_amount": 250.0},
            {"order_date": today, "utm_source": "instagram", "total_amount": 350.0},
            {"order_date": today, "utm_source": "fb_paid", "total_amount": 100.0},
        ]))
        r = requests.get(f"{API}/dashboard/meta-summary",
                         headers=_headers(token), timeout=15)
        body = r.json()
        assert body["today"]["spend"] == 600.0
        assert body["today"]["orders"] == 3
        assert body["today"]["revenue"] == 700.0


class TestNoFalsePositives:
    def test_no_attributed_orders_returns_zero(self):
        """If there are NO matching utm_source orders, the card should
        still show 0 (not throw an error or invent data)."""
        token, uid = _register()
        today = _riyadh_today().isoformat()
        asyncio.run(_seed_zero_pixel_spend(uid, "tiktok_ads_daily", today, 500.0))
        # Only google orders — should NOT be attributed to TikTok
        asyncio.run(_seed_orders_with_utm(uid, [
            {"order_date": today, "utm_source": "google", "total_amount": 999.0},
        ]))
        r = requests.get(f"{API}/dashboard/tiktok-summary",
                         headers=_headers(token), timeout=15)
        body = r.json()
        assert body["today"]["spend"] == 500.0
        assert body["today"]["orders"] == 0
        assert body["today"]["revenue"] == 0.0
