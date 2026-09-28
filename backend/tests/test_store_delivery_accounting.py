import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

mongomock_motor = pytest.importorskip("mongomock_motor")

from financial_position_ssot import compute_financial_position
from ledger_core import compute_balance
from store_delivery_accounting import (
    delivery_journal_entries,
    financial_cutover_is_active,
    post_delivery_journal,
    post_settlement_journal,
    store_driver_ledger_balances,
)


@pytest_asyncio.fixture
async def transactional_db():
    """Use a disposable replica-set database for the financial writer path."""
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI is required for transactional delivery accounting")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database_name = f"store_delivery_accounting_{uuid.uuid4().hex}"
    db = client[database_name]
    try:
        hello = await db.command("hello")
        if not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
            pytest.skip("transactional Mongo replica set is required")
        yield db
    finally:
        await client.drop_database(database_name)
        client.close()


def _driver():
    return {"id": "driver-1", "name": "موصل رقم 1"}


def _assignment():
    return {
        "id": "assignment-1",
        "order_id": "order-1",
        "order_number": "1001",
    }


async def _activate_p02(db, user_id="merchant-1"):
    await db.settings.update_one(
        {"user_id": user_id},
        {"$set": {"mezan2_financial_cutover": {
            "operation_id": "MZ2-FIN-CUTOVER-001",
            "status": "active",
            "cutover_at": "2020-01-01T00:00:00Z",
            "p02_shipping_cod_enabled": True,
            "p02_shipping_cod_activation_ref": "SYN-P02-EXPLICIT-ACTIVATION",
        }}},
        upsert=True,
    )


def test_delivery_entries_keep_cod_and_driver_fee_as_separate_balanced_legs():
    entries = delivery_journal_entries(cod_custody_amount=250, delivery_fee=20)
    assert len(entries) == 4
    assert sum(row["amount"] for row in entries if row["side"] == "debit") == 270
    assert sum(row["amount"] for row in entries if row["side"] == "credit") == 270
    assert any(
        row["entity_type"] == "store_driver"
        and row["sub_account"] == "cod_receivable"
        and row["side"] == "debit"
        and row["amount"] == 250
        for row in entries
    )
    assert any(
        row["entity_type"] == "store_driver"
        and row["sub_account"] == "delivery_fee_payable"
        and row["side"] == "credit"
        and row["amount"] == 20
        for row in entries
    )


def test_prepaid_delivery_posts_only_the_individual_driver_fee():
    entries = delivery_journal_entries(cod_custody_amount=0, delivery_fee=18)
    assert [(row["entity_type"], row["side"], row["amount"]) for row in entries] == [
        ("expense", "debit", 18.0),
        ("store_driver", "credit", 18.0),
    ]


@pytest.mark.asyncio
async def test_financial_cutover_gate_fails_closed_until_operation_and_timestamp_are_approved():
    client = mongomock_motor.AsyncMongoMockClient()
    db = client.test_store_delivery_cutover_gate
    assert await financial_cutover_is_active(
        db, user_id="merchant-1", event_at="2026-08-23T12:00:00+03:00",
    ) is False

    await db.settings.insert_one({
        "user_id": "merchant-1",
        "mezan2_financial_cutover": {
            "operation_id": "MZ2-FIN-CUTOVER-001",
            "status": "active",
        },
    })
    assert await financial_cutover_is_active(
        db, user_id="merchant-1", event_at="2026-08-23T12:00:00+03:00",
    ) is False

    await db.settings.update_one(
        {"user_id": "merchant-1"},
        {"$set": {"mezan2_financial_cutover.cutover_at": "2026-08-23T13:00:00+03:00"}},
    )
    assert await financial_cutover_is_active(
        db, user_id="merchant-1", event_at="2026-08-23T12:00:00+03:00",
    ) is False
    assert await financial_cutover_is_active(
        db, user_id="merchant-1", event_at="2026-08-23T13:00:00+03:00",
    ) is False

    await db.settings.update_one(
        {"user_id": "merchant-1"},
        {"$set": {"mezan2_financial_cutover.p02_shipping_cod_enabled": True}},
    )
    assert await financial_cutover_is_active(
        db, user_id="merchant-1", event_at="2026-08-23T13:00:00+03:00",
    ) is False

    await db.settings.update_one(
        {"user_id": "merchant-1"},
        {"$set": {"mezan2_financial_cutover.p02_shipping_cod_activation_ref": "SYN-P02-ACTIVATION"}},
    )
    assert await financial_cutover_is_active(
        db, user_id="merchant-1", event_at="2026-08-23T13:00:00+03:00",
    ) is True


