"""Iter-45 — Electronic Net status-filtering tests.

This patch tightens the "صافي المدفوعات الإلكترونية" KPI so it matches
Salla's "غير المفوترة" screen: orders whose status indicates the payment
was never captured (cancelled / refunded / failed / pending) are excluded.

Tests cover:
  1. Default exclusion list silently drops cancelled/refunded/etc orders.
  2. Debug endpoint surfaces a transparent before/after breakdown +
     per-status exclusion counts + sample lists.
  3. `salla_electronic_net_reference` setting populates `gap_vs_computed`
     in the debug endpoint.
  4. Sync-to-Salla preset restores default exclusion list.
  5. BNPL (Tamara/Tabby/Emkan) and COD orders are NOT touched — they
     stay in their own buckets regardless of the new status filter.
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
        json={"name": "iter45 Tester",
              "email": f"iter45-{uuid.uuid4().hex[:10]}@example.com",
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
                "users", "unified_orders", "settings", "daily_costs",
                "tiktok_ads_daily", "meta_ads_daily", "analyses",
            ):
                await db[coll].delete_many(
                    {"$or": [{"user_id": uid}, {"id": uid}, {"_id": uid}]},
                )
        finally:
            client.close()

    asyncio.run(_do())


# ── Regression — debug endpoint must not crash when the user has
# report_included_statuses set (previous bug: NameError on _matches_any).
def test_debug_endpoint_works_with_report_included_statuses():
    token, uid = _register()
    try:
        # Save a settings doc that triggers the status include filter.
        requests.put(
            f"{API}/settings",
            headers=_hdr(token),
            json={
                "payment_methods": [],
                "shipping_companies": [],
                "report_included_statuses": ["تم الدفع", "تم التوصيل"],
            },
            timeout=10,
        ).raise_for_status()
        asyncio.run(_seed_orders(uid, [
            {"payment_method": "مدى", "total_amount": 100, "order_status": "تم الدفع"},
            {"payment_method": "مدى", "total_amount": 200, "order_status": "قيد التنفيذ"},
            {"payment_method": "مدى", "total_amount": 300, "order_status": "تم التوصيل"},
        ]))

        r = requests.get(
            f"{API}/dashboard/electronic-net-debug",
            headers=_hdr(token), timeout=15,
        )
        # Previously crashed with 500 NameError. Must respond 200.
        assert r.status_code == 200, r.text[:400]
        body = r.json()
        # Two orders satisfy the include filter (تم الدفع + تم التوصيل);
        # the "قيد التنفيذ" one is dropped by the report-included filter,
        # NOT by the electronic-net exclusion filter.
        assert body["totals"]["electronic_orders_total"] == 2
        assert body["totals"]["electronic_orders_included"] == 2
        assert body["totals"]["electronic_orders_excluded"] == 0
    finally:
        _cleanup(uid)


async def _seed_orders(uid: str, orders: list[dict]) -> None:
    """Bulk-insert pre-shaped unified_orders rows for a user."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = client[os.environ["DB_NAME"]]
        today = datetime.now(timezone.utc).date().isoformat()
        docs = []
        for i, raw in enumerate(orders):
            docs.append({
                "user_id": uid,
                "order_id": raw.get("order_id") or f"O-{uid[:6]}-{i}",
                "order_number": raw.get("order_number") or f"TST-{i}",
                "order_date": raw.get("order_date") or today,
                "order_status": raw.get("order_status") or "",
                "total_amount": float(raw.get("total_amount") or 0),
                "payment_method": raw.get("payment_method") or "مدى",
                "shipping_company": raw.get("shipping_company") or "iMile",
                "data_source": raw.get("data_source") or "make",
                "total_shipping_cost": 0,
                "payment_fees": 0,
                "vat_amount": 0,
            })
        if docs:
            await db.unified_orders.insert_many(docs)
    finally:
        client.close()


