"""Current carrier facts must survive old shipment history and sparse events."""
import asyncio
import copy
import logging
from unittest.mock import AsyncMock
import pytest
from mongomock_motor import AsyncMongoMockClient

from orders_db import upsert_order
from order_engine.mapper import map_salla_order
from order_engine.repository import MongoOrderRepository
from salla_integration.sync import _salla_order_to_doc
from salla_integration.webhook_order_sync import sync_shipment_payload_from_verified_webhook, sync_order_from_verified_webhook
from salla_shipping import CURRENT_SHIPPING, accept_shipping, extract_shipping, outbound_shipment
from salla_integration import webhook_event_capture as capture


def order_payload(company="iMile", version="2026-10-01T09:00:00Z", **extra):
    return {
        "id": "9001", "reference_id": "3001", "date": "2026-10-01T08:00:00Z", "updated_at": version,
        "shipping": {"company": {"name": company, "id": "old" if company == "iMile" else "new"}},
        **extra,
    }


@pytest.fixture
async def db(monkeypatch):
    async def local_owner(database, _owner, callback, **_kwargs):
        return await callback(database)
    monkeypatch.setattr("operational_atomic.operational_owner", local_owner)
    database = AsyncMongoMockClient(tz_aware=True).test_shipping
    await database.unified_orders.create_index([("user_id", 1), ("order_number", 1)], unique=True)
    await database.salla_integrations.insert_one({"user_id": "u1", "store_id": "merchant"})
    return database


async def ingest(db, payload, source="salla_direct"):
    return await upsert_order(db, "u1", "3001", _salla_order_to_doc(payload), source, raw=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("make_touched", [False, True])
async def test_changed_carrier_updates_root_and_dto_even_after_make(db, make_touched):
    await ingest(db, order_payload())
    if make_touched:
        await db.unified_orders.update_one({"user_id": "u1"}, {"$set": {"last_make_update_at": "earlier"}})
    await ingest(db, order_payload("مندوب الرياض", "2026-10-01T10:00:00Z"))
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"
    raw = await MongoOrderRepository(db).get_salla_order(user_id="u1", order_number="3001")
    assert map_salla_order(raw.salla_raw).shipping.company == "مندوب الرياض"


def test_order_current_carrier_wins_over_cancelled_old_shipment():
    raw = order_payload("مندوب الرياض", shipments=[{
        "id": "s-old", "status": "cancelled", "courier_name": "iMile", "courier_id": "old",
        "tracking_number": "OLD-AWB", "label_url": "https://labels.example/old.pdf",
    }])
    doc = _salla_order_to_doc(raw)
    dto = map_salla_order(raw)
    assert doc["shipping_company"] == "مندوب الرياض"
    assert dto.shipping.company == "مندوب الرياض"
    assert dto.shipping.tracking_number is None
    assert dto.shipping.label_url is None


@pytest.mark.asyncio
async def test_old_and_sparse_snapshots_do_not_regress_new_carrier(db):
    await ingest(db, order_payload("مندوب الرياض", "2026-10-01T10:00:00Z"))
    await ingest(db, order_payload("iMile", "2026-10-01T09:00:00Z"))
    raw = await MongoOrderRepository(db).get_salla_order(user_id="u1", order_number="3001")
    assert map_salla_order(raw.salla_raw).shipping.company == "مندوب الرياض"
    await ingest(db, {"id": "9001", "reference_id": "3001", "date": "2026-10-01T08:00:00Z", "updated_at": "2026-10-01T11:00:00Z", "shipments": []})
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"
    raw = await MongoOrderRepository(db).get_salla_order(user_id="u1", order_number="3001")
    assert map_salla_order(raw.salla_raw).shipping.company == "مندوب الرياض"


@pytest.mark.asyncio
async def test_carrier_switch_clears_old_identity_and_label(db):
    await ingest(db, order_payload(shipments=[{
        "id": "s-old", "courier_name": "iMile", "courier_id": "old", "courier_logo": "old-logo",
        "status": "created", "tracking_number": "OLD-AWB", "label_url": "https://labels.example/old.pdf",
    }]))
    await ingest(db, {"id": "9001", "reference_id": "3001", "date": "2026-10-01T08:00:00Z", "updated_at": "2026-10-01T10:00:00Z", "shipping": {"company_name": "مندوب الرياض"}})
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"
    for key in ("shipping_company_code", "shipping_company_logo", "shipping_label_url", "tracking_number", "salla_shipment_id"):
        assert row.get(key) is None
    raw = await MongoOrderRepository(db).get_salla_order(user_id="u1", order_number="3001")
    dto = map_salla_order(raw.salla_raw)
    assert dto.shipping.company_code is None
    assert dto.shipping.label_url is None


@pytest.mark.asyncio
async def test_return_shipment_does_not_replace_outbound_carrier(db):
    await ingest(db, order_payload("مندوب الرياض", "2026-10-01T10:00:00Z"))
    before = await db.unified_orders.find_one({"user_id": "u1"})
    await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.return.creating", "merchant": "merchant",
        "created_at": "2026-10-01T11:00:00Z", "data": {
            "id": "return-id", "order_reference_id": "3001", "order_id": "9001",
            "courier_name": "iMile", "courier_id": "old", "type": "return", "status": "creating",
        },
    })
    after = await db.unified_orders.find_one({"user_id": "u1"})
    for key in ("shipping_company", "shipping_company_code", "salla_shipment_id", "shipping_status"):
        assert after.get(key) == before.get(key)


