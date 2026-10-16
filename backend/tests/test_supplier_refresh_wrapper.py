"""Exercise the installed Production wrapper, not only the unwrapped route."""
import importlib
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
import supplier_receiving_routes as receiving
from product_field_cost_support import install_product_field_cost_support


@pytest.fixture
def installed_live_cost_support(monkeypatch):
    # Restore every installed wrapper so unrelated tests retain their own setup.
    names = ("product_v2_details_routes", "product_option_cost_routes",
             "order_option_cost_snapshot_routes", "product_v2_routes",
             "product_cost_setup_routes", "product_fulfillment_rules",
             "supplier_receiving_routes")
    for name in names:
        module = importlib.import_module(name)
        for key, value in list(vars(module).items()):
            if callable(value):
                monkeypatch.setattr(module, key, value)
    return install_product_field_cost_support


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, False, True])
async def test_installed_wrapper_forwards_refresh_flag_and_transaction(
    monkeypatch, installed_live_cost_support, mode
):
    original = AsyncMock(return_value=[])
    monkeypatch.setattr(receiving, "_recent_session_events", original)
    # AsyncMock dynamically manufactures attributes; set the install marker explicitly.
    original._mezan_live_invoice_costs = False
    installed_live_cost_support()
    db, transaction = object(), object()
    kwargs = {} if mode is None else {"refresh_product_services": mode}
    result = await receiving._recent_session_events(
        db, user_id="merchant", session_id="draft", limit=37,
        mongo_session=transaction, **kwargs
    )
    assert result == []
    scoped_db = original.await_args.args[0]
    assert scoped_db._source_db is db
    assert scoped_db._session is transaction
    original.assert_awaited_once_with(
        scoped_db, user_id="merchant", session_id="draft", limit=37,
        mongo_session=transaction, refresh_product_services=mode is True
    )


async def option_cost_draft(choices):
    db = AsyncMongoMockClient().option_cost_refresh
    piece = {"user_id": "merchant", "piece_id": "piece-1", "product_id": "product-1",
             "sku": "SKU-1", "services": [], **choices}
    await db[receiving.PIECES].insert_one(deepcopy(piece))
    await db[receiving.PRODUCTS].insert_one({"user_id": "merchant", "id": "product-1",
                                            "salla_product_id": "product-1"})
    await db[receiving.COST_PROFILES].insert_one({"user_id": "merchant",
        "salla_product_id": "product-1", "base_cost": 40})
    await db[receiving.BINDINGS].insert_one({"user_id": "merchant", "salla_product_id": "product-1",
        "mode": "direct", "option_name": "Customization", "value_name": "Name",
        "direct_amount": 10, "quantity": 1})
    await db[receiving.SESSIONS].insert_one({"user_id": "merchant", "id": "draft", "status": "open",
        "supplier_snapshot": {"id": "supplier-1", "service_links": []}})
    # The scan event carries the price, not a copy of the piece's choice snapshots.
    await db[receiving.RECEIVING_EVENTS].insert_one({"user_id": "merchant", "session_id": "draft",
        "event_type": "supplier_piece_scanned", "piece_id": "piece-1", "product_id": "product-1",
        "sku": "SKU-1", "occurred_at": datetime.now(timezone.utc)})
    return db, piece


@pytest.mark.asyncio
@pytest.mark.parametrize("choices", [
    {"product_options_snapshot": {"Customization": "Name"}},
    {"service_specifications_snapshot": [{"name": "Customization", "value": "Name"}]},
    {"specifications_snapshot": [{"name": "Customization", "value": "Name"}]},
    {"options_raw": [{"option_name": "Customization", "value_name": "Name"}]},
    {"selected_options": {"Customization": "Name"}},
    {"product_options_snapshot": {"Customization": "Name"},
     "options_normalized": {"Customization": "Name"}},
])
async def test_installed_pricing_retains_option_once_across_scan_and_refresh(
    installed_live_cost_support, choices
):
    installed_live_cost_support()
    db, piece = await option_cost_draft(choices)
    scanned = await receiving._supplier_product_reference_price(db, user_id="merchant", piece=piece)
    assert scanned["reference_product_unit_price_halalas"] == 5000
    assert scanned["reference_product_option_cost_halalas"] == 1000
    for _ in range(2):
        rows = await receiving._recent_session_events(db, user_id="merchant", session_id="draft")
        assert len(rows) == 1
        assert rows[0]["reference_product_unit_price_halalas"] == 5000
        assert rows[0]["reference_product_option_cost_halalas"] == 1000
    assert await db[receiving.PIECES].find_one({"piece_id": "piece-1"}, {"_id": 0}) == piece
    assert await db[receiving.SUPPLIER_INVOICES].count_documents({}) == 0


