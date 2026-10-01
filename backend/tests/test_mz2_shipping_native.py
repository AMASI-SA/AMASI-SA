"""Real isolated replica-set tests; never use production data or credentials."""
import asyncio
from copy import deepcopy
from decimal import Decimal
import os
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException, APIRouter, FastAPI
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

import accounting_shipping_bank_port as bank_port
from accounting_shipping_native_contract import (
    SETUP, EVIDENCE, EVENTS, INBOX, CourierInput, RateInput, BindingInput, SettlementInput, select_rate,
)
from accounting_shipping_native_setup import save_setup, read_setup
from accounting_shipping_native import recognize_cod, recognize_fee_delivery, accrue_fee, settle, statement, _post, _leg
from accounting_shipping_native_observer import observe_delivery
from accounting_shipping_native_routes import install_shipping_native_routes, readiness
from accounting_onboarding_identities import identities
from accounting_atomic import atomic_owner
from accounting_sales_tax_service import save_policy
from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes, post_opening_journal_v2, read_reporting_entries_v2
from accounting_module_contract import OPERATION_ID, EVIDENCE_SECTIONS

OWNER = "track-f-synthetic"
CUT = "2026-09-01T00:00:00.000000Z"
AT = "2026-09-03T12:00:00+00:00"


class NoLegacy(CommandListener):
    def __init__(self):
        self.accesses = []
    def started(self, event):
        value = event.command.get(event.command_name)
        if value in ("general_ledger", "accounts", "counterparties", "shipping_company_settings"):
            self.accesses.append((event.command_name, value))
    def succeeded(self, event): pass
    def failed(self, event): pass


def raw_order(number="1", value="500.00", **changes):
    raw = {"id": "salla-" + number, "reference_id": number, "date": "2026-09-02T12:00:00Z",
           "updated_at": AT, "status": {"slug": "delivered", "name": "تم التوصيل"},
           "payment_method": "cod", "amounts": {"total": {"amount": value, "currency": "SAR"}},
           "shipping": {"company": {"name": "SMSA", "code": "smsa-code"}, "delivered_at": AT}}
    raw.update(changes)
    return raw


async def source(db, number="1", **changes):
    await db.unified_orders.replace_one({"user_id": OWNER, "order_number": number},
        {"user_id": OWNER, "order_number": number, "raw_by_source": {"salla_direct": raw_order(number, **changes)}}, upsert=True)


def courier(version=0, key="smsa"):
    return CourierInput(request_id="courier-" + key, version=version, confirmed=True, reason="owner contract",
        courier_key=key, name=key, salla_carrier_keys=["smsa-code" if key == "smsa" else key])


def rate(version=1, kind="courier", identity="smsa", **changes):
    data = dict(request_id=f"rate-{kind}-{identity}", version=version, confirmed=True, reason="owner contract",
        party_type=kind, party_id=identity, context="delivery", effective_from=CUT, effective_to=None,
        currency="SAR", delivery_fee="17.25", cod_fixed_fee="0.00", cod_percent="0", vat_percent="15",
        vat_included=True, vat_treatment="gross_expense_no_input_vat", contract_reference="owner-confirmed-rate")
    data.update(changes)
    return RateInput(**data)


async def bind(db, kind="courier", identity="smsa"):
    setup = await read_setup(db, OWNER)
    return await save_setup(db, OWNER, OWNER, BindingInput(request_id="binding-" + identity,
        version=setup["version"], confirmed=True, reason="owner bank binding", party_type=kind,
        party_id=identity, financial_account_id="bank-f"))


async def movement(db, key, value, direction):
    await db.mz2_daily_movements.insert_one({"_id": key, "id": key, "user_id": OWNER,
        "status": "unclassified", "bank_account_id": "bank-f", "amount": value,
        "direction": direction, "currency": "SAR", "movement_date": "2026-09-04"})


