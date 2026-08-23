import pytest

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


def _driver():
    return {"id": "driver-1", "name": "موصل رقم 1"}


def _assignment():
    return {
        "id": "assignment-1",
        "order_id": "order-1",
        "order_number": "1001",
    }


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
    ) is True


@pytest.mark.asyncio
async def test_driver_delivery_and_net_settlement_reach_ledger_and_financial_position():
    client = mongomock_motor.AsyncMongoMockClient()
    db = client.test_store_delivery_accounting
    user_id = "merchant-1"

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
