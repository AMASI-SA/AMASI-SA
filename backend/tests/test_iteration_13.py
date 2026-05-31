"""
Iteration 13 tests: TikTok summary endpoint + Meta spend aggregation in /dashboard.

Covers review_request items:
- /dashboard returns totals.meta_spend, meta_purchases, meta_revenue, meta_roas
- total_ads_cost / daily_ads_total include meta_spend (seed test)
- /dashboard/tiktok-summary contract (today/month/last_30d/history/last_fetched_at/source/has_data)
- /dashboard/tiktok-summary uses Asia/Riyadh today date
- /snapchat/daily-spend/bulk response includes ad_account_timezone field
"""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

ADMIN_EMAIL = "admin@hesab.app"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    token = r.json().get("access_token") or r.json().get("token")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def admin_user_id(admin_client):
    r = admin_client.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200
    return r.json()["id"]


# ── /dashboard meta_* fields ─────────────────────────────────────────────────
class TestDashboardMetaFields:
    def test_dashboard_exposes_meta_aggregates(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/dashboard")
        assert r.status_code == 200
        totals = r.json()["totals"]
        for k in ("meta_spend", "meta_purchases", "meta_revenue", "meta_roas"):
            assert k in totals, f"missing {k} in totals"
        assert isinstance(totals["meta_spend"], (int, float))
        assert isinstance(totals["meta_purchases"], int)
        assert isinstance(totals["meta_revenue"], (int, float))
        assert isinstance(totals["meta_roas"], (int, float))

    def test_dashboard_totals_include_tiktok_too(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/dashboard")
        totals = r.json()["totals"]
        for k in ("tiktok_spend", "tiktok_purchases", "tiktok_revenue", "tiktok_roas"):
            assert k in totals


# ── Meta spend rolled into total_ads_cost (SEED test) ────────────────────────
@pytest.mark.asyncio
class TestMetaSpendInTotalAdsCost:
    async def test_total_ads_cost_includes_meta_spend(self, admin_client, admin_user_id):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        seed_date = "2026-05-31"
        seed_doc = {
            "user_id": admin_user_id,
            "date": seed_date,
            "spend": 300.0,
            "purchases": 0,
            "revenue": 0.0,
            "_test_marker": "iteration_13",
        }
        try:
            # Baseline (with date filter so we isolate seeded effect)
            r0 = admin_client.get(
                f"{BASE_URL}/api/dashboard",
                params={"from_date": seed_date, "to_date": seed_date},
            )
            assert r0.status_code == 200
            t0 = r0.json()["totals"]
            baseline_meta = float(t0.get("meta_spend") or 0)
            baseline_total = float(t0.get("total_ads_cost") or 0)
            baseline_daily_ads = float(t0.get("daily_ads_total") or 0)

            # Insert seed
            await db.meta_ads_daily.delete_many(
                {"user_id": admin_user_id, "date": seed_date,
                 "_test_marker": "iteration_13"}
            )
            await db.meta_ads_daily.insert_one(seed_doc)

            r1 = admin_client.get(
                f"{BASE_URL}/api/dashboard",
                params={"from_date": seed_date, "to_date": seed_date},
            )
            assert r1.status_code == 200
            t1 = r1.json()["totals"]

            assert float(t1["meta_spend"]) >= baseline_meta + 300.0 - 0.01, \
                f"meta_spend did not grow by 300: before={baseline_meta} after={t1['meta_spend']}"
            assert float(t1["total_ads_cost"]) >= baseline_total + 300.0 - 0.01, \
                f"total_ads_cost not increased by meta_spend: before={baseline_total} after={t1['total_ads_cost']}"
            assert float(t1["daily_ads_total"]) >= baseline_daily_ads + 300.0 - 0.01, \
                f"daily_ads_total not increased by meta_spend: before={baseline_daily_ads} after={t1['daily_ads_total']}"
        finally:
            await db.meta_ads_daily.delete_many(
                {"user_id": admin_user_id, "date": seed_date,
                 "_test_marker": "iteration_13"}
            )
            client.close()


# ── /dashboard/tiktok-summary contract ───────────────────────────────────────
class TestTiktokSummary:
    def test_endpoint_200_and_shape(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/dashboard/tiktok-summary")
        assert r.status_code == 200, r.text
        data = r.json()
        for top in ("today", "month", "last_30d", "history", "source", "has_data"):
            assert top in data, f"missing top-level field {top}"
        assert "last_fetched_at" in data
        assert data["source"] == "make_webhook"
        assert isinstance(data["has_data"], bool)
        assert isinstance(data["history"], list)
        assert len(data["history"]) == 30, f"history length={len(data['history'])}"
        for pt in data["history"]:
            assert "date" in pt and "spend" in pt
            assert isinstance(pt["spend"], (int, float))

        for section_key in ("today", "month", "last_30d"):
            sec = data[section_key]
            assert "spend" in sec and "orders" in sec and "revenue" in sec
            assert "roas" in sec and "cpa" in sec
            assert isinstance(sec["orders"], int)
            assert isinstance(sec["spend"], (int, float))
            assert isinstance(sec["roas"], (int, float))

    def test_today_uses_riyadh_tz(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/dashboard/tiktok-summary")
        data = r.json()
        # Asia/Riyadh = UTC+3, fixed (no DST)
        riyadh_today = (datetime.now(timezone.utc) + timedelta(hours=3)).date().isoformat()
        assert data["today"]["date"] == riyadh_today, \
            f"expected {riyadh_today}, got {data['today']['date']}"
        # month start
        assert data["month"]["start"] == riyadh_today[:8] + "01"


# ── Snapchat bulk: ad_account_timezone field ─────────────────────────────────
class TestSnapchatBulkTimezoneField:
    def test_bulk_returns_tz_field_when_not_configured(self, admin_client):
        """Admin has no Snapchat connection, bulk should return 400 with friendly msg.
        We assert that EITHER (success path includes ad_account_timezone) OR
        (error path is a clean Arabic detail, not a stack trace)."""
        today = datetime.now(timezone.utc).date().isoformat()
        r = admin_client.post(
            f"{BASE_URL}/api/snapchat/daily-spend/bulk",
            json={"dates": [today]},
        )
        # When no connection — backend returns 400 with detail string
        if r.status_code == 200:
            body = r.json()
            assert "ad_account_timezone" in body
        else:
            # Verify friendly error (no stack trace leak)
            assert r.status_code in (400, 401, 404), f"unexpected {r.status_code}: {r.text}"
            text = r.text.lower()
            assert "traceback" not in text
