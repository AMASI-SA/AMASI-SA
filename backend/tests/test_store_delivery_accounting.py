import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

mongomock_motor = pytest.importorskip("mongomock_motor")

from financial_position_ssot import compute_financial_position
from ledger_core import compute_balance
from store_delivery_settlement_routes import make_store_delivery_settlement_router
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


def test_store_delivery_settlement_routes_expose_bank_and_cash_account_picker():
    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    router = make_store_delivery_settlement_router(object(), current_user)
    paths = {
        (route.path, method)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert ("/store-delivery/settlements/accounts", "GET") in paths
    assert ("/store-delivery/settlements/driver/{driver_id}/cod-remittance", "POST") in paths
    assert ("/store-delivery/settlements/driver/{driver_id}/earning-payment", "POST") in paths


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


@pytest.mark.asyncio
@pytest.mark.parametrize("created,code", [
    (None, "order_creation_timestamp_required"),
    ("", "order_creation_timestamp_required"),
    ("invalid", "order_creation_timestamp_invalid"),
    ("2019-12-31T23:59:59Z", "pre_cutover_order"),
])
async def test_driver_delivery_rejects_creation_before_any_journal(created, code):
    db = mongomock_motor.AsyncMongoMockClient().test_creation_fence
    await _activate_p02(db)
    await db.mz2_salla_order_evidence.insert_one({
        "user_id": "merchant-1", "order_number": "1001",
        "order_date_source_text": created,
    })
    with patch("store_delivery_accounting.post_txn_group", new_callable=AsyncMock) as post:
        post.return_value = {"txn_group_id": "must-not-post"}
        with pytest.raises(HTTPException) as result:
            await post_delivery_journal(
                db, user_id="merchant-1", actor_id="driver-user-1", actor_name="driver",
                driver=_driver(), assignment=_assignment(),
                cod_custody_amount=250, delivery_fee=20,
            )
        assert result.value.detail["code"] == code
        post.assert_not_awaited()
    assert await db.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_driver_delivery_cannot_trust_conflicted_creation_evidence():
    db = mongomock_motor.AsyncMongoMockClient().test_creation_conflict
    await _activate_p02(db)
    await db.mz2_salla_order_evidence.insert_one({
        "user_id": "merchant-1", "order_number": "1001",
        "order_date_source_text": "2020-01-02T00:00:00Z", "conflict": True,
    })
    with patch("store_delivery_accounting.post_txn_group", new_callable=AsyncMock) as post:
        with pytest.raises(HTTPException) as denied:
            await post_delivery_journal(db, user_id="merchant-1", actor_id="driver-user-1",
                actor_name="driver", driver=_driver(), assignment=_assignment(),
                cod_custody_amount=250, delivery_fee=20)
        assert denied.value.detail["code"] == "order_evidence_conflict"
        post.assert_not_awaited()


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
@pytest.mark.parametrize("created", ["2020-01-01T00:00:00Z", "2020-01-02 03:00:00"])
async def test_driver_delivery_and_net_settlement_reach_ledger_and_financial_position(
    transactional_db, created,
):
    db = transactional_db
    user_id = "merchant-1"
    # Explicitly enable this isolated successful-write fixture.
    await db.mz2_atomic_owners.insert_one({
        "_id": user_id, "revision": 0, "writes_paused": False, "control_revision": 0,
    })
    await _activate_p02(db, user_id)
    await db.mz2_salla_order_evidence.insert_one({
        "user_id": user_id, "order_number": "1001", "order_date_source_text": created,
    })

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


@pytest.mark.asyncio
async def test_delivery_creation_is_tenant_scoped_and_rechecked_inside_transaction(transactional_db):
    from accounting_atomic import atomic_owner
    db = transactional_db
    await _activate_p02(db)
    await db.mz2_atomic_owners.insert_one({"_id": "merchant-1", "revision": 0, "writes_paused": False})
    await db.mz2_salla_order_evidence.insert_one({
        "user_id": "other-merchant", "order_number": "1001", "order_date_source_text": "2020-01-02T00:00:00Z",
    })
    kwargs = dict(user_id="merchant-1", actor_id="driver-user-1", actor_name="driver",
        driver=_driver(), assignment=_assignment(), cod_custody_amount=250, delivery_fee=20)
    with pytest.raises(HTTPException) as denied:
        await post_delivery_journal(db, **kwargs)
    assert denied.value.detail["code"] == "unique_order_creation_evidence_required"
    await db.mz2_salla_order_evidence.insert_one({
        "user_id": "merchant-1", "order_number": "1001", "order_date_source_text": "2020-01-02T00:00:00Z",
    })
    async def evidence_changes_before_transaction(database, owner, callback):
        await database.mz2_salla_order_evidence.update_one({"user_id": owner},
            {"$set": {"order_date_source_text": "2019-12-31T23:59:59Z"}})
        return await atomic_owner(database, owner, callback)
    with patch("accounting_atomic.atomic_owner", side_effect=evidence_changes_before_transaction):
        with pytest.raises(HTTPException) as denied:
            await post_delivery_journal(db, **kwargs)
    assert denied.value.detail["code"] == "pre_cutover_order"
    assert await db.general_ledger.count_documents({}) == 0
    assert (await db.mz2_atomic_owners.find_one({"_id": "merchant-1"}))["revision"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("created,code", [
    (None, "order_creation_timestamp_required"),
    ("invalid", "order_creation_timestamp_invalid"),
    ("2019-12-31T23:59:59Z", "pre_cutover_order"),
])
async def test_driver_http_requires_cash_confirmation_without_touching_accounting(created, code):
    from store_delivery_driver_app_routes import (
        make_store_delivery_driver_app_router, DRIVER_EARNINGS, DRIVER_COLLECTIONS,
        DRIVER_PAYMENT_REVIEWS, ASSIGNMENTS, ORDERS, WORKFLOWS, STORE_DRIVERS,
    )
    db = mongomock_motor.AsyncMongoMockClient().test_http_creation_fence
    await _activate_p02(db)
    actor = {"id": "driver-user-1", "role": "store_driver", "created_by": "merchant-1", "_session_client": "amasi_mobile"}
    await db[STORE_DRIVERS].insert_one({**_driver(), "user_id": "merchant-1",
        "account_user_id": actor["id"], "status": "active"})
    await db[ASSIGNMENTS].insert_one({**_assignment(), "user_id": "merchant-1", "driver_id": "driver-1",
        "active": True, "status": "out_for_delivery", "delivery_fee_snapshot": 20})
    await db[ORDERS].insert_one({"user_id": "merchant-1", "order_id": "order-1",
        "order_number": "1001", "remaining_amount": 250})
    await db.mz2_salla_order_evidence.insert_one({
        "user_id": "merchant-1", "order_number": "1001", "order_date_source_text": created,
    })
    names = [DRIVER_EARNINGS, DRIVER_COLLECTIONS, DRIVER_PAYMENT_REVIEWS, ASSIGNMENTS, ORDERS, WORKFLOWS, "general_ledger"]
    before = {name: await db[name].find({}).to_list(None) for name in names}
    app = FastAPI()
    async def current_user():
        return actor
    app.include_router(make_store_delivery_driver_app_router(db, current_user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
        for _ in range(2):
            response = await client.post("/store-delivery/app/deliveries/status", json={
                "barcode": "1001", "target_status": "delivered", "payment_method": "cash"})
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "driver_physical_cash_confirmation_required"
            assert {name: await db[name].find({}).to_list(None) for name in names} == before


def test_driver_status_schema_keeps_delivery_proof_target_specific():
    from store_delivery_driver_app_routes import DriverStatusUpdate

    payload = DriverStatusUpdate(
        barcode="1001",
        target_status="out_for_delivery",
    )
    assert payload.delivery_proof_reference is None
    assert payload.conversation_evidence_reference is None
