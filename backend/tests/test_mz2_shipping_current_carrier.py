"""Current-carrier fee eligibility through the adapter and existing P02 reader."""
from copy import deepcopy

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from accounting_shipping_current_guard import (
    CurrentShippingError, capture_native_fee_proof, require_native_current_fee,
)
from accounting_shipping_p02 import prepare_courier_fee, ShippingAccountingError


OWNER = "synthetic-current-shipping"
AT = "2026-09-03T12:00:00+00:00"


def current(**changes):
    return {"source_kind": "order", "company_name": "iMile", "company_code": "imile-code",
            "shipment_id": "shipment-new", "tracking_number": "AWB-NEW", "status": "delivered",
            "carrier_updated_at": AT, "shipment_updated_at": AT, "superseded_shipment_ids": [], **changes}


def setup():
    return {"couriers": [{"courier_key": key, "name": name, "status": "active",
             "confirmed_by": OWNER, "confirmed_at": AT, "salla_carrier_keys": [code, name]}
             for key, name, code in (("imile", "iMile", "imile-code"), ("smsa", "SMSA", "smsa-code"))]}


def evidence(**changes):
    return {"order_id": "salla-1", "order_number": "1", "party_id": "imile", "party_type": "courier",
            "delivery_status": "delivered", "source_carrier_key": "imile-code",
            "current_shipping_fee_proof": {"schema": "current_courier_fee_proof_v1", "carrier_key": "imile-code",
                 "shipment_id": "shipment-new", "waybill": "AWB-NEW"}, **changes}


def raw():
    return {"id": "salla-1", "shipments": [{"id": "shipment-new", "tracking_number": "AWB-NEW",
             "status": "delivered", "courier": {"code": "imile-code"}}]}


@pytest_asyncio.fixture
async def db():
    database = AsyncMongoMockClient()["unit_current_shipping"]
    await database.unified_orders.insert_one({"user_id": OWNER, "order_number": "1",
        "raw_by_source": {"salla_direct": raw()}, "salla_shipping_current": current()})
    yield database


@pytest.mark.asyncio
async def test_current_native_imile_evidence_is_valid(db):
    before = await db.unified_orders.find_one({})
    await require_native_current_fee(db, owner=OWNER, evidence=evidence(), setup=setup())
    assert await db.unified_orders.find_one({}) == before
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,code", [
    ({"company_code": "store_driver", "company_name": "مندوب المتجر"}, "shipping_current_store_driver_no_courier_fee"),
    ({"company_code": "smsa-code", "company_name": "SMSA"}, "shipping_current_carrier_conflict"),
    ({"status": "archived"}, "shipping_current_shipment_inactive"),
    ({"status": "cancelled"}, "shipping_current_shipment_inactive"),
    ({"status": "returned"}, "shipping_current_shipment_inactive"),
    ({"type": "return"}, "shipping_current_shipment_inactive"),
    ({"archived": True}, "shipping_current_shipment_inactive"),
    ({"superseded_shipment_ids": ["shipment-new"]}, "shipping_current_shipment_inactive"),
    ({"shipment_id": "replacement"}, "shipping_current_shipment_evidence_required"),
    ({"tracking_number": "REPLACED-AWB"}, "shipping_current_shipment_evidence_required"),
    ({"shipment_id": None}, "shipping_current_shipment_evidence_required"),
    ({"tracking_number": None}, "shipping_current_shipment_evidence_required"),
    ({"company_code": None, "company_name": None}, "shipping_current_carrier_unresolved"),
    ({"company_code": None, "company_name": "123"}, "shipping_current_carrier_unresolved"),
    ({"source_kind": "make"}, "shipping_current_carrier_unresolved"),
    ({"company_code": "unknown-provider"}, "shipping_current_carrier_unresolved"),
    ({"company_code": "imile-code", "company_name": "SMSA"}, "shipping_current_carrier_unresolved"),
])
async def test_invalid_or_stale_current_shipping_cannot_authorize_fee(db, changes, code):
    await db.unified_orders.update_one({}, {"$set": {"salla_shipping_current": current(**changes)}})
    before = await db.unified_orders.find_one({})
    with pytest.raises(CurrentShippingError, match=code):
        await require_native_current_fee(db, owner=OWNER, evidence=evidence(), setup=setup())
    assert await db.unified_orders.find_one({}) == before


@pytest.mark.asyncio
async def test_missing_or_ambiguous_current_order_fails_closed(db):
    with pytest.raises(CurrentShippingError, match="shipping_current_order_missing"):
        await require_native_current_fee(db, owner="other-owner", evidence=evidence(), setup=setup())
    await db.unified_orders.insert_one({"user_id": OWNER, "order_number": 1})
    with pytest.raises(CurrentShippingError, match="shipping_current_order_ambiguous"):
        await require_native_current_fee(db, owner=OWNER, evidence=evidence(), setup=setup())


@pytest.mark.asyncio
async def test_missing_native_binding_or_conflicting_roots_fails_closed(db):
    with pytest.raises(CurrentShippingError, match="shipping_current_shipment_evidence_required"):
        await require_native_current_fee(db, owner=OWNER, evidence=evidence(current_shipping_fee_proof=None), setup=setup())
    await db.unified_orders.update_one({}, {"$set": {"tracking_number": "OLD-AWB"}})
    with pytest.raises(CurrentShippingError, match="shipping_current_shipping_identity_conflict"):
        await require_native_current_fee(db, owner=OWNER, evidence=evidence(), setup=setup())


