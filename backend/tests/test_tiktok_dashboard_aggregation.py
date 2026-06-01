"""Regression tests for TikTok dashboard summary aggregation (iteration 16).

These tests lock in the fixes for two production bugs reported by the
merchant:

  Bug A (multi-campaign per date overwrite):
      `tt_by_date = {r["date"]: r for r in tt_rows}` silently dropped
      all but the last campaign per date. With 3 campaigns spending
      100 SAR each on the same day, the dashboard showed 100 SAR.

  Bug B (daily_costs partial coverage drops webhook spend):
      The `_agg()` loop iterated over dc_spend_by_date.items() only,
      and the `if spend == 0.0` fallback only fired when daily_costs
      contributed ZERO. As a result, if a merchant had ANY old
      daily_costs.tiktok_ads row in the range (even with tiny value),
      all webhook spend for dates NOT in daily_costs was dropped.
      Affected ALL merchants whose Make.com webhook fed TikTok data
      after they had previously entered manual tiktok_ads in
      daily_costs.

  Bug C (dashboard daily_ads_total missed TikTok webhook):
      The master `/api/dashboard` endpoint summed TikTok only from
      `daily_costs.tiktok_ads`, ignoring `tiktok_ads_daily` (the
      webhook target). The "إجمالي تكلفة الإعلانات" card therefore
      undercounted TikTok by the full webhook amount.

Run:
  export REACT_APP_BACKEND_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
  pytest /app/backend/tests/test_tiktok_dashboard_aggregation.py -v
"""
from __future__ import annotations

import os
import uuid
import asyncio
import pytest
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"tt{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "TT Bug", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _riyadh_today_iso():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Riyadh")).date().isoformat()
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(hours=3)).date().isoformat()


async def _seed(uid: str, rows_tt: list, rows_dc: list):
    """Seed tiktok_ads_daily + daily_costs rows for the given user."""
    from motor.motor_asyncio import AsyncIOMotorClient
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    now = datetime.now(timezone.utc).isoformat()
    for r in rows_tt:
        await db.tiktok_ads_daily.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "platform": "tiktok",
            "campaign_name": r.get("campaign_name", ""),
            "purchases": int(r.get("purchases", 0)),
            "revenue": float(r.get("revenue", 0.0)),
            "updated_at": now, "created_at": now, **r,
        })
    for r in rows_dc:
        await db.daily_costs.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "snapchat_ads": 0.0, "snapchat_ads_2": 0.0,
            "instagram_ads": 0.0, "google_ads": 0.0,
            "product_costs": 0.0, "notes": "test seed",
            "created_at": now, **r,
        })
    c.close()


class TestBugA_MultiCampaignPerDate:
    """3 campaigns spending on the SAME date must AGGREGATE not OVERWRITE."""

    def test_three_campaigns_one_date_sum_correctly(self):
        token, uid = _register()
        today = _riyadh_today_iso()
        d_yesterday = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
        asyncio.run(_seed(uid, [
            {"date": d_yesterday, "campaign_id": "camp_A", "spend": 100.0, "purchases": 2, "revenue": 500.0},
            {"date": d_yesterday, "campaign_id": "camp_B", "spend": 100.0, "purchases": 1, "revenue": 300.0},
            {"date": d_yesterday, "campaign_id": "camp_C", "spend": 100.0, "purchases": 3, "revenue": 800.0},
        ], []))
        r = requests.get(f"{API}/dashboard/tiktok-summary", headers=_headers(token), timeout=15)
        assert r.status_code == 200
        body = r.json()
        # last_30d must sum all 3 campaigns: 100+100+100=300, not just 100
        assert body["last_30d"]["spend"] == 300.0, (
            f"Multi-campaign aggregation broken: got {body['last_30d']['spend']}, expected 300.0"
        )
        # And orders + revenue must also aggregate
        assert body["last_30d"]["orders"] == 6
        assert body["last_30d"]["revenue"] == 1600.0


