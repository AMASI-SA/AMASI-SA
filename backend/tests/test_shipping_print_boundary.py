"""Print boundary regression fixtures; order numbers/AWBs are owner-reported,
not Production snapshots. All other values are synthetic. No provider writes.
"""
from copy import deepcopy
import asyncio
import os
from urllib.parse import urlparse
import uuid

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient

import order_engine.shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING
from test_shipping_label_current_guard import seed, OWNER, ORDER


@pytest_asyncio.fixture(params=["memory", "mongo"])
async def database(request, monkeypatch):
    if request.param == "mongo":
        uri = os.environ.get("BUILD37_TEST_MONGO_URI")
        if not uri:
            pytest.skip("isolated loopback replica set not configured")
        assert urlparse(uri).hostname in {"localhost", "127.0.0.1"}
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    else:
        client = AsyncMongoMockClient()
        async def owner(db, user, callback, **kwargs):
            assert user == OWNER
            return await callback(db)
        monkeypatch.setattr(shipping, "operational_owner", owner)
    db = client["shipping_print_test_" + uuid.uuid4().hex]
    try:
        yield db
    finally:
        await client.drop_database(db.name)
        client.close()


async def providers(monkeypatch, *, rows=None, order=None, resync=None):
    async def resolve(*args):
        return "salla-synthetic", deepcopy(order or {"status": "completed", "shipments": []})
    async def shipments(*args):
        if rows is None:
            pytest.fail("internal delivery must not request external shipments")
        return deepcopy(rows)
    async def noop(*args):
        return None
    async def store(*args):
        return {"name": "Test store"}
    async def forbidden(*args, **kwargs):
        pytest.fail("no provider mutation expected")
    monkeypatch.setattr(shipping, "_resolve_order", resolve)
    monkeypatch.setattr(shipping, "_shipment_rows", shipments)
    monkeypatch.setattr(shipping, "_best_effort_resync", resync or noop)
    monkeypatch.setattr(shipping, "_store_identity", store)
    monkeypatch.setattr(shipping, "call_salla", forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["issue_shipping_label", "refresh_shipping_label"])
@pytest.mark.parametrize("previous", [False, True])
async def test_internal_print_needs_no_external_shipment_or_awb(database, monkeypatch, action, previous):
    await seed(database, company="مندوب المتجر", code="0", superseded=["old-imile"])
    order = {"status": "completed", "shipping": {"company_name": "مندوب المتجر", "company_code": "0",
        "address": {"city": "Current city", "address_line": "Current address"}},
        "customer": {"full_name": "Fixture", "mobile": "000"},
        "shipments": [{"id": "old-imile", "courier_name": "iMile", "ship_to": {"address_line": "OLD"}}] if previous else []}
    await providers(monkeypatch, order=order)
    result = await getattr(shipping, action)(database, OWNER, ORDER)
    assert result["ready"] and result["label_type"] == "store_courier"
    assert result["tracking_number"] is None and result["shipment_id"] is None
    assert result["print_data"]["address"]["address_line"] == "Current address"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["issue_shipping_label", "refresh_shipping_label"])
@pytest.mark.parametrize("new_code", ["imile", "smsa"])
async def test_issue_resync_is_preserved_but_legacy_print_does_not_sync(database, monkeypatch, action, new_code):
    await seed(database, shipment_id="old")
    calls = []
    async def sync(db, *_args):
        calls.append("sync")
        await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
            f"{CURRENT_SHIPPING}.company_code": new_code, f"{CURRENT_SHIPPING}.company_name": new_code,
            f"{CURRENT_SHIPPING}.shipment_id": "current", "salla_shipment_id": "current",
            f"{CURRENT_SHIPPING}.superseded_shipment_ids": ["old"]}})
    rows = [{"id": "current", "courier_id": new_code, "courier_name": new_code, "status": "created",
        "updated_at": "2026-10-01T10:02:00Z", "tracking_number": "NEW-AWB", "label_url": "https://labels.test/current.pdf"}]
    await providers(monkeypatch, rows=rows, resync=sync)
    result = await getattr(shipping, action)(database, OWNER, ORDER)
    assert result["ready"] and result["shipment_id"] == "current"
    assert calls == (["sync"] if action == "issue_shipping_label" else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("order_number,awb", [("289131568", "6100326425847"), ("290336917", "6100226225106")])
async def test_reported_imile_identities_synthetic_fixture(database, monkeypatch, order_number, awb):
    await seed(database)
    await database.unified_orders.update_one({"user_id": OWNER}, {"$set": {
        "order_number": order_number, "tracking_number": awb, f"{CURRENT_SHIPPING}.tracking_number": awb}})
    rows = [{"id": "new-id", "courier_id": "imile", "courier_name": "iMile", "status": "created",
        "updated_at": "2026-10-01T10:02:00Z", "tracking_number": awb, "label_url": "https://labels.test/current.pdf"}]
    await providers(monkeypatch, rows=rows)
    result = await shipping.issue_shipping_label(database, OWNER, order_number)
    assert result["ready"] and result["tracking_number"] == awb


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["shipment", "carrier", "awb", "superseded", "timestamp"])
async def test_concurrent_or_stale_external_facts_remain_blocked(database, monkeypatch, change):
    await seed(database)
    rows = [{"id": "new-id", "courier_id": "imile", "courier_name": "iMile", "status": "created",
        "updated_at": "2026-10-01T10:02:00Z", "tracking_number": "NEW-AWB", "label_url": "https://labels.test/current.pdf"}]
    await providers(monkeypatch, rows=rows)
    async def resolve(*args):
        updates = {
            "shipment": {f"{CURRENT_SHIPPING}.shipment_id": "replacement", "salla_shipment_id": "replacement"},
            "carrier": {f"{CURRENT_SHIPPING}.company_code": "smsa"},
            "awb": {f"{CURRENT_SHIPPING}.tracking_number": "REPLACED", "tracking_number": "REPLACED"},
            "superseded": {f"{CURRENT_SHIPPING}.superseded_shipment_ids": ["new-id"]},
            "timestamp": {f"{CURRENT_SHIPPING}.shipment_updated_at": "2026-10-01T10:03:00Z"},
        }[change]
        await database.unified_orders.update_one({"user_id": OWNER}, {"$set": updates})
        return "salla-synthetic", {"status": "completed"}
    monkeypatch.setattr(shipping, "_resolve_order", resolve)
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping.issue_shipping_label(database, OWNER, ORDER)
    assert caught.value.code == "shipping_snapshot_changed"
    assert await database.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_store_switch_during_document_fetch_is_blocked(database, monkeypatch):
    await seed(database, company="مندوب المتجر", code="0")
    await providers(monkeypatch)
    async def store(*args):
        await database.unified_orders.update_one({"user_id": OWNER}, {"$set": {
            f"{CURRENT_SHIPPING}.company_code": "imile", f"{CURRENT_SHIPPING}.company_name": "iMile"}})
        return {}
    monkeypatch.setattr(shipping, "_store_identity", store)
    with pytest.raises(shipping.ShippingLabelError) as caught:
        await shipping.issue_shipping_label(database, OWNER, ORDER)
    assert caught.value.code == "shipping_snapshot_changed"


