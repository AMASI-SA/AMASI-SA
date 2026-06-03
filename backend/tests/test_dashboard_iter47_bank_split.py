"""Iter-47 — Bank transfers split into their own KPI card.

Confirms the dashboard correctly separates bank-transfer orders from the
electronic-net bucket and surfaces them under `bank_sales / bank_fees /
bank_net`. Also re-verifies that:
  • cancelled/refunded bank orders DON'T sneak back into electronic_net
    (the iter-45 status filter shouldn't pick them up after exclusion).
  • The electronic-net debug endpoint also excludes bank orders from its
    audit numbers so the modal stays consistent with the card.
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
        json={"name": "iter47 Tester",
              "email": f"iter47-{uuid.uuid4().hex[:10]}@example.com",
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
            for coll in ("users", "unified_orders", "settings", "daily_costs", "analyses"):
                await db[coll].delete_many(
                    {"$or": [{"user_id": uid}, {"id": uid}, {"_id": uid}]},
                )
        finally:
            client.close()

    asyncio.run(_do())


async def _seed(uid: str, orders: list[dict]) -> None:
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
                "order_status": raw.get("order_status") or "تم الدفع",
                "total_amount": float(raw.get("total_amount") or 0),
                "payment_method": raw.get("payment_method") or "مدى",
                "shipping_company": "iMile",
                "data_source": "make",
                "total_shipping_cost": 0,
                "payment_fees": 0,
                "vat_amount": 0,
            })
        if docs:
            await db.unified_orders.insert_many(docs)
    finally:
        client.close()


def _dashboard(token: str) -> dict:
    r = requests.get(f"{API}/dashboard", headers=_hdr(token), timeout=15)
    r.raise_for_status()
    return r.json()


# ── 1. Bank orders surface under bank_net, NOT electronic_net ──────────
def test_bank_orders_have_dedicated_kpi():
    token, uid = _register()
    try:
        asyncio.run(_seed(uid, [
            {"payment_method": "مدى", "total_amount": 200},
            {"payment_method": "تحويل بنكي", "total_amount": 500},
            {"payment_method": "Bank Transfer", "total_amount": 300},
            {"payment_method": "حوالة بنكية", "total_amount": 100},
        ]))
        t = _dashboard(token)["totals"]
        # Bank bucket: 500 + 300 + 100 = 900 SAR
        assert t["bank_sales"] == pytest.approx(900.0, abs=0.5)
        # No fee config for bank → bank_fees = 0, bank_net = 900
        assert t["bank_fees"] == pytest.approx(0.0, abs=0.5)
        assert t["bank_net"] == pytest.approx(900.0, abs=0.5)
        # electronic_net only sees the mada order (200 SAR less default fees)
        assert t["electronic_net"] == pytest.approx(200.0, abs=10)
        # Sanity — bank rows are NOT folded into other_payment_sales (gross before
        # fees should equal mada gross, i.e. 200, not 1100).
        gross_after = t.get("electronic_net_breakdown", {}).get("gross_after_filter", 0)
        assert gross_after == pytest.approx(200.0, abs=0.5)
    finally:
        _cleanup(uid)


# ── 2. Bare "bank" name (no Arabic, no qualifier) is detected ──────────
def test_bare_bank_name_is_classified_as_bank():
    token, uid = _register()
    try:
        asyncio.run(_seed(uid, [
            {"payment_method": "Bank", "total_amount": 1000},
            {"payment_method": "Mada", "total_amount": 500},
        ]))
        t = _dashboard(token)["totals"]
        assert t["bank_net"] == pytest.approx(1000.0, abs=0.5)
        # 500 mada minus default fees → ~492-500
        assert t["electronic_net"] == pytest.approx(500.0, abs=15)
    finally:
        _cleanup(uid)


# ── 3. Cancelled bank orders excluded from bank_net is NOT a goal ─────
# Iter-45 status filter applies ONLY to electronic_net. Bank card should
# keep showing ALL bank orders (cash-flow view, not gateway view).
def test_bank_card_includes_all_statuses():
    token, uid = _register()
    try:
        asyncio.run(_seed(uid, [
            {"payment_method": "تحويل بنكي", "total_amount": 400, "order_status": "تم الدفع"},
            {"payment_method": "تحويل بنكي", "total_amount": 100, "order_status": "ملغي"},
        ]))
        t = _dashboard(token)["totals"]
        # Both bank orders included (no status filter on bank).
        assert t["bank_sales"] == pytest.approx(500.0, abs=0.5)
    finally:
        _cleanup(uid)


# ── 4. Electronic-net debug endpoint also excludes bank orders ─────────
def test_debug_endpoint_excludes_bank_orders():
    token, uid = _register()
    try:
        asyncio.run(_seed(uid, [
            {"payment_method": "مدى", "total_amount": 250, "order_status": "تم الدفع",
             "order_number": "ELEC-1"},
            {"payment_method": "تحويل بنكي", "total_amount": 999, "order_status": "تم الدفع",
             "order_number": "BANK-1"},
        ]))
        r = requests.get(
            f"{API}/dashboard/electronic-net-debug",
            headers=_hdr(token), timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        # Only the mada order is electronic. Bank order is invisible here.
        assert body["totals"]["electronic_orders_total"] == 1
        assert body["totals"]["post_filter_gross"] == pytest.approx(250.0, abs=0.5)
        # Bank order does NOT appear in either included or excluded samples
        all_orders = [o["order_number"] for o in body["included_orders_sample"]] + \
                     [o["order_number"] for o in body["excluded_orders_sample"]]
        assert "BANK-1" not in all_orders, "Bank order leaked into electronic-net audit"
    finally:
        _cleanup(uid)


# ── 5. Mixed: bank + BNPL + COD + electronic — every bucket isolated ─
def test_mixed_payment_types_are_independent():
    token, uid = _register()
    try:
        asyncio.run(_seed(uid, [
            {"payment_method": "مدى", "total_amount": 100},
            {"payment_method": "تحويل بنكي", "total_amount": 200},
            {"payment_method": "تمارا", "total_amount": 300},
            {"payment_method": "الدفع عند الاستلام", "total_amount": 400},
        ]))
        t = _dashboard(token)["totals"]
        # mada minus default fees → ~95-100
        assert t["electronic_net"] == pytest.approx(100.0, abs=10)
        assert t["bank_net"] == pytest.approx(200.0, abs=0.5)
        assert t["bnpl_net"] == pytest.approx(300.0, abs=30)
        # Total sales sanity — should be 1000.
        assert t["total_sales"] == pytest.approx(1000.0, abs=0.5)
    finally:
        _cleanup(uid)