@pytest.mark.asyncio
async def test_installed_refresh_preserves_added_service_and_exposes_real_price_changes(installed_live_cost_support):
    installed_live_cost_support()
    db, piece = await option_cost_draft({"product_options_snapshot": {"Customization": "Name"}})
    service = {"service_id": "service-1", "service_name": "Tailoring", "source": "supplier_receiving_permanent",
               "status": "pending", "required_quantity": 2, "supplier_invoice_required": True}
    await db[receiving.PIECES].update_one({"piece_id": "piece-1"}, {"$set": {"services": [service]}})
    await db[receiving.RESOURCES].insert_one({"user_id": "merchant", "id": "service-1", "name": "Tailoring",
        "kind": "service", "unit_cost": 3, "status": "active"})
    first = (await receiving._recent_session_events(db, user_id="merchant", session_id="draft"))[0]
    second = (await receiving._recent_session_events(db, user_id="merchant", session_id="draft"))[0]
    assert first == second
    assert second["reference_product_unit_price_halalas"] == 5000
    assert len(second["invoice_services"]) == 1
    assert second["invoice_services"][0]["reference_unit_price_halalas"] == 300
    assert second["invoice_services"][0]["required_quantity"] == 2
    await db[receiving.BINDINGS].update_one({"mode": "direct"}, {"$set": {"direct_amount": 12}})
    await db[receiving.RESOURCES].update_one({"id": "service-1"}, {"$set": {"unit_cost": 4}})
    changed = (await receiving._recent_session_events(db, user_id="merchant", session_id="draft"))[0]
    assert changed["reference_product_unit_price_halalas"] == 5200
    assert changed["reference_product_option_cost_halalas"] == 1200
    assert changed["invoice_services"][0]["reference_unit_price_halalas"] == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_present", [False, True])
async def test_installed_pricing_uses_option_quantity_once_and_rejects_missing_cost(
    installed_live_cost_support, snapshot_present
):
    installed_live_cost_support()
    choices = {"options_normalized": {"Customization": "Name"}}
    if snapshot_present:
        choices["product_options_snapshot"] = {"Customization": "Name"}
    db, piece = await option_cost_draft(choices)
    await db[receiving.BINDINGS].update_one({"mode": "direct"}, {"$set": {"quantity": 2}})
    price = await receiving._supplier_product_reference_price(db, user_id="merchant", piece=piece)
    assert price["reference_product_unit_price_halalas"] == 6000
    assert price["reference_product_option_cost_halalas"] == 2000
    await db[receiving.BINDINGS].update_one({"mode": "direct"}, {"$set": {"direct_amount": None}})
    with pytest.raises(HTTPException) as error:
        await receiving._supplier_product_reference_price(db, user_id="merchant", piece=piece)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "supplier_receiving_option_cost_missing"


@pytest.mark.asyncio
async def test_installed_pricing_keeps_selected_service_separate_and_excludes_unselected_cost(installed_live_cost_support):
    installed_live_cost_support()
    db, piece = await option_cost_draft({"product_options_snapshot": {"Customization": "Name"}})
    await db[receiving.BINDINGS].insert_many([
        {"user_id": "merchant", "salla_product_id": "product-1", "mode": "direct",
         "option_name": "Customization", "value_name": "Other", "direct_amount": 99},
        {"user_id": "merchant", "salla_product_id": "product-1", "mode": "resource",
         "option_name": "Customization", "value_name": "Name", "resource_id": "service-1"},
        {"user_id": "merchant", "salla_product_id": "product-1", "mode": "resource",
         "option_name": "Customization", "value_name": "Name", "resource_id": "component-1"},
    ])
    await db[receiving.RESOURCES].insert_many([
        {"user_id": "merchant", "id": "service-1", "kind": "service", "unit_cost": 3},
        {"user_id": "merchant", "id": "component-1", "kind": "component", "unit_cost": 20},
    ])
    price = await receiving._supplier_product_reference_price(db, user_id="merchant", piece=piece)
    assert price["reference_product_unit_price_halalas"] == 5000
    assert price["reference_product_option_cost_halalas"] == 1000
    assert price["reference_product_component_cost_halalas"] == 0
    assert [s["service_id"] for s in price["live_invoice_services"]] == ["service-1"]
    assert price["live_invoice_services"][0]["reference_unit_price_halalas"] == 300
