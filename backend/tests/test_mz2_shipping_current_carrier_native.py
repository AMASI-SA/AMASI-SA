"""A-I acceptance on the existing V2 writer and disposable loopback Mongo."""
import asyncio
from copy import deepcopy
import os

import pytest
from fastapi import HTTPException

from accounting_shipping_native import recognize_cod, recognize_fee_delivery, accrue_fee
from accounting_shipping_native_contract import EVIDENCE, EVENTS, SETUP
from accounting_shipping_native_setup import save_setup, read_setup
from accounting_shipping_p02 import prepare_courier_fee, ShippingAccountingError
from test_mz2_shipping_native import db, OWNER, AT, source, courier, rate

pytestmark = pytest.mark.asyncio


async def configure_imile(database):
    assert os.environ["MZ2_TEST_MONGO_URI"].startswith("mongodb://127.0.0.1:")
    state = await read_setup(database, OWNER)
    await save_setup(database, OWNER, OWNER, courier(state["version"], "imile"))
    state = await read_setup(database, OWNER)
    await save_setup(database, OWNER, OWNER, rate(state["version"], identity="imile"))
    state = await database.settings.find_one({"user_id": OWNER})
    zeroes = deepcopy(state["mezan2_financial_cutover"]["opening_balance_zero_accounts"])
    zeroes += [{**r, "entity_id": "imile"} for r in zeroes if r["entity_type"] == "courier" and r["entity_id"] == "smsa"]
    await database.settings.update_one({"user_id": OWNER}, {"$set": {
        "mezan2_financial_cutover.opening_balance_zero_accounts": zeroes}})


async def current_source(database, *, key="imile", name="iMile", ship="current-imile", awb="IMILE-CURRENT", prepaid=False):
    await source(database, payment_method="credit_card" if prepaid else "cod",
        shipping={"company": {"name": name, "code": key}, "shipment_id": ship,
                  "tracking_number": awb, "status": "delivered", "delivered_at": AT})
    await database.unified_orders.update_one({"user_id": OWNER, "order_number": "1"}, {"$set": {
        "salla_shipping_current": {"source_kind": "order", "company_code": key, "company_name": name,
             "shipment_id": ship, "tracking_number": awb, "status": "delivered",
             "carrier_updated_at": AT, "shipment_updated_at": AT, "superseded_shipment_ids": []}}})


async def snapshot(database):
    names = ("accounting_journal_groups_v2", "accounting_general_ledger_v2", "accounting_audit_log_v2", EVIDENCE, EVENTS)
    return {name: await database[name].find({}).to_list(None) for name in names}


async def seal_imile(database, *, prepaid=False):
    await configure_imile(database)
    await current_source(database, prepaid=prepaid)
    return await recognize_cod(database, owner=OWNER, actor_id=OWNER, order_number="1") if not prepaid else await recognize_fee_delivery(database, owner=OWNER, actor_id=OWNER, order_number="1")


async def change_group(database, **changes):
    row = await database.unified_orders.find_one({"user_id": OWNER, "order_number": "1"})
    current = {**row["salla_shipping_current"], **changes}
    await database.unified_orders.update_one({"_id": row["_id"]}, {"$set": {"salla_shipping_current": current}})


async def test_native_current_imile_fee_uses_existing_v2_writer(db):
    sealed = await seal_imile(db)
    before = await snapshot(db)
    result = await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=sealed["evidence_id"])
    assert result["state"] == "posted" and result["costs"]["gross"] == "17.25"
    event = await db[EVENTS].find_one({"kind": "fee"})
    assert event["party_type"] == "courier" and event["party_id"] == "imile"
    assert event["rate"]["party_id"] == "imile"
    assert await db.accounting_general_ledger_v2.count_documents({}) == len(before["accounting_general_ledger_v2"]) + 2
    assert await db[EVIDENCE].find_one({"id": sealed["evidence_id"]}) == before[EVIDENCE][0]


