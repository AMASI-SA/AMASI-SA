"""Iter-246r — Forensic engine hardening tests.

Verifies:
  * Captures pinned to historical settlement (Fix #3) are NOT
    recovered by Iter-234 orphan-refund-recovery.
  * Same-Week Net-Zero captures are excluded from Gross but their
    refund stays in Refunds.
  * Tabby untouched.
  * Read-only: zero writes to general_ledger / bank_accounts.
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
        json={"name": "iter246r", "email": f"iter246r-{suf}@x.com",
              "password": "pw1234567"},
    )
    body = r.json()
    token = body["access_token"]
    uid = body["id"]

    await db_cli.bnpl_settings.insert_one({
        "user_id": uid, "provider": "tamara",
        "commission_mode": "manual",
        "mdr_percent": 0.0699, "vat_on_fees_percent": 0.15,
        "fixed_fee_per_order": 1.5,
        "refundable_commission_percent": 0.0,
        "settlement_fee_per_invoice": 0.0,
        "settlement_fee_vat_applicable": False,
        "settlement_period_days": 7,
        "invoice_weekdays": [], "transfer_weekdays": [],
    })

    # Scenario:
    #  - PINNED: capture pinned to a past week via Fix #3
    #    (settlement_source=settlement_entries_historical); its refund
    #    is in the current window. Must NOT be recovered by Iter-234.
    #  - SAMEWEEK: capture + refund both in window, flagged Net-Zero.
    #    Must be excluded from gross but kept in refunds.
    #  - NORMAL: regular in-window capture, no special flags.
    await db_cli.payment_transactions.insert_many([
        {"id": f"pinned-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-pinned-{suf}",
         "order_reference_id": "PINNED-1", "order_number": "PINNED-1",
         "amount": 100.0, "captured_amount": 100.0,
         "refunded_amount": 100.0, "currency": "SAR",
         "status": "fully_refunded",
         "created_at_provider": "2026-05-22T08:00:00Z",
         "effective_settlement_date": "2026-05-23",
         "settlement_source": "settlement_entries_historical",
         "is_pre_accounting": False},
        {"id": f"sw-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-sw-{suf}",
         "order_reference_id": "SAMEWEEK-1",
         "order_number": "SAMEWEEK-1",
         "amount": 60.0, "captured_amount": 60.0,
         "refunded_amount": 60.0, "currency": "SAR",
         "status": "fully_refunded",
         "created_at_provider": "2026-06-08T08:00:00Z",
         "effective_settlement_date": "2026-06-08",
         "settlement_source": "provider_official",
         "same_week_netzero_exclusion": True,
         "is_pre_accounting": False},
        {"id": f"normal-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-normal-{suf}",
         "order_reference_id": "NORMAL-1", "order_number": "NORMAL-1",
         "amount": 200.0, "captured_amount": 200.0,
         "refunded_amount": 0.0, "currency": "SAR",
         "status": "fully_captured",
         "created_at_provider": "2026-06-09T08:00:00Z",
         "effective_settlement_date": "2026-06-09",
         "settlement_source": "provider_official",
         "is_pre_accounting": False},
    ])

    # Refunds: PINNED's refund is in window (the post-Fix-#3 scenario);
    # SAMEWEEK's refund is in window.
    await db_cli.payment_refunds.insert_many([
        {"id": f"rf-pin-{suf}", "user_id": uid, "provider": "tamara",
         "provider_payment_id": f"pid-pinned-{suf}",
         "provider_refund_id": f"synthetic:pid-pinned-{suf}",
         "order_reference_id": "PINNED-1", "amount": 100.0,
         "currency": "SAR", "status": "fully_refunded",
         "refunded_at": "2026-06-12T20:59:59Z",
         "is_pre_accounting": False},
        {"id": f"rf-sw-{suf}", "user_id": uid, "provider": "tamara",
         "provider_payment_id": f"pid-sw-{suf}",
         "provider_refund_id": f"synthetic:pid-sw-{suf}",
         "order_reference_id": "SAMEWEEK-1", "amount": 60.0,
         "currency": "SAR", "status": "fully_refunded",
         "refunded_at": "2026-06-08T10:00:00Z",
         "is_pre_accounting": False},
    ])

    # Seed an untouchable Tabby txn to verify isolation.
    await db_cli.payment_transactions.insert_one({
        "id": f"tabby-{suf}", "user_id": uid, "provider": "tabby",
        "provider_id": f"pid-tabby-{suf}", "order_number": "TABBY-1",
        "amount": 500.0, "status": "fully_captured",
        "effective_settlement_date": "2026-06-09",
        "is_pre_accounting": False,
    })

    yield {"uid": uid, "token": token, "suf": suf}

    await db_cli.payment_transactions.delete_many({"user_id": uid})
    await db_cli.payment_refunds.delete_many({"user_id": uid})
    await db_cli.bnpl_settings.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_historical_pinned_capture_not_recovered(ctx):
    """PINNED-1 has settlement_source=settlement_entries_historical
    and a refund in this week. Iter-234 must NOT recover its capture
    into gross."""
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    data = r.json()
    fi = data["forensic_iterated_totals"]
    # No recovery should happen for the historically-pinned capture.
    assert fi["recovered_orders_count"] == 0
    recovered_nums = {o["order_number"]
                      for o in data["recovered_orders_iter234"]}
    assert "PINNED-1" not in recovered_nums

    # But the refund stays in the refunds list (link_status reflects
    # that the capture lives outside the window).
    refund_nums = {r["order_number"] for r in data["refunds"]}
    assert "PINNED-1" in refund_nums


@pytest.mark.asyncio
async def test_netzero_excluded_from_gross_kept_in_refunds(ctx):
    """SAMEWEEK-1 (Net-Zero) is excluded from gross but its refund
    stays in the refunds total."""
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    data = r.json()
    sys_t = data["system_totals"]
    # Only NORMAL-1 (200) contributes to gross.
    assert sys_t["gross_sales"] == 200.0
    # Refunds = PINNED-1 (100) + SAMEWEEK-1 (60) = 160.
    assert sys_t["total_refunds"] == 160.0


@pytest.mark.asyncio
async def test_forensic_is_read_only_tabby_untouched(ctx, db_cli):
    uid = ctx["uid"]
    before_gl = await db_cli.general_ledger.count_documents(
        {"user_id": uid})
    before_bank = await db_cli.bank_accounts.count_documents(
        {"user_id": uid})
    before_tabby = await db_cli.payment_transactions.find_one(
        {"user_id": uid, "provider": "tabby"})

    requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )

    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == before_gl
    assert await db_cli.bank_accounts.count_documents(
        {"user_id": uid}) == before_bank
    after_tabby = await db_cli.payment_transactions.find_one(
        {"user_id": uid, "provider": "tabby"})
    before_tabby.pop("_id", None); after_tabby.pop("_id", None)
    assert before_tabby == after_tabby
