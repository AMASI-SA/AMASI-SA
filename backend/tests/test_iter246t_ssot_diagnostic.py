"""Iter-246t — SSOT diagnostic strict-equality tests.

Asserts that the modal compute path (`compute_settlement_for_provider`,
which `import-preview` consumes) and the forensic compute path return
NUMERICALLY IDENTICAL results for the same window, with tolerance
<= 0.01 SAR on every numeric field.

If this test ever fails on production, the diagnostic endpoint
`/api/audit/tamara-ssot-diagnostic` will surface the inferred cause.

Read-only. No writes. Tabby untouched.
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

TOL = 0.01


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


@pytest_asyncio.fixture
async def db_cli():
    c = AsyncIOMotorClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest_asyncio.fixture
async def ctx(db_cli):
    suf = uuid.uuid4().hex[:8]
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "iter246t", "email": f"iter246t-{suf}@x.com",
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
        "invoice_weekdays": ["sunday"], "transfer_weekdays": ["tuesday"],
    })

    # 3 captures with all three flag combinations + 2 refunds.
    await db_cli.payment_transactions.insert_many([
        {"id": f"pin-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-pin-{suf}",
         "order_reference_id": "PIN", "order_number": "PIN",
         "amount": 100.0, "captured_amount": 100.0,
         "refunded_amount": 100.0, "currency": "SAR",
         "status": "fully_refunded",
         "created_at_provider": "2026-05-22T08:00:00Z",
         "effective_settlement_date": "2026-05-23",
         "settlement_source": "settlement_entries_historical",
         "is_pre_accounting": False},
        {"id": f"sw-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-sw-{suf}",
         "order_reference_id": "SW", "order_number": "SW",
         "amount": 60.0, "captured_amount": 60.0,
         "refunded_amount": 60.0, "currency": "SAR",
         "status": "fully_refunded",
         "created_at_provider": "2026-06-08T08:00:00Z",
         "effective_settlement_date": "2026-06-08",
         "settlement_source": "provider_official",
         "same_week_netzero_exclusion": True,
         "is_pre_accounting": False},
        {"id": f"nm-{suf}", "user_id": uid, "provider": "tamara",
         "provider_id": f"pid-nm-{suf}",
         "order_reference_id": "NM", "order_number": "NM",
         "amount": 200.0, "captured_amount": 200.0,
         "refunded_amount": 0.0, "currency": "SAR",
         "status": "fully_captured",
         "created_at_provider": "2026-06-09T08:00:00Z",
         "effective_settlement_date": "2026-06-09",
         "settlement_source": "provider_official",
         "is_pre_accounting": False},
    ])
    await db_cli.payment_refunds.insert_many([
        {"id": f"rfp-{suf}", "user_id": uid, "provider": "tamara",
         "provider_payment_id": f"pid-pin-{suf}",
         "provider_refund_id": f"synthetic:pid-pin-{suf}",
         "order_reference_id": "PIN", "amount": 100.0,
         "currency": "SAR", "status": "fully_refunded",
         "refunded_at": "2026-06-12T20:59:59Z",
         "is_pre_accounting": False},
        {"id": f"rfs-{suf}", "user_id": uid, "provider": "tamara",
         "provider_payment_id": f"pid-sw-{suf}",
         "provider_refund_id": f"synthetic:pid-sw-{suf}",
         "order_reference_id": "SW", "amount": 60.0,
         "currency": "SAR", "status": "fully_refunded",
         "refunded_at": "2026-06-08T10:00:00Z",
         "is_pre_accounting": False},
    ])

    yield {"uid": uid, "token": token}

    await db_cli.payment_transactions.delete_many({"user_id": uid})
    await db_cli.payment_refunds.delete_many({"user_id": uid})
    await db_cli.bnpl_settings.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_diagnostic_endpoint_reports_iter246r(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-ssot-diagnostic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["modal_path"]["engine_version"] == "iter246r"
    assert body["forensic_path"]["engine_version"] == "iter246r"


@pytest.mark.asyncio
async def test_modal_vs_forensic_all_fields_within_tolerance(ctx):
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-ssot-diagnostic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    body = r.json()
    for field, d in body["delta"]["modal_vs_forensic"].items():
        assert abs(d["delta"]) <= TOL, (
            f"SSOT drift on {field!r}: modal={d['a']} forensic={d['b']} "
            f"delta={d['delta']}"
        )


@pytest.mark.asyncio
async def test_modal_gross_matches_only_normal_captures(ctx):
    """With iter246r flags applied, Gross MUST equal sum of NORMAL
    captures only (no historical-pinned, no netzero)."""
    r = requests.get(
        f"{BASE_URL}/api/audit/tamara-ssot-diagnostic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    body = r.json()
    d = body["delta"]["modal_vs_normal_only"]["gross_sales"]
    assert abs(d["delta"]) <= TOL, (
        f"Modal Gross={d['a']} does NOT equal NORMAL-only "
        f"sum={d['b']}. iter246r filters are not applied to the modal "
        f"path. Inferred cause: {body['inferred_cause']}"
    )
    # Sanity: only NORMAL-1 (200) contributes.
    assert body["raw_db_counts"]["normal_counted_in_gross"]["sum"] == 200.0
    assert body["modal_path"]["gross_sales"] == 200.0


@pytest.mark.asyncio
async def test_diagnostic_is_read_only(ctx, db_cli):
    uid = ctx["uid"]
    gl_before = await db_cli.general_ledger.count_documents({"user_id": uid})
    pt_before = await db_cli.payment_transactions.count_documents(
        {"user_id": uid})
    requests.get(
        f"{BASE_URL}/api/audit/tamara-ssot-diagnostic",
        params={"date_from": "2026-06-06", "date_to": "2026-06-12"},
        headers=_h(ctx["token"]),
    )
    assert await db_cli.general_ledger.count_documents(
        {"user_id": uid}) == gl_before
    assert await db_cli.payment_transactions.count_documents(
        {"user_id": uid}) == pt_before