def settlement(key, action="receive_cod", kind="courier", identity="smsa"):
    return SettlementInput(request_id="request-" + key, movement_id=key, action=action,
        party_type=kind, party_id=identity, reason="synthetic bank movement")


@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("isolated replica-set MZ2_TEST_MONGO_URI required")
    listener = NoLegacy()
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[listener])
    database = client["mz2_track_f_" + uuid4().hex]
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
                         _leg(("equity", "opening", ""), "credit", Decimal("1000"), "equity", "opening_balance")], mongo_session=s)
        result = await session.with_transaction(opening)
    group = result["group"]["txn_group_id"]
    zeroes = [{"entity_type": kind, "entity_id": identity, "sub_account": sub,
        "evidence_ref": "synthetic-zero", "accounting_at": CUT, "opening_balance_txn_group_id": group}
        for kind, identity, subs in (("courier", "smsa", ("cod_receivable", "payable")),
                                    ("store_driver", "driver-f", ("cod_receivable", "delivery_fee_payable")),
                                    ("customer", "customer-f", ("receivable",))) for sub in subs]
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


@pytest.fixture
def bank(monkeypatch):
    # Explicit test-only final-integration adapter, not a production bypass.
    resolver = AsyncMock(return_value={"id": "bank-f", "account_type": "bank", "currency": "SAR", "status": "active",
                                      "entity_type": "bank", "entity_id": "bank-f", "sub_account": "main"})
    monkeypatch.setattr(bank_port, "require_shipping_bank_identity", resolver)
    return resolver


async def report(db, kind="courier", identity="smsa"):
    return await statement(db, owner=OWNER, actor_id=OWNER, kind=kind, identity=identity)


@pytest.mark.asyncio
async def test_first_delivered_no_upload_retry_second_order_and_separate_fee(db):
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert result["mode"] == "first_sale"
    assert (await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1"))["state"] == "already_posted"
    await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    view = await report(db)
    assert (view["cod_receivable"], view["payable"]) == ("500.00", "17.25")
    await source(db, "2")
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="2")
    assert (await report(db))["cod_receivable"] == "1000.00"
    rows = await read_reporting_entries_v2(db, user_id=OWNER, effective_before="2026-10-01T00:00:00Z")
    assert sum(Decimal(r["amount"]) for r in rows if r["entity_type"] == "revenue") == Decimal("869.56")
    assert await db[EVIDENCE].count_documents({}) == 2


@pytest.mark.asyncio
async def test_prior_sale_reclassifies_no_duplicate_revenue(db):
    async def previous(scoped):
        return await _post(scoped, OWNER, {"id": OWNER}, "prior-sale", "cod_sale", AT,
            [_leg(("customer", "customer-f", "receivable"), "debit", Decimal("500"), "ar", "cod_sale"),
             _leg(("revenue", "bnpl_sales", ""), "credit", Decimal("500"), "sale", "cod_sale")], {"order_number": "1"})
    await atomic_owner(db, OWNER, previous)
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert result["mode"] == "reclassify"
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    rows = await read_reporting_entries_v2(db, user_id=OWNER, effective_before="2026-10-01T00:00:00Z")
    assert sum(1 for r in rows if r["entity_type"] == "revenue") == 1
    assert (await report(db))["cod_receivable"] == "500.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,code", [
    ({"payment_method": "credit_card"}, "shipping_cod_payment_required"),
    *[({"status": {"slug": status}}, "shipping_canonical_delivered_required") for status in
       ("completed", "delivering", "processing", "reviewed", "تم التنفيذ", "جاري التوصيل", "قيد التنفيذ", "تم المراجعة", "cancelled", "refunded")],
    ({"date": "2026-08-31T00:00:00Z"}, "pre_cutover_order"),
    ({"shipping": {}}, "shipping_canonical_identity_required"),
    ({"amounts": {}}, "shipping_cod_amount_missing_or_ambiguous"),
    ({"remaining_amount": "400", "payment": {"remaining_amount": "300"}}, "shipping_cod_amount_missing_or_ambiguous"),
    ({"refunded_amount": "1"}, "shipping_cancelled_or_refunded"),
    ({"amounts": {"total": {"amount": "500"}}}, "shipping_currency_unsupported"),
    ({"currency": "USD"}, "shipping_currency_unsupported"),
    ({"remaining_amount": "500", "payment_actions": {"remaining_action": {"remaining_amount": "300"}}}, "shipping_cod_amount_missing_or_ambiguous"),
])
async def test_invalid_operational_facts_fail_closed(db, changes, code):
    await source(db, **changes)
    with pytest.raises(HTTPException) as error:
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert error.value.detail["code"] == code
    assert await db[EVIDENCE].count_documents({}) == 0
    assert (await report(db))["cod_receivable"] == "0.00"


