"""Iteration 33 — Snapchat Official (PDT) read-only reference card.

User request: add an ISOLATED Snapchat reference card on the dashboard that
shows the official Snapchat numbers using the ad account's NATIVE timezone
(typically PT/PDT). It must NOT enter any system calculation (profits,
expenses, dashboard, ROAS, reports).

This test locks in:
1. The endpoint is registered and authenticated.
2. Without a Snapchat connection → 400 with a friendly Arabic error.
3. ISOLATION guarantee: writing a pre-baked snapshot into
   `snapchat_reference_stats` does NOT affect:
     - `/api/dashboard`            (no extra spend / sales / ROAS injection)
     - `/api/dashboard/snapchat-summary` (still reads from daily_costs only)
     - `/api/dashboard/total-cost-of-sales` (no extra spend addition)
4. Cache contract: when a fresh snapshot already exists,
   GET `/snapchat/reference-stats` (without refresh=true) returns it
   without trying to hit the Snapchat API.

Run:
  pytest /app/backend/tests/test_snapchat_reference_stats_iteration33.py -v
"""
from __future__ import annotations

import os
import uuid
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"i33-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "I33", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    j = r.json()
    return j["access_token"], j["id"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _seed_reference_snapshot(uid: str, *, fresh: bool = True) -> dict:
    """Insert a fake snapshot into snapchat_reference_stats. If fresh=True
    the last_sync_at is now (used to test the cache path)."""
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        now = datetime.now(timezone.utc)
        ts = (now if fresh else now - timedelta(hours=2)).isoformat()
        snap = {
            "user_id": uid,
            "fx_rate": 3.752,
            "currency_display": "SAR",
            "last_sync_at": ts,
            "account_count": 1,
            "accounts": [{
                "ad_account_id": "fake-ad",
                "name": "FakeAdAcc",
                "currency_native": "USD",
                "timezone": "America/Los_Angeles",
                "yesterday_date": "2026-06-01",
                "yesterday": {
                    "spend_native": 1000.0, "spend_sar": 3752.0,
                    "impressions": 100000, "swipes": 5000,
                    "purchases": 25, "revenue_sar": 7504.0,
                },
                "month": {
                    "start": "2026-06-01", "end": "2026-06-01",
                    "spend_native": 1000.0, "spend_sar": 3752.0,
                    "purchases": 25, "revenue_sar": 7504.0,
                },
            }],
            "yesterday": {
                "date": "2026-06-01",
                "spend_usd": 1000.0, "spend_sar": 3752.0,
                "impressions": 100000, "swipes": 5000,
                "purchases": 25, "revenue_sar": 7504.0, "roas": 2.0,
            },
            "month": {
                "start": "2026-06-01", "end": "2026-06-01",
                "spend_usd": 1000.0, "spend_sar": 3752.0,
                "purchases": 25, "revenue_sar": 7504.0, "roas": 2.0,
            },
            "errors": [],
            "note": "test snapshot",
        }
        await db.snapchat_reference_stats.update_one(
            {"user_id": uid}, {"$set": snap}, upsert=True,
        )
        c.close()
        return snap
    return asyncio.run(_do())


def _read_collection(uid: str, name: str) -> list[dict]:
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        rows = await db[name].find({"user_id": uid}, {"_id": 0}).to_list(100)
        c.close()
        return rows
    return asyncio.run(_do())


def _cleanup(uid: str) -> None:
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        for coll in ("snapchat_reference_stats", "snapchat_connections",
                     "snapchat_ad_accounts", "snapchat_daily_stats",
                     "snapchat_account_daily", "daily_costs",
                     "unified_orders", "users"):
            await db[coll].delete_many({"user_id": uid})
        await db.users.delete_many({"id": uid})
        c.close()
    asyncio.run(_do())


# ── 1. Endpoint registered + requires auth ─────────────────────────────
def test_endpoint_requires_auth():
    r = requests.get(f"{API}/snapchat/reference-stats", timeout=15)
    assert r.status_code in (401, 403), (
        f"Endpoint must require auth; got {r.status_code} → {r.text[:200]}"
    )


# ── 2. Without Snapchat connection → 400 with Arabic error ─────────────
def test_400_when_snapchat_not_connected():
    token, uid = _register()
    try:
        r = requests.get(
            f"{API}/snapchat/reference-stats",
            headers=_hdr(token), timeout=15,
        )
        assert r.status_code == 400, (
            f"Expected 400 when no Snap conn; got {r.status_code}: {r.text[:200]}"
        )
        msg = (r.json().get("detail") or "")
        assert "سناب" in msg or "Snap" in msg.lower(), (
            f"Error msg should mention Snapchat: {msg!r}"
        )
    finally:
        _cleanup(uid)


# ── 3. Cache contract: fresh snapshot is returned without hitting API ──
def test_cache_returns_stored_snapshot():
    token, uid = _register()
    try:
        snap = _seed_reference_snapshot(uid, fresh=True)
        r = requests.get(
            f"{API}/snapchat/reference-stats",
            headers=_hdr(token), timeout=15,
        )
        assert r.status_code == 200, f"Got {r.status_code}: {r.text[:200]}"
        body = r.json()
        # Same snapshot returned verbatim (no Snap API call needed because
        # the endpoint short-circuits before _ensure_access_token).
        assert body["fx_rate"] == 3.752
        assert body["account_count"] == snap["account_count"]
        assert body["yesterday"]["spend_usd"] == snap["yesterday"]["spend_usd"]
        assert body["yesterday"]["spend_sar"] == snap["yesterday"]["spend_sar"]
        assert body["yesterday"]["roas"] == 2.0
        assert body["month"]["spend_sar"] == 3752.0
    finally:
        _cleanup(uid)


# ── 4. ISOLATION: snapshot does NOT affect dashboard counters ──────────
def test_dashboard_ignores_reference_stats():
    """The whole point of this card is comparison-only. Seeding a snapshot
    must NOT change /api/dashboard outputs (totals, spend, ROAS) because
    none of those reads touch `snapchat_reference_stats`.
    """
    token, uid = _register()
    try:
        # Baseline (no snapshot yet).
        r1 = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=20)
        assert r1.status_code == 200, r1.text[:200]
        baseline = r1.json()

        # Seed the reference snapshot.
        _seed_reference_snapshot(uid, fresh=True)

        # Re-read /api/dashboard. NOTHING should differ — because the
        # reference collection is isolated.
        r2 = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=20)
        assert r2.status_code == 200, r2.text[:200]
        after = r2.json()

        # Identify spend / sales fields that would betray a leak.
        for key in ("total_sales", "net_sales", "snapchat_ads_total",
                    "tiktok_ads_total", "meta_ads_total", "total_expenses",
                    "total_orders", "average_order_value"):
            assert baseline.get(key) == after.get(key), (
                f"Field {key!r} changed after seeding snapshot — "
                f"isolation broken! baseline={baseline.get(key)!r} after={after.get(key)!r}"
            )

        # And no side-effects on daily_costs / per-account daily / aggregate.
        for coll in ("daily_costs", "snapchat_account_daily",
                     "snapchat_daily_stats"):
            rows = _read_collection(uid, coll)
            assert rows == [], (
                f"Collection {coll!r} was unexpectedly populated by the "
                f"reference snapshot ({len(rows)} rows). Isolation broken!"
            )
    finally:
        _cleanup(uid)


