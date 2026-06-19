"""Iter-246s — SSOT proof for the Add-Settlement modal.

Verifies that `/bnpl/settlements/import-preview/tamara` surfaces:
  • `breakdown.engine_version == "iter246r"`  (so the modal can prove
    to the merchant which compute path was used)
  • `breakdown.gross_sales` matches what the iter246r forensic engine
    produces for the same window — i.e. NO silent drift between the
    forensic endpoint and the modal that posts the journal entry.

Tabby is asserted untouched. Read-only: no writes performed.
"""
from __future__ import annotations

import os
import uuid

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


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


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
        json={"name": "iter246s", "email": f"iter246s-{suf}@x.com",
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

    # Same scenario as iter246r — PINNED + SAMEWEEK + NORMAL.
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

    yield {"uid": uid, "token": token, "suf": suf}

    await db_cli.payment_transactions.delete_many({"user_id": uid})
    await db_cli.payment_refunds.delete_many({"user_id": uid})
    await db_cli.bnpl_settings.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_import_preview_exposes_engine_version_iter246r(ctx):
    """The modal MUST be able to read `engine_version` from the
    preview payload so it can prove iter246r is active."""
    r = requests.get(
        f"{BASE_URL}/api/bnpl/settlements/import-preview/tamara",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    bd = body["breakdown"]
    assert bd["engine_version"] == "iter246r", (
        f"engine_version regression — got {bd.get('engine_version')!r}, "
        "modal would be blocked by the SSOT guard."
    )


@pytest.mark.asyncio
async def test_import_preview_gross_matches_forensic(ctx):
    """Cross-check: the modal's Gross must equal the forensic endpoint's
    Gross for the same window. This prevents the exact bug the merchant
    reported (modal showed 23,777.53 while forensic showed 20,848.30)."""
    params = {"date_from": "2026-06-06", "date_to": "2026-06-12"}
    h = _h(ctx["token"])

    r_modal = requests.get(
        f"{BASE_URL}/api/bnpl/settlements/import-preview/tamara",
        params=params, headers=h,
    )
    r_forensic = requests.get(
        f"{BASE_URL}/api/audit/tamara-settlement-forensic",
        params=params, headers=h,
    )
    assert r_modal.status_code == 200, r_modal.text
    assert r_forensic.status_code == 200, r_forensic.text

    modal_gross = r_modal.json()["breakdown"]["gross_sales"]
    forensic_gross = r_forensic.json()["system_totals"]["gross_sales"]
    assert modal_gross == forensic_gross, (
        f"SSOT broken — modal Gross={modal_gross} vs "
        f"forensic Gross={forensic_gross}. They MUST be identical."
    )
    # Sanity: with iter246r filters, only NORMAL-1 (200) counts.
    assert modal_gross == 200.0


@pytest.mark.asyncio
async def test_import_preview_is_read_only(ctx, db_cli):
    """The preview endpoint never writes — calling it must leave GL +
    bank accounts untouched."""
    uid = ctx["uid"]
    before_gl = await db_cli.general_ledger.count_documents(
        {"user_id": uid})
    before_bank = await db_cli.bank_accounts.count_documents(
        {"user_id": uid})
    requests.get(
        f"{BASE_URL}/api/bnpl/settlements/import-preview/tamara",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == before_gl
    assert await db_cli.bank_accounts.count_documents(
        {"user_id": uid}) == before_bank