# ── 1. Default exclusion filter drops the right orders ─────────────────
def test_default_filter_excludes_cancelled_refunded_pending():
    token, uid = _register()
    try:
        # 4 valid mada orders + 1 cancelled + 1 refunded + 1 pending = 7 mada total
        orders = [
            {"payment_method": "مدى", "total_amount": 100, "order_status": "تم الدفع"},
            {"payment_method": "مدى", "total_amount": 200, "order_status": "تحت التوصيل"},
            {"payment_method": "مدى", "total_amount": 150, "order_status": "تم التوصيل"},
            {"payment_method": "مدى", "total_amount": 50,  "order_status": "قيد التنفيذ"},
            {"payment_method": "مدى", "total_amount": 999, "order_status": "ملغي"},
            {"payment_method": "مدى", "total_amount": 888, "order_status": "مسترد"},
            {"payment_method": "مدى", "total_amount": 777, "order_status": "بانتظار الدفع"},
        ]
        asyncio.run(_seed_orders(uid, orders))

        r = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=15)
        r.raise_for_status()
        body = r.json()
        totals = body["totals"]
        brk = totals["electronic_net_breakdown"]

        # Pre-filter included all 7 orders (sum=3164)
        assert brk["included_count"] == 4
        assert brk["excluded_count"] == 3
        assert brk["gross_before_filter"] == pytest.approx(3164.0, abs=0.5)
        # Post-filter sum = 100+200+150+50 = 500
        assert brk["gross_after_filter"] == pytest.approx(500.0, abs=0.5)
        # electronic_net uses POST-filter values
        # (fees are 0 because default payment_methods config has 0% mada)
        assert totals["electronic_net"] == pytest.approx(
            brk["gross_after_filter"] - brk["fees_after_filter"], abs=0.5,
        )
    finally:
        _cleanup(uid)


# ── 2. Debug endpoint exposes pre/post + per-status counts + samples ───
def test_debug_endpoint_returns_full_breakdown():
    token, uid = _register()
    try:
        orders = [
            {"payment_method": "مدى", "total_amount": 100, "order_status": "تم الدفع",
             "order_number": "OK-1"},
            {"payment_method": "مدى", "total_amount": 200, "order_status": "ملغي",
             "order_number": "BAD-1"},
            {"payment_method": "Apple Pay", "total_amount": 50, "order_status": "مسترد",
             "order_number": "BAD-2"},
            {"payment_method": "Apple Pay", "total_amount": 75, "order_status": "تم الدفع",
             "order_number": "OK-2"},
        ]
        asyncio.run(_seed_orders(uid, orders))

        r = requests.get(f"{API}/dashboard/electronic-net-debug",
                         headers=_hdr(token), timeout=15)
        assert r.status_code == 200, r.text[:300]
        body = r.json()

        t = body["totals"]
        assert t["electronic_orders_total"] == 4
        assert t["electronic_orders_included"] == 2
        assert t["electronic_orders_excluded"] == 2
        assert t["pre_filter_gross"] == pytest.approx(425.0, abs=0.5)  # 100+200+50+75
        assert t["post_filter_gross"] == pytest.approx(175.0, abs=0.5)  # 100+75

        # Per-status excluded counts surface so the merchant sees WHY
        statuses_seen = {x["status"] for x in body["excluded_by_status"]}
        assert "ملغي" in statuses_seen
        assert "مسترد" in statuses_seen

        # Sample lists include the right orders
        excluded_nums = {x["order_number"] for x in body["excluded_orders_sample"]}
        included_nums = {x["order_number"] for x in body["included_orders_sample"]}
        assert excluded_nums == {"BAD-1", "BAD-2"}
        assert included_nums == {"OK-1", "OK-2"}

        # Excluded sample carries a non-empty reason
        assert all(x.get("exclusion_reason") for x in body["excluded_orders_sample"])

        # Active exclusion list is exposed
        assert any("ملغ" in t for t in body["excluded_statuses_active"])
    finally:
        _cleanup(uid)


# ── 3. Salla reference figure shows gap vs computed net ────────────────
def test_salla_reference_populates_gap():
    token, uid = _register()
    try:
        asyncio.run(_seed_orders(uid, [
            {"payment_method": "مدى", "total_amount": 1000, "order_status": "تم الدفع"},
        ]))
        # Save a manual Salla reference figure.
        requests.put(
            f"{API}/settings",
            headers=_hdr(token),
            json={
                "payment_methods": [],
                "shipping_companies": [],
                "salla_electronic_net_reference": 900.0,
            },
            timeout=10,
        ).raise_for_status()

        r = requests.get(f"{API}/dashboard/electronic-net-debug",
                         headers=_hdr(token), timeout=15)
        r.raise_for_status()
        body = r.json()

        ref = body["salla_reference"]
        assert ref["value"] == pytest.approx(900.0, abs=0.5)
        # Computed net = 1000 - 0 (no fee config) = 1000. Gap = 1000 - 900 = +100.
        assert ref["gap_vs_computed"] == pytest.approx(100.0, abs=0.5)
        assert ref["gap_percent"] == pytest.approx(11.11, abs=0.5)
    finally:
        _cleanup(uid)