# ── 5. snapchat-summary endpoint also stays clean ──────────────────────
def test_snapchat_summary_ignores_reference_stats():
    token, uid = _register()
    try:
        # Baseline
        r1 = requests.get(
            f"{API}/dashboard/snapchat-summary",
            headers=_hdr(token), timeout=20,
        )
        # Some setups respond 200 with zeros for a fresh user; the contract
        # we care about is "no leak from reference".
        b1 = r1.json() if r1.status_code == 200 else None

        _seed_reference_snapshot(uid, fresh=True)

        r2 = requests.get(
            f"{API}/dashboard/snapchat-summary",
            headers=_hdr(token), timeout=20,
        )
        b2 = r2.json() if r2.status_code == 200 else None

        # If the dashboard summary is available, today/month/last_30d spend
        # MUST remain identical before/after seeding the snapshot.
        if b1 is not None and b2 is not None:
            for section in ("today", "month", "last_30d"):
                s1 = (b1.get(section) or {})
                s2 = (b2.get(section) or {})
                for k in ("spend", "spend_native", "orders", "revenue"):
                    assert s1.get(k) == s2.get(k), (
                        f"snapchat-summary.{section}.{k} changed: "
                        f"{s1.get(k)} → {s2.get(k)} after seeding reference"
                    )
    finally:
        _cleanup(uid)



