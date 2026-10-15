"""Iter-251 · Phase 2B — Settlement Engine Generation tests.

Validates:
  • Generation is BLOCKED unless `settlement_engine_enabled` flag is on.
  • dry_run does not persist anything.
  • Salla generation reads from `settlement_entries` and creates one
    period + one invoice + one expected_transfer per settlement_reference.
  • Idempotency: second run reuses the same period/invoice/xfer ids.
  • Rules snapshot is captured on each period & invoice.
  • Cancel transitions invoice & xfer to `cancelled`.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from settlement_engine_routes import make_settlement_engine_router  # noqa: E402
from settlement_engine_generation import generate_for_provider  # noqa: E402


@pytest.fixture
def db():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return c[os.environ["DB_NAME"]]


def _user(uid):
    return {"id": uid, "name": "Tester", "email": f"{uid}@t.local"}


def _router_for(db, uid):
    async def dep():
        return _user(uid)
    return make_settlement_engine_router(db, dep)


def _ep(router, method, path):
    for r in router.routes:
        if r.path.endswith(path) and method in (r.methods or set()):
            return r.endpoint
    raise AssertionError(f"{method} {path} not found")


async def _seed_salla(db, uid):
    """Insert two settlement_entries rows under the same reference."""
    ref = f"SALLA-{uuid.uuid4().hex[:6]}"
    await db.settlement_entries.insert_many([
        {"user_id": uid, "provider": "salla",
         "settlement_reference": ref,
         "settlement_date": "2026-02-10",
         "actual_gross_amount": 1000.0,
         "actual_refund_amount": 50.0,
         "actual_payment_fee": 20.0,
         "actual_payment_vat": 3.0,
         "actual_net_amount": 927.0,
         "event_type": "sale"},
        {"user_id": uid, "provider": "salla",
         "settlement_reference": ref,
         "settlement_date": "2026-02-11",
         "actual_gross_amount": 500.0,
         "actual_refund_amount": 0.0,
         "actual_payment_fee": 10.0,
         "actual_payment_vat": 1.5,
         "actual_net_amount": 488.5,
         "event_type": "sale"},
    ])
    return ref


@pytest.mark.asyncio
async def test_phase2b_generate_disabled_blocks_writes(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    router = _router_for(db, uid)
    from fastapi import HTTPException
    from settlement_engine_routes import GenerateIn
    generate = _ep(router, "POST", "/generate")

    # Default: flag is OFF
    with pytest.raises(HTTPException) as exc:
        await generate(
            GenerateIn(provider="salla", date_from="2026-02-01",
                       date_to="2026-02-28", dry_run=False),
            _user(uid),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_phase2b_dry_run_no_writes(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    await _seed_salla(db, uid)
    res = await generate_for_provider(
        db, uid, _user(uid), "salla",
        "2026-02-01", "2026-02-28", dry_run=True,
    )
    assert res["dry_run"] is True
    assert res["counts"]["periods_new"] == 1
    assert res["counts"]["invoices_new"] == 1
    # Nothing persisted
    assert await db.settlement_periods.count_documents(
        {"user_id": uid}) == 0
    assert await db.settlement_invoices.count_documents(
        {"user_id": uid}) == 0


@pytest.mark.asyncio
async def test_phase2b_salla_generation_persists_and_links(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    ref = await _seed_salla(db, uid)
    res = await generate_for_provider(
        db, uid, _user(uid), "salla",
        "2026-02-01", "2026-02-28", dry_run=False,
    )
    assert res["counts"]["periods_new"] == 1
    assert res["counts"]["invoices_new"] == 1
    assert res["counts"]["expected_transfers_new"] == 1

    p = await db.settlement_periods.find_one(
        {"user_id": uid, "provider": "salla"}, {"_id": 0})
    inv = await db.settlement_invoices.find_one(
        {"user_id": uid, "provider_name": "salla"}, {"_id": 0})
    xfer = await db.expected_transfers.find_one(
        {"user_id": uid, "provider_name": "salla"}, {"_id": 0})

    assert p is not None and inv is not None and xfer is not None
    assert inv["settlement_period_id"] == p["id"]
    assert inv["expected_transfer_id"] == xfer["id"]
    assert xfer["settlement_invoice_id"] == inv["id"]
    assert xfer["settlement_period_id"] == p["id"]
    # Status flow: invoice → waiting_transfer (expected_transfer exists)
    assert inv["status"] == "waiting_transfer"
    assert xfer["status"] == "pending"
    # source_orders_count, refunds_count, expected amounts
    assert inv["source_orders_count"] == 2
    assert inv["provider_reference"] == ref
    assert xfer["expected_amount"] == round(488.5 + 927.0, 2)
    # rules_snapshot captured (per Salla: from settlement_entries)
    assert p["rules_snapshot"]["fee_source"] == "settlement_entries"


@pytest.mark.asyncio
async def test_phase2b_idempotent_second_run(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    await _seed_salla(db, uid)
    r1 = await generate_for_provider(
        db, uid, _user(uid), "salla",
        "2026-02-01", "2026-02-28", dry_run=False)
    inv1 = await db.settlement_invoices.find_one(
        {"user_id": uid}, {"_id": 0, "id": 1})
    r2 = await generate_for_provider(
        db, uid, _user(uid), "salla",
        "2026-02-01", "2026-02-28", dry_run=False)
    inv2 = await db.settlement_invoices.find_one(
        {"user_id": uid}, {"_id": 0, "id": 1})
    assert r1["counts"]["periods_new"] == 1
    assert r2["counts"]["periods_new"] == 0
    assert r2["counts"]["periods_reused"] == 1
    assert inv1["id"] == inv2["id"]


@pytest.mark.asyncio
async def test_phase2b_cancel_invoice(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    await _seed_salla(db, uid)
    await generate_for_provider(
        db, uid, _user(uid), "salla",
        "2026-02-01", "2026-02-28", dry_run=False)
    inv = await db.settlement_invoices.find_one(
        {"user_id": uid}, {"_id": 0})

    from settlement_engine_generation import cancel_invoice
    res = await cancel_invoice(
        db, uid, _user(uid), inv["id"], reason="testing")
    assert res.get("ok") is True
    inv2 = await db.settlement_invoices.find_one(
        {"id": inv["id"]}, {"_id": 0})
    assert inv2["status"] == "cancelled"
    xfer = await db.expected_transfers.find_one(
        {"id": inv["expected_transfer_id"]}, {"_id": 0})
    assert xfer["status"] == "cancelled"


@pytest.mark.asyncio
async def test_phase2b_unknown_provider_returns_rule_source_missing(db):
    uid = f"u_{uuid.uuid4().hex[:6]}"
    res = await generate_for_provider(
        db, uid, _user(uid), "unknown-provider",
        "2026-02-01", "2026-02-28", dry_run=False)
    assert res.get("rule_source_missing") is True
    assert res["generated"]["periods"] == 0
