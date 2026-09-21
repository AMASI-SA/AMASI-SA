import pytest
from pathlib import Path
from fastapi import HTTPException

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
async def test_explicit_mz2_p02_disables_legacy_financial_writers():
    client = mongomock_motor.AsyncMongoMockClient()
    db = client.test_store_delivery_legacy_disabled
    user_id = "merchant-1"
    await _activate_p02(db, user_id)

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
    assert delivery.value.status_code == 409
    assert delivery.value.detail["code"] == "legacy_store_delivery_financial_writer_disabled"

    with pytest.raises(HTTPException) as settlement:
        await post_settlement_journal(
            db,
            user_id=user_id,
            actor_id="accountant-1",
            actor_name="المحاسب",
            settlement_id="settlement-old-path",
            driver=_driver(),
            account={"id": "bank-1", "name": "الإنماء"},
            settlement_type="net_settlement",
            bank_amount=230,
            earning_offset=20,
        )
    assert settlement.value.status_code == 409
    assert settlement.value.detail["code"] == "legacy_store_delivery_financial_writer_disabled"
    assert await db.general_ledger.count_documents({}) == 0


def test_runtime_driver_route_uses_native_mz2_writer_not_legacy_delivery_journal():
    root = Path(__file__).resolve().parents[1]
    source = (root / "store_delivery_driver_app_routes.py").read_text(encoding="utf-8")
    assert "post_delivery_journal" not in source
    assert "post_store_driver_cod" in source
    assert "post_store_driver_fee" in source
    assert "accounting_shipping_p02" in source
    assert "Best-effort automation after the operational delivery is durable" in source


def test_legacy_settlement_route_blocks_manual_amount_account_for_mz2_owner():
    root = Path(__file__).resolve().parents[1]
    source = (root / "store_delivery_settlement_routes.py").read_text(encoding="utf-8")
    assert "store_delivery_settlement_requires_mz2_bank_evidence" in source
    assert "/shipping-p02/settlements/post" in source