@pytest.mark.asyncio
async def test_obsolete_shipment_cancellation_does_not_cancel_replacement(db):
    await ingest(db, order_payload("مندوب الرياض", "2026-10-01T10:00:00Z", shipments=[{
        "id": "s-new", "courier_name": "مندوب الرياض", "courier_id": "new",
        "status": "created", "tracking_number": "NEW-AWB", "label_url": "https://labels.example/new.pdf",
    }]))
    await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.cancelled", "merchant": "merchant", "created_at": "2026-10-01T11:00:00Z",
        "data": {"id": "s-old", "order_reference_id": "3001", "courier_name": "iMile", "status": "cancelled"},
    })
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"
    assert row.get("salla_shipment_id") == "s-new"
    assert row.get("tracking_number") == "NEW-AWB"


@pytest.mark.asyncio
async def test_current_shipping_reaches_repository_detail_and_list_projection(db):
    await ingest(db, order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old", "tracking_number": "OLD"}]))
    await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.created", "merchant": "merchant", "created_at": "2026-10-01T10:00:00Z",
        "data": {"id": "s-new", "order_reference_id": "3001", "courier_name": "مندوب الرياض", "courier_id": "new", "tracking_number": "NEW"},
    })
    repo = MongoOrderRepository(db)
    details = await repo.get_salla_order(user_id="u1", order_number="3001")
    rows = await repo.list_salla_orders(user_id="u1", limit=10)
    for raw in (details.salla_raw, rows[0].salla_raw):
        dto = map_salla_order(raw)
        assert dto.shipping.company == "مندوب الرياض"
        assert dto.shipping.tracking_number == "NEW"
    stored = await db.unified_orders.find_one({"user_id": "u1"})
    assert stored["raw_by_source"]["salla_direct"]["shipments"][0]["id"] == "s-old"


@pytest.mark.asyncio
async def test_code_only_new_carrier_does_not_resurrect_old_name(db):
    await ingest(db, order_payload())
    payload = order_payload(version="2026-10-01T10:00:00Z")
    payload["shipping"] = {"company": {"id": "new"}}
    await ingest(db, payload)
    raw = await MongoOrderRepository(db).get_salla_order(user_id="u1", order_number="3001")
    dto = map_salla_order(raw.salla_raw)
    assert dto.shipping.company is None
    assert dto.shipping.company_code == "new"


@pytest.mark.asyncio
async def test_salla_date_updated_is_a_provider_version(db):
    await ingest(db, order_payload())
    payload = order_payload("مندوب الرياض")
    payload.pop("updated_at")
    payload["date"] = {"created": "2026-10-01T08:00:00Z", "updated": {"date": "2026-10-01 13:00:00", "timezone": "Asia/Riyadh"}}
    await ingest(db, payload)
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"
    assert row[CURRENT_SHIPPING]["provider_updated_at"] == "2026-10-01T10:00:00+00:00"


