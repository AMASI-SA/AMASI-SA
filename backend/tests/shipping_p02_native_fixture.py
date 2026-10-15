"""Isolated native fixture for P02 migration with canonical report identities."""
import os
from decimal import Decimal
from uuid import uuid4
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes, post_opening_journal_v2
from accounting_module_contract import OPERATION_ID, EVIDENCE_SECTIONS
from accounting_shipping_native import _leg
from accounting_shipping_native_setup import save_setup
from accounting_sales_tax_service import save_policy
from test_mz2_shipping_native import OWNER, CUT, NoLegacy, courier, rate, source


@pytest_asyncio.fixture
async def db():
    uri = os.environ["MZ2_TEST_MONGO_URI"]
    listener = NoLegacy()
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[listener])
    database = client["mz2_p02_native_regression_" + uuid4().hex]
    assert (await database.command("hello")).get("setName"), "isolated replica set required"
    await ensure_accounting_ledger_v2_indexes(database)
    await database.users.insert_one({"id": OWNER, "role": "owner", "is_active": True})
    await database.mz2_atomic_owners.insert_one({"_id": OWNER, "revision": 0, "writes_paused": False,
        "ledger_backend_state": "v2_active", "ledger_backend_revision": 1,
        "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "synthetic-only"})
    await database.store_drivers.insert_one({"id": "driver-f", "user_id": OWNER, "status": "active", "name": "Driver"})
    # Synthetic opening in the unique disposable test database. No live P07.
    async with await client.start_session() as session:
        async def opening(s):
            return await post_opening_journal_v2(database, user_id=OWNER, actor_id=OWNER, actor_name=OWNER,
                opening_operation_id="synthetic-opening", approved_preview_hash="a" * 64, effective_at=CUT,
                entries=[_leg(("bank", "bank-f", "main"), "debit", Decimal("1000"), "bank", "opening_balance"),
                         _leg(("equity", "opening_balance_equity", "main"), "credit", Decimal("1000"), "equity", "opening_balance")], mongo_session=s)
        result = await session.with_transaction(opening)
    group = result["group"]["txn_group_id"]
    zeroes = [{"entity_type": kind, "entity_id": identity, "sub_account": sub,
        "evidence_ref": "synthetic-zero", "accounting_at": CUT, "opening_balance_txn_group_id": group}
        for kind, identity, subs in (("courier", "smsa", ("cod_receivable", "payable")),
                                    ("store_driver", "driver-f", ("cod_receivable", "delivery_fee_payable"))) for sub in subs]
    await database.settings.insert_one({"user_id": OWNER, "mezan2_financial_cutover": {
        "operation_id": OPERATION_ID, "status": "active", "cutover_at": CUT,
        "opening_active_txn_group_id": group, "opening_root_txn_group_id": group,
        "opening_balance_txn_group_id": group, "opening_balance_zero_accounts": zeroes,
        "evidence_sheet_ref": "synthetic", "evidence_sections": {s["id"]: "synthetic" for s in EVIDENCE_SECTIONS},
        "opening_balance_preview_id": "synthetic", "opening_balance_preview_balanced": True,
        "opening_balance_approved_at": CUT, "opening_balance_approved_by": OWNER,
        "p02_shipping_cod_enabled": True, "p02_shipping_cod_activation_ref": "test-only"}})
    await save_setup(database, OWNER, OWNER, courier())
    await save_setup(database, OWNER, OWNER, rate())
    await save_policy(database, owner=OWNER, actor_id=OWNER, rate="15", effective_at=CUT, revision=0, reason="test tax")
    await source(database)
    try:
        yield database
        assert listener.accesses == [], "Native path accessed Legacy storage"
    finally:
        await client.drop_database(database.name)
        client.close()