@pytest.mark.asyncio
async def test_p02_financial_writers_are_locked_by_default_without_ledger_delta():
    client = mongomock_motor.AsyncMongoMockClient()
    db = client.test_store_delivery_p02_locked
    user_id = "merchant-1"

    with pytest.raises(HTTPException) as delivery:
        await post_delivery_journal(
            db,
            user_id=user_id,
            actor_id="driver-user-1",
            actor_name="موصل رقم 1",
            driver=_driver(),
            assignment=_assignment(),
            cod_custody_amount=250,
            delivery_fee=20,
        )
    assert delivery.value.status_code == 423
    assert delivery.value.detail["code"] == "p02_shipping_cod_locked"
    assert await db.general_ledger.count_documents({}) == 0

    with pytest.raises(HTTPException) as settlement:
        await post_settlement_journal(
            db,
            user_id=user_id,
            actor_id="accountant-1",
            actor_name="المحاسب",
            settlement_id="settlement-locked",
            driver=_driver(),
            account={"id": "bank-1", "name": "الإنماء"},
            settlement_type="net_settlement",
            bank_amount=230,
            earning_offset=20,
        )
    assert settlement.value.status_code == 423
    assert settlement.value.detail["code"] == "p02_shipping_cod_locked"
    assert await db.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_driver_delivery_and_net_settlement_reach_ledger_and_financial_position(
    transactional_db,
):
    db = transactional_db
    user_id = "merchant-1"
    await _activate_p02(db, user_id)

    first = await post_delivery_journal(
        db,
        user_id=user_id,
        actor_id="driver-user-1",
        actor_name="موصل رقم 1",
        driver=_driver(),
        assignment=_assignment(),
        cod_custody_amount=250,
        delivery_fee=20,
    )
    duplicate = await post_delivery_journal(
        db,
        user_id=user_id,
        actor_id="driver-user-1",
        actor_name="موصل رقم 1",
        driver=_driver(),
        assignment=_assignment(),
        cod_custody_amount=250,
        delivery_fee=20,
    )
    assert first["txn_group_id"]
    assert duplicate == {
        "ok": True,
        "skipped": True,
        "reason": "idempotent_duplicate",
        "txn_group_id": first["txn_group_id"],
    }
    assert await db.general_ledger.count_documents({"user_id": user_id}) == 4

    balances = await store_driver_ledger_balances(
        db, user_id=user_id, driver_id="driver-1",
    )
    assert balances == {
        "cod_receivable": 250.0,
        "delivery_fee_payable": 20.0,
        "net_due_from_driver": 230.0,
        "net_due_to_driver": 0.0,
        "net_balance": 230.0,
    }

    position = await compute_financial_position(db, user_id)
    assert position["assets"]["store_driver_cod_receivable"] == 250.0
    assert position["liabilities"]["store_driver_payable"] == 20.0

    await db.accounts.insert_one({
        "id": "bank-1",
        "user_id": user_id,
        "name": "الإنماء",
        "account_type": "bank",
        "status": "active",
        "current_balance": 0.0,
    })
    settlement = await post_settlement_journal(
        db,
        user_id=user_id,
        actor_id="accountant-1",
        actor_name="المحاسب",
        settlement_id="settlement-1",
        driver=_driver(),
        account={"id": "bank-1", "name": "الإنماء"},
        settlement_type="net_settlement",
        bank_amount=230,
        earning_offset=20,
    )
    assert settlement["cod_settled_amount"] == 250.0
    assert settlement["delivery_fee_settled_amount"] == 20.0
    assert await store_driver_ledger_balances(
        db, user_id=user_id, driver_id="driver-1",
    ) == {
        "cod_receivable": 0.0,
        "delivery_fee_payable": 0.0,
        "net_due_from_driver": 0.0,
        "net_due_to_driver": 0.0,
        "net_balance": 0.0,
    }
    bank = await compute_balance(
        db,
        user_id=user_id,
        entity_type="bank",
        entity_id="bank-1",
        sub_account="main",
    )
    assert bank["net_balance"] == 230.0