def test_new_shipment_cannot_inherit_old_delivery_status_or_label():
    old = extract_shipping(order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old", "status": "delivered", "tracking_number": "OLD"}]))
    new = extract_shipping({"id": "s-new", "courier_name": "iMile", "courier_id": "old", "updated_at": "2026-10-01T10:00:00Z"}, event_name="order.shipment.created")
    accepted = accept_shipping({CURRENT_SHIPPING: old, "salla_shipment_id": "s-old"}, new)
    assert accepted["status"] == "pending"
    assert accepted["tracking_number"] is None
    assert accepted["shipment_id"] == "s-new"


def test_new_order_envelope_cannot_reactivate_superseded_embedded_awb():
    old = order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old", "status": "delivered", "tracking_number": "OLD"}])
    current = accept_shipping({}, extract_shipping(old))
    current = accept_shipping({CURRENT_SHIPPING: current}, extract_shipping({"id": "s-new", "courier_name": "iMile", "courier_id": "old", "updated_at": "2026-10-01T10:00:00Z", "status": "creating"}, event_name="order.shipment.created"))
    old["updated_at"] = "2026-10-01T11:00:00Z"
    accepted = accept_shipping({CURRENT_SHIPPING: current}, extract_shipping(old))
    assert accepted["shipment_id"] == "s-new"
    assert accepted["status"] == "creating"
    assert accepted["tracking_number"] is None
    assert "s-old" in accepted["superseded_shipment_ids"]


@pytest.mark.asyncio
async def test_normalized_salla_intake_preserves_explicit_code_logo_and_tracking(db):
    await upsert_order(db, "u1", "3001", {"shipping_company": "iMile", "shipping_company_code": "known-id", "shipping_company_logo": "logo", "salla_shipment_id": "known-shipment", "tracking_number": "AWB"}, "salla_direct")
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company_code"] == "known-id"
    assert row["shipping_company_logo"] == "logo"
    assert row["salla_shipment_id"] == "known-shipment"
    assert row["tracking_number"] == "AWB"


def test_latest_active_outbound_selection_excludes_returns_and_cancelled():
    rows = [
        {"id": "old", "courier_name": "iMile", "updated_at": "2026-10-01T08:00:00Z"},
        {"id": "new", "courier_name": "iMile للتوصيل", "updated_at": "2026-10-01T09:00:00Z"},
        {"id": "return", "courier_name": "iMile", "type": "return", "updated_at": "2026-10-01T11:00:00Z"},
        {"id": "cancel", "courier_name": "iMile", "status": "cancelled", "updated_at": "2026-10-01T12:00:00Z"},
    ]
    assert outbound_shipment({"shipments": rows}, {"company_name": "iMile"})["id"] == "new"


@pytest.mark.asyncio
async def test_later_old_shipment_event_cannot_restore_superseded_carrier(db):
    await ingest(db, order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old"}]))
    await ingest(db, order_payload("مندوب الرياض", "2026-10-01T10:00:00Z"))
    result = await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.updated", "merchant": "merchant", "created_at": "2026-10-01T12:00:00Z",
        "data": {"id": "s-old", "order_reference_id": "3001", "courier_name": "iMile", "courier_id": "old", "tracking_number": "OLD"},
    })
    assert result["order_modified"] is False
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"