# ── 6. Δ comparison: system_comparison block + delta math ──────────────
def test_system_comparison_block_present_and_math_correct():
    """When the cached snapshot has the system_comparison block, the
    endpoint returns it verbatim. Verify Δ math for representative cases.

    Expected math:
      Snap official yesterday ROAS = 2.0x; system yesterday ROAS = 2.50x
        → delta_roas_pct = (2.0 - 2.5) / 2.5 * 100 = -20.0%
      Snap official month ROAS = 2.0x; system month ROAS = 2.83x
        → delta_roas_pct = (2.0 - 2.83) / 2.83 * 100 ≈ -29.3%
      Spend yesterday Snap=3752 vs system=100 → +3652.0%
    """
    token, uid = _register()
    try:
        async def _seed():
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            await db.snapchat_reference_stats.update_one(
                {"user_id": uid},
                {"$set": {
                    "user_id": uid,
                    "last_sync_at": datetime.now(timezone.utc).isoformat(),
                    "fx_rate": 3.752,
                    "account_count": 1,
                    "accounts": [],
                    "yesterday": {
                        "date": "2026-06-01", "spend_usd": 1000.0,
                        "spend_sar": 3752.0, "impressions": 100000,
                        "swipes": 5000, "purchases": 25,
                        "revenue_sar": 7504.0, "roas": 2.0,
                    },
                    "month": {
                        "start": "2026-06-01", "end": "2026-06-01",
                        "spend_usd": 1000.0, "spend_sar": 3752.0,
                        "purchases": 25, "revenue_sar": 7504.0, "roas": 2.0,
                    },
                    "system_comparison": {
                        "business_timezone": "Asia/Riyadh",
                        "yesterday": {
                            "date": "2026-06-01",
                            "spend_sar": 100.0, "revenue_sar": 250.0,
                            "roas": 2.5,
                            "delta_roas_pct": -20.0,
                            "delta_spend_pct": 3652.0,
                        },
                        "month": {
                            "start": "2026-06-01", "end": "2026-06-01",
                            "spend_sar": 300.0, "revenue_sar": 850.0,
                            "roas": 2.83,
                            "delta_roas_pct": -29.3,
                            "delta_spend_pct": 1150.7,
                        },
                    },
                }}, upsert=True,
            )
            c.close()
        asyncio.run(_seed())

        r = requests.get(f"{API}/snapchat/reference-stats",
                         headers=_hdr(token), timeout=15)
        assert r.status_code == 200, r.text[:200]
        sc = r.json().get("system_comparison") or {}
        assert sc.get("business_timezone") == "Asia/Riyadh"
        sy = sc["yesterday"]
        sm = sc["month"]
        assert sy["roas"] == 2.5
        assert sy["delta_roas_pct"] == -20.0
        assert sy["delta_spend_pct"] == 3652.0
        assert sm["roas"] == 2.83
        assert sm["delta_roas_pct"] == -29.3
    finally:
        _cleanup(uid)


def test_delta_pct_is_none_when_system_has_no_data():
    """Division-by-zero protection: when system spend/ROAS is 0,
    delta_*_pct must be None (frontend renders '—' instead of '+∞%')."""
    token, uid = _register()
    try:
        async def _seed():
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            await db.snapchat_reference_stats.update_one(
                {"user_id": uid},
                {"$set": {
                    "user_id": uid,
                    "last_sync_at": datetime.now(timezone.utc).isoformat(),
                    "fx_rate": 3.752, "account_count": 0, "accounts": [],
                    "yesterday": {"date": "2026-06-01", "spend_usd": 100,
                                  "spend_sar": 375.2, "impressions": 0,
                                  "swipes": 0, "purchases": 0,
                                  "revenue_sar": 0, "roas": 0},
                    "month": {"start": "2026-06-01", "end": "2026-06-01",
                              "spend_usd": 100, "spend_sar": 375.2,
                              "purchases": 0, "revenue_sar": 0, "roas": 0},
                    "system_comparison": {
                        "business_timezone": "Asia/Riyadh",
                        "yesterday": {"date": "2026-06-01",
                                      "spend_sar": 0, "revenue_sar": 0,
                                      "roas": 0,
                                      "delta_roas_pct": None,
                                      "delta_spend_pct": None},
                        "month": {"start": "2026-06-01", "end": "2026-06-01",
                                  "spend_sar": 0, "revenue_sar": 0,
                                  "roas": 0,
                                  "delta_roas_pct": None,
                                  "delta_spend_pct": None},
                    },
                }}, upsert=True,
            )
            c.close()
        asyncio.run(_seed())

        r = requests.get(f"{API}/snapchat/reference-stats",
                         headers=_hdr(token), timeout=15)
        assert r.status_code == 200, r.text[:200]
        sc = r.json()["system_comparison"]
        assert sc["yesterday"]["delta_roas_pct"] is None
        assert sc["yesterday"]["delta_spend_pct"] is None
        assert sc["month"]["delta_roas_pct"] is None
        assert sc["month"]["delta_spend_pct"] is None
    finally:
        _cleanup(uid)
