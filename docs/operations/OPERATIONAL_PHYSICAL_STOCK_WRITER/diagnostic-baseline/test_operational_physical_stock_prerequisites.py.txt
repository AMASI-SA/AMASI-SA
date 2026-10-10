"""Real replica-set diagnostics; PASS here is NOT physical-writer acceptance.

These tests expose existing unsafe eligibility and verify the existing restricted
transaction boundary. No physical receipt API or new writer is implemented.
"""
import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_atomic import atomic_owner
from fulfillment_v2_routes import _inventory_rows
from operational_atomic import operational_owner
from stock_component_consumption_service import _available


def run(scenario):
    async def execute():
        client = AsyncIOMotorClient('mongodb://127.0.0.1:27462/?replicaSet=operationalphysical',
                                   serverSelectionTimeoutMS=5000)
        db = client['operational_physical_test_' + uuid4().hex]
        try:
            hello = await client.admin.command('hello')
            assert hello['setName'] == 'operationalphysical' and hello['isWritablePrimary']
            await db.mz2_atomic_owners.insert_one({'_id': 'owner', 'revision': 0, 'writes_paused': True})
            await scenario(db)
            assert await db.mz2_inventory_cost_states.count_documents({}) == 0
            assert await db.general_ledger.count_documents({}) == 0
        finally:
            assert db.name.startswith('operational_physical_test_')
            await client.drop_database(db.name)
            client.close()
    asyncio.run(execute())


@pytest.mark.parametrize('kind', ['product', 'component'])
@pytest.mark.parametrize('condition', ['damaged', 'quarantine', 'pending_inspection'])
def test_diagnostic_existing_consumers_expose_ineligible_lots(kind, condition):
    async def scenario(db):
        item = {'receipt_id': 'synthetic-lot', 'quantity': 10, 'condition': condition,
                'receipt_confirmed': False, 'source_type': 'diagnostic_fixture_only'}
        item.update({'product_id': 'product'} if kind == 'product' else
                    {'resource_id': 'component', 'item_type': 'stock_component'})
        location = {'user_id': 'owner', 'id': 'loc', 'warehouse_id': 'wh', 'state': 'occupied',
                    'occupancy': {'items': [item], 'total_quantity': 10}}
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                await db.warehouse_locations.insert_one(location, session=session)
        if kind == 'product':
            rows = _inventory_rows(await db.warehouse_locations.find({'user_id': 'owner'}).to_list(10))
            assert rows[0]['remaining'] == 10  # BLOCKER: required eligible amount is zero.
        else:
            rows = await _available(db, 'owner', ['wh'])
            assert rows[0]['available'] == Decimal(10)  # Same BLOCKER for components.
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
    run(scenario)


def test_existing_operational_boundary_rolls_back_all_changes():
    async def scenario(db):
        async def fail_mid_transaction(scoped):
            await scoped.warehouse_location_events.insert_one({'user_id': 'owner', 'id': 'event'})
            await scoped.warehouse_location_events.insert_one({'user_id': 'owner', 'id': 'second-event'})
            raise RuntimeError('synthetic transaction failure')
        with pytest.raises(RuntimeError):
            await operational_owner(db, 'owner', fail_mid_transaction)
        assert await db.warehouse_location_events.count_documents({}) == 0
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
        assert (await db.mz2_atomic_owners.find_one({'_id': 'owner'}))['revision'] == 0
    run(scenario)


def test_existing_fulfillment_profile_refuses_unapproved_receipt_contract():
    async def scenario(db):
        async def forbidden(scoped):
            await scoped.warehouse_location_events.insert_one({'user_id': 'owner', 'id': 'event'})
            await scoped.mezan_inventory_receipts_v2.insert_one({'user_id': 'owner', 'id': 'receipt',
                                                               'source_type': 'operational_receipt'})
        with pytest.raises(HTTPException) as exc:
            await operational_owner(db, 'owner', forbidden)
        assert exc.value.detail['code'] == 'operational_financial_write_forbidden'
        assert await db.warehouse_location_events.count_documents({}) == 0
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
    run(scenario)


def test_existing_operational_boundary_rejects_financial_collection_write():
    async def scenario(db):
        async def forbidden(scoped):
            await scoped.warehouse_location_events.insert_one({'user_id': 'owner', 'id': 'event'})
            await scoped.mz2_inventory_cost_states.insert_one({'user_id': 'owner', 'authoritative': True})
        with pytest.raises(HTTPException):
            await operational_owner(db, 'owner', forbidden)
        assert await db.warehouse_location_events.count_documents({}) == 0
        assert (await db.mz2_atomic_owners.find_one({'_id': 'owner'}))['revision'] == 0
    run(scenario)


def test_existing_financial_owner_boundary_rejects_explicit_pause():
    async def scenario(db):
        async def never_enter(scoped):
            raise AssertionError('Financial callback must not run while paused')
        with pytest.raises(HTTPException) as exc:
            await atomic_owner(db, 'owner', never_enter)
        assert exc.value.status_code == 423
        assert exc.value.detail['code'] == 'mz2_writes_paused'
        assert (await db.mz2_atomic_owners.find_one({'_id': 'owner'}))['revision'] == 0
    run(scenario)