@pytest.mark.asyncio
async def test_courier_missing_and_legacy_catalog_cannot_authorize(db):
    await db[SETUP].delete_one({"_id": OWNER})
    await db.settings.update_one({"user_id": OWNER}, {"$set": {"shipping_companies": [{"name": "SMSA"}]}})
    assert await identities(db, OWNER, "courier") == []
    with pytest.raises(HTTPException) as error:
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert error.value.detail["code"] == "shipping_canonical_identity_required"


@pytest.mark.asyncio
async def test_receive_partial_full_and_over_receive_never_touch_payable(db, bank):
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    await bind(db)
    await movement(db, "partial", "300.00", "in")
    await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("partial"))
    assert (await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("partial")))["state"] == "already_posted"
    view = await report(db)
    assert (view["cod_receivable"], view["payable"]) == ("200.00", "17.25")
    await movement(db, "too-much", "200.01", "in")
    with pytest.raises(HTTPException, match="shipping_cod_over_receive"):
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("too-much"))
    await movement(db, "full", "200.00", "in")
    await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("full"))
    view = await report(db)
    assert (view["cod_receivable"], view["payable"], view["collections"]) == ("0.00", "17.25", "500.00")


@pytest.mark.asyncio
async def test_remittance_without_recognized_cod_cannot_invent_receivable(db, bank):
    await bind(db)
    await movement(db, "no-cod", "500.00", "in")
    with pytest.raises(HTTPException, match="shipping_cod_over_receive"):
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("no-cod"))
    assert await db[EVIDENCE].count_documents({}) == 0


@pytest.mark.asyncio
async def test_pay_partial_full_and_excess_do_not_change_cod(db, bank):
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    await bind(db)
    for key, value in (("p1", "10.00"), ("p2", "7.25")):
        await movement(db, key, value, "out")
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement(key, "pay_fee"))
    await movement(db, "over", "0.01", "out")
    with pytest.raises(HTTPException, match="shipping_fee_over_payment"):
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("over", "pay_fee"))
    view = await report(db)
    assert (view["cod_receivable"], view["payable"], view["payments"]) == ("500.00", "0.00", "17.25")


@pytest.mark.asyncio
async def test_setup_works_paused_but_financial_actions_423(db):
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    await save_setup(db, OWNER, OWNER, courier(2, "imile"))
    await save_setup(db, OWNER, OWNER, rate(3, identity="imile"))
    await bind(db, identity="imile")
    for call in (recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1"),
                 accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id="none"),
                 settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("none"))):
        with pytest.raises(HTTPException) as error:
            await call
        assert error.value.status_code == 423
    assert (await report(db))["cod_receivable"] == "0.00"


@pytest.mark.asyncio
async def test_missing_canonical_bank_and_disabled_p02_remain_closed(db):
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await bind(db)
    await movement(db, "bank", "100.00", "in")
    with pytest.raises(HTTPException, match="MZ2_LINK_REQUIRED"):
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("bank"))
    await db.settings.update_one({"user_id": OWNER}, {"$set": {"mezan2_financial_cutover.p02_shipping_cod_enabled": False}})
    with pytest.raises(HTTPException) as error:
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert error.value.status_code == 423


