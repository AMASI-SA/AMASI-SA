"""Evidence-loading dependency, not a corrected eligibility acceptance test.

Uses the dedicated real replica set. Original prerequisite evidence is retained.
No production code, receipt writer or external sync is invoked.
"""
from copy import deepcopy
from decimal import Decimal

import pytest

from fulfillment_v2_routes import _inventory_rows
from stock_component_consumption_service import _available
from test_operational_physical_stock_prerequisites import run


@pytest.mark.parametrize('kind', ['product', 'component'])
def test_current_readers_cannot_distinguish_posted_and_pending_receipt(kind):
    async def scenario(db):
        item = {'quantity': 10, 'receipt_id': 'known-receipt', 'lot_id': 'known-receipt',
                'source_type': 'purchase_invoice', 'source_id': 'invoice', 'source_line_id': 'line',
                'preparation_state': 'requires_preparation', 'condition': 'sellable',
                'configuration_key': 'known-config'}
        item.update({'product_id': 'product', 'item_type': 'product'} if kind == 'product' else
                    {'resource_id': 'component', 'item_type': 'stock_component'})
        location = {'id': 'loc', 'user_id': 'owner', 'warehouse_id': 'wh', 'state': 'occupied',
                    'occupancy': {'items': [item], 'total_quantity': 10}}
        await db.warehouse_locations.insert_one(deepcopy(location))
        await db.mezan_inventory_receipts_v2.insert_one({
            **item, 'id': 'known-receipt', 'user_id': 'owner', 'schema_version': 'g47-v1',
            'location_id': 'loc', 'warehouse_id': 'wh', 'status': 'posted'})
        projection = {'_id': 0, 'id': 1, 'warehouse_id': 1, 'state': 1, 'occupancy': 1}
        before = await db.warehouse_locations.find({'user_id': 'owner'}, projection).to_list(10)
        if kind == 'product':
            first = _inventory_rows(before)
            assert first[0]['remaining'] == 10
        else:
            first = await _available(db, 'owner', ['wh'])
            assert first[0]['available'] == Decimal(10)
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                await db.mezan_inventory_receipts_v2.update_one(
                    {'id': 'known-receipt', 'user_id': 'owner'},
                    {'$set': {'status': 'pending'}}, session=session)
        after = await db.warehouse_locations.find({'user_id': 'owner'}, projection).to_list(10)
        assert before == after  # Existing callers provide indistinguishable inputs.
        second = _inventory_rows(after) if kind == 'product' else await _available(db, 'owner', ['wh'])
        assert second == first  # Diagnostic: required pending availability is zero.
        assert (await db.mezan_inventory_receipts_v2.find_one({'id': 'known-receipt'}))['status'] == 'pending'
    run(scenario)