@pytest.mark.asyncio
async def test_store_document_reads_current_assignment(database, monkeypatch):
    await seed(database, company="مندوب المتجر", code="0")
    await database.order_review_workflows.insert_one({"user_id": OWNER, "order_number": ORDER,
        "store_courier_assignee_id": "current-driver", "store_courier_assignee_name": "Current driver",
        "store_delivery_assignment_id": "assignment-current"})
    await providers(monkeypatch)
    result = await shipping.issue_shipping_label(database, OWNER, ORDER)
    assert result["print_data"]["assigned_courier_id"] == "current-driver"
    assert result["print_data"]["assignment_id"] == "assignment-current"


@pytest.mark.asyncio
async def test_canonical_shipment_wins_over_old_larger_id(database, monkeypatch):
    await seed(database, shipment_id="10", superseded=["999"])
    rows = [{"id": sid, "courier_id": "imile", "courier_name": "iMile", "status": "created",
        "updated_at": "2026-10-01T10:02:00Z", "tracking_number": awb,
        "label_url": "https://labels.test/" + sid + ".pdf"} for sid, awb in [("999", "OLD"), ("10", "NEW-AWB")]]
    await providers(monkeypatch, rows=rows)
    result = await shipping.issue_shipping_label(database, OWNER, ORDER)
    assert result["shipment_id"] == "10" and result["tracking_number"] == "NEW-AWB"


@pytest.mark.asyncio
async def test_imile_to_internal_during_own_sync(database, monkeypatch):
    await seed(database)
    async def sync(db, *_args):
        await db.unified_orders.update_one({"user_id": OWNER}, {"$set": {
            f"{CURRENT_SHIPPING}.company_code": "0", f"{CURRENT_SHIPPING}.company_name": "مندوب المتجر"}})
    await providers(monkeypatch, resync=sync)
    result = await shipping.issue_shipping_label(database, OWNER, ORDER)
    assert result["label_type"] == "store_courier" and result["tracking_number"] is None


@pytest.mark.asyncio
async def test_repeated_and_concurrent_external_print(database, monkeypatch):
    await seed(database)
    await providers(monkeypatch, rows=[{"id": "new-id", "courier_id": "imile", "courier_name": "iMile",
        "status": "created", "tracking_number": "NEW-AWB", "label_url": "https://labels.test/current.pdf",
        "updated_at": "2026-10-01T10:02:00Z"}])
    results = await asyncio.gather(*(shipping.issue_shipping_label(database, OWNER, ORDER) for _ in range(2)))
    results.append(await shipping.issue_shipping_label(database, OWNER, ORDER))
    assert all(r["ready"] and r["tracking_number"] == "NEW-AWB" for r in results)
    assert await database.unified_orders.count_documents({}) == 1
    assert await database.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_transaction_failure_leaves_no_partial_label(database, monkeypatch, request):
    if request.node.callspec.params["database"] != "mongo":
        pytest.skip("transaction rollback requires real Mongo")
    before = await seed(database)
    collection_type = type(database.unified_orders)
    original = collection_type.update_one
    async def fail_after_write(collection, selector, update, **kwargs):
        result = await original(collection, selector, update, **kwargs)
        if collection.name == "unified_orders" and "shipping_verified_at" in update.get("$set", {}):
            raise RuntimeError("isolated failure after label write")
        return result
    monkeypatch.setattr(collection_type, "update_one", fail_after_write)
    with pytest.raises(RuntimeError, match="isolated failure"):
        await shipping._persist_verified_snapshot(database, OWNER, ORDER, {
            "shipment_id": "new-id", "courier_code": "imile", "courier_name": "iMile",
            "status": "created", "ready": True, "tracking_number": "NEW-AWB",
            "label_url": "https://labels.test/current.pdf"})
    assert await database.unified_orders.find_one({"user_id": OWNER}) == before
    assert await database.mz2_atomic_owners.count_documents({}) == 0
