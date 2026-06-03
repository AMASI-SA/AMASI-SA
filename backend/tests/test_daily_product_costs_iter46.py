"""Iter-46 — Daily aggregate product cost entry (temporary).

Confirms that:
  1. Upserting `daily_costs` with a `product_costs` value works.
  2. The dashboard's product-cost calculation picks it up via the
     `product_cost_effective = max(computed, manual_daily)` rule.
  3. Updating the same date REPLACES the value (not adds).
  4. Setting product_costs=0 effectively removes it from the calc.

No new endpoints are introduced — this exercises the EXISTING
`POST /api/daily-costs` route, but verifies the path the new
`DailyProductCostModal` UI uses end-to-end.
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


def _hdr(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def _register() -> tuple[str, str]:
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "iter46 Tester",
              "email": f"iter46-{uuid.uuid4().hex[:10]}@example.com",
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
            for coll in ("users", "unified_orders", "daily_costs", "settings"):
                await db[coll].delete_many(
                    {"$or": [{"user_id": uid}, {"id": uid}, {"_id": uid}]},
                )
        finally:
            client.close()

    asyncio.run(_do())


def _post_daily(token: str, **fields) -> dict:
    r = requests.post(f"{API}/daily-costs", headers=_hdr(token), json=fields, timeout=15)
    r.raise_for_status()
    return r.json()


def _dashboard(token: str) -> dict:
    r = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=15)
    r.raise_for_status()
    return r.json()


# ── 1. Daily aggregate entry shows up in the dashboard product cost ────
def test_daily_product_cost_flows_into_dashboard():
    token, uid = _register()
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        # Save 1,250 SAR as aggregate cost for today (no per-SKU data yet).
        body = _post_daily(token,
                           date=today,
                           snapchat_ads=0, tiktok_ads=0, instagram_ads=0,
                           snapchat_ads_2=0, google_ads=0,
                           product_costs=1250.50,
                           notes="دفعة موردين تجربة")
        assert body["product_costs"] == pytest.approx(1250.50, abs=0.01)
        assert body["notes"] == "دفعة موردين تجربة"

        # Dashboard exposes the manual aggregate.
        ds = _dashboard(token)
        t = ds["totals"]
        # `manual_product_cost` mirrors daily_costs.product_costs sum.
        assert t["manual_product_cost"] == pytest.approx(1250.50, abs=0.5)
        # No per-SKU records → computed == 0, so effective uses the manual.
        assert t["computed_product_cost"] == pytest.approx(0.0, abs=0.01)
        # Dashboard prefers max(computed, manual) → manual wins here.
        assert t["total_product_cost"] == pytest.approx(1250.50, abs=0.5)
    finally:
        _cleanup(uid)


# ── 2. Upsert replaces the value for the same date (not adds) ──────────
def test_daily_product_cost_upsert_replaces_for_same_date():
    token, uid = _register()
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        _post_daily(token, date=today,
                    snapchat_ads=0, tiktok_ads=0, instagram_ads=0,
                    snapchat_ads_2=0, google_ads=0,
                    product_costs=500.0, notes="أولاً")
        # Now overwrite same date with a different value.
        _post_daily(token, date=today,
                    snapchat_ads=0, tiktok_ads=0, instagram_ads=0,
                    snapchat_ads_2=0, google_ads=0,
                    product_costs=2000.0, notes="بعد التعديل")

        # Listing returns ONE row, value=2000.
        r = requests.get(f"{API}/daily-costs", headers=_hdr(token), timeout=15)
        r.raise_for_status()
        items = [it for it in r.json() if it["date"] == today]
        assert len(items) == 1
        assert items[0]["product_costs"] == pytest.approx(2000.0, abs=0.5)
        assert items[0]["notes"] == "بعد التعديل"
    finally:
        _cleanup(uid)


# ── 3. Setting product_costs=0 removes the day from the manual total ───
def test_zeroing_product_cost_removes_it_from_total():
    token, uid = _register()
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        _post_daily(token, date=today,
                    snapchat_ads=0, tiktok_ads=0, instagram_ads=0,
                    snapchat_ads_2=0, google_ads=0,
                    product_costs=900.0, notes="")
        # Confirm it's there.
        assert _dashboard(token)["totals"]["manual_product_cost"] == pytest.approx(900.0, abs=0.5)

        # Zero it out (frontend's "delete-but-keep-ad-spend" path).
        _post_daily(token, date=today,
                    snapchat_ads=0, tiktok_ads=0, instagram_ads=0,
                    snapchat_ads_2=0, google_ads=0,
                    product_costs=0, notes="")
        assert _dashboard(token)["totals"]["manual_product_cost"] == pytest.approx(0.0, abs=0.01)
    finally:
        _cleanup(uid)


# ── 4. Multiple days sum correctly in the dashboard total ──────────────
def test_multiple_days_sum_correctly():
    token, uid = _register()
    try:
        from datetime import timedelta
        d1 = datetime.now(timezone.utc).date()
        d0 = d1 - timedelta(days=1)
        _post_daily(token, date=d0.isoformat(),
                    snapchat_ads=0, tiktok_ads=0, instagram_ads=0,
                    snapchat_ads_2=0, google_ads=0,
                    product_costs=300.0, notes="يوم سابق")
        _post_daily(token, date=d1.isoformat(),
                    snapchat_ads=0, tiktok_ads=0, instagram_ads=0,
                    snapchat_ads_2=0, google_ads=0,
                    product_costs=700.0, notes="اليوم")

        # Dashboard default range covers both days → sum = 1000.
        t = _dashboard(token)["totals"]
        assert t["manual_product_cost"] == pytest.approx(1000.0, abs=0.5)
    finally:
        _cleanup(uid)


# ── 5. Auth required ──────────────────────────────────────────────────
def test_endpoint_requires_auth():
    today = datetime.now(timezone.utc).date().isoformat()
    r = requests.post(
        f"{API}/daily-costs",
        json={"date": today, "snapchat_ads": 0, "tiktok_ads": 0,
              "instagram_ads": 0, "snapchat_ads_2": 0, "google_ads": 0,
              "product_costs": 100, "notes": ""},
        timeout=10,
    )
    assert r.status_code in (401, 403)