@pytest.mark.asyncio
async def test_current_cancellation_clears_label_and_is_idempotent(db):
    await ingest(db, order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old", "tracking_number": "OLD", "label_url": "https://labels.example/old.pdf"}]))
    event = {"event": "order.shipment.cancelled", "merchant": "merchant", "created_at": "2026-10-01T10:00:00Z", "data": {"id": "s-old", "order_reference_id": "3001"}}
    await sync_shipment_payload_from_verified_webhook(db, event)
    replay = await sync_shipment_payload_from_verified_webhook(db, event)
    assert replay["order_modified"] is False
    raw = await MongoOrderRepository(db).get_salla_order(user_id="u1", order_number="3001")
    dto = map_salla_order(raw.salla_raw)
    assert dto.shipping.status == "cancelled"
    assert dto.shipping.label_url is None
    assert dto.shipping.tracking_number is None
    assert (await db.unified_orders.find_one({"user_id": "u1"}))["shipping_number"] is None


@pytest.mark.asyncio
async def test_conflicting_order_identity_is_rejected_without_cross_owner_write(db):
    await ingest(db, order_payload())
    await db.unified_orders.insert_one({"user_id": "u2", "order_number": "3001", "shipping_company": "other-owner"})
    result = await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.created", "merchant": "merchant", "created_at": "2026-10-01T10:00:00Z",
        "data": {"id": "s-new", "order_reference_id": "3001", "order_id": "wrong", "courier_name": "مندوب الرياض"},
    })
    assert result["reason"] == "shipment_order_identity_conflict"
    assert (await db.unified_orders.find_one({"user_id": "u2"}))["shipping_company"] == "other-owner"
    assert (await db.unified_orders.find_one({"user_id": "u1"}))["shipping_company"] == "iMile"


@pytest.mark.asyncio
@pytest.mark.parametrize("already_exists", [False, True])
async def test_concurrent_old_intake_cannot_overwrite_new_carrier(db, already_exists):
    if already_exists:
        await ingest(db, order_payload())
    read_done, release = asyncio.Event(), asyncio.Event()

    class PausedCollection:
        paused = False
        async def find_one(self, *args, **kwargs):
            row = await db.unified_orders.find_one(*args, **kwargs)
            if not self.paused:
                self.paused = True
                read_done.set()
                await release.wait()
            return row
        def __getattr__(self, name):
            return getattr(db.unified_orders, name)

    class PausedDB:
        unified_orders = PausedCollection()
        def __getitem__(self, name):
            return self.unified_orders if name == "unified_orders" else db[name]
        def __getattr__(self, name):
            return getattr(db, name)

    old_task = asyncio.create_task(ingest(PausedDB(), order_payload()))
    await asyncio.wait_for(read_done.wait(), 2)
    await ingest(db, order_payload("مندوب الرياض", "2026-10-01T10:00:00Z"))
    release.set()
    await asyncio.wait_for(old_task, 2)
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"


@pytest.mark.asyncio
async def test_late_make_cannot_restore_shipping_or_change_financial_collections(db):
    await ingest(db, order_payload("مندوب الرياض", "2026-10-01T10:00:00Z"))
    await upsert_order(db, "u1", "3001", {"shipping_company": "iMile", "tracking_number": "OLD", "shipping_label_url": "old"}, "make")
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"
    assert row.get("tracking_number") is None
    populated = {name for name in await db.list_collection_names() if await db[name].count_documents({})}
    assert populated <= {"unified_orders", "salla_integrations"}


@pytest.mark.asyncio
async def test_ambiguous_internal_order_id_has_no_shipping_write(db):
    await ingest(db, order_payload())
    await db.unified_orders.insert_one({"user_id": "u1", "order_number": "3002", "order_id": "9001", "shipping_company": "other-order"})
    result = await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.created", "merchant": "merchant", "created_at": "2026-10-01T10:00:00Z",
        "data": {"id": "s-new", "order_id": "9001", "courier_name": "مندوب الرياض"},
    })
    assert result["reason"] == "shipment_order_identity_ambiguous"
    assert (await db.unified_orders.find_one({"order_number": "3001"}))["shipping_company"] == "iMile"
    assert (await db.unified_orders.find_one({"order_number": "3002"}))["shipping_company"] == "other-order"


async def local_source_persist(database, *, persist, **_kwargs):
    # Exercise real intake/read code; the transactional/G47 boundary has its
    # own replica-set suite and is intentionally not simulated here.
    return await persist(database)