@pytest.mark.asyncio
async def test_capture_binds_the_current_shipment_and_preserves_cod_facts(db):
    facts = evidence()
    before = deepcopy(facts)
    result = await capture_native_fee_proof(db, owner=OWNER, facts=facts, raw=raw())
    assert result == {"schema": "current_courier_fee_proof_v1", "carrier_key": "imile-code",
                      "shipment_id": "shipment-new", "waybill": "AWB-NEW"}
    assert facts == before
    old = raw()
    old["shipments"][0]["id"] = "archived-shipment"
    assert await capture_native_fee_proof(db, owner=OWNER, facts=facts, raw=old) is None
    assert await capture_native_fee_proof(db, owner=OWNER, facts={**facts, "source_carrier_key": "smsa-code"}, raw=raw()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["returned", "archived", "conflicting_awb", "conflicting_carrier", "multiple_representations"])
async def test_capture_requires_consistent_outbound_payload_proof(db, edit):
    payload = raw()
    if edit == "returned": payload["shipments"][0]["type"] = "return"
    elif edit == "archived": payload["shipments"][0]["archived"] = True
    elif edit == "conflicting_awb": payload["shipments"].append({"id": "shipment-new", "tracking_number": "OLD"})
    elif edit == "conflicting_carrier": payload["shipments"][0]["courier"] = {"code": "smsa-code"}
    else: payload["shipping"] = {"shipment_id": "shipment-new", "tracking_number": "AWB-NEW"}
    result = await capture_native_fee_proof(db, owner=OWNER, facts=evidence(), raw=payload)
    assert bool(result) == (edit == "multiple_representations")


async def imported(db):
    await db.unified_orders.update_one({}, {"$set": {"salla_shipping_current": current(company_code=None)}})
    await db.mz2_salla_order_evidence.insert_one({"id": "import-old", "user_id": OWNER,
        "order_number": "1", "delivery_source_text": "2026-09-03 15:00:00", "shipping_company": "iMile",
        "waybill": "AWB-NEW", "shipping_cost_source": "999.00"})
    await db.mz2_shipping_rate_policies.insert_one({"_id": OWNER, "user_id": OWNER, "revision": 1,
        "versions": [{"id": "approved-imile-rate", "courier_id": "imile", "name": "iMile",
            "aliases_normalized": ["imile"], "effective_at": "2026-09-01T00:00:00+00:00",
            "revision": 1, "verification_status": "approved", "total_fee": "17.25",
            "evidence_ref": "confirmed-rate", "tax_treatment": "gross_expense_no_input_vat"}]})


@pytest.mark.asyncio
async def test_existing_imported_preparation_uses_approved_current_rate_only(db):
    await imported(db)
    result = await prepare_courier_fee(db, owner=OWNER, evidence_id="import-old")
    assert result["state"] == "eligible"
    assert result["facts"]["total_fee"] == "17.25"
    assert result["facts"]["courier_id"] == "imile"
    assert await db.mz2_shipping_accounting_events.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["store_driver", "smsa", "replacement", "missing"])
async def test_existing_imported_preparation_rejects_old_evidence(db, change):
    await imported(db)
    newer = current(company_code=None)
    if change == "store_driver": newer["company_name"] = "مندوب المتجر"
    elif change == "smsa": newer["company_name"] = "SMSA"
    elif change == "replacement": newer["tracking_number"] = "CURRENT-NEW-AWB"
    else: newer = {}
    await db.unified_orders.update_one({}, {"$set": {"salla_shipping_current": newer}})
    before = await db.mz2_salla_order_evidence.find_one({})
    with pytest.raises(ShippingAccountingError, match="shipping_current_"):
        await prepare_courier_fee(db, owner=OWNER, evidence_id="import-old")
    assert await db.mz2_salla_order_evidence.find_one({}) == before
    assert await db.mz2_shipping_accounting_events.count_documents({}) == 0


@pytest.mark.asyncio
async def test_imported_posted_replay_ignores_carrier_change_without_rewriting(db):
    await imported(db)
    proposal = await prepare_courier_fee(db, owner=OWNER, evidence_id="import-old")
    event = {"_id": proposal["event_id"], "user_id": OWNER, "status": "posted",
             "economic_hash": proposal["economic_hash"], "txn_group_id": "immutable-journal"}
    await db.mz2_shipping_accounting_events.insert_one(event)
    await db.unified_orders.update_one({}, {"$set": {"salla_shipping_current": current(company_name="مندوب المتجر", company_code="store_driver")}})
    assert (await prepare_courier_fee(db, owner=OWNER, evidence_id="import-old"))["state"] == "already_posted"
    assert await db.mz2_shipping_accounting_events.find_one({}) == event


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["archived", "superseded", "cancelled", "returned"])
async def test_inactive_imported_evidence_cannot_prepare_new_fee(db, flag):
    await imported(db)
    await db.mz2_salla_order_evidence.update_one({}, {"$set": {flag: True}})
    with pytest.raises(ShippingAccountingError, match="shipping_current_shipment_inactive"):
        await prepare_courier_fee(db, owner=OWNER, evidence_id="import-old")


@pytest.mark.asyncio
async def test_imported_reused_waybill_needs_current_shipment_identity(db):
    await imported(db)
    await db.unified_orders.update_one({}, {"$set": {"salla_shipping_current.superseded_shipment_ids": ["old-id"]}})
    with pytest.raises(ShippingAccountingError, match="shipping_current_shipment_evidence_required"):
        await prepare_courier_fee(db, owner=OWNER, evidence_id="import-old")
    await db.mz2_salla_order_evidence.update_one({}, {"$set": {"salla_shipment_id": "shipment-new"}})
    assert (await prepare_courier_fee(db, owner=OWNER, evidence_id="import-old"))["state"] == "eligible"
