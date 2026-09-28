"""Actual pinned accounting code + disposable Mongo, not merchant activation.

The business marker is synthetic: atomicity here is not full supplier/bank/app
acceptance. Only this dedicated workflow sets the URI. No default application
URI or credentials are read.
"""
import asyncio
from dataclasses import replace
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from mezan_special_orders.mz2_v2_port import V2Event, V2Leg, V2PortError, post_in_owner_transaction

URI = os.environ.get("MEZAN_SPECIAL_MZ2_TARGET_URI")
pytestmark = pytest.mark.skipif(not URI, reason="Explicit isolated Accounting V2 target fixture not supplied")
OWNER = "compat-owner"
CUTOVER = "2026-09-01T00:00:00.000000Z"
EVENT_TIME = "2026-09-28T09:00:00.000000Z"


def plan(amount=74140, **extra):
    values = dict(owner=OWNER, order_id="MZ-compat", event_id="event-1", purpose="replacement",
        effective_at=EVENT_TIME, evidence_digest="a"*64, policy_digest="b"*64,
        legs=(V2Leg("cash", "bank", "bank-compat", "debit", amount, "payment"),
              V2Leg("receivable", "customer", "MZ-compat", "credit", amount, "payment")))
    return V2Event(**(values | extra))


async def isolated(scenario):
    from motor.motor_asyncio import AsyncIOMotorClient
    from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes
    assert urlsplit(URI).hostname in {"127.0.0.1", "localhost", "::1"}
    client=AsyncIOMotorClient(URI, serverSelectionTimeoutMS=5000)
    database="test_mz2_special_compat_"+uuid4().hex
    db=client[database]
    try:
        hello=await client.admin.command("hello")
        assert hello.get("setName") and hello.get("logicalSessionTimeoutMinutes")
        await ensure_accounting_ledger_v2_indexes(db)
        await db.users.insert_one(dict(id=OWNER,role="owner",is_active=True))
        await db.mz2_atomic_owners.insert_one(dict(_id=OWNER,revision=0,writes_paused=False,
            control_revision=1,ledger_backend_state="v2_active",ledger_backend_revision=2,
            ledger_backend_contract_revision=1,ledger_backend_activation_ref="SYNTHETIC-ONLY"))
        await db.settings.insert_one(dict(user_id=OWNER,mezan2_financial_cutover=dict(
            operation_id="MZ2-FIN-CUTOVER-001",status="active",cutover_at=CUTOVER,
            opening_active_txn_group_id="zero:compat",opening_root_txn_group_id="zero:compat")))
        await db.mz2_opening_balance_drafts.insert_one(dict(user_id=OWNER,id="compat",status="posted",zero_only=True,
            txn_group_id="zero:compat",opening_root_txn_group_id="zero:compat",cutover_at=CUTOVER,
            preview_hash="a"*64,approval_hash="b"*64,evidence_snapshot=[{"source":"synthetic-zero-opening"}]))
        await scenario(db)
    finally:
        assert database.startswith("test_mz2_special_compat_")
        await client.drop_database(database)
        client.close()


async def execute(db, event=None, actor=OWNER, after=None):
    from accounting_atomic import atomic_owner
    event=event or plan()
    async def business(scoped):
        await scoped.special_compat_business.update_one({"_id":event.event_id},{"$set":{"stage":"prepared"}},upsert=True)
        journal=await post_in_owner_transaction(scoped, actor_id=actor,
            required_permission="accounting.settlements.post",event=event)
        await scoped.special_compat_business.update_one({"_id":event.event_id},{"$set":{"group":journal["group"]["txn_group_id"]}})
        if after:
            await after(scoped,journal)
        return journal
    return await atomic_owner(db, event.owner, business)


async def empty(db):
    from accounting_ledger_v2 import query_entries_v2
    assert await query_entries_v2(db,user_id=OWNER)==[]
    assert await db.special_compat_business.count_documents({})==0
    assert await db.general_ledger.count_documents({})==0


def test_actual_v2_post_replay_and_balance_without_legacy_write():
    async def scenario(db):
        from accounting_ledger_v2 import compute_balance_v2,verify_journal_v2
        first=await execute(db)
        again=await execute(db)
        assert first==again
        group=first["group"]
        assert group["schema_version"]==2 and group["source"]=="mezan_special_orders_v2"
        assert (await verify_journal_v2(db,user_id=OWNER,txn_group_id=group["txn_group_id"]))["verified"]
        balance=await compute_balance_v2(db,user_id=OWNER,entity_type="bank",entity_id="bank-compat")
        assert balance["net_balance_minor"]==74140 and balance["entry_count"]==1
        assert await db.general_ledger.count_documents({})==0
    asyncio.run(isolated(scenario))