@pytest.mark.asyncio
async def test_verified_order_update_persists_new_carrier_without_provider_calls(db, monkeypatch):
    monkeypatch.setattr("fulfillment_v2_routes.persist_component_source_snapshot", local_source_persist)
    monkeypatch.setattr("fulfillment_v2_routes.auto_route_instant_order", AsyncMock(return_value={"promoted": False}))
    monkeypatch.setattr("first_party_attribution.link_order_attribution", AsyncMock(return_value={}))
    monkeypatch.setattr("mezan_attribution_ledger_sync.safe_sync_order_to_attribution_ledger", AsyncMock(return_value={}))
    provider = AsyncMock(side_effect=AssertionError("webhook must not fetch Salla"))
    monkeypatch.setattr("salla_integration.service.call_salla", provider)
    await ingest(db, order_payload())
    result = await sync_order_from_verified_webhook(db, {"event": "order.updated", "merchant": "merchant", "data": order_payload("مندوب الرياض", "2026-10-01T10:00:00Z")})
    assert result["synced"] is True
    assert (await db.unified_orders.find_one({"user_id": "u1"}))["shipping_company"] == "مندوب الرياض"
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_light_refresh_preserves_verified_current_carrier_and_rich_items(db, monkeypatch):
    from order_engine.salla_refresh import refresh_order_from_salla
    monkeypatch.setattr("fulfillment_v2_routes.persist_component_source_snapshot", local_source_persist)
    monkeypatch.setattr("fulfillment_v2_routes.auto_route_instant_order", AsyncMock(return_value={"promoted": False}))
    await ingest(db, order_payload(items=[{"id": "i1", "name": "test item", "quantity": 1, "options": [{"name": "gift", "value": {"name": "yes"}}]}]))
    await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.created", "merchant": "merchant", "created_at": "2026-10-01T10:00:00Z",
        "data": {"id": "s-new", "order_reference_id": "3001", "courier_name": "مندوب الرياض", "courier_id": "new"},
    })
    paths = []
    async def provider(_db, _user, _method, path, **_kwargs):
        paths.append(path)
        if path == "/orders/9001":
            return {"data": {"id": "9001", "reference_id": "3001", "date": "2026-10-01T08:00:00Z", "updated_at": "2026-10-01T11:00:00Z"}}
        if path == "/orders/items":
            return {"data": []}
        raise AssertionError(path)
    monkeypatch.setattr("order_engine.salla_refresh.call_salla", provider)
    result = await refresh_order_from_salla(db, "u1", "3001", force=True)
    assert result["ok"] is True
    assert paths == ["/orders/9001", "/orders/items"]
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["shipping_company"] == "مندوب الرياض"
    assert row["raw_by_source"]["salla_direct"]["items"][0]["options"][0]["value"]["name"] == "yes"
    raw = await MongoOrderRepository(db).get_salla_order(user_id="u1", order_number="3001")
    assert map_salla_order(raw.salla_raw).shipping.company == "مندوب الرياض"
    assert map_salla_order(raw.salla_raw).shipping.shipment_id == "s-new"


@pytest.mark.asyncio
async def test_sparse_newer_order_clock_does_not_suppress_current_shipment_metadata(db):
    await ingest(db, order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old", "status": "creating", "updated_at": "2026-10-01T09:00:00Z"}]))
    # The order changed at 11, but its current shipment last changed at 09.
    await ingest(db, order_payload(version="2026-10-01T11:00:00Z"))
    result = await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.updated", "merchant": "merchant", "created_at": "2026-10-01T12:00:00Z",
        "data": {"id": "s-old", "order_reference_id": "3001", "courier_name": "iMile", "courier_id": "old", "updated_at": "2026-10-01T10:00:00Z", "status": "created", "tracking_number": "NEW-AWB"},
    })
    assert result["synced"] is True
    row = await db.unified_orders.find_one({"user_id": "u1"})
    assert row["tracking_number"] == "NEW-AWB"
    # A different carrier from the same old entity clock still cannot undo
    # the newer order's current carrier identity.
    conflict = await sync_shipment_payload_from_verified_webhook(db, {
        "event": "order.shipment.updated", "merchant": "merchant", "created_at": "2026-10-01T12:00:00Z",
        "data": {"id": "foreign", "order_reference_id": "3001", "courier_name": "مندوب الرياض", "courier_id": "new", "updated_at": "2026-10-01T10:30:00Z"},
    })
    assert conflict["order_modified"] is False