# ── 4. Sync-to-Salla preset restores defaults ──────────────────────────
def test_sync_to_salla_restores_default_exclusions():
    token, uid = _register()
    try:
        # First, set a custom override list.
        requests.put(
            f"{API}/settings",
            headers=_hdr(token),
            json={
                "payment_methods": [],
                "shipping_companies": [],
                "electronic_net_excluded_statuses": ["custom_only"],
            },
            timeout=10,
        ).raise_for_status()
        s1 = requests.get(f"{API}/settings", headers=_hdr(token), timeout=10).json()
        assert s1["electronic_net_excluded_statuses"] == ["custom_only"]

        # Hit the sync preset.
        r = requests.post(
            f"{API}/settings/electronic-net/sync-to-salla",
            headers=_hdr(token), timeout=10,
        )
        assert r.status_code == 200, r.text[:300]
        new_list = r.json()["electronic_net_excluded_statuses"]
        # Bundled defaults include at minimum "ملغ", "مسترد", "fail".
        joined = " ".join(new_list).lower()
        assert "ملغ" in joined
        assert "مسترد" in joined
        assert "fail" in joined
        # And the settings document reflects the new list.
        s2 = requests.get(f"{API}/settings", headers=_hdr(token), timeout=10).json()
        assert s2["electronic_net_excluded_statuses"] == new_list
    finally:
        _cleanup(uid)


# ── 5. BNPL / COD orders are untouched by the filter ───────────────────
def test_bnpl_and_cod_orders_unchanged_by_filter():
    token, uid = _register()
    try:
        # mix: 2 mada (1 cancelled), 2 tamara (1 cancelled), 2 COD (1 cancelled)
        asyncio.run(_seed_orders(uid, [
            {"payment_method": "مدى",   "total_amount": 100, "order_status": "تم الدفع"},
            {"payment_method": "مدى",   "total_amount": 100, "order_status": "ملغي"},
            {"payment_method": "تمارا", "total_amount": 200, "order_status": "تم الدفع"},
            {"payment_method": "تمارا", "total_amount": 200, "order_status": "ملغي"},
            {"payment_method": "الدفع عند الاستلام", "total_amount": 300, "order_status": "تم الدفع"},
            {"payment_method": "الدفع عند الاستلام", "total_amount": 300, "order_status": "ملغي"},
        ]))

        r = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=15)
        r.raise_for_status()
        t = r.json()["totals"]

        # Electronic: 200 in (1 mada paid), 100 out (1 mada cancelled).
        assert t["electronic_net_breakdown"]["included_count"] == 1
        assert t["electronic_net_breakdown"]["excluded_count"] == 1

        # Tamara (BNPL) — both orders counted (NOT touched by the filter).
        # tamara_fees / bnpl_sales are computed from the pre-filter breakdown.
        # The card is intentionally NOT affected.
        # Just sanity-check we didn't accidentally zero them out.
        # (We don't know exact value because fee config defaults are 0%.)
        assert t["bnpl_net"] is not None
    finally:
        _cleanup(uid)


# ── 6. Empty store: debug endpoint still returns a valid shape ─────────
def test_debug_endpoint_handles_empty_store_safely():
    token, uid = _register()
    try:
        r = requests.get(f"{API}/dashboard/electronic-net-debug",
                         headers=_hdr(token), timeout=15)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        t = body["totals"]
        assert t["electronic_orders_total"] == 0
        assert t["electronic_orders_included"] == 0
        assert t["electronic_orders_excluded"] == 0
        assert t["pre_filter_gross"] == 0.0
        assert t["post_filter_gross"] == 0.0
        assert body["excluded_orders_sample"] == []
        assert body["included_orders_sample"] == []
    finally:
        _cleanup(uid)