async def test_native_old_imile_evidence_cannot_fee_store_driver(db):
    sealed = await seal_imile(db)
    await change_group(db, company_code="store_driver", company_name="مندوب المتجر",
                       shipment_id=None, tracking_number=None, superseded_shipment_ids=["current-imile"])
    before = await snapshot(db)
    with pytest.raises(HTTPException) as denied:
        await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=sealed["evidence_id"])
    assert denied.value.detail["code"] == "shipping_current_store_driver_no_courier_fee"
    assert await snapshot(db) == before
    assert await db[EVENTS].count_documents({"kind": "fee"}) == 0


async def test_changed_imile_to_smsa_rejects_import_and_requires_current_smsa_proof(db):
    await configure_imile(db)
    await current_source(db)
    imported = {"id": "import-imile-old", "user_id": OWNER, "order_number": "1", "shipping_company": "iMile",
                "waybill": "IMILE-CURRENT", "delivery_source_text": "2026-09-03 15:00:00"}
    await db.mz2_salla_order_evidence.insert_one(imported)
    await db.mz2_shipping_rate_policies.insert_one({"_id": OWNER, "versions": [{"id": "imile-old-rate", "courier_id": "imile",
        "name": "iMile", "aliases_normalized": ["imile"], "effective_at": "2026-09-01T00:00:00+00:00",
        "revision": 1, "verification_status": "approved", "total_fee": "17.25", "evidence_ref": "synthetic",
        "tax_treatment": "gross_expense_no_input_vat"}]})
    await current_source(db, key="smsa-code", name="SMSA", ship="current-smsa", awb="SMSA-CURRENT", prepaid=True)
    await change_group(db, superseded_shipment_ids=["current-imile"], status="pending")
    with pytest.raises(ShippingAccountingError, match="shipping_current_"):
        await prepare_courier_fee(db, owner=OWNER, evidence_id=imported["id"])
    # Missing proof for the new shipment cannot be replaced by the old import.
    before = await snapshot(db)
    with pytest.raises(HTTPException, match="shipping_current_shipment_inactive"):
        await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert await db[EVENTS].count_documents({"kind": "fee"}) == 0
    # The existing delivery sealer never rewrites its old evidence. A new
    # unposted incomplete evidence remains closed until explicitly reviewed.
    assert await db[EVIDENCE].count_documents({}) == 1
    assert await db.mz2_salla_order_evidence.find_one({"id": imported["id"]}) == imported


async def test_valid_current_smsa_can_fee_while_old_imile_import_is_preserved(db):
    await configure_imile(db)
    await current_source(db)
    imported = {"id": "import-imile-old", "user_id": OWNER, "order_number": "1", "shipping_company": "iMile", "waybill": "IMILE-CURRENT"}
    await db.mz2_salla_order_evidence.insert_one(imported)
    await current_source(db, key="smsa-code", name="SMSA", ship="current-smsa", awb="SMSA-CURRENT", prepaid=True)
    await change_group(db, superseded_shipment_ids=["current-imile"])
    result = await recognize_fee_delivery(db, owner=OWNER, actor_id=OWNER, order_number="1")
    assert result["fee"]["costs"]["gross"] == "17.25"
    event = await db[EVENTS].find_one({"kind": "fee"})
    assert event["party_id"] == event["rate"]["party_id"] == "smsa"
    assert await db[EVENTS].count_documents({"kind": "fee", "party_id": "imile"}) == 0
    assert await db.mz2_salla_order_evidence.find_one({"id": imported["id"]}) == imported


@pytest.mark.parametrize("changes", [
    {"archived": True}, {"status": "cancelled"}, {"type": "return"},
    {"superseded_shipment_ids": ["current-imile"]},
    {"shipment_id": "new-same-carrier", "tracking_number": "NEW-SAME-CARRIER"},
])
async def test_inactive_or_replaced_same_carrier_shipment_cannot_create_new_fee(db, changes):
    sealed = await seal_imile(db)
    await change_group(db, **changes)
    before = await snapshot(db)
    with pytest.raises(HTTPException, match="shipping_current_"):
        await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=sealed["evidence_id"])
    assert await snapshot(db) == before


async def test_posted_fee_replay_never_rewrites_history_after_carrier_change(db):
    first = await seal_imile(db, prepaid=True)
    await change_group(db, company_code="store_driver", company_name="مندوب المتجر",
                       shipment_id=None, tracking_number=None)
    before = await snapshot(db)
    replay = await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=first["evidence_id"])
    assert replay["state"] == "already_posted"
    assert replay["txn_group_id"] == first["fee"]["txn_group_id"]
    assert await snapshot(db) == before


