"""Iter-246o — Tamara refund-sync & old-capture forensic tests.

Verifies the second READ-ONLY diagnostic at
`/api/audit/tamara-refund-and-old-capture-forensic`:

  * Track A — surfaces every refunded-status payment_transaction in
    the window and tells the merchant whether a matching
    payment_refunds row exists.
  * Track B — given specific order_numbers, dumps full local state
    (txn, refunds, ALL settlement_entries across periods).
  * Read-only — no writes to general_ledger / payment_transactions /
    payment_refunds.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def db_cli():
    client = AsyncIOMotorClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest_asyncio.fixture
async def ctx(db_cli):
    suf = uuid.uuid4().hex[:8]
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "iter246o", "email": f"iter246o-{suf}@x.com",
              "password": "pw1234567"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["access_token"]
    uid = body["id"]

    # 3 refunded captures in window: one HAS a payment_refunds row,
    # two are MISSING one.  Plus one old captured-only order with
    # settlement_entries from a previous period.
    captures = [
        {"id": f"o-1-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-1-{suf}", "order_reference_id": "ORD-A",
         "order_number": "ORD-A", "amount": 100.0,
         "captured_amount": 100.0, "refunded_amount": 100.0,
         "currency": "SAR", "status": "fully_refunded",
         "created_at_provider": "2026-05-22T08:00:00Z",
         "updated_at_provider": "2026-06-08T10:00:00Z",
         "billing_eligible_at": None,
         "effective_settlement_date": "2026-06-08",
         "is_pre_accounting": False,
         "raw_payload": {
             "status": "fully_refunded",
             "total_refunded_amount": {"amount": 100.0, "currency": "SAR"},
             "refunds": [{"created_at": "2026-06-08T10:00:00Z",
                          "amount": {"amount": 100.0,
                                     "currency": "SAR"}}],
         }},
        {"id": f"o-2-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-2-{suf}", "order_reference_id": "ORD-B",
         "order_number": "ORD-B", "amount": 200.0,
         "captured_amount": 200.0, "refunded_amount": 80.0,
         "currency": "SAR", "status": "partially_refunded",
         "created_at_provider": "2026-05-25T08:00:00Z",
         "updated_at_provider": "2026-06-09T10:00:00Z",
         "billing_eligible_at": None,
         "effective_settlement_date": "2026-06-09",
         "is_pre_accounting": False,
         "raw_payload": {
             "status": "partially_refunded",
             "total_refunded_amount": {"amount": 80.0, "currency": "SAR"},
             "refunds": [],
         }},
        {"id": f"o-3-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-3-{suf}", "order_reference_id": "ORD-C",
         "order_number": "ORD-C", "amount": 150.0,
         "captured_amount": 150.0, "refunded_amount": 150.0,
         "currency": "SAR", "status": "fully_refunded",
         "created_at_provider": "2026-05-28T08:00:00Z",
         "updated_at_provider": "2026-06-11T10:00:00Z",
         "billing_eligible_at": None,
         "effective_settlement_date": "2026-06-11",
         "is_pre_accounting": False,
         "raw_payload": {}},
        # Old captured-only order — Track B target.
        {"id": f"o-4-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-4-{suf}", "order_reference_id": "OLD-X",
         "order_number": "OLD-X", "amount": 300.0,
         "captured_amount": 300.0, "refunded_amount": 0.0,
         "currency": "SAR", "status": "fully_captured",
         "created_at_provider": "2026-05-22T08:00:00Z",
         "updated_at_provider": "2026-06-08T10:00:00Z",
         "billing_eligible_at": None,
         "effective_settlement_date": "2026-06-08",
         "is_pre_accounting": False,
         "raw_payload": {}},
    ]
    await db_cli.payment_transactions.insert_many(captures)

    # Existing payment_refund for ORD-A only (so ORD-B and ORD-C
    # should appear in `track_a` as MISSING).
    await db_cli.payment_refunds.insert_one({
        "id": f"r-A-{suf}", "user_id": uid, "provider": "tamara",
        "provider_payment_id": f"pid-1-{suf}",
        "provider_refund_id": f"synthetic:pid-1-{suf}",
        "order_reference_id": "ORD-A",
        "amount": 100.0, "currency": "SAR",
        "status": "fully_refunded",
        "refunded_at": "2026-06-08T10:00:00Z",
        "is_pre_accounting": False,
    })

    # Previous-period settlement_entry for OLD-X to test that Track B
    # surfaces ALL periods.
    await db_cli.settlement_entries.insert_many([
        {"user_id": uid, "provider": "tamara",
         "order_number": "OLD-X", "event_type": "sale",
         "settlement_date": "2026-05-30",
         "actual_gross_amount": 300.0,
         "actual_payment_fee": 20.97, "actual_payment_vat": 3.15,
         "actual_net_amount": 275.88, "currency": "SAR",
         "file_hash": "fh-prev-week",
         "created_at": datetime.now(timezone.utc),
         "is_pre_accounting": False},
    ])

    yield {"uid": uid, "token": token, "suf": suf}

    await db_cli.payment_transactions.delete_many({"user_id": uid})
    await db_cli.payment_refunds.delete_many({"user_id": uid})
    await db_cli.settlement_entries.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_track_a_flags_missing_refund_rows(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-refund-and-old-capture-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    a = data["track_a_missing_refunds"]
    # 3 refunded-status txns in the window.
    assert a["total_refunded_status_orders_in_window"] == 3
    # Only ORD-A has a payment_refunds row.
    assert a["with_existing_payment_refund"] == 1
    assert a["without_payment_refund"] == 2

    by_order = {row["order_number"]: row for row in a["rows"]}
    assert by_order["ORD-A"]["has_payment_refund"] is True
    assert by_order["ORD-B"]["has_payment_refund"] is False
    assert by_order["ORD-C"]["has_payment_refund"] is False
    # ORD-B is partially_refunded with refunded_amount=80, ORD-C is
    # fully_refunded with refunded_amount=150 → sum_missing = 230.
    assert a["sum_refunded_amount_missing"] == 230.0
    # Tamara raw_payload hints surfaced.
    assert by_order["ORD-A"]["tamara_refunds_array_len"] == 1
    assert by_order["ORD-A"]["tamara_total_refunded_amount"] == 100.0
    assert by_order["ORD-B"]["tamara_total_refunded_amount"] == 80.0
    assert by_order["ORD-C"]["tamara_total_refunded_amount"] is None


@pytest.mark.asyncio
async def test_track_b_dumps_all_periods_settlement_entries(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-refund-and-old-capture-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "order_numbers": "OLD-X,DOES-NOT-EXIST"},
        headers=_h(ctx["token"]),
    )
    data = r.json()
    b = data["track_b_targeted_orders"]
    assert b["queried_order_numbers"] == ["OLD-X", "DOES-NOT-EXIST"]
    assert b["probe_tamara_api"] is False

    by_q = {row["order_number_query"]: row for row in b["rows"]}
    old_x = by_q["OLD-X"]
    assert old_x["found_in_payment_transactions"] is True
    assert old_x["settlement_entries_count"] == 1
    # Settlement entry dated 2026-05-30 — BEFORE the queried window —
    # proves Track B surfaces ALL periods.
    assert "2026-05-30" in old_x["settlement_dates_observed"]
    # OLD-X has no local refunds.
    assert old_x["payment_refunds_count"] == 0

    missing = by_q["DOES-NOT-EXIST"]
    assert missing["found_in_payment_transactions"] is False
    assert missing["settlement_entries_count"] == 0


@pytest.mark.asyncio
async def test_endpoint_is_read_only(ctx, db_cli):
    uid = ctx["uid"]
    before_gl = await db_cli.general_ledger.count_documents(
        {"user_id": uid})
    before_pt = await db_cli.payment_transactions.count_documents(
        {"user_id": uid})
    before_pr = await db_cli.payment_refunds.count_documents(
        {"user_id": uid})

    requests.get(
        f"{BASE_URL}/api/audit/tamara-refund-and-old-capture-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "order_numbers": "OLD-X"},
        headers=_h(ctx["token"]),
    )

    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == before_gl
    assert await db_cli.payment_transactions.count_documents(
        {"user_id": uid}) == before_pt
    assert await db_cli.payment_refunds.count_documents(
        {"user_id": uid}) == before_pr
