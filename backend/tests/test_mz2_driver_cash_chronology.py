"""Real Native cash settlement chronology, with separate noncash responsibility."""
from decimal import Decimal
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_ledger_v2 import read_reporting_entries_v2
from accounting_shipping_native import recognize_cod, accrue_fee, settle, statement
from accounting_shipping_native_setup import save_setup, ensure_shipping_native_indexes
from accounting_shipping_native_contract import EVENTS
from accounting_sales_tax_service import save_policy
from mz2_native_fixture import provision_native_opening
from test_mz2_shipping_native import OWNER, CUT, NoLegacy, courier, rate, source, bind, settlement
from test_mz2_driver_payment_review import driver_delivery
from test_mz2_bank_evidence_adapters import imported


@pytest_asyncio.fixture
async def chronology_db(request):
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("isolated replica-set MZ2_TEST_MONGO_URI required")
    listener = NoLegacy()
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[listener])
    database = client["mz2_driver_cash_chronology_" + uuid4().hex]
    try:
        opening = Decimal(str(getattr(request, "param", "0.00")))
        await database.users.insert_one({"id": OWNER, "role": "owner", "is_active": True})
        await database.store_drivers.insert_one({"id": "driver-f", "user_id": OWNER,
                                                "status": "active", "name": "Synthetic driver"})
        entries = [] if opening == 0 else [
            {"entity_type": "store_driver", "entity_id": "driver-f", "sub_account": "cod_receivable",
             "side": "debit", "amount": format(opening, ".2f")},
            {"entity_type": "equity", "entity_id": "opening_balance_equity", "sub_account": "main",
             "side": "credit", "amount": format(opening, ".2f")},
        ]
        await provision_native_opening(database, owner=OWNER, cutover=CUT,
            bank_balances={"bank-f": "1000.00"}, entries=entries,
            zero_accounts=[("courier", "smsa", "cod_receivable"), ("courier", "smsa", "payable"),
                           ("store_driver", "driver-f", "cod_receivable"),
                           ("store_driver", "driver-f", "delivery_fee_payable")])
        await database.settings.update_one({"user_id": OWNER}, {"$set": {
            "mezan2_financial_cutover.p02_shipping_cod_enabled": True,
            "mezan2_financial_cutover.p02_shipping_cod_activation_ref": "synthetic-isolated-only"}})
        await ensure_shipping_native_indexes(database)
        await save_setup(database, OWNER, OWNER, courier())
        await save_setup(database, OWNER, OWNER, rate(1, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
        await save_policy(database, owner=OWNER, actor_id=OWNER, rate="15", effective_at=CUT,
                          revision=0, reason="Synthetic tax policy")
        await bind(database, "store_driver", "driver-f")
        yield database
        assert listener.accesses == [], "Cash chronology path accessed Legacy storage"
    finally:
        await client.drop_database(database.name)
        client.close()


async def delivery(db, number, method, day, amount="500.00"):
    assignment = await driver_delivery(db, method, number)
    await source(db, number, value=amount)
    at = day + "T12:00:00+00:00"
    await db.store_delivery_assignments.update_one({"user_id": OWNER, "id": assignment},
                                                   {"$set": {"delivered_at": at}})
    await db.store_delivery_collections.update_one({"user_id": OWNER, "assignment_id": assignment},
        {"$set": {"collected_at": at, "amount": amount,
                   "cod_custody_amount": amount if method == "cash" else "0.00"}})
    return await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id=assignment)


async def incoming(db, amount, day, reference):
    return await imported(db, OWNER, "bank-f", value=amount, direction="in", day=day, reference=reference)


async def receive(db, movement):
    return await settle(db, owner=OWNER, actor_id=OWNER,
        payload=settlement(movement["id"], kind="store_driver", identity="driver-f"))


async def snapshot(db):
    return {name: sorted(await db[name].find({}).to_list(None), key=lambda row: str(row.get("_id")))
            for name in await db.list_collection_names()}


async def balance(db):
    return await statement(db, owner=OWNER, actor_id=OWNER, kind="store_driver", identity="driver-f")


@pytest.mark.asyncio
@pytest.mark.parametrize("chronology_db", ["0.00", "500.00"], indirect=True)
@pytest.mark.parametrize("earlier_method", [None, "bank_transfer", "card_terminal"])
async def test_noncash_or_opening_cod_cannot_authorize_cash_before_collection(chronology_db, earlier_method):
    db = chronology_db
    if earlier_method:
        await delivery(db, "early-noncash", earlier_method, "2026-09-03")
    await delivery(db, "later-cash", "cash", "2026-09-05")
    movement = await incoming(db, "200.00", "2026-09-04", "backdated-cash")
    before = await snapshot(db)
    with pytest.raises(HTTPException) as denied:
        await receive(db, movement)
    assert denied.value.status_code == 409
    assert denied.value.detail["code"] == "shipping_settlement_before_liability"
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_valid_later_partial_full_receipts_preserve_noncash_and_fee(chronology_db):
    db = chronology_db
    await delivery(db, "pending-transfer", "bank_transfer", "2026-09-03")
    cash = await delivery(db, "collected-cash", "cash", "2026-09-05")
    await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=cash["evidence_id"])
    for amount, day, expected in (("200.00", "2026-09-06", "800.00"), ("300.00", "2026-09-07", "500.00")):
        movement = await incoming(db, amount, day, "valid-cash-" + day)
        posted = await receive(db, movement)
        assert (await receive(db, movement))["txn_group_id"] == posted["txn_group_id"]
        view = await balance(db)
        assert view["cod_receivable"] == expected
        assert view["payable"] == "20.00"
        rows = await read_reporting_entries_v2(db, user_id=OWNER, effective_before="2030-01-01T00:00:00Z")
        posted_rows = [r for r in rows if r["txn_group_id"] == posted["txn_group_id"]]
        assert len(posted_rows) == 2
        assert {(r["entity_type"], r["side"], r["amount"]) for r in posted_rows} == {
            ("bank", "debit", amount), ("store_driver", "credit", amount)}
    excess = await incoming(db, "0.01", "2026-09-08", "cannot-use-pending-noncash")
    before = await snapshot(db)
    with pytest.raises(HTTPException, match="driver_non_cash_approved_review_required"):
        await receive(db, excess)
    assert await snapshot(db) == before
    assert (await db.store_delivery_payment_reviews.find_one({"assignment_id": "assignment-pending-transfer"}))["status"] == "pending"