@pytest.mark.parametrize("change",["missing","paused","field_missing"])
def test_global_pause_precedes_callback_and_does_not_bootstrap(change):
    async def scenario(db):
        from fastapi import HTTPException
        if change=="missing": await db.mz2_atomic_owners.delete_one({"_id":OWNER})
        elif change=="paused": await db.mz2_atomic_owners.update_one({"_id":OWNER},{"$set":{"writes_paused":True}})
        else: await db.mz2_atomic_owners.update_one({"_id":OWNER},{"$unset":{"writes_paused":""}})
        before=await db.mz2_atomic_owners.find_one({"_id":OWNER})
        with pytest.raises(HTTPException) as exc: await execute(db)
        assert exc.value.status_code==423
        assert before==await db.mz2_atomic_owners.find_one({"_id":OWNER})
        await empty(db)
    asyncio.run(isolated(scenario))


@pytest.mark.parametrize("state",["legacy_active","transition_blocked"])
def test_transition_is_not_overridden_or_fallback_used(state):
    async def scenario(db):
        from fastapi import HTTPException
        await db.mz2_atomic_owners.update_one({"_id":OWNER},{"$set":{"ledger_backend_state":state}})
        with pytest.raises(HTTPException) as exc: await execute(db)
        assert exc.value.status_code==423
        await empty(db)
    asyncio.run(isolated(scenario))


@pytest.mark.parametrize("change",["missing","unapproved","inactive","pre_cutover"])
def test_verified_opening_and_effective_date_remain_required(change):
    async def scenario(db):
        from accounting_ledger_v2 import AccountingLedgerV2Error
        if change=="missing": await db.mz2_opening_balance_drafts.delete_many({})
        elif change=="unapproved": await db.mz2_opening_balance_drafts.update_one({"id":"compat"},{"$unset":{"approval_hash":""}})
        elif change=="inactive": await db.settings.update_one({"user_id":OWNER},{"$set":{"mezan2_financial_cutover.status":"draft"}})
        p=plan(effective_at="2026-08-01T00:00:00Z") if change=="pre_cutover" else plan()
        with pytest.raises((V2PortError,AccountingLedgerV2Error)): await execute(db,p)
        await empty(db)
    asyncio.run(isolated(scenario))


@pytest.mark.parametrize("change",["disabled","deleted","no_permission","foreign_owner"])
def test_fresh_persisted_actor_not_old_owner_session(change):
    async def scenario(db):
        from fastapi import HTTPException
        if change=="disabled": await db.users.update_one({"id":OWNER},{"$set":{"disabled":True}})
        elif change=="deleted": await db.users.delete_one({"id":OWNER})
        else:
            await db.users.update_one({"id":OWNER},{"$set":{"role":"accountant","created_by": "foreign" if change=="foreign_owner" else OWNER,
              "accounting_permissions":["accounting.home.view"]+(["accounting.settlements.post"] if change=="foreign_owner" else [])}})
        with pytest.raises((HTTPException,V2PortError)): await execute(db)
        await empty(db)
    asyncio.run(isolated(scenario))


def test_posting_requires_accounting_session_not_plain_database():
    async def scenario(db):
        with pytest.raises(V2PortError,match="accounting_owner_transaction_required"):
            await post_in_owner_transaction(db,actor_id=OWNER,required_permission="accounting.settlements.post",event=plan())
        await empty(db)
    asyncio.run(isolated(scenario))


def test_failure_after_post_rolls_back_business_and_journal():
    async def scenario(db):
        async def fail(scoped,journal): raise RuntimeError("SYNTHETIC_ABORT")
        with pytest.raises(RuntimeError,match="SYNTHETIC_ABORT"): await execute(db,after=fail)
        await empty(db)
        assert (await execute(db))["group"]["debit_total"]=="741.40"
    asyncio.run(isolated(scenario))


def test_same_event_different_amount_is_conflict_not_second_payment():
    async def scenario(db):
        from accounting_ledger_v2 import AccountingLedgerV2Error,compute_balance_v2
        await execute(db)
        with pytest.raises(AccountingLedgerV2Error) as exc: await execute(db,plan(amount=80000))
        assert exc.value.code=="accounting_v2_idempotency_conflict"
        assert (await compute_balance_v2(db,user_id=OWNER,entity_type="bank",entity_id="bank-compat"))["net_balance_minor"]==74140
    asyncio.run(isolated(scenario))


def test_concurrent_retry_posts_one_v2_group():
    async def scenario(db):
        from accounting_ledger_v2 import compute_balance_v2
        results=await asyncio.gather(*(execute(db) for _ in range(5)))
        assert len({r["group"]["txn_group_id"] for r in results})==1
        assert (await compute_balance_v2(db,user_id=OWNER,entity_type="bank",entity_id="bank-compat"))["entry_count"]==1
    asyncio.run(isolated(scenario))
