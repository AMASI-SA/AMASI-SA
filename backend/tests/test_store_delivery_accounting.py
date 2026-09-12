import pytest
from fastapi import HTTPException

mongomock_motor = pytest.importorskip("mongomock_motor")

import store_delivery_accounting as accounting
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
async def test_financial_cutover_gate_rejects_forged_legacy_state_until_p07():
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
            "cutover_at": "2026-08-23T13:00:00+03:00",
            "opening_balance_preview_id": "legacy-preview",
            "opening_balance_preview_balanced": True,
            "opening_balance_approved_at": "2026-08-23T13:00:00+03:00",
            "opening_balance_approved_by": "merchant-1",
            "opening_balance_txn_group_id": "legacy-opening",
        },
    })
    await db.general_ledger.insert_many([
        {
            "user_id": "merchant-1", "txn_group_id": "legacy-opening",
            "entry_type": "opening_balance", "status": "posted",
            "side": "debit", "amount": 100,
            "metadata": {"operation_id": "MZ2-FIN-CUTOVER-001"},
        },
        {
            "user_id": "merchant-1", "txn_group_id": "legacy-opening",
            "entry_type": "opening_balance", "status": "posted",
            "side": "credit", "amount": 100,
            "metadata": {"operation_id": "MZ2-FIN-CUTOVER-001"},
        },
    ])
    assert await financial_cutover_is_active(
        db, user_id="merchant-1", event_at="2026-08-23T13:00:00+03:00",
    ) is False


@pytest.mark.asyncio
async def test_direct_delivery_and_settlement_writers_are_locked_until_p07():
    client = mongomock_motor.AsyncMongoMockClient()
    db = client.test_store_delivery_accounting
    user_id = "merchant-1"

    calls = [
        post_delivery_journal(
            db,
            user_id=user_id,
            actor_id="driver-user-1",
            actor_name="موصل رقم 1",
            driver=_driver(),
            assignment=_assignment(),
            cod_custody_amount=250,
            delivery_fee=20,
        ),
        post_settlement_journal(
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
        ),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "accounting_cutover_not_safe_active"
    assert await db.general_ledger.count_documents({"user_id": user_id}) == 0


@pytest.mark.asyncio
async def test_store_driver_balances_only_include_mz2_operation_rows():
    client = mongomock_motor.AsyncMongoMockClient()
    db = client.test_store_delivery_scoped_balances
    common = {
        "user_id": "merchant-1",
        "entity_type": "store_driver",
        "entity_id": "driver-1",
        "status": "posted",
        "entry_type": "store_delivery_accrual",
    }
    await db.general_ledger.insert_many([
        {
            **common,
            "sub_account": "cod_receivable",
            "side": "debit",
            "amount": 1000,
            "metadata": {"source": "legacy"},
        },
        {
            **common,
            "sub_account": "delivery_fee_payable",
            "side": "credit",
            "amount": 1000,
            "metadata": {"source": "legacy"},
        },
        {
            **common,
            "sub_account": "cod_receivable",
            "side": "debit",
            "amount": 250,
            "metadata": {"operation_id": accounting.OPERATION_ID},
        },
        {
            **common,
            "sub_account": "delivery_fee_payable",
            "side": "credit",
            "amount": 20,
            "metadata": {"operation_id": accounting.OPERATION_ID},
        },
    ])

    assert await store_driver_ledger_balances(
        db, user_id="merchant-1", driver_id="driver-1",
    ) == {
        "cod_receivable": 250.0,
        "delivery_fee_payable": 20.0,
        "net_due_from_driver": 230.0,
        "net_due_to_driver": 0,
        "net_balance": 230.0,
    }


@pytest.mark.asyncio
async def test_settlement_ignores_legacy_balance_and_legacy_idempotency(monkeypatch):
    client = mongomock_motor.AsyncMongoMockClient()
    db = client.test_store_delivery_scoped_settlement
    user_id = "merchant-1"
    idempotency_key = "store_delivery:settlement:settlement-1"
    await db.general_ledger.insert_many([
        {
            "user_id": user_id,
            "entity_type": "store_driver",
            "entity_id": "driver-1",
            "sub_account": "cod_receivable",
            "status": "posted",
            "entry_type": "store_delivery_accrual",
            "side": "debit",
            "amount": 250,
            "metadata": {"source": "legacy"},
        },
        {
            "user_id": user_id,
            "txn_group_id": "legacy-group",
            "status": "posted",
            "entry_type": "store_delivery_settlement",
            "metadata": {"idempotency_key": idempotency_key},
        },
    ])

    async def allow_p07(*_args, **_kwargs):
        return None

    posted = []

    async def fake_post(*_args, **kwargs):
        posted.append(kwargs)
        return {"txn_group_id": "mz2-group"}

    monkeypatch.setattr(accounting, "require_accounting_safe_active", allow_p07)
    monkeypatch.setattr(accounting, "post_txn_group", fake_post)
    call = {
        "user_id": user_id,
        "actor_id": "accountant-1",
        "actor_name": "Accountant",
        "settlement_id": "settlement-1",
        "driver": _driver(),
        "account": {"id": "bank-1", "name": "Bank"},
        "settlement_type": "cod_remittance",
        "bank_amount": 100,
    }

    with pytest.raises(HTTPException) as exc:
        await post_settlement_journal(db, **call)
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "store_driver_settlement_exceeds_ledger_cod",
        "available": 0.0,
    }
    assert posted == []

    await db.general_ledger.insert_one({
        "user_id": user_id,
        "entity_type": "store_driver",
        "entity_id": "driver-1",
        "sub_account": "cod_receivable",
        "status": "posted",
        "entry_type": "store_delivery_accrual",
        "side": "debit",
        "amount": 100,
        "metadata": {"operation_id": accounting.OPERATION_ID},
    })
    result = await post_settlement_journal(db, **call)
    assert result["skipped"] is False
    assert result["txn_group_id"] == "mz2-group"
    assert posted[0]["metadata"]["operation_id"] == accounting.OPERATION_ID
