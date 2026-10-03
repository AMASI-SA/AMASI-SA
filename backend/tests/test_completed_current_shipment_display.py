"""Completed-order reads expose canonical shipping, never old print snapshots."""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

from fulfillment_v2_routes import _order_view
from order_engine.repository import MongoOrderRepository
from salla_shipping import CURRENT_SHIPPING


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["printed", "replacement", "carrier_changed", "none", "cancelled"])
async def test_current_shipping_survives_old_workflow_and_reopen(scenario):
    db = AsyncMongoMockClient().current_display
    current = {
        "company_name": "iMile", "company_code": "imile",
        "shipment_id": "current-shipment", "tracking_number": "6100326425847",
        "label_url": "https://labels.example/current.pdf", "status": "printed",
    }
    if scenario == "carrier_changed":
        current.update(company_name="Current carrier", company_code="new", tracking_number="NEW-AWB")
    if scenario == "replacement":
        current.update(shipment_id="replacement", tracking_number="REPLACEMENT-AWB")
    if scenario == "none":
        current.update(shipment_id=None, tracking_number=None, label_url=None, status=None)
    if scenario == "cancelled":
        current["status"] = "cancelled"
    record = {
        "user_id": "test-owner", "order_number": "289131568",
        "order_date": "2026-10-01T08:00:00Z",
        CURRENT_SHIPPING: current,
        "raw_by_source": {"salla_direct": {
            "id": "test-order", "reference_id": "289131568", "date": "2026-10-01T08:00:00Z",
            "shipping": {"company": "Old carrier"},
            "shipments": [{"id": "old", "tracking_number": "OLD-AWB", "label_url": "https://labels.example/old.pdf"}],
        }},
    }
    await db.unified_orders.insert_one(record)
    workflow = {
        "order_number": "289131568", "carrier_name": "Old carrier",
        "carrier_tracking_number": "OLD-AWB", "carrier_label_ready": False,
        "carrier_label_error_code": "shipping_snapshot_changed",
        "carrier_label_error_message": "Saved rejection from old snapshot",
    }
    original = deepcopy(workflow)
    before = await db.unified_orders.find_one({})
    repo = MongoOrderRepository(db)
    for _ in range(3):  # reload/reopen must not turn current facts into old printing facts
        response = await _order_view(repo, user_id="test-owner", workflow=workflow)
        actual = response["current_shipment"]
        assert actual["carrier_name"] == current["company_name"]
        assert actual["shipment_id"] == current["shipment_id"]
        assert actual["tracking_number"] == current["tracking_number"]
        assert actual["source"] == "salla_current_shipping"
        assert actual["label_status"] == ("none" if scenario == "none" else "cancelled" if scenario == "cancelled" else "available")
        assert actual["label_available"] == (scenario not in {"none", "cancelled"})
        # Displaying provider facts is not a print authorization or a print confirmation.
        assert response["carrier_label_ready"] is False
        assert response["carrier_label_print_confirmed"] is False
        assert response["carrier_label_error_code"] == "shipping_snapshot_changed"
    assert workflow == original
    assert await db.unified_orders.find_one({}) == before
    assert await db.list_collection_names() == ["unified_orders"]
