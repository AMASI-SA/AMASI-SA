"""Iter-246q — Tamara Apply (Dry-Run + Gated Execute) regression tests.

Verifies:
  * Apply Dry-Run is READ-ONLY (zero writes to Mongo).
  * Apply Dry-Run produces an accurate plan with all 4 decisions
    (refunded_at_override, exclude list, Same-Week Net-Zero, Fix #3
    pinning).
  * Execute endpoint REJECTS without a valid confirm_token (403).
  * Execute endpoint with valid confirm_token applies the writes
    correctly and is IDEMPOTENT (re-run inserts 0 new refunds).
  * Execute endpoint NEVER touches general_ledger / bank_accounts.
  * Same-Week Net-Zero Exclusion correctly flags the matching txn.
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

OVERRIDE = "2026-06-12T20:59:59Z"


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
        json={"name": "iter246q", "email": f"iter246q-{suf}@x.com",
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

    # Seed scenario mirroring the merchant's production case:
    #  - OLD-1: in window now but historical settlement in MAY (Fix #3
    #    + Fix #1 candidate because status=fully_captured locally)
    #  - SYNTH-1: has synthesised refund with refunded_at = capture
    #    date (Fix #2 candidate)
    #  - SAMEWEEK-1: captured + refunded both inside window (Net-Zero)
    #  - SKIP-1: would be Fix #1 but explicitly excluded by merchant
    await db_cli.payment_transactions.insert_many([
        {"id": f"old-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-old-{suf}",
         "order_reference_id": "OLD-1", "order_number": "OLD-1",
         "amount": 100.0, "captured_amount": 100.0,
         "refunded_amount": 0.0, "currency": "SAR",
         "status": "fully_captured",
         "created_at_provider": "2026-05-22T08:00:00Z",
         "effective_settlement_date": "2026-06-08",
         "settlement_source": "provider_official",
         "is_pre_accounting": False},
        {"id": f"synth-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-synth-{suf}",
         "order_reference_id": "SYNTH-1", "order_number": "SYNTH-1",
         "amount": 200.0, "captured_amount": 200.0,
         "refunded_amount": 200.0, "currency": "SAR",
         "status": "fully_refunded",
         "created_at_provider": "2026-05-25T08:00:00Z",
         "effective_settlement_date": "2026-06-09",
         "settlement_source": "provider_official",
         "is_pre_accounting": False},
        {"id": f"sameweek-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-sw-{suf}",
         "order_reference_id": "SAMEWEEK-1",
         "order_number": "SAMEWEEK-1",
         "amount": 50.0, "captured_amount": 50.0,
         "refunded_amount": 50.0, "currency": "SAR",
         "status": "fully_refunded",
         "created_at_provider": "2026-06-07T08:00:00Z",
         "effective_settlement_date": "2026-06-07",
         "settlement_source": "provider_official",
         "is_pre_accounting": False},
        {"id": f"skip-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-skip-{suf}",
         "order_reference_id": "SKIP-1", "order_number": "SKIP-1",
         "amount": 70.0, "captured_amount": 70.0,
         "refunded_amount": 0.0, "currency": "SAR",
         "status": "fully_captured",
         "created_at_provider": "2026-06-10T08:00:00Z",
         "effective_settlement_date": "2026-06-10",
         "settlement_source": "provider_official",
         "is_pre_accounting": False},
    ])

    # Historical settlement for OLD-1 (Fix #3 + Fix #1), SKIP-1 (Fix
    # #1 candidate if not excluded) and SYNTH-1 (Fix #3 + Fix #2 —
    # the merchant's real-world pattern requires the synthesised
    # refund's order to also be in Fix #3 because that's how the apply
    # determines which refunds to repoint to the override timestamp).
    await db_cli.settlement_entries.insert_many([
        {"user_id": uid, "provider": "tamara",
         "order_number": "OLD-1", "event_type": "sale",
         "settlement_date": "2026-05-23",
         "actual_gross_amount": 100.0, "actual_payment_fee": 6.99,
         "actual_payment_vat": 1.05, "actual_net_amount": 91.96,
         "currency": "SAR", "file_hash": "fh-old",
         "created_at": datetime.now(timezone.utc),
         "is_pre_accounting": False},
        {"user_id": uid, "provider": "tamara",
         "order_number": "SKIP-1", "event_type": "sale",
         "settlement_date": "2026-06-04",
         "actual_gross_amount": 70.0, "actual_payment_fee": 4.89,
         "actual_payment_vat": 0.73, "actual_net_amount": 64.38,
         "currency": "SAR", "file_hash": "fh-skip",
         "created_at": datetime.now(timezone.utc),
         "is_pre_accounting": False},
        {"user_id": uid, "provider": "tamara",
         "order_number": "SYNTH-1", "event_type": "sale",
         "settlement_date": "2026-05-29",
         "actual_gross_amount": 200.0, "actual_payment_fee": 13.98,
         "actual_payment_vat": 2.10, "actual_net_amount": 183.92,
         "currency": "SAR", "file_hash": "fh-synth",
         "created_at": datetime.now(timezone.utc),
         "is_pre_accounting": False},
    ])

    # Synthesised refund for SYNTH-1 (Fix #2 candidate) and a
    # correct-in-window refund for SAMEWEEK-1 (drives net-zero rule).
    await db_cli.payment_refunds.insert_many([
        {"id": f"rf-synth-{suf}", "user_id": uid, "provider": "tamara",
         "provider_payment_id": f"pid-synth-{suf}",
         "provider_refund_id": f"synthetic:pid-synth-{suf}",
         "order_reference_id": "SYNTH-1", "amount": 200.0,
         "currency": "SAR", "status": "fully_refunded",
         "refunded_at": "2026-05-25T08:00:00Z",
         "synthesised": True, "is_pre_accounting": False},
        {"id": f"rf-sw-{suf}", "user_id": uid, "provider": "tamara",
         "provider_payment_id": f"pid-sw-{suf}",
         "provider_refund_id": f"synthetic:pid-sw-{suf}",
         "order_reference_id": "SAMEWEEK-1", "amount": 50.0,
         "currency": "SAR", "status": "fully_refunded",
         "refunded_at": "2026-06-07T10:00:00Z",
         "synthesised": True, "is_pre_accounting": False},
    ])

    yield {"uid": uid, "token": token, "suf": suf}

    await db_cli.payment_transactions.delete_many({"user_id": uid})
    await db_cli.payment_refunds.delete_many({"user_id": uid})
    await db_cli.settlement_entries.delete_many({"user_id": uid})
    await db_cli.bnpl_settings.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_dryrun_is_read_only_and_returns_full_plan(ctx, db_cli):
    uid = ctx["uid"]
    before_pt = await db_cli.payment_transactions.count_documents(
        {"user_id": uid})
    before_pr = await db_cli.payment_refunds.count_documents(
        {"user_id": uid})

    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-apply-dryrun",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "refunded_at_override": OVERRIDE,
                "exclude_order_numbers": "SKIP-1"},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["read_only"] is True
    assert data["dry_run"] is True
    assert data["decisions"]["refunded_at_override"] == OVERRIDE
    assert data["decisions"]["excluded_from_apply"] == ["SKIP-1"]

    # Fix #3 must include OLD-1 + SYNTH-1 (both have historical
    # settlement_entries earlier than current effective_settlement_date).
    f3 = data["fix_3_pin_settlement_date"]
    assert f3["count"] == 2
    pinned = {row["order_number"] for row in f3["rows"]}
    assert pinned == {"OLD-1", "SYNTH-1"}

    # Fix #1 must include OLD-1 (captured + historical entry +
    # status=fully_captured); SKIP-1 excluded.
    f1 = data["fix_1_resync"]
    f1_orders = {row["order_number"] for row in f1["rows"]}
    assert "OLD-1" in f1_orders
    assert "SKIP-1" not in f1_orders

    # Fix #2 must propose correcting SYNTH-1's refunded_at.
    f2 = data["fix_2_refunded_at_correction"]
    f2_orders = {row["order_number"] for row in f2["rows"]}
    assert "SYNTH-1" in f2_orders
    assert f2["rows"][0]["after_refunded_at"] == OVERRIDE

    # Same-Week Net-Zero must flag SAMEWEEK-1.
    nz = data["same_week_netzero_exclusion"]
    nz_orders = {row["order_number"] for row in nz["rows"]}
    assert "SAMEWEEK-1" in nz_orders

    # READ-ONLY assertion.
    assert await db_cli.payment_transactions.count_documents(
        {"user_id": uid}) == before_pt
    assert await db_cli.payment_refunds.count_documents(
        {"user_id": uid}) == before_pr
    # confirm_token must be present and non-empty.
    assert data["confirm_token_to_pass_to_execute"]


@pytest.mark.asyncio
async def test_execute_rejects_without_valid_confirm_token(ctx, db_cli):
    uid = ctx["uid"]
    before_pt = await db_cli.payment_transactions.count_documents(
        {"user_id": uid})

    r = requests.post(
        f"{BASE_URL}/api/admin/tamara-apply-execute",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "refunded_at_override": OVERRIDE,
                "confirm_token": "WRONG"},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 403, r.text
    # No writes happened.
    assert await db_cli.payment_transactions.count_documents(
        {"user_id": uid}) == before_pt


@pytest.mark.asyncio
async def test_execute_applies_all_fixes_idempotently(ctx, db_cli):
    uid = ctx["uid"]
    # 1) Get the dry-run + token.
    r1 = requests.get(
        f"{BASE_URL}/api/audit/tamara-apply-dryrun",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "refunded_at_override": OVERRIDE,
                "exclude_order_numbers": "SKIP-1"},
        headers=_h(ctx["token"]),
    )
    token = r1.json()["confirm_token_to_pass_to_execute"]

    # Baselines.
    before_gl = await db_cli.general_ledger.count_documents(
        {"user_id": uid})
    before_bank = await db_cli.bank_accounts.count_documents(
        {"user_id": uid})

    # 2) Execute.
    r2 = requests.post(
        f"{BASE_URL}/api/admin/tamara-apply-execute",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "refunded_at_override": OVERRIDE,
                "exclude_order_numbers": "SKIP-1",
                "confirm_token": token},
        headers=_h(ctx["token"]),
    )
    assert r2.status_code == 200, r2.text
    applied = r2.json()["applied"]
    assert applied["fix3_repinned_txns"] == 2   # OLD-1 + SYNTH-1
    assert applied["fix1_updated_txns"] == 1    # OLD-1 status updated
    assert applied["fix1_inserted_refunds"] == 1
    assert applied["fix2_updated_refunds"] == 1
    assert applied["netzero_flagged_txns"] == 1

    # Verify Fix #3 effective_settlement_date now = 2026-05-23.
    old1 = await db_cli.payment_transactions.find_one(
        {"user_id": uid, "order_number": "OLD-1"})
    assert old1["effective_settlement_date"] == "2026-05-23"
    assert old1["settlement_source"] == "settlement_entries_historical"
    assert old1["status"] == "fully_refunded"
    # Verify Fix #1 refund row created with override timestamp.
    new_rf = await db_cli.payment_refunds.find_one(
        {"user_id": uid,
         "provider_refund_id": f"synthetic:pid-old-{ctx['suf']}"})
    assert new_rf is not None
    assert new_rf["refunded_at"] == OVERRIDE
    assert new_rf["amount"] == 100.0
    # Verify Fix #2 corrected the SYNTH-1 refund row.
    synth_rf = await db_cli.payment_refunds.find_one(
        {"user_id": uid,
         "provider_refund_id": f"synthetic:pid-synth-{ctx['suf']}"})
    assert synth_rf["refunded_at"] == OVERRIDE
    # Verify Net-Zero flag on SAMEWEEK-1.
    sw = await db_cli.payment_transactions.find_one(
        {"user_id": uid, "order_number": "SAMEWEEK-1"})
    assert sw["same_week_netzero_exclusion"] is True

    # Verify NO writes to general_ledger / bank_accounts.
    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == before_gl
    assert await db_cli.bank_accounts.count_documents(
        {"user_id": uid}) == before_bank

    # 3) Re-execute → must be idempotent.
    r3 = requests.post(
        f"{BASE_URL}/api/admin/tamara-apply-execute",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "refunded_at_override": OVERRIDE,
                "exclude_order_numbers": "SKIP-1",
                "confirm_token": token},
        headers=_h(ctx["token"]),
    )
    assert r3.status_code == 200
    applied2 = r3.json()["applied"]
    # Already-applied: 0 new inserts, the updates are noop because
    # the docs already have the new values.
    assert applied2["fix1_inserted_refunds"] == 0
    # update_one with $set on identical values yields modified_count=0
    # in MongoDB, so all update counters should be 0 on re-run.
    assert applied2["fix3_repinned_txns"] == 0
    assert applied2["fix1_updated_txns"] == 0
    assert applied2["fix2_updated_refunds"] == 0


@pytest.mark.asyncio
async def test_netzero_excludes_gross_only_keeps_refund(ctx):
    """Pin the merchant-approved Net-Zero policy:
       - Same-week capture+refund order is REMOVED from gross.
       - Its refund STAYS in the Refunds total (mirrors Tamara
         invoice convention).
    Validates SAMEWEEK-1 (50.0) is netzero-flagged but its 50.0
    refund is included in `simulated_forensic_compute.refunds`."""
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-apply-dryrun",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "refunded_at_override": OVERRIDE,
                "exclude_order_numbers": "SKIP-1"},
        headers=_h(ctx["token"]),
    )
    data = r.json()
    nz = data["same_week_netzero_exclusion"]
    assert {row["order_number"] for row in nz["rows"]} == {"SAMEWEEK-1"}
    # SAMEWEEK-1 (50.0) should NOT be in gross orders.
    # Remaining gross txns: none — OLD-1 + SYNTH-1 pinned out via
    # Fix #3, SKIP-1 excluded.
    sim = data["simulated_forensic_compute"]
    # Refunds include: Fix #1 OLD-1 (100) + Fix #2 SYNTH-1 (200)
    # + SAMEWEEK-1 existing refund (50) = 350.0
    assert sim["refunds"] == 350.0
    # Gross excludes SAMEWEEK-1 (Net-Zero) and pinned orders, but
    # SKIP-1 (70) stays as it's excluded from all fixes.
    assert sim["gross_sales"] == 70.0
    assert sim["orders_count"] == 1


@pytest.mark.asyncio
async def test_tabby_untouched_by_apply(ctx, db_cli):
    uid = ctx["uid"]
    # Seed a Tabby txn to prove it isn't touched.
    await db_cli.payment_transactions.insert_one({
        "id": f"tabby-untouched-{ctx['suf']}", "user_id": uid,
        "provider": "tabby", "provider_id": "tabby-untouched",
        "order_number": "TABBY-1", "amount": 999.99,
        "status": "fully_captured",
        "effective_settlement_date": "2026-06-09",
        "is_pre_accounting": False,
    })
    before = await db_cli.payment_transactions.find_one(
        {"user_id": uid, "provider": "tabby", "order_number": "TABBY-1"})

    r1 = requests.get(
        f"{BASE_URL}/api/audit/tamara-apply-dryrun",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "refunded_at_override": OVERRIDE,
                "exclude_order_numbers": "SKIP-1"},
        headers=_h(ctx["token"]),
    )
    token = r1.json()["confirm_token_to_pass_to_execute"]
    requests.post(
        f"{BASE_URL}/api/admin/tamara-apply-execute",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12",
                "refunded_at_override": OVERRIDE,
                "exclude_order_numbers": "SKIP-1",
                "confirm_token": token},
        headers=_h(ctx["token"]),
    )
    after = await db_cli.payment_transactions.find_one(
        {"user_id": uid, "provider": "tabby", "order_number": "TABBY-1"})
    # Strip Mongo's auto _id to compare cleanly.
    before.pop("_id", None); after.pop("_id", None)
    assert before == after, "Tabby txn was modified!"