class TestBugB_PartialDailyCostsCoverageDropsWebhookSpend:
    """If daily_costs has ANY tiktok_ads row in range, webhook spend for
    OTHER dates must NOT be dropped (this exact case broke admin's card)."""

    def test_webhook_spend_not_dropped_by_unrelated_daily_costs(self):
        token, uid = _register()
        today = _riyadh_today_iso()
        d_today = datetime.fromisoformat(today).date()
        d_1 = (d_today - timedelta(days=1)).isoformat()
        d_5 = (d_today - timedelta(days=5)).isoformat()
        d_10 = (d_today - timedelta(days=10)).isoformat()

        asyncio.run(_seed(uid,
            # Webhook seeded for d_1 (350 SAR) — this is what was dropped
            rows_tt=[
                {"date": d_1, "campaign_id": "cA", "spend": 350.0, "purchases": 5, "revenue": 2000.0},
            ],
            # Legacy manual daily_costs.tiktok_ads on OTHER unrelated dates
            rows_dc=[
                {"date": d_5, "tiktok_ads": 33.0, "snapchat_ads": 0.0,
                 "snapchat_ads_2": 0.0, "instagram_ads": 0.0, "google_ads": 0.0,
                 "product_costs": 0.0, "notes": ""},
                {"date": d_10, "tiktok_ads": 40.0, "snapchat_ads": 0.0,
                 "snapchat_ads_2": 0.0, "instagram_ads": 0.0, "google_ads": 0.0,
                 "product_costs": 0.0, "notes": ""},
            ],
        ))
        r = requests.get(f"{API}/dashboard/tiktok-summary", headers=_headers(token), timeout=15)
        body = r.json()
        # last_30d must include BOTH the webhook 350 AND the manual 33 + 40 = 423.
        # Before the iteration-16 fix, this returned 73.0 (webhook 350 SILENTLY DROPPED).
        assert body["last_30d"]["spend"] == 423.0, (
            f"Bug B regression: got {body['last_30d']['spend']}, expected 423.0 "
            "(webhook spend was dropped when daily_costs had partial coverage)"
        )

    def test_same_date_both_sources_dedupes_via_max(self):
        """When BOTH webhook AND daily_costs have a value for the SAME date,
        we MUST NOT double-count — take max() per date."""
        token, uid = _register()
        today = _riyadh_today_iso()
        d_yest = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
        asyncio.run(_seed(uid,
            rows_tt=[{"date": d_yest, "campaign_id": "X", "spend": 500.0,
                      "purchases": 5, "revenue": 1000.0}],
            rows_dc=[{"date": d_yest, "tiktok_ads": 200.0, "snapchat_ads": 0.0,
                      "snapchat_ads_2": 0.0, "instagram_ads": 0.0,
                      "google_ads": 0.0, "product_costs": 0.0, "notes": ""}],
        ))
        r = requests.get(f"{API}/dashboard/tiktok-summary", headers=_headers(token), timeout=15)
        body = r.json()
        # max(500, 200) = 500 (not 700)
        assert body["last_30d"]["spend"] == 500.0


class TestBugC_DashboardTotalIncludesWebhookTiktok:
    """The master `/api/dashboard` daily_ads_total must include TikTok
    webhook spend (was previously zero because the sum only read
    daily_costs.tiktok_ads, not tiktok_ads_daily)."""

    def test_dashboard_daily_ads_total_includes_webhook(self):
        token, uid = _register()
        today = _riyadh_today_iso()
        d_yest = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
        asyncio.run(_seed(uid,
            rows_tt=[{"date": d_yest, "campaign_id": "wh", "spend": 999.0,
                      "purchases": 10, "revenue": 5000.0}],
            rows_dc=[],
        ))
        # Use a wide range to cover the seeded date
        from_d = (datetime.fromisoformat(today) - timedelta(days=29)).date().isoformat()
        r = requests.get(
            f"{API}/dashboard?from_date={from_d}&to_date={today}",
            headers=_headers(token), timeout=15,
        )
        assert r.status_code == 200
        totals = r.json()["totals"]
        assert totals["tiktok_spend"] == 999.0
        # daily_ads_total MUST include the webhook 999 (was 0 before the fix)
        assert totals["daily_ads_total"] >= 999.0, (
            f"Bug C regression: daily_ads_total={totals['daily_ads_total']} "
            "but tiktok_spend=999. The webhook spend was dropped from the master total."
        )
        # And total_ads_cost (the field the dashboard card binds to) must
        # include it too.
        assert totals["total_ads_cost"] >= 999.0
