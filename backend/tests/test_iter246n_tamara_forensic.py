"""Iter-246n — Tamara Settlement Forensic Endpoint regression tests.

Verifies the READ-ONLY forensic endpoint at
`/api/audit/tamara-settlement-forensic` returns:
  * Per-order breakdown with `order_number`, `amount`,
    `commission_calc`, `vat_calc`, dates and link status.
  * Per-refund breakdown with link to the original capture.
  * Orphan-refund detection (refund inside the window with no
    matching capture row at all).
  * Iter-234 recovery list (capture outside window but refund inside).
  * Cross-reference against `settlement_entries` when an official
    Tamara file is uploaded.
  * `delta_vs_baseline` block when the merchant passes their official
    invoice numbers via query string.
  * Read-only: no general_ledger or payment_transactions writes.
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
    """Register a fresh user and seed Tamara payment_transactions +
    refunds + settlement_entries for a controlled scenario."""
    suf = uuid.uuid4().hex[:8]
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "iter246n", "email": f"iter246n-{suf}@x.com",
              "password": "pw1234567"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["access_token"]
    uid = body["id"]

    await db_cli.bnpl_settings.insert_one({
        "user_id": uid, "provider": "tamara",
        "commission_mode": "manual",
        "mdr_percent": 0.0699, "vat_on_fees_percent": 0.15,
        "fixed_fee_per_order": 0.0,
        "refundable_commission_percent": 0.0,
        "settlement_fee_per_invoice": 0.0,
        "settlement_fee_vat_applicable": False,
        "settlement_period_days": 7,
        "invoice_weekdays": [], "transfer_weekdays": [],
    })

    captures = [
        {"id": f"t-cap-1-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-1-{suf}", "order_reference_id": "ORD-001",
         "order_number": "ORD-001", "amount": 100.0, "currency": "SAR",
         "status": "captured",
         "created_at_provider": "2026-06-07T09:00:00Z",
         "billing_eligible_at": "2026-06-08T09:00:00Z",
         "effective_settlement_date": "2026-06-08T09:00:00Z",
         "is_pre_accounting": False},
        {"id": f"t-cap-2-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-2-{suf}", "order_reference_id": "ORD-002",
         "order_number": "ORD-002", "amount": 250.0, "currency": "SAR",
         "status": "captured",
         "created_at_provider": "2026-06-09T11:00:00Z",
         "billing_eligible_at": "2026-06-09T11:00:00Z",
         "effective_settlement_date": "2026-06-09T11:00:00Z",
         "is_pre_accounting": False},
        {"id": f"t-cap-3-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-3-{suf}", "order_reference_id": "ORD-003",
         "order_number": "ORD-003", "amount": 150.0, "currency": "SAR",
         "status": "captured",
         "created_at_provider": "2026-06-11T14:00:00Z",
         "billing_eligible_at": "2026-06-11T14:00:00Z",
         "effective_settlement_date": "2026-06-11T14:00:00Z",
         "is_pre_accounting": False},
        # Outside-window capture (June 4) refunded INSIDE the window
        # → Iter-234 recovery target.
        {"id": f"t-cap-4-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-4-{suf}", "order_reference_id": "ORD-004",
         "order_number": "ORD-004", "amount": 80.0, "currency": "SAR",
         "status": "captured",
         "created_at_provider": "2026-06-04T08:00:00Z",
         "billing_eligible_at": "2026-06-04T08:00:00Z",
         "effective_settlement_date": "2026-06-04T08:00:00Z",
         "is_pre_accounting": False},
    ]
    await db_cli.payment_transactions.insert_many(captures)

    refunds = [
        {"id": f"r-1-{suf}", "user_id": uid, "provider": "tamara",
         "provider_refund_id": "RFD-1",
         "provider_payment_id": f"pid-2-{suf}",
         "order_reference_id": "ORD-002",
         "amount": 50.0, "refunded_at": "2026-06-10T10:00:00Z",
         "status": "refunded", "is_pre_accounting": False},
        {"id": f"r-2-{suf}", "user_id": uid, "provider": "tamara",
         "provider_refund_id": "RFD-2",
         "provider_payment_id": f"pid-4-{suf}",
         "order_reference_id": "ORD-004",
         "amount": 30.0, "refunded_at": "2026-06-08T10:00:00Z",
         "status": "refunded", "is_pre_accounting": False},
        {"id": f"r-3-{suf}", "user_id": uid, "provider": "tamara",
         "provider_refund_id": "RFD-3",
         "provider_payment_id": f"pid-ghost-{suf}",
         "order_reference_id": "ORD-GHOST",
         "amount": 12.0, "refunded_at": "2026-06-09T10:00:00Z",
         "status": "refunded", "is_pre_accounting": False},
    ]
    await db_cli.payment_refunds.insert_many(refunds)

    await db_cli.settlement_entries.insert_many([
        {"user_id": uid, "provider": "tamara",
         "order_number": "ORD-001", "event_type": "sale",
         "settlement_date": "2026-06-08",
         "actual_gross_amount": 100.0,
         "actual_payment_fee": 6.99, "actual_payment_vat": 1.05,
         "actual_net_amount": 91.96, "currency": "SAR",
         "created_at": datetime.now(timezone.utc),
         "is_pre_accounting": False},
        {"user_id": uid, "provider": "tamara",
         "order_number": "ORD-003", "event_type": "sale",
         "settlement_date": "2026-06-11",
         "actual_gross_amount": 150.0,
         "actual_payment_fee": 10.49, "actual_payment_vat": 1.57,
         "actual_net_amount": 137.94, "currency": "SAR",
         "created_at": datetime.now(timezone.utc),
         "is_pre_accounting": False},
        {"user_id": uid, "provider": "tamara",
         "order_number": "ORD-002", "event_type": "refund",
         "settlement_date": "2026-06-10",
         "actual_refund_amount": 50.0,
         "actual_net_amount": -50.0, "currency": "SAR",
         "created_at": datetime.now(timezone.utc),
         "is_pre_accounting": False},
    ])

    yield {"uid": uid, "token": token, "suf": suf}

    await db_cli.payment_transactions.delete_many({"user_id": uid})
    await db_cli.payment_refunds.delete_many({"user_id": uid})
    await db_cli.settlement_entries.delete_many({"user_id": uid})
    await db_cli.bnpl_settings.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_endpoint_returns_per_order_breakdown(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["read_only"] is True
    assert data["provider"] == "tamara"

    order_numbers = {o["order_number"] for o in data["orders"]}
    assert order_numbers == {"ORD-001", "ORD-002", "ORD-003"}

    for o in data["orders"]:
        assert "commission_calc" in o
        assert "vat_calc" in o
        assert o["in_window"] is True


@pytest.mark.asyncio
async def test_orphan_and_recovered_refunds_classified(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    data = r.json()
    fi = data["forensic_iterated_totals"]

    assert fi["refunds_count_iterated"] == 3
    assert fi["orphan_refunds_count"] == 1   # RFD-3 truly orphan
    assert fi["recovered_orders_count"] == 1  # ORD-004 outside-window
    assert fi["recovered_orders_sum"] == 80.0

    recovered_nums = {o["order_number"]
                      for o in data["recovered_orders_iter234"]}
    assert recovered_nums == {"ORD-004"}


@pytest.mark.asyncio
async def test_baseline_delta_block(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={
            "date_from": "2026-06-06", "date_to": "2026-06-12",
            "baseline_gross": 600.0, "baseline_refunds": 92.0,
            "baseline_commission": 42.65, "baseline_vat": 6.40,
            "baseline_net": 458.95,
        },
        headers=_h(ctx["token"]),
    )
    data = r.json()
    assert data["baseline_from_user"]["gross_sales"] == 600.0
    assert data["baseline_from_user"]["net_payable"] == 458.95

    delta = data["delta_vs_baseline"]
    assert delta is not None
    assert "gross_sales" in delta
    assert isinstance(
        delta["gross_sales"]["delta_system_minus_baseline"], float,
    )


@pytest.mark.asyncio
async def test_official_file_cross_reference(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    data = r.json()
    assert data["cross_reference"]["official_file_present"] is True

    in_db_not = set(data["cross_reference"]["orders_in_db_not_in_official"])
    in_off_not = set(data["cross_reference"]["orders_in_official_not_in_db"])
    # All three in-window orders (001, 002, 003) appear in the
    # official file (002 as a refund row) — so no diffs expected.
    assert in_db_not == set()
    assert in_off_not == set()
    # ORD-004 isn't in db_order_numbers because its
    # effective_settlement_date is outside the window — it's recovered
    # via Iter-234 only.
    assert "ORD-004" not in in_db_not


@pytest.mark.asyncio
async def test_status_and_attribution_breakdowns(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    data = r.json()
    sb = data["order_status_breakdown"]
    ab = data["attribution_source_breakdown"]
    assert isinstance(sb, dict)
    assert isinstance(ab, dict)
    # All 3 seeded captures are "captured" → 1 status bucket, count == 3.
    assert sb["captured"]["count"] == 3
    assert sb["captured"]["sum"] == 500.0
    # attribution_source wasn't seeded in the test docs → falls back to
    # "unknown" bucket (acceptable — production rows will have real
    # settlement_source values like provider_captured / billing_eligible).
    total_attr_count = sum(v["count"] for v in ab.values())
    assert total_attr_count == 3


@pytest.mark.asyncio
async def test_missing_refunds_cross_reference(ctx, db_cli):
    """Seed an extra refund row in settlement_entries for an order
    that has NO matching payment_refunds entry, and verify the
    forensic endpoint flags it under
    `refund_order_numbers_in_official_not_in_db`."""
    uid = ctx["uid"]
    await db_cli.settlement_entries.insert_one({
        "user_id": uid, "provider": "tamara",
        "order_number": "ORD-MISSING-RFD",
        "event_type": "refund",
        "settlement_date": "2026-06-11",
        "actual_refund_amount": 77.5,
        "actual_net_amount": -77.5, "currency": "SAR",
        "created_at": datetime.now(timezone.utc),
        "is_pre_accounting": False,
    })
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    data = r.json()
    missing = data["cross_reference"][
        "refund_order_numbers_in_official_not_in_db"]
    assert "ORD-MISSING-RFD" in missing
    assert data["cross_reference"][
        "missing_refunds_sum_from_official"] >= 77.5


@pytest.mark.asyncio
async def test_endpoint_is_read_only(ctx, db_cli):
    """Calling the forensic endpoint must NOT touch general_ledger
    or alter payment_transactions/refunds."""
    uid = ctx["uid"]
    before_gl = await db_cli.general_ledger.count_documents(
        {"user_id": uid})
    before_pt = await db_cli.payment_transactions.count_documents(
        {"user_id": uid})
    before_pr = await db_cli.payment_refunds.count_documents(
        {"user_id": uid})

    requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "baseline_gross": 600.0},
        headers=_h(ctx["token"]),
    )

    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == before_gl
    assert await db_cli.payment_transactions.count_documents(
        {"user_id": uid}) == before_pt
    assert await db_cli.payment_refunds.count_documents(
        {"user_id": uid}) == before_pr
