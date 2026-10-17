"""Existing handover endpoints: synthetic loopback Mongo, no shipping providers."""
import os
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import store_delivery_handover_routes as handover


@pytest_asyncio.fixture
async def env():
    uri = os.environ.get('WAITING_TEST_MONGO_URL', 'mongodb://127.0.0.1:27464')
    assert urlparse(uri).hostname in {'127.0.0.1', 'localhost'}
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo['courier_manual_test_' + uuid4().hex]
    actor = {'id': 'synthetic-operator', 'created_by': 'synthetic-owner', 'role': 'operations'}
    async def user():
        return actor
    app = FastAPI()
    app.include_router(handover.make_store_delivery_handover_router(db, user))
    await db[handover.STORE_DRIVERS].insert_one({'id': 'driver', 'user_id': 'synthetic-owner',
        'name': 'Synthetic driver', 'city': 'test-city', 'status': 'active', 'delivery_fee': 10})
    await db[handover.ORDERS].insert_one({'user_id': 'synthetic-owner', 'order_number': '900123',
        'order_id': 'order-1', 'shipping_city': 'test-city', 'shipping_barcode': 'SHIP-SYNTHETIC'})
    await db[handover.WORKFLOWS].insert_one({'user_id': 'synthetic-owner', 'order_number': '900123',
        'carrier_label_type': 'store_courier', 'carrier_label_ready': True,
        'stage': 'completed', 'assembly_status': 'completed'})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
            response = await client.post('/store-delivery/handover/sessions', json={'driver_id': 'driver'})
            assert response.status_code == 201
            yield db, client, actor, '/store-delivery/handover/sessions/' + response.json()['id']
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('first,second', [('900123', 'SHIP-SYNTHETIC'), ('SHIP-SYNTHETIC', '900123')])
async def test_number_and_barcode_resolve_same_order_without_duplicate_assignment(env, first, second):
    db, client, _, path = env
    result = (await client.post(path + '/scan', json={'barcode': first})).json()
    assert result['accepted'] is True
    assert result['shipment']['order_id'] == 'order-1'
    assert await db[handover.ASSIGNMENTS].count_documents({}) == 0
    duplicate = (await client.post(path + '/scan', json={'barcode': second})).json()
    assert duplicate['code'] == 'shipment_already_scanned'
    assert (await client.post(path + '/confirm')).json()['assigned_count'] == 1
    assert (await client.post(path + '/confirm')).status_code == 409
    assert await db[handover.ASSIGNMENTS].count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change,code', [
    ('city', 'driver_city_mismatch'), ('incomplete', 'store_courier_order_not_completed'),
    ('label', 'store_courier_label_not_ready'), ('delivered', 'shipment_already_delivered'),
    ('same_driver', 'shipment_already_assigned_to_driver'), ('foreign', 'shipment_not_found'),
])
async def test_manual_number_preserves_server_eligibility(env, change, code):
    db, client, _, path = env
    if change == 'city':
        await db[handover.ORDERS].update_one({}, {'$set': {'shipping_city': 'other-city'}})
    elif change == 'foreign':
        await db[handover.ORDERS].update_one({}, {'$set': {'user_id': 'another-owner'}})
    elif change in {'incomplete', 'label'}:
        await db[handover.WORKFLOWS].update_one({}, {'$set':
            {'stage': 'in_progress'} if change == 'incomplete' else {'carrier_label_ready': False}})
    else:
        await db[handover.ASSIGNMENTS].insert_one({'id': 'assignment', 'user_id': 'synthetic-owner',
            'order_id': 'order-1', 'driver_id': 'driver', 'active': True,
            'status': 'delivered' if change == 'delivered' else 'assigned'})
    result = (await client.post(path + '/scan', json={'barcode': '900123'})).json()
    assert result['accepted'] is False
    assert result['code'] == code
    session = await db[handover.SESSIONS].find_one({})
    assert session['accepted'] == []


@pytest.mark.asyncio
async def test_manual_number_cannot_bypass_operator_permission(env):
    db, client, actor, path = env
    actor['denied_permissions'] = [handover.HANDOVER_PERMISSION]
    assert (await client.post(path + '/scan', json={'barcode': '900123'})).status_code == 403
    assert (await db[handover.SESSIONS].find_one({}))['accepted'] == []