def test_new_order_envelope_cannot_regress_current_shipment_metadata_clock():
    raw = order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old", "updated_at": "2026-10-01T09:00:00Z", "status": "creating"}])
    current = accept_shipping({}, extract_shipping(raw))
    current = accept_shipping({CURRENT_SHIPPING: current}, extract_shipping({"id": "s-old", "courier_name": "iMile", "courier_id": "old", "updated_at": "2026-10-01T10:00:00Z", "status": "delivered", "tracking_number": "CURRENT"}, event_name="order.shipment.updated"))
    raw["updated_at"] = "2026-10-01T11:00:00Z"
    accepted = accept_shipping({CURRENT_SHIPPING: current}, extract_shipping(raw))
    assert accepted["status"] == "delivered"
    assert accepted["tracking_number"] == "CURRENT"


@pytest.mark.parametrize("embedded_clock", ["2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z"])
def test_cancel_event_clock_wins_over_old_created_at_and_embedded_active_shipment(embedded_clock):
    raw = order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old", "updated_at": embedded_clock, "status": "created", "tracking_number": "OLD"}])
    current = accept_shipping({}, extract_shipping(raw))
    cancelled = extract_shipping({"id": "s-old", "created_at": "2026-10-01T08:00:00Z"}, event_name="order.shipment.cancelled", event_time="2026-10-01T10:00:00Z")
    current = accept_shipping({CURRENT_SHIPPING: current}, cancelled)
    assert current["shipment_updated_at"] == "2026-10-01T10:00:00+00:00"
    raw["updated_at"] = "2026-10-01T11:00:00Z"
    accepted = accept_shipping({CURRENT_SHIPPING: current}, extract_shipping(raw))
    assert accepted["status"] == "cancelled"
    assert accepted["tracking_number"] is None


def test_same_carrier_delivery_does_not_advance_order_carrier_identity_clock():
    current = accept_shipping({}, extract_shipping(order_payload(shipments=[{"id": "s-old", "courier_name": "iMile", "courier_id": "old"}])))
    current = accept_shipping({CURRENT_SHIPPING: current}, extract_shipping({"id": "s-old", "courier_name": "iMile", "courier_id": "old", "status": "delivered", "updated_at": "2026-10-01T15:00:00Z"}, event_name="order.shipment.updated"))
    changed = accept_shipping({CURRENT_SHIPPING: current}, extract_shipping(order_payload("مندوب الرياض", "2026-10-01T12:00:00Z")))
    assert changed["company_name"] == "مندوب الرياض"
    assert changed["shipment_id"] is None


def test_unknown_old_shipment_update_cannot_restore_previous_carrier():
    current = accept_shipping({}, extract_shipping(order_payload("مندوب الرياض", "2026-10-01T12:00:00Z")))
    old = extract_shipping({"id": "unknown-old", "courier_name": "iMile", "courier_id": "old", "updated_at": "2026-10-01T15:00:00Z", "status": "delivered"}, event_name="order.shipment.updated")
    assert accept_shipping({CURRENT_SHIPPING: current}, old) is None
    created = extract_shipping({"id": "fresh-new", "courier_name": "iMile", "courier_id": "old", "updated_at": "2026-10-01T16:00:00Z", "status": "creating"}, event_name="order.shipment.created")
    assert accept_shipping({CURRENT_SHIPPING: current}, created)["company_name"] == "iMile"


