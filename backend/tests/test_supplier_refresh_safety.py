"""Synthetic-only regression cases; never connects to Production."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient

import supplier_receiving_routes as receiving


def piece(**overrides):
    return {"user_id": "owner", "piece_id": "piece-1", "product_id": "product-1",
            "sku": "SKU-1", "supplier_receiving_session_id": "session-1",
            "product_options_snapshot": {"Length": "Long"}, "services": [], **overrides}


async def seed(db):
    await db[receiving.PRODUCTS].insert_one({"user_id": "owner", "id": "product-1",
                                          "salla_product_id": "salla-1"})


@pytest.mark.parametrize("options", [[], [{"name": "Length", "value": "Long"}], "Long", 7])
def test_invalid_options_fail_explicitly_not_empty_or_attribute_error(options):
    with pytest.raises(HTTPException) as caught:
        receiving._supplier_piece_option_tokens(piece(product_options_snapshot=options))
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "supplier_receiving_options_invalid"


@pytest.mark.parametrize("options", [None, {}])
def test_optional_options_have_no_invented_tokens(options):
    assert receiving._supplier_piece_option_tokens(piece(product_options_snapshot=options)) == set()


@pytest.mark.asyncio
async def test_matched_missing_option_cost_is_not_silently_zero(monkeypatch):
    db = AsyncMongoMockClient().test
    await seed(db)
    await db[receiving.BINDINGS].insert_one({"user_id": "owner", "salla_product_id": "salla-1",
        "mode": "direct", "option_name": "Length", "value_name": "Long", "direct_amount": None})
    monkeypatch.setattr(receiving, "supplier_mezan_product_reference_price", lambda **kw: {
        "reference_product_price_complete": True, "reference_product_unit_price_halalas": 1000})
    with pytest.raises(HTTPException) as caught:
        await receiving._supplier_product_reference_price(db, user_id="owner", piece=piece())
    assert caught.value.detail["code"] == "supplier_receiving_option_cost_missing"


@pytest.mark.asyncio
async def test_missing_linked_resource_does_not_drop_service():
    db = AsyncMongoMockClient().test
    await seed(db)
    await db[receiving.PRODUCT_RESOURCE_BINDINGS].insert_one({"user_id": "owner",
        "salla_product_id": "salla-1", "resource_id": "service-1"})
    with pytest.raises(HTTPException) as caught:
        await receiving._supplier_live_piece_services(db, user_id="owner", piece=piece(
            services=[{"service_id": "service-1", "source": "product", "status": "pending"}]))
    assert caught.value.detail["code"] == "supplier_receiving_resource_missing"


@pytest.mark.asyncio
async def test_size_selected_missing_resource_does_not_disappear():
    db = AsyncMongoMockClient().test
    await seed(db)
    await db[receiving.BINDINGS].insert_one({"user_id": "owner", "salla_product_id": "salla-1",
        "mode": "resource", "option_name": "المقاس", "value_name": "L", "resource_id": "missing"})
    with pytest.raises(HTTPException) as caught:
        await receiving._supplier_live_piece_services(db, user_id="owner",
            piece=piece(product_options_snapshot={}, size="L"))
    assert caught.value.detail["code"] == "supplier_receiving_resource_missing"


@pytest.mark.asyncio
async def test_price_failure_leaves_all_piece_services_unchanged(monkeypatch):
    db = AsyncMongoMockClient().test
    original = piece()
    await db[receiving.PIECES].insert_one(deepcopy(original))
    await db[receiving.SESSIONS].insert_one({"user_id": "owner", "id": "session-1", "status": "open"})
    await db[receiving.RECEIVING_EVENTS].insert_one({"user_id": "owner", "session_id": "session-1",
        "event_type": "supplier_piece_scanned", "piece_id": "piece-1", "product_id": "product-1",
        "occurred_at": datetime.now(timezone.utc)})
    async def services(*args, **kw):
        return [{"service_id": "service-new", "status": "pending", "source": "product"}]
    async def price(*args, **kw):
        raise HTTPException(409, detail={"code": "synthetic_source_failure"})
    monkeypatch.setattr(receiving, "_supplier_live_piece_services", services)
    monkeypatch.setattr(receiving, "_supplier_product_reference_price", price)
    with pytest.raises(HTTPException):
        await receiving._recent_session_events(db, user_id="owner", session_id="session-1",
                                               refresh_product_services=True)
    stored = await db[receiving.PIECES].find_one({"piece_id": "piece-1"}, {"_id": 0})
    assert stored == original


@pytest.mark.asyncio
async def test_empty_session_never_reads_product_options():
    db = AsyncMongoMockClient().test
    await db[receiving.SESSIONS].insert_one({"user_id": "owner", "id": "session-1", "status": "open"})
    assert await receiving._recent_session_events(db, user_id="owner", session_id="session-1",
                                                   refresh_product_services=True) == []
