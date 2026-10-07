"""Real local orders/receipts + current native accounts/movements/V2 owner.

The already-posted receivable is an explicit synthetic prerequisite. This suite
is not evidence that a production agreement producer or full lifecycle is wired.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pymongo.monitoring import CommandListener

from mezan_special_orders.binding import Enablement, SpecialOrdersDatabase, transaction
from mezan_special_orders.contracts import Actor, CreateOrder, ReceiptClaim
from mezan_special_orders.domain import DomainError, add_receipt, creation, digest
from mezan_special_orders.mz2_v2_port import V2Event, V2Leg, post_in_owner_transaction
from mezan_special_orders.v2_owner import execute_in_v2_owner, accounting_scope
from mezan_special_orders.v2_bank_receipts import CollectReceiptV2, collect_existing_receipt, BINDINGS, EVENTS, exact_minor
from mezan_special_orders.write_control import SCOPES, CONTROL

URI = os.environ.get("MEZAN_SPECIAL_MZ2_TARGET_URI")
pytestmark = pytest.mark.skipif(not URI, reason="Explicit disposable native V2 URI required")
OWNER = "r8-synthetic-owner"
BANK = "r8-native-bank"
AT = "2026-09-28T00:00:00Z"
DAY = "2026-09-29"


class Monitor(CommandListener):
    def __init__(self): self.blocked = []; self.enabled = False; self.writes = []
    def started(self, event):
        if not self.enabled: return
        collection = event.command.get(event.command_name)
        if collection in {"accounts", "general_ledger", "account_transactions", "accounting_audit_log"}:
            self.blocked.append((event.command_name, collection))
        if event.command_name in {"insert", "update", "delete"}:
            self.writes.append((collection, event.command.get("lsid"), event.command.get("txnNumber")))
    def succeeded(self, event): pass
    def failed(self, event): pass


async def fixture(scenario):
    from motor.motor_asyncio import AsyncIOMotorClient
    from mz2_native_fixture import provision_native_opening
    assert urlsplit(URI).hostname in {"127.0.0.1", "localhost", "::1"}
    monitor = Monitor()
    client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=5000, event_listeners=[monitor])
    name = "test_mz2_special_r8_" + uuid4().hex
    db = client[name]
    binding = SpecialOrdersDatabase(db, Enablement(True, True, True, True))
    try:
        await db.users.insert_one({"id": OWNER, "role": "owner", "is_active": True})
        await provision_native_opening(db, owner=OWNER, cutover="2026-09-01T00:00:00Z", bank_balances={BANK: "0.00"})
        await db[CONTROL].insert_one({"_id": OWNER, "tenant_id": OWNER, "schema_version": 1,
            "revision": 1, "write_epoch": 1, "activity_seq": 0,
            "enabled": {s: True for s in SCOPES}, "last_command_id": "synthetic-init",
            "changed_by": OWNER, "changed_at": AT})
        await db[BINDINGS].create_index([("tenant_id",1),("account_id",1),("reference_key",1)], unique=True)
        await db[BINDINGS].create_index([("tenant_id",1),("transaction_id",1)], unique=True)
        await db[EVENTS].create_index([("tenant_id",1),("movement_id",1)],unique=True)
        for coll in ("mezan_special_orders_v1", "mezan_special_order_evidence_v1", "order_review_workflows", "mz2_daily_movements"):
            await db.create_collection(coll)
        await scenario(db,binding,monitor)
    finally:
        monitor.enabled = False
        assert name.startswith("test_mz2_special_r8_")
        await client.drop_database(name)
        client.close()


async def prepare(db, *, suffix="one", amount=4000):
    from accounting_atomic import atomic_owner
    from mezan_special_orders.contracts import Product, Recipient
    data = dict(purpose="replacement", expense_bucket="compensation", reason="تعويض اختباري",
        original_order_number="ORIGINAL-SYN", delivery={"method":"courier", "customer_charge_minor":2000},
        items=[dict(line_key="one",original_item_id="original-item",quantity=1,customer_charge_minor=4000)],
        fx=dict(currency="SAR",rate_to_sar="1",captured_at=AT,evidence_id="sar-fixed-1"),
        collection=dict(bank_transfer_minor=4000,cod_minor=2000))
    from mezan_special_orders.contracts import OriginalOrder, OriginalLine
    product=Product(tenant_id=OWNER,product_id="synthetic-product",name="منتج اختباري",catalog_revision="v1")
    recipient=Recipient(name="مستلم اختباري",mobile="0500000000",address=dict(city="مدينة اختبار",district="حي اختبار",formatted="عنوان اصطناعي لاستخدام الاختبارات"))
    original=OriginalOrder(tenant_id=OWNER,order_id="original",order_number="ORIGINAL-SYN",source_revision="v1",
        recipient=recipient,items=(OriginalLine(item_id="original-item",product=product,quantity=1),))
    doc=creation(Actor(tenant_id=OWNER,actor_id=OWNER),CreateOrder.model_validate(data),"r8-create-"+suffix,original,{})
    doc["created_at"]=AT
    # A genuine inert PNG fixture, structurally parsed using the existing checker.
    # It is not an actual customer receipt and does not call an external bank.
    from io import BytesIO
    from PIL import Image, PngImagePlugin
    from mezan_special_orders.document_validator import inspect
    stream=BytesIO(); metadata=PngImagePlugin.PngInfo()
    metadata.add_text("synthetic-reference",suffix)
    Image.new("RGB", (2,2)).save(stream,format="PNG",pnginfo=metadata)
    content=stream.getvalue(); inspection=inspect(content)
    fingerprint=hashlib.sha256(content).hexdigest()
    evidence=dict(object_id="receipt-"+suffix,kind="bank_receipt",sha256=fingerprint)
    await db.mezan_special_order_evidence_v1.insert_one({"tenant_id":OWNER, **evidence,
        "status":"available","size":len(content),"content":content,
        "content_type":"image/png","inspection":inspection,"created_by":OWNER})
    claim=ReceiptClaim(evidence=evidence,bank_account_id=BANK,amount_minor=amount,
        transferred_at=DAY+"T10:00:00+03:00")
    claim_id=add_receipt(doc,claim)
    native_id="native-movement-"+suffix
    await db.mz2_daily_movements.insert_one({"_id":native_id,"id":native_id,"user_id":OWNER,
        "bank_account_id":BANK,"movement_date":DAY,"direction":"in","amount":f"{amount//100}.{amount%100:02d}",
        "reference":"SYN-REFERENCE-"+suffix,"file_id":"SYN-STATEMENT","status":"unclassified"})
    receivable=dict(entity_type="asset",entity_id="receivable-"+suffix,sub_account="other_receivable")
    await db.mz2_opening_facts_v2.insert_one({"user_id":OWNER,"status":"active", **receivable})
    plan=V2Event(owner=OWNER,order_id=doc["order_id"],event_id="agreement-"+suffix,
        purpose=doc["purpose"],effective_at=AT,evidence_digest=doc["snapshot_digest"],policy_digest="b"*64,
        legs=(V2Leg("debt","asset",receivable["entity_id"],"debit",6000,"payment","other_receivable"),
              V2Leg("recovery","other_income","synthetic_recovery","credit",6000,"payment")))
    async def recognize(scoped):
        return await post_in_owner_transaction(scoped,actor_id=OWNER,required_permission="accounting.receivables.post",event=plan)
    journal=await atomic_owner(db,OWNER,recognize)
    doc["financial_agreement_v2"]={"txn_group_id":journal["group"]["txn_group_id"],"event_id":plan.event_id,
        "source_digest":doc["snapshot_digest"],"policy_digest":plan.policy_digest,
        "gross_sar_minor":6000,"receivable":receivable}
    await db.mezan_special_orders_v1.insert_one(deepcopy(doc))
    request=CollectReceiptV2(expected_revision=1,expected_epoch=1,receipt_claim_id=claim_id,
        movement_id=native_id,confirmation="CONFIRM_SPECIAL_ORDER_BANK_ARRIVAL")
    return doc,request


async def collect(binding,doc,request,key="r8-collect-one",session_user=None):
    return await collect_existing_receipt(binding,tenant_id=OWNER,session_user=session_user or {"id":OWNER},
        order_id=doc["order_id"],request=request,idempotency_key=key)


async def financial_snapshot(db):
    return {name: await db[name].find({}, {"_id":0}).sort("id",1).to_list(1000) for name in (
        "mz2_daily_movements","mezan_special_orders_v1",EVENTS,BINDINGS,
        "accounting_general_ledger_v2","accounting_journal_groups_v2",
        "accounting_audit_log_v2","accounting_ledger_sequences_v2")}


def test_actual_order_native_movement_bank_receivable_and_exact_replay():
    async def scenario(db,binding,monitor):
        from accounting_ledger_v2 import compute_balance_v2
        doc,request=await prepare(db)
        monitor.enabled=True
        result=await collect(binding,doc,request)
        after=await financial_snapshot(db)
        again=await collect(binding,doc,request)
        assert again["replayed"] and result["txn_group_id"]==again["txn_group_id"]
        assert after==await financial_snapshot(db)
        assert result["balances"]["remaining_minor"]==2000
        assert result["balances"]["cod_to_collect_minor"]==2000
        assert result["balances"]["bank_collected_minor"]==4000
        assert (await compute_balance_v2(db,user_id=OWNER,entity_type="bank",entity_id=BANK,sub_account="main"))["net_balance_minor"]==4000
        assert (await compute_balance_v2(db,user_id=OWNER,**doc["financial_agreement_v2"]["receivable"]))["net_balance_minor"]==2000
        updated=await db.mezan_special_orders_v1.find_one({"order_id":doc["order_id"]})
        assert updated["receipt_claims"][0]["state"]=="confirmed"
        assert updated["financial_backend"]=="v2"
        assert not updated["policy"]["counts_in_sales_orders"] and not updated["policy"]["counts_in_marketing_orders"]
        assert not monitor.blocked
        assert all(session and txn is not None for _,session,txn in monitor.writes)
    asyncio.run(fixture(scenario))


@pytest.mark.parametrize("change",["global_missing","global_pause","local_missing","local_pause","epoch","transition","opening","static_off","actor_disabled"])
def test_no_half_payment_when_global_or_local_authority_denies(change):
    async def scenario(db,binding,monitor):
        doc,request=await prepare(db)
        if change=="global_missing":await db.mz2_atomic_owners.delete_one({"_id":OWNER})
        elif change=="global_pause":await db.mz2_atomic_owners.update_one({"_id":OWNER},{"$set":{"writes_paused":True}})
        elif change=="local_missing":await db[CONTROL].delete_one({"_id":OWNER})
        elif change=="local_pause":await db[CONTROL].update_one({"_id":OWNER},{"$set":{"enabled.financial":False}})
        elif change=="epoch":request=request.model_copy(update={"expected_epoch":2})
        elif change=="transition":await db.mz2_atomic_owners.update_one({"_id":OWNER},{"$set":{"ledger_backend_state":"transition_blocked"}})
        elif change=="opening":await db.settings.update_one({"user_id":OWNER},{"$set":{"mezan2_financial_cutover.status":"draft"}})
        elif change=="static_off":binding=SpecialOrdersDatabase(db,Enablement(reads=True,commands=True))
        elif change=="actor_disabled":await db.users.update_one({"id":OWNER},{"$set":{"disabled":True}})
        before=await financial_snapshot(db)
        monitor.enabled=True
        with pytest.raises((DomainError,HTTPException)):await collect(binding,doc,request)
        assert before==await financial_snapshot(db)
        assert not monitor.blocked
    asyncio.run(fixture(scenario))


@pytest.mark.parametrize("change",["missing_bank","legacy_bank_only","wrong_bank","inactive_bank","wrong_amount","fractional_amount","outgoing","allocated","claimed_by_native","corrupt_evidence","date","reference","missing_agreement","wrong_agreement","unrelated_receivable"])
def test_native_source_validation_blocks_before_bank_or_order_mutation(change):
    async def scenario(db,binding,monitor):
        doc,request=await prepare(db)
        if change in {"missing_bank","legacy_bank_only"}:
            await db.mz2_financial_accounts.delete_one({"id":BANK})
            if change=="legacy_bank_only":await db.accounts.insert_one({"id":BANK,"user_id":OWNER,"account_type":"bank","currency":"SAR","status":"active"})
        elif change=="inactive_bank":await db.mz2_financial_accounts.update_one({"id":BANK},{"$set":{"status":"inactive"}})
        elif change in {"wrong_bank","wrong_amount","fractional_amount","outgoing","allocated","date","reference"}:
            fields={"wrong_bank":{"bank_account_id":"another"},"wrong_amount":{"amount":"41.00"},"fractional_amount":{"amount":"40.001"},
                "outgoing":{"direction":"out"},"allocated":{"status":"classified","accounting_event_id":"other"},
                "date":{"movement_date":"2026-09-30"},"reference":{"reference":""}}[change]
            await db.mz2_daily_movements.update_one({"id":request.movement_id},{"$set":fields})
        elif change=="corrupt_evidence":await db.mezan_special_order_evidence_v1.update_one({"object_id":"receipt-one"},{"$set":{"content":b"tampered"}})
        elif change=="claimed_by_native":await db.mz2_bank_transfer_receipts.insert_one({"user_id":OWNER,"status":"recognized","bank_movement_id":request.movement_id})
        elif change=="missing_agreement":await db.mezan_special_orders_v1.update_one({"order_id":doc["order_id"]},{"$unset":{"financial_agreement_v2":""}})
        elif change=="wrong_agreement":await db.mezan_special_orders_v1.update_one({"order_id":doc["order_id"]},{"$set":{"financial_agreement_v2.policy_digest":"c"*64}})
        elif change=="unrelated_receivable":await db.mz2_opening_facts_v2.delete_many({})
        before=await financial_snapshot(db)
        monitor.enabled=True
        with pytest.raises((DomainError,HTTPException)):await collect(binding,doc,request)
        assert before==await financial_snapshot(db)
        assert not monitor.blocked
    asyncio.run(fixture(scenario))


def test_failure_after_real_v2_post_aborts_receipt_bank_and_order(monkeypatch):
    async def scenario(db,binding,monitor):
        import mezan_special_orders.v2_bank_receipts as module
        doc,request=await prepare(db)
        before=await financial_snapshot(db)
        actual=module.post_in_owner_transaction
        async def fail(*args,**kwargs):
            await actual(*args,**kwargs)
            raise RuntimeError("SYNTHETIC_AFTER_POST")
        monkeypatch.setattr(module,"post_in_owner_transaction",fail)
        with pytest.raises(RuntimeError,match="SYNTHETIC_AFTER_POST"):await collect(binding,doc,request)
        assert before==await financial_snapshot(db)
        monkeypatch.setattr(module,"post_in_owner_transaction",actual)
        assert not (await collect(binding,doc,request))["replayed"]
    asyncio.run(fixture(scenario))


def test_concurrent_retries_produce_one_bank_effect_and_one_event():
    async def scenario(db,binding,monitor):
        doc,request=await prepare(db)
        results=await asyncio.gather(*(collect(binding,doc,request) for _ in range(5)),return_exceptions=True)
        assert all(isinstance(r,dict) for r in results),results
        assert len({r["txn_group_id"] for r in results})==1
        assert await db[EVENTS].count_documents({"operation":"bank_collection"})==1
        assert await db[BINDINGS].count_documents({})==1
    asyncio.run(fixture(scenario))


def test_same_bank_movement_cannot_pay_two_orders():
    async def scenario(db,binding,monitor):
        a,ar=await prepare(db,suffix="a")
        b,br=await prepare(db,suffix="b")
        br=br.model_copy(update={"movement_id":ar.movement_id})
        await collect(binding,a,ar,key="collect-order-a")
        before=await financial_snapshot(db)
        with pytest.raises(DomainError,match="already_allocated"):await collect(binding,b,br,key="collect-order-b")
        assert before==await financial_snapshot(db)
    asyncio.run(fixture(scenario))


def test_global_first_scope_is_same_session_and_refuses_legacy_local_first():
    async def scenario(db,binding,monitor):
        async def inner(local,scoped,actor):
            assert accounting_scope(local,OWNER) is scoped
            assert local.session is scoped._session
            with pytest.raises(DomainError,match="public_ledger_api"):_=local.general_ledger
            async def nested(nested_local,native,native_actor):
                assert native is scoped and nested_local.session is scoped._session
                return "nested"
            return await execute_in_v2_owner(local,tenant_id=OWNER,session_user={"id":OWNER},callback=nested,
                scopes=frozenset({"financial"}))
        assert await execute_in_v2_owner(binding,tenant_id=OWNER,session_user={"id":OWNER},callback=inner)=="nested"
        async def wrong_order(local):
            with pytest.raises(DomainError,match="global_first"):
                await execute_in_v2_owner(local,tenant_id=OWNER,session_user={"id":OWNER},callback=inner)
        await transaction(binding,wrong_order,tenant_id=OWNER,scopes=frozenset({"workflow","financial","evidence"}))
    asyncio.run(fixture(scenario))


def test_replay_rejects_changed_immutable_native_movement():
    async def scenario(db,binding,monitor):
        doc,request=await prepare(db)
        await collect(binding,doc,request)
        await db.mz2_daily_movements.update_one({"id":request.movement_id},{"$set":{"amount":"41.00"}})
        before=await financial_snapshot(db)
        with pytest.raises(DomainError,match="native_movement_changed"):await collect(binding,doc,request)
        assert before==await financial_snapshot(db)
    asyncio.run(fixture(scenario))


def test_closed_period_rejects_native_collection_without_backdating():
    async def scenario(db,binding,monitor):
        doc,request=await prepare(db)
        await db.mz2_accounting_periods.insert_one({"user_id":OWNER,"month":"2026-09","closed":True})
        before=await financial_snapshot(db)
        with pytest.raises(HTTPException) as err:await collect(binding,doc,request)
        assert err.value.detail["code"]=="accounting_period_closed"
        assert before==await financial_snapshot(db)
    asyncio.run(fixture(scenario))


@pytest.mark.parametrize("value",[True,40.0,"NaN","Infinity","0.001","-1",None])
def test_native_amount_never_coerces_invalid_money(value):
    with pytest.raises(DomainError):exact_minor(value)


def test_actual_dependency_modules_come_only_from_current_reference():
    expected=Path(os.environ["MEZAN_SPECIAL_ACCOUNTING_REFERENCE"]).resolve()
    for name in ("accounting_atomic","accounting_ledger_v2","accounting_write_control", "accounting_financial_identity",
                 "accounting_mz2_balances","accounting_mz2_reports","employee_payroll_status","supplier_identity_service"):
        module=importlib.import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(expected), (name,module.__file__)


@pytest.mark.parametrize("changed", ["claim", "binding", "native_claim"])
def test_replay_rejects_broken_receipt_binding_history(changed):
    async def scenario(db, binding, monitor):
        doc, request = await prepare(db)
        await collect(binding, doc, request)
        if changed == "claim":
            await db.mezan_special_orders_v1.update_one({"order_id": doc["order_id"]},
                {"$set": {"receipt_claims.0.state": "pending"}})
        elif changed == "binding":
            await db[BINDINGS].update_one({"order_id": doc["order_id"]},
                {"$set": {"transaction_id": "corrupted-history"}})
        else:
            await db.mz2_daily_movements.update_one({"id": request.movement_id},
                {"$set": {"special_receipt_claim_id": "wrong-claim"}})
        before = await financial_snapshot(db)
        with pytest.raises(DomainError):
            await collect(binding, doc, request)
        assert before == await financial_snapshot(db)
    asyncio.run(fixture(scenario))


def test_duplicate_observed_payment_does_not_reduce_balance_twice():
    async def scenario(db, binding, monitor):
        doc, request = await prepare(db)
        await collect(binding, doc, request)
        current = await db.mezan_special_orders_v1.find_one({"order_id": doc["order_id"]})
        await db.mezan_special_orders_v1.update_one({"order_id": doc["order_id"]},
            {"$push": {"payments": current["payments"][0]}})
        before = await financial_snapshot(db)
        with pytest.raises(DomainError):
            await collect(binding, doc, request)
        assert before == await financial_snapshot(db)
    asyncio.run(fixture(scenario))


def test_real_http_bank_approval_uses_server_resolved_tenant_and_exact_replay():
    async def scenario(db, binding, monitor):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from mezan_special_orders.v2_bank_routes import make_v2_bank_collection_router
        doc, request = await prepare(db)
        app = FastAPI()
        app.include_router(make_v2_bank_collection_router(binding, lambda: {"id": OWNER}))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated") as client:
            path = f"/special-orders-v1/{doc['order_id']}/v2-bank-collections"
            headers = {"Idempotency-Key": "synthetic-http-one"}
            result = await client.post(path, headers=headers, json=request.model_dump(mode="json"))
            assert result.status_code == 200, result.text
            assert result.json()["balances"]["remaining_minor"] == 2000
            repeated = await client.post(path, headers=headers, json=request.model_dump(mode="json"))
            assert repeated.status_code == 200 and repeated.json()["replayed"]
            assert result.json()["txn_group_id"] == repeated.json()["txn_group_id"]
    asyncio.run(fixture(scenario))


@pytest.mark.parametrize("change", ["amount", "tenant", "legs", "bank", "confirmation", "disabled", "paused"])
def test_http_cannot_supply_financial_facts_or_bypass_authority(change):
    async def scenario(db, binding, monitor):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from mezan_special_orders.v2_bank_routes import make_v2_bank_collection_router
        doc, request = await prepare(db)
        app = FastAPI()
        app.include_router(make_v2_bank_collection_router(binding, lambda: {"id": OWNER, "role": "owner"}))
        payload = request.model_dump(mode="json")
        fields = {"amount": ("amount_minor", 4000), "tenant": ("tenant_id", "wrong-owner"),
                  "legs": ("entries", []), "bank": ("bank_account_id", "wrong-bank"),
                  "confirmation": ("confirmation", "")}
        if change in fields:
            key, value = fields[change]; payload[key] = value
        if change == "disabled": await db.users.update_one({"id": OWNER}, {"$set": {"disabled": True}})
        if change == "paused": await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
        before = await financial_snapshot(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated") as client:
            r = await client.post(f"/special-orders-v1/{doc['order_id']}/v2-bank-collections",
                headers={"Idempotency-Key": "synthetic-http-denied"}, json=payload)
            assert r.status_code == (403 if change == "disabled" else 423 if change == "paused" else 422), r.text
        assert before == await financial_snapshot(db)
    asyncio.run(fixture(scenario))


@pytest.mark.parametrize("exc", [RuntimeError("PRIVATE-BANK-DATA"), HTTPException(409, detail={"code": "PRIVATE-BANK-DATA", "source": "secret"}),
    DomainError("PRIVATE-BANK-DATA"), HTTPException(503, detail="PRIVATE-BANK-DATA")])
def test_http_rejection_does_not_disclose_dependency_text(exc):
    from mezan_special_orders.v2_bank_routes import public_rejection
    result = public_rejection(exc)
    assert "PRIVATE" not in repr(result.detail) and "secret" not in repr(result.detail)
    assert result.detail == {"code": "special_v2_bank_approval_rejected"}


def test_no_router_registration_with_default_closed_binding():
    from mezan_special_orders.v2_bank_routes import make_v2_bank_collection_router
    class Database: pass
    assert make_v2_bank_collection_router(SpecialOrdersDatabase(Database(), Enablement()), lambda: {}).routes == []


def test_native_movement_currency_is_not_silently_converted():
    async def scenario(db, binding, monitor):
        doc, request = await prepare(db)
        await db.mz2_daily_movements.update_one({"id": request.movement_id}, {"$set": {"currency": "USD"}})
        before = await financial_snapshot(db)
        with pytest.raises(DomainError, match="native_movement_currency_mismatch"):
            await collect(binding, doc, request)
        assert before == await financial_snapshot(db)
    asyncio.run(fixture(scenario))


def test_payment_before_receivable_is_held_for_native_advance_workflow():
    async def scenario(db, binding, monitor):
        doc, request = await prepare(db)
        await db.mz2_daily_movements.update_one({"id": request.movement_id}, {"$set": {"movement_date": "2026-09-27"}})
        await db.mezan_special_orders_v1.update_one({"order_id": doc["order_id"]},
            {"$set": {"receipt_claims.0.transferred_at": "2026-09-27T10:00:00+03:00"}})
        before = await financial_snapshot(db)
        with pytest.raises(DomainError, match="before_receivable_requires_advance"):
            await collect(binding, doc, request)
        assert before == await financial_snapshot(db)
    asyncio.run(fixture(scenario))