@pytest.mark.asyncio
async def test_dated_cash_credits_prevent_reusing_earlier_cash_after_later_collection(chronology_db):
    db = chronology_db
    await delivery(db, "early-noncash", "card_terminal", "2026-09-03")
    await delivery(db, "early-cash", "cash", "2026-09-03")
    await receive(db, await incoming(db, "300.00", "2026-09-04", "early-remittance"))
    await delivery(db, "late-cash", "cash", "2026-09-08")
    await receive(db, await incoming(db, "100.00", "2026-09-09", "later-remittance"))
    # Current cash authority is600, total dated COD is700, but cash available
    # at this remittance date is only500-300=200. Neither may fund500 here.
    too_much = await incoming(db, "500.00", "2026-09-05", "dated-cash-over-receive")
    before = await snapshot(db)
    with pytest.raises(HTTPException, match="shipping_settlement_before_liability"):
        await receive(db, too_much)
    assert await snapshot(db) == before
    exact = await incoming(db, "200.00", "2026-09-05", "dated-cash-exact")
    assert (await receive(db, exact))["state"] == "posted"
    assert (await balance(db))["cod_receivable"] == "900.00"


@pytest.mark.asyncio
async def test_riyadh_start_of_movement_day_remains_before_same_day_noon_collection(chronology_db):
    db = chronology_db
    await delivery(db, "prior-bank", "bank_transfer", "2026-09-03")
    await delivery(db, "same-day-cash", "cash", "2026-09-05")
    movement = await incoming(db, "100.00", "2026-09-05", "same-day-cutoff")
    before = await snapshot(db)
    with pytest.raises(HTTPException, match="shipping_settlement_before_liability"):
        await receive(db, movement)
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_paused_cash_settlement_remains_423_without_mutation(chronology_db):
    db = chronology_db
    await delivery(db, "cash", "cash", "2026-09-03")
    movement = await incoming(db, "100.00", "2026-09-04", "paused-cash")
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    before = await snapshot(db)
    with pytest.raises(HTTPException) as denied:
        await receive(db, movement)
    assert denied.value.status_code == 423
    assert denied.value.detail["code"] == "mz2_writes_paused"
    assert await snapshot(db) == before


@pytest.mark.asyncio
async def test_valid_cash_settlement_still_rolls_back_after_actual_journal(chronology_db, monkeypatch):
    db = chronology_db
    await delivery(db, "cash", "cash", "2026-09-03")
    movement = await incoming(db, "100.00", "2026-09-04", "rollback-cash")
    before = await snapshot(db)
    import accounting_shipping_native as native
    actual_post = native._post

    async def fail_after_journal(*args, **kwargs):
        await actual_post(*args, **kwargs)
        raise RuntimeError("synthetic-after-cash-journal")

    with monkeypatch.context() as patch:
        patch.setattr(native, "_post", fail_after_journal)
        with pytest.raises(RuntimeError, match="synthetic-after-cash-journal"):
            await receive(db, movement)
    assert await snapshot(db) == before
    assert (await receive(db, movement))["state"] == "posted"
    assert await db[EVENTS].count_documents({"kind": "settlement"}) == 1