@pytest.mark.parametrize("mode", ["missing", "ambiguous"])
async def test_missing_or_ambiguous_current_carrier_creates_no_fee(db, mode):
    sealed = await seal_imile(db)
    if mode == "missing":
        await db.unified_orders.update_one({"user_id": OWNER}, {"$unset": {"salla_shipping_current": ""}})
    else:
        await db.unified_orders.insert_one({"user_id": OWNER, "order_number": 1})
    before = await snapshot(db)
    with pytest.raises(HTTPException, match="shipping_current_"):
        await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=sealed["evidence_id"])
    assert await snapshot(db) == before


async def test_concurrent_fee_retries_produce_one_existing_v2_journal(db):
    sealed = await seal_imile(db)
    before = await snapshot(db)
    results = await asyncio.gather(*(accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=sealed["evidence_id"]) for _ in range(5)))
    assert sum(r["state"] == "posted" for r in results) == 1
    assert sum(r["state"] == "already_posted" for r in results) == 4
    assert len({r["txn_group_id"] for r in results}) == 1
    assert await db[EVENTS].count_documents({"kind": "fee"}) == 1
    assert await db.accounting_general_ledger_v2.count_documents({}) == len(before["accounting_general_ledger_v2"]) + 2


def event_payload(row, *, name, key, version, shipment):
    raw = deepcopy(row["raw_by_source"]["salla_direct"])
    raw["updated_at"] = version
    raw["shipping"] = {"company": {"name": name, "code": key}}
    raw["shipments"] = [{"id": shipment, "tracking_number": "AWB-" + shipment, "status": "delivered",
                         "courier": {"name": name, "code": key}, "updated_at": version}]
    return raw


async def operational_ingest(database, payload):
    from orders_db import upsert_order
    from salla_integration.sync import _salla_order_to_doc
    return await upsert_order(database, OWNER, "1", _salla_order_to_doc(payload), "salla_direct", raw=payload)


async def test_latest_authorized_carrier_wins_over_late_old_provider_event(db):
    sealed = await seal_imile(db)
    row = await db.unified_orders.find_one({"user_id": OWNER, "order_number": "1"})
    newer = event_payload(row, name="مندوب المتجر", key="store_driver", version="2026-09-05T12:00:00Z", shipment="driver-current")
    stale = event_payload(row, name="iMile", key="imile", version="2026-09-04T12:00:00Z", shipment="current-imile")
    await operational_ingest(db, newer)
    await operational_ingest(db, stale)
    current = (await db.unified_orders.find_one({"user_id": OWNER, "order_number": "1"}))["salla_shipping_current"]
    assert current["company_code"] == "store_driver"
    before = await snapshot(db)
    with pytest.raises(HTTPException, match="shipping_current_store_driver_no_courier_fee"):
        await accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=sealed["evidence_id"])
    assert await snapshot(db) == before


async def test_fee_waits_for_operational_carrier_commit_then_refuses_old_evidence(db):
    from operational_atomic import operational_owner
    sealed = await seal_imile(db)
    row = await db.unified_orders.find_one({"user_id": OWNER, "order_number": "1"})
    payload = event_payload(row, name="مندوب المتجر", key="store_driver", version="2026-09-05T12:00:00Z", shipment="driver-current")
    persisted, release = asyncio.Event(), asyncio.Event()
    async def intake(scoped):
        await operational_ingest(scoped, payload)
        persisted.set()
        await asyncio.wait_for(release.wait(), 5)
    carrier = asyncio.create_task(operational_owner(db, OWNER, intake))
    await asyncio.wait_for(persisted.wait(), 5)
    before = await snapshot(db)
    fee = asyncio.create_task(accrue_fee(db, owner=OWNER, actor_id=OWNER, evidence_id=sealed["evidence_id"]))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(fee), .15)
    finally:
        release.set()
        await carrier
    with pytest.raises(HTTPException, match="shipping_current_store_driver_no_courier_fee"):
        await fee
    assert await snapshot(db) == before
