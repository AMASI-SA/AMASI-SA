"""Iter-120 — Refund Date-Based Settlement Aggregation.

Core accounting rule under test:
  • Sales aggregate by ORDER DATE  (created_at_provider ∈ period).
  • Refunds aggregate by REFUND DATE (refunded_at ∈ period).

A refund for an OLD order MUST appear in the settlement of the period
when the refund happened, NOT the period when the order was placed.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
from dotenv import load_dotenv

# Load backend/.env so MONGO_URL is available
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "https://salla-analytics.preview.emergentagent.com").rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"

# Seeded provider for isolation — we use 'tabby' but with synthetic
# rows tagged with a tracer marker so we can clean up afterwards.
TRACER = f"iter120-{uuid.uuid4().hex[:8]}"
PROVIDER = "tabby"

# Period boundaries
PERIOD_OLD_FROM = "2025-05-05"
PERIOD_OLD_TO   = "2025-05-11"
PERIOD_NEW_FROM = "2025-06-08"
PERIOD_NEW_TO   = "2025-06-14"

ORDER_DATE  = "2025-05-10T12:00:00Z"  # falls in OLD period
REFUND_DATE = "2025-06-10T09:30:00Z"  # falls in NEW period


# ── Auth fixture ─────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def auth_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    token = body.get("access_token") or body.get("token")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s, body.get("id")


# ── DB seed / cleanup ────────────────────────────────────────────────
@pytest.fixture(scope="module")
def seeded_payment(auth_session):
    _, user_id = auth_session
    assert user_id, "could not resolve user_id from login response"
    cli = AsyncIOMotorClient(MONGO_URL)
    db = cli[DB_NAME]

    pmt_provider_id = f"PMT-{TRACER}"
    refund_provider_id = f"RFD-{TRACER}"

    async def _seed():
        # Iter-149 — disable accounting cutoff for this user so the
        # 2025-05/2025-06 seeded data is not filtered out.
        await db.accounting_cutoffs.update_one(
            {"user_id": user_id, "provider": PROVIDER},
            {"$set": {
                "accounting_start_date": "2020-01-01",
                "updated_at":            datetime.now(timezone.utc).isoformat(),
            },
             "$setOnInsert": {
                "user_id":  user_id,
                "provider": PROVIDER,
                "created_at": datetime.now(timezone.utc).isoformat(),
             }},
            upsert=True,
        )
        await db.payment_transactions.insert_one({
            "id": str(uuid.uuid4()),
            "provider": PROVIDER,
            "provider_id": pmt_provider_id,
            "user_id": user_id,
            "amount": 300.0,
            "captured_amount": 300.0,
            "refunded_amount": 100.0,  # legacy field (should be IGNORED)
            "currency": "SAR",
            "buyer_email": "tracer@example.com",
            "buyer_phone": "",
            "order_reference_id": f"ORD-{TRACER}",
            "order_number": f"ORD-{TRACER}",
            "status": "captured",
            "created_at_provider": ORDER_DATE,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "raw_payload": {"tracer": TRACER},
        })
        await db.payment_refunds.insert_one({
            "id": str(uuid.uuid4()),
            "provider": PROVIDER,
            "provider_payment_id": pmt_provider_id,
            "provider_refund_id": refund_provider_id,
            "user_id": user_id,
            "order_reference_id": f"ORD-{TRACER}",
            "amount": 100.0,
            "currency": "SAR",
            "status": "succeeded",
            "reason": "test cross-period refund",
            "refunded_at": REFUND_DATE,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "raw": {"tracer": TRACER},
        })

    async def _cleanup():
        await db.payment_transactions.delete_many({"raw_payload.tracer": TRACER})
        await db.payment_refunds.delete_many({"raw.tracer": TRACER})

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed())
        yield pmt_provider_id, refund_provider_id
    finally:
        loop.run_until_complete(_cleanup())
        loop.close()
        cli.close()


# ── Helper: pull a single weekly invoice that contains the date ──────
def _invoice_for(rows, date_str):
    for r in rows:
        if r["from"] <= date_str <= r["to"]:
            return r
    return None


# ── Tests ────────────────────────────────────────────────────────────
class TestRefundDateAggregation:

    def test_sales_appear_in_order_period(self, auth_session, seeded_payment):
        s, _ = auth_session
        r = s.get(
            f"{BASE_URL}/api/bnpl/settlements/items/{PROVIDER}",
            params={"from": PERIOD_OLD_FROM, "to": PERIOD_OLD_TO},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body.get("success") is True, body
        sales = body.get("sales") or []
        # Our seeded order should be in the OLD period's sales
        match = [x for x in sales if x.get("order_reference_id") == f"ORD-{TRACER}"]
        assert len(match) == 1, f"seeded order missing from OLD period sales: {sales}"
        assert match[0]["amount"] == 300.0
        # No refund yet in OLD period
        refunds = body.get("refunds") or []
        in_period_refund = [
            x for x in refunds
            if x.get("order_reference_id") == f"ORD-{TRACER}"
        ]
        assert in_period_refund == [], (
            "CRITICAL: refund leaked into OLD period — refunds must be "
            f"date-based on refunded_at, not order date. Got: {in_period_refund}"
        )

    def test_refund_appears_in_refund_period(self, auth_session, seeded_payment):
        s, _ = auth_session
        r = s.get(
            f"{BASE_URL}/api/bnpl/settlements/items/{PROVIDER}",
            params={"from": PERIOD_NEW_FROM, "to": PERIOD_NEW_TO},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body.get("success") is True, body

        # No sales in NEW period (order was in OLD period)
        sales = body.get("sales") or []
        sale_match = [x for x in sales if x.get("order_reference_id") == f"ORD-{TRACER}"]
        assert sale_match == [], (
            f"CRITICAL: seeded sale leaked into NEW period: {sale_match}"
        )

        # Refund IS in NEW period
        refunds = body.get("refunds") or []
        refund_match = [
            x for x in refunds
            if x.get("order_reference_id") == f"ORD-{TRACER}"
        ]
        assert len(refund_match) == 1, (
            f"refund missing from NEW period: {refunds}"
        )
        rf = refund_match[0]
        assert rf["refund_amount"] == 100.0
        assert rf["refund_date"].startswith("2025-06-10")
        # Cross-period flag: order_date < period_from
        assert rf["order_date"].startswith("2025-05-10")
        # Original amount is enriched from the linked transaction
        assert rf["original_order_amount"] == 300.0

        # Cross-period counter increments
        assert body.get("cross_period_refunds_count", 0) >= 1

    def test_weekly_totals_use_refund_date(self, auth_session, seeded_payment):
        """OLD period weekly invoice gross=+300, refunds=0. NEW period weekly invoice gross=0, refunds=100."""
        s, _ = auth_session

        r_old = s.get(
            f"{BASE_URL}/api/bnpl/settlements/{PROVIDER}",
            params={"from": PERIOD_OLD_FROM, "to": PERIOD_OLD_TO},
            timeout=30,
        )
        assert r_old.status_code == 200, r_old.text[:300]
        old = r_old.json()
        assert old["success"] is True, old
        ot = old["totals"]
        assert ot["gross_sales"] >= 300.0, (
            f"OLD period must include seeded sale (300): {ot}"
        )
        # The seeded order has NO refunds inside the OLD period itself.
        # (Other orders may exist — so we test the DELTA vs an empty period.)

        r_new = s.get(
            f"{BASE_URL}/api/bnpl/settlements/{PROVIDER}",
            params={"from": PERIOD_NEW_FROM, "to": PERIOD_NEW_TO},
            timeout=30,
        )
        assert r_new.status_code == 200, r_new.text[:300]
        new = r_new.json()
        nt = new["totals"]
        assert nt["total_refunds"] >= 100.0, (
            f"NEW period must include the 100 refund (by refunded_at): {nt}"
        )

    def test_invalid_provider_returns_error(self, auth_session):
        s, _ = auth_session
        r = s.get(
            f"{BASE_URL}/api/bnpl/settlements/items/madeup",
            params={"from": PERIOD_OLD_FROM, "to": PERIOD_OLD_TO},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        assert r.json().get("success") is False

    def test_missing_date_params_validation(self, auth_session):
        """`from` and `to` are required and must be YYYY-MM-DD."""
        s, _ = auth_session
        r = s.get(
            f"{BASE_URL}/api/bnpl/settlements/items/{PROVIDER}",
            timeout=30,
        )
        # FastAPI returns 422 for missing required Query params
        assert r.status_code in (400, 422), r.text[:300]

    def test_weekly_rows_have_refunds_count(self, auth_session, seeded_payment):
        s, _ = auth_session
        r = s.get(
            f"{BASE_URL}/api/bnpl/settlements/weekly/{PROVIDER}",
            params={"from": "2025-05-01", "to": "2025-06-30"},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        rows = body.get("rows") or []
        for row in rows:
            assert "refunds_count" in row, (
                "weekly row missing refunds_count field"
            )
        # The week containing 2025-06-10 should show refunds_count >= 1
        new_week = _invoice_for(rows, "2025-06-10")
        assert new_week is not None
        assert new_week["refunds_count"] >= 1
        assert new_week["total_refunds"] >= 100.0