@pytest.mark.asyncio
async def test_missing_fee_policy_does_not_block_delivered_cod(db):
    await db[SETUP].update_one({"_id": OWNER}, {"$set": {"contracts": []}})
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    with pytest.raises(HTTPException, match="shipping_rate_policy_missing"):
        await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    assert (await report(db))["cod_receivable"] == "500.00"


@pytest.mark.asyncio
async def test_overlap_rejected_and_exact_effective_boundary(db):
    with pytest.raises(HTTPException, match="shipping_rate_policy_overlap"):
        await save_setup(db, OWNER, OWNER, rate(2, request_id="overlapping-rate"))
    setup = await read_setup(db, OWNER)
    first = setup["contracts"][0]
    first["effective_to"] = "2026-09-04T00:00:00Z"
    second = {**first, "id": "next", "delivery_fee": "25.00", "effective_from": first["effective_to"], "effective_to": None}
    setup["contracts"] = [first, second]
    assert select_rate(setup, "courier", "smsa", "delivery", first["effective_to"])["id"] == "next"
    setup["contracts"].append(second)
    with pytest.raises(HTTPException, match="shipping_rate_policy_overlap"):
        select_rate(setup, "courier", "smsa", "delivery", first["effective_to"])


@pytest.mark.asyncio
async def test_concurrent_recognition_once_and_concurrent_receive_bounded(db, bank):
    results = await asyncio.gather(*[recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1") for _ in range(4)])
    assert sum(r["state"] == "posted" for r in results) == 1
    await bind(db)
    await movement(db, "a", "300.00", "in")
    await movement(db, "b", "300.00", "in")
    outcomes = await asyncio.gather(*[settle(db, owner=OWNER, actor_id=OWNER, payload=settlement(k)) for k in ("a", "b")], return_exceptions=True)
    assert sum(isinstance(r, HTTPException) for r in outcomes) == 1
    assert (await report(db))["cod_receivable"] == "200.00"


@pytest.mark.asyncio
async def test_changed_source_retry_conflict_and_seal_integrity(db):
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await source(db, value="600.00")
    with pytest.raises(HTTPException, match="shipping_recognition_source_changed"):
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await db[EVIDENCE].update_one({"_id": result["evidence_id"]}, {"$set": {"cod_amount": "600.00"}})
    with pytest.raises(HTTPException, match="shipping_sealed_delivery_evidence_required"):
        await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])


@pytest.mark.asyncio
async def test_driver_evidence_exact_identity_and_external_separation(db):
    await save_setup(db, OWNER, OWNER, rate(2, kind="store_driver", identity="driver-f", delivery_fee="20.00"))
    await db.store_delivery_assignments.insert_one({"id": "assign-f", "user_id": OWNER, "driver_id": "driver-f",
        "order_id": "salla-1", "order_number": "1", "status": "delivered", "delivered_at": AT})
    await db.store_delivery_collections.insert_one({"id": "collection-f", "user_id": OWNER, "driver_id": "driver-f",
        "assignment_id": "assign-f", "order_id": "salla-1", "order_number": "1", "payment_method": "cash",
        "amount": "500.00", "cod_custody_amount": "500.00", "accounting_status": "operational_only",
        "delivery_proof_reference": "proof-f", "review_status": "not_required"})
    await db.store_delivery_delivery_proofs.insert_one({"user_id": OWNER, "driver_id": "driver-f",
        "token": "proof-f", "status": "bound", "bound_assignment_id": "assign-f"})
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id="assign-f")
    await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    assert (await report(db, "store_driver", "driver-f"))["cod_receivable"] == "500.00"
    assert (await report(db))["cod_receivable"] == "0.00"
    with pytest.raises(HTTPException, match="shipping_recognition_source_changed"):
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await db.store_drivers.update_one({"id": "driver-f"}, {"$set": {"user_id": "other-owner"}})
    with pytest.raises(HTTPException, match="shipping_canonical_identity_required"):
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, assignment_id="assign-f")


