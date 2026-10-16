"""External printing reads the same saved shipping projection as order details."""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

import order_engine.shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING


@pytest.fixture
async def saved(monkeypatch):
    db = AsyncMongoMockClient().saved_label_print
    row = {
        "user_id": "owner", "order_number": "3001", "order_date": "2026-10-01T10:00:00Z",
        "raw_by_source": {"salla_direct": {
            "id": 123, "reference_id": "3001", "status": "completed",
            "date": {"date": "2026-10-01T10:00:00Z"}, "items": [],
            "shipping": {"company": "Old carrier", "label_url": "https://labels.test/old.pdf"},
        }},
        "shipping_label_url": "https://labels.test/new.pdf",
        CURRENT_SHIPPING: {
            "company_name": "iMile", "shipment_id": "new", "status": "created",
            "tracking_number": "NEW-AWB", "label_url": "https://labels.test/new.pdf",
            "tracking_url": "https://labels.test/tracking",
        },
    }
    await db.unified_orders.insert_one(deepcopy(row))

    async def forbidden(*args, **kwargs):
        pytest.fail("external printing must not call Salla or resync")

    monkeypatch.setattr(shipping, "call_salla", forbidden)
    monkeypatch.setattr(shipping, "_best_effort_resync", forbidden)
    return db


@pytest.mark.asyncio
async def test_saved_pdf_prints_without_provider_or_writes(saved):
    before = await saved.unified_orders.find_one({})
    result = await shipping.refresh_shipping_label(saved, "owner", "3001")
    assert result["ready"] is True
    assert result["label_url"] == "https://labels.test/new.pdf"
    assert result["shipment_id"] == "new"
    assert result["tracking_number"] == "NEW-AWB"
    assert result["source"] == "mezan"
    assert await saved.unified_orders.find_one({}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"label_url": None}, {"status": "cancelled"},
    {"shipment_id": "replacement", "label_url": None, "tracking_number": None},
    {"company_name": None, "label_url": None},
])
async def test_unavailable_current_label_never_falls_back(saved, change):
    await saved.unified_orders.update_one({}, {"$set": {
        f"{CURRENT_SHIPPING}.{key}": value for key, value in change.items()
    }})
    if "label_url" in change:
        await saved.unified_orders.update_one({}, {"$set": {"shipping_label_url": change["label_url"]}})
    result = await shipping.refresh_shipping_label(saved, "owner", "3001")
    assert result["ready"] is False
    assert result["label_url"] is None
    assert result["message"]


@pytest.mark.asyncio
async def test_other_owner_cannot_read_label_or_trigger_lookup(saved):
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping.refresh_shipping_label(saved, "other-owner", "3001")
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_saved_courier_preserves_provider_formatter(saved, monkeypatch):
    await saved.unified_orders.update_one({}, {"$set": {
        f"{CURRENT_SHIPPING}.company_name": "مندوب المتجر",
        f"{CURRENT_SHIPPING}.company_code": "0",
    }})
    expected = {"ready": True, "label_type": "store_courier", "print_data": {"address": "current"}}
    calls = []

    async def courier(db, owner, number, *, store_courier_only):
        assert store_courier_only is True
        calls.append((db, owner, number))
        return expected

    monkeypatch.setattr(shipping, "_refresh_provider_shipping_label", courier)
    assert await shipping.refresh_shipping_label(saved, "owner", "3001") == expected
    assert calls == [(saved, "owner", "3001")]


@pytest.mark.asyncio
async def test_courier_changed_to_external_never_searches_shipments(saved, monkeypatch):
    await saved.unified_orders.update_one({}, {"$set": {
        f"{CURRENT_SHIPPING}.company_name": "مندوب المتجر",
        f"{CURRENT_SHIPPING}.company_code": "0",
    }})

    async def resolve(*args):
        return "123", {"id": "123", "reference_id": "3001", "shipping": {"company": "iMile"}}

    monkeypatch.setattr(shipping, "_resolve_order", resolve)
    with pytest.raises(shipping.ShippingLabelError) as error:
        await shipping.refresh_shipping_label(saved, "owner", "3001")
    assert error.value.code == "shipping_snapshot_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["shipments", "shipping.shipment"])
async def test_ingested_label_becomes_printable_and_survives_sparse_update(monkeypatch, shape):
    from orders_db import upsert_order
    from salla_integration.sync import _salla_order_to_doc

    db = AsyncMongoMockClient(tz_aware=True).label_arrival

    async def owner(database, _owner, callback, **kwargs):
        return await callback(database)

    async def forbidden(*args, **kwargs):
        pytest.fail("printing or ingestion made a provider call")

    monkeypatch.setattr("operational_atomic.operational_owner", owner)
    monkeypatch.setattr(shipping, "call_salla", forbidden)
    base = {"id": "123", "reference_id": "3001", "date": "2026-10-01T08:00:00Z",
        "shipping": {"company": "iMile"}}

    async def ingest(payload):
        await upsert_order(db, "owner", "3001", _salla_order_to_doc(payload),
                           source="salla_direct", raw=payload)

    await ingest({**base, "updated_at": "2026-10-01T09:00:00Z"})
    assert not (await shipping.refresh_shipping_label(db, "owner", "3001"))["ready"]
    shipment = {
        "id": "current", "courier_name": "iMile", "status": "created",
        "tracking_number": "AWB", "label_url": "https://labels.test/arrived.pdf",
    }
    arriving = {**base, "updated_at": "2026-10-01T10:00:00Z", "shipments": [shipment]}
    if shape == "shipping.shipment":
        nested = {key: value for key, value in shipment.items() if key not in {"courier_name", "label_url"}}
        nested["label"] = {"url": shipment["label_url"]}
        arriving = {**base, "updated_at": "2026-10-01T10:00:00Z",
            "shipping": {"company": "iMile", "shipment": nested}}
    await ingest(arriving)
    result = await shipping.refresh_shipping_label(db, "owner", "3001")
    assert result["ready"] and result["label_url"] == "https://labels.test/arrived.pdf"
    await ingest({**base, "updated_at": "2026-10-01T11:00:00Z"})
    assert await shipping.refresh_shipping_label(db, "owner", "3001") == result


@pytest.mark.parametrize("nested", [
    {"id": "0"}, {"id": "nested", "type": "return"},
    {"id": "nested", "courier_name": "DHL"},
])
def test_nested_label_rejects_placeholder_return_or_other_carrier(nested):
    from salla_shipping import extract_shipping
    result = extract_shipping({"shipping": {"company": "iMile", "shipment": {
        **nested, "label": "https://labels.test/wrong.pdf", "tracking_number": "WRONG",
    }}})
    assert not result.get("label_url")


def test_nested_label_never_overrides_explicit_shipment_history():
    from salla_shipping import extract_shipping
    result = extract_shipping({"shipping": {"company": "iMile", "shipment": {
        "id": "nested", "label": "https://labels.test/wrong.pdf",
    }}, "shipments": [{"id": "current", "courier_name": "iMile",
        "label_url": "https://labels.test/current.pdf"}]})
    assert result["label_url"] == "https://labels.test/current.pdf"


def test_nested_cancellation_clears_saved_label():
    from salla_shipping import extract_shipping
    result = extract_shipping({"shipping": {"company": "iMile", "shipment": {
        "id": "current", "status": "cancelled", "label": "https://labels.test/cancelled.pdf",
    }}})
    assert result["status"] == "cancelled"
    assert result["label_url"] is None
