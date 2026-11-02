"""Read-only, per-driver receive session report and company selector."""
import asyncio
import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from store_delivery_driver_app_routes import driver_receive_session_views
from store_delivery_driver_routes import _city_courier_companies, _validate_city_courier_company


def test_mixed_historical_counter_cannot_leak_another_drivers_shipments():
    async def go():
        db = AsyncMongoMockClient().privacy
        await db.store_delivery_assignments.insert_many([
            {"id": "a", "user_id": "shop", "driver_id": "talal", "active": False},
            {"id": "b", "user_id": "shop", "driver_id": "other", "active": True},
        ])
        sessions = [
            {"id": "one", "user_id": "shop", "driver_id": "talal", "status": "open",
             "accepted_count": 32, "collection_total": 900,
             "accepted": [
                 {"assignment_id": "a", "order_number": "OWN", "outstanding_amount": 50, "outstanding_amount_available": True},
                 {"assignment_id": "b", "order_number": "PRIVATE", "outstanding_amount": 850, "outstanding_amount_available": True},
                 {"assignment_id": "a", "order_number": "OWN", "outstanding_amount": 50, "outstanding_amount_available": True},
             ]},
            {"id": "two", "user_id": "shop", "driver_id": "other", "status": "open",
             "accepted": [{"assignment_id": "b", "order_number": "PRIVATE"}]},
        ]
        await db.store_delivery_driver_receive_sessions.insert_many(sessions)
        result = await driver_receive_session_views(db,user_id="shop",driver_id="talal",sessions=sessions)
        assert len(result) == 1
        assert result[0]["accepted_count"] == 1
        assert result[0]["collection_total"] == 50
        assert result[0]["collection_count"] == 1
        assert result[0]["accepted"][0]["order_number"] == "OWN"
        assert "PRIVATE" not in str(result)
        stored = await db.store_delivery_driver_receive_sessions.find_one({"id": "one"})
        assert stored["accepted_count"] == 32
    asyncio.run(go())


def test_dropdown_only_shows_configured_city_couriers_and_rejects_manual_value():
    async def go():
        db = AsyncMongoMockClient().choices
        await db.settings.insert_one({"user_id":"shop","shipping_companies":[
            {"name":"مندوب الرياض"},{"name":"مندوب جدة"},{"name":"سمسا"},
            {"name":"مندوب"},{"name":"مندوب الرياض"}]})
        assert await _city_courier_companies(db,"shop")==["مندوب الرياض","مندوب جدة"]
        assert await _validate_city_courier_company(db,"shop","مندوب الرياض")=="مندوب الرياض"
        with pytest.raises(HTTPException) as raised:
            await _validate_city_courier_company(db,"shop","مندوب الخبر")
        assert raised.value.status_code==422
    asyncio.run(go())
