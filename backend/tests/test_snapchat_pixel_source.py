"""Snapchat Pixel source priority test.

When `snapchat_daily_stats` has rows for the period (populated by the
bulk Snapchat API fetch), the dashboard MUST use those numbers for
orders + revenue rather than counting unified_orders. Falls back to
unified_orders only when no Snapchat Pixel data exists.
"""
import os
import uuid
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"u{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "T", "email": email, "password": "test12345"},
    )
    r.raise_for_status()
    return r.json()["access_token"], r.json()["id"]


async def _insert_snapchat_stats(user_id: str, date: str, purchases: int, revenue: float):
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    await db.snapchat_daily_stats.update_one(
        {"user_id": user_id, "date": date},
        {"$set": {
            "user_id": user_id, "date": date,
            "purchases": purchases, "revenue": revenue,
            "spend": 100.0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    cli.close()


def test_summary_uses_snapchat_pixel_when_present():
    """Seed snapchat_daily_stats and verify summary returns those numbers."""
    token, uid = _register()
    h = {"Authorization": f"Bearer {token}"}
    today = datetime.now(timezone.utc).date().isoformat()

    # Seed Snapchat-side data
    asyncio.run(_insert_snapchat_stats(uid, today, purchases=15, revenue=1200.50))

    r = requests.get(f"{API}/dashboard/snapchat-summary", headers=h).json()
    assert r["source"] == "snapchat_pixel"
    assert r["today"]["orders"] == 15
    assert r["today"]["revenue"] == 1200.50
    assert r["month"]["orders"] == 15
    assert r["month"]["revenue"] == 1200.50


def test_summary_falls_back_to_store_when_no_pixel_data():
    """No Snapchat stats → fallback to unified_orders counts."""
    token, _ = _register()
    h = {"Authorization": f"Bearer {token}"}
    today = datetime.now(timezone.utc).date().isoformat()

    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "STORE-1",
            "created_at": f"{today}T10:00:00+03:00",
            "total": 300, "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    r = requests.get(f"{API}/dashboard/snapchat-summary", headers=h).json()
    assert r["source"] == "store_orders"
    assert r["today"]["orders"] == 1
    assert r["today"]["revenue"] == 300.0


def test_pixel_data_takes_priority_over_store_data():
    """When both sources exist, Pixel wins (it's the attribution truth)."""
    token, uid = _register()
    h = {"Authorization": f"Bearer {token}"}
    today = datetime.now(timezone.utc).date().isoformat()

    # Store has 1 order (300 SAR)
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "STORE-1",
            "created_at": f"{today}T10:00:00+03:00",
            "total": 300, "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    # Snap Pixel says 5 orders, 999 SAR
    asyncio.run(_insert_snapchat_stats(uid, today, purchases=5, revenue=999.0))

    r = requests.get(f"{API}/dashboard/snapchat-summary", headers=h).json()
    assert r["source"] == "snapchat_pixel"
    assert r["today"]["orders"] == 5
    assert r["today"]["revenue"] == 999.0