@pytest.mark.asyncio
@pytest.mark.parametrize("representation", ["plain", "nested", "aliases"])
async def test_capture_result_log_excludes_private_ids_without_changing_audit(
    db, caplog, representation,
):
    private_order = "synthetic-order-private@example.invalid"
    private_shipment = "synthetic-shipment-secret-+966500001234"
    order_id, shipment_id = private_order, private_shipment
    event = {"event": "order.shipment.created", "merchant": "merchant"}
    if representation == "nested":
        order_id = {"secret": private_order}
        shipment_id = {"access_token": private_shipment}
        event["data"] = {"shipment": {
            "order_reference_id": order_id, "shipment_id": shipment_id,
        }}
    elif representation == "aliases":
        event["data"] = {"order": {"order_number": order_id}}
        event["shipment_id"] = shipment_id
    else:
        event["data"] = {"order_reference_id": order_id, "id": shipment_id}
    original = copy.deepcopy(event)
    caplog.set_level(logging.INFO, logger="salla.webhook_capture")

    first = await capture.capture_unknown_event(db, event, known_events=())
    replay = await capture.capture_unknown_event(db, event, known_events=())

    assert event == original
    assert first["created"] is True and replay["created"] is False
    assert first["shipment_sync"] == replay["shipment_sync"]
    assert first["shipment_sync"]["order_reference_id"] == str(order_id)
    assert first["shipment_sync"]["shipment_id"] == str(shipment_id)
    assert first["shipment_sync"]["synced"] is False
    assert first["shipment_sync"]["reason"] == "order_not_found"
    rows = await db.salla_webhook_event_captures.find({}).to_list(length=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["delivery_count"] == 2
    assert row["payload"] == capture._sanitize(original)
    assert row["event_hash"] == capture._fingerprint(capture._sanitize(original))
    assert row["shipment_sync"] == first["shipment_sync"]
    assert await db.unified_orders.count_documents({}) == 0
    assert set(await db.list_collection_names()) == {
        "salla_integrations", "unified_orders", "salla_webhook_event_captures",
    }
    records = [r for r in caplog.records if r.msg.startswith("salla_webhook.result")]
    assert len(records) == 2
    for record in records:
        for secret in (private_order, private_shipment):
            assert secret not in record.getMessage()
            assert secret not in repr(record.args)
        assert "shipment_synced=False" in record.getMessage()
        assert "reason=order_not_found" in record.getMessage()
        assert first["event_hash_prefix"] in record.getMessage()


@pytest.mark.asyncio
async def test_capture_result_log_retains_success_and_replay_correlation(db, caplog):
    await ingest(db, order_payload())
    event = {"event": "order.shipment.created", "merchant": "merchant",
             "created_at": "2026-10-01T10:00:00Z", "data": {
                 "id": "s-current", "order_reference_id": "3001",
                 "courier_name": "مندوب الرياض", "courier_id": "new",
             }}
    caplog.set_level(logging.INFO, logger="salla.webhook_capture")
    first = await capture.capture_unknown_event(db, event, known_events=())
    replay = await capture.capture_unknown_event(db, event, known_events=())
    assert first["shipment_sync"]["synced"] is True
    assert first["shipment_sync"]["order_modified"] is True
    assert replay["shipment_sync"]["synced"] is True
    assert replay["shipment_sync"]["order_modified"] is False
    assert replay["created"] is False
    assert first["event_hash_prefix"] == replay["event_hash_prefix"]
    order = await db.unified_orders.find_one({"user_id": "u1", "order_number": "3001"})
    assert order["shipping_company"] == "مندوب الرياض"
    assert order["salla_shipment_id"] == "s-current"
    messages = [r.getMessage() for r in caplog.records if r.msg.startswith("salla_webhook.result")]
    assert len(messages) == 2
    assert all("shipment_synced=True" in message for message in messages)
    assert all(first["event_hash_prefix"] in message for message in messages)
    assert "reason=synced_from_webhook" in messages[0]
    assert "reason=shipping_snapshot_already_current" in messages[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("private_value", ["synthetic-private-value", {"secret": "synthetic-private-value"}])
async def test_capture_result_log_projects_opaque_results_without_mutating_them(
    db, caplog, monkeypatch, private_value,
):
    result = {"synced": private_value, "reason": private_value,
              "order_reference_id": private_value, "shipment_id": private_value}
    original = copy.deepcopy(result)
    monkeypatch.setattr(capture, "sync_shipment_payload_from_verified_webhook", AsyncMock(return_value=result))
    caplog.set_level(logging.INFO, logger="salla.webhook_capture")
    response = await capture.capture_unknown_event(
        db, {"event": "order.shipment.created", "merchant": "merchant"}, known_events=(),
    )
    assert result == original
    assert response["shipment_sync"] == original
    row = await db.salla_webhook_event_captures.find_one({})
    assert row["shipment_sync"] == original
    record = next(r for r in caplog.records if r.msg.startswith("salla_webhook.result"))
    assert "synthetic-private-value" not in record.getMessage()
    assert "synthetic-private-value" not in repr(record.args)
    assert "shipment_synced=False" in record.getMessage()
    assert "reason=unrecognized" in record.getMessage()