@pytest.mark.asyncio
async def test_observer_duplicate_sync_no_duplicate_revenue(db):
    assert (await observe_delivery(db, owner=OWNER, order_number="1"))["state"] == "posted"
    assert (await observe_delivery(db, owner=OWNER, order_number="1"))["state"] == "already_posted"
    assert await db[EVIDENCE].count_documents({}) == 1
    assert await db[INBOX].count_documents({"state": "processed"}) == 1


@pytest.mark.asyncio
async def test_http_setup_and_financial_pause_boundary(db):
    app, router = FastAPI(), APIRouter()
    async def user(): return {"id": OWNER}
    install_shipping_native_routes(router, db, user)
    app.include_router(router)
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        result = await client.post("/accounting-module/shipping-v2/couriers", json=courier(2, "imile").model_dump(mode="json"))
        assert result.status_code == 200, result.text
        result = await client.post("/accounting-module/shipping-v2/recognize-courier", json={"order_number": "1"})
        assert result.status_code == 423, result.text
        result = await client.get("/accounting-module/shipping-v2/context")
        assert result.status_code == 200, result.text
        assert result.json()["bank_port"]["ready"] is False


@pytest.mark.asyncio
async def test_prepaid_delivery_fee_has_no_cod_or_revenue(db):
    await source(db, payment_method="credit_card")
    await db[SETUP].update_one({"_id": OWNER}, {"$set": {"contracts.0.cod_fixed_fee": "9.00"}})
    result = await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert result["mode"] == "non_cod_delivery"
    view = await report(db)
    assert (view["cod_receivable"], view["payable"]) == ("0.00", "17.25")
    assert not any(r["entity_type"] == "revenue" for r in await read_reporting_entries_v2(
        db, user_id=OWNER, effective_before="2026-10-01T00:00:00Z"))
    assert (await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1"))["state"] == "already_posted"


@pytest.mark.asyncio
async def test_setup_exact_confirmation_retry_owner_and_concurrency(db):
    setup = await read_setup(db, OWNER)
    same = courier(setup["version"], "imile")
    results = await asyncio.gather(*[save_setup(db, OWNER, OWNER, same) for _ in range(3)], return_exceptions=True)
    assert sum(isinstance(r, dict) and r["state"] == "saved" for r in results) == 1
    assert (await save_setup(db, OWNER, OWNER, same))["state"] == "already_saved"
    changed = same.model_copy(update={"name": "different"})
    with pytest.raises(HTTPException, match="shipping_setup_idempotency_conflict"):
        await save_setup(db, OWNER, OWNER, changed)
    await db.users.insert_one({"id": "other-owner", "role": "owner", "is_active": True})
    with pytest.raises(HTTPException) as error:
        await save_setup(db, OWNER, "other-owner", courier(3, "third"))
    assert error.value.status_code == 403
    with pytest.raises(HTTPException, match="shipping_owner_confirmation_required"):
        await save_setup(db, OWNER, OWNER, courier(3, "third").model_copy(update={"confirmed": False}))
    await db[SETUP].update_one({"_id": OWNER}, {"$unset": {"couriers.0.confirmed_at": ""}})
    with pytest.raises(HTTPException, match="shipping_canonical_identity_required"):
        await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")


@pytest.mark.asyncio
async def test_source_sync_post_commit_hook_and_paused_retry(db):
    from fulfillment_v2_routes import persist_component_source_snapshot
    async def persist(scoped):
        await scoped.unified_orders.update_one({"user_id": OWNER, "order_number": "1"},
            {"$set": {"raw_by_source.salla_direct": raw_order()}})
        return {"created": False}
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    kwargs = dict(user_id=OWNER, order_number="1", payload=raw_order(), persist=persist)
    await persist_component_source_snapshot(db, **kwargs)
    assert await db[EVIDENCE].count_documents({}) == 0
    assert await db[INBOX].count_documents({"state": "pending"}) == 1
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": False}})
    await persist_component_source_snapshot(db, **kwargs)
    await persist_component_source_snapshot(db, **kwargs)
    assert (await report(db))["cod_receivable"] == "500.00"
    assert await db[EVIDENCE].count_documents({}) == 1


@pytest.mark.asyncio
async def test_backdated_remittance_rejected(db, bank):
    await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await bind(db)
    await movement(db, "backdated", "500", "in")
    await db.mz2_daily_movements.update_one({"id": "backdated"}, {"$set": {"movement_date": "2026-09-02"}})
    with pytest.raises(HTTPException, match="shipping_settlement_before_liability"):
        await settle(db, owner=OWNER, actor_id=OWNER, payload=settlement("backdated"))


@pytest.mark.asyncio
async def test_readiness_requires_courier_and_exact_opening(db):
    context = await readiness(db, OWNER)
    assert context["stages"]["7"]["ready"] and context["stages"]["8"]["ready"]
    assert not context["stages"]["9"]["ready"]  # Driver needs its own rate.
    assert {r["id"] for r in await identities(db, OWNER, "courier")} == {"smsa"}
    await db[SETUP].delete_one({"_id": OWNER})
    context = await readiness(db, OWNER)
    assert not context["stages"]["7"]["ready"] and not context["stages"]["8"]["ready"]


@pytest.mark.asyncio
async def test_prior_sale_with_exact_partial_payment_reclasses_only_remaining(db):
    async def previous(scoped):
        await _post(scoped, OWNER, {"id": OWNER}, "prior-partial-sale", "cod_sale", AT,
            [_leg(("customer", "customer-f", "receivable"), "debit", Decimal("500"), "ar", "cod_sale"),
             _leg(("revenue", "bnpl_sales", ""), "credit", Decimal("500"), "sale", "cod_sale")], {"order_number": "1"})
        await _post(scoped, OWNER, {"id": OWNER}, "prior-partial-payment", "customer_payment", AT,
            [_leg(("bank", "bank-f", "main"), "debit", Decimal("200"), "bank", "customer_payment"),
             _leg(("customer", "customer-f", "receivable"), "credit", Decimal("200"), "ar", "customer_payment")], {"order_number": "1"})
    await atomic_owner(db, OWNER, previous)
    await source(db, payment_actions={"remaining_action": {"remaining_amount": "300", "paid_amount": "200"}})
    assert (await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1"))["mode"] == "reclassify"
    assert (await report(db))["cod_receivable"] == "300.00"
    rows = await read_reporting_entries_v2(db, user_id=OWNER, effective_before="2026-10-01T00:00:00Z")
    assert sum(Decimal(r["amount"]) for r in rows if r["entity_type"] == "revenue") == Decimal("500")


@pytest.mark.asyncio
async def test_arabic_delivered_and_percentage_fee_vat(db):
    await source(db, status={"slug": "تم التوصيل"})
    await db[SETUP].update_one({"_id": OWNER}, {"$set": {
        "contracts.0.delivery_fee": "10.00", "contracts.0.cod_fixed_fee": "0.00",
        "contracts.0.cod_percent": "2", "contracts.0.vat_included": False,
        "contracts.0.vat_treatment": "net_plus_input_vat"}})
    result = await recognize_cod(db, owner=OWNER, actor_id=OWNER, order_number="1")
    fee = await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=result["evidence_id"])
    assert fee["costs"] == {"gross": "23.00", "expense": "20.00", "input_vat": "3.00"}
    assert (await report(db))["cod_receivable"] == "500.00"


@pytest.mark.asyncio
async def test_same_salla_id_under_changed_number_cannot_duplicate_delivery_fee(db):
    await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    await source(db, "2", id="salla-1")
    with pytest.raises(HTTPException, match="shipping_recognition_source_changed"):
        await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="2")
    view = await report(db)
    assert (view["cod_receivable"], view["payable"]) == ("500.00", "17.25")
