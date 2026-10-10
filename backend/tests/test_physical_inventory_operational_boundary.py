"""Real Mongo/HTTP proof of the current BLOCKER, not receiving acceptance."""
from copy import deepcopy

import httpx
from fastapi import FastAPI

from product_inventory_receipt_routes import make_product_inventory_receipt_router
from test_operational_balance_integration import run


def test_raw10_ready100_receiving_is_blocked_and_preserves_initial3_on_retry():
    async def scenario(db):
        initial = {
            'id': 'isolated-location', 'user_id': 'owner', 'warehouse_id': 'isolated-warehouse',
            'state': 'occupied', 'occupancy': {'total_quantity': 3, 'items': [
                {'product_id': 'independent-initial-product', 'quantity': 3,
                 'receipt_id': 'initial-fixture-evidence'}]},
        }
        await db.warehouse_locations.insert_one(deepcopy(initial))
        before = {name: await db[name].find({}).to_list(1000)
                  for name in await db.list_collection_names()}

        async def owner():
            return {'id': 'owner', 'role': 'owner'}

        app = FastAPI()
        app.include_router(make_product_inventory_receipt_router(db, owner), prefix='/api')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated') as client:
            for quantity, color, state, specs in [
                (10, 'gold', 'requires_preparation', [{'name': 'اللون', 'value': 'ذهبي'}]),
                (100, 'silver', 'ready_complete', [{'name': 'اللون', 'value': 'فضي'}, {'name': 'الاسم', 'value': 'عبير'}]),
            ]:
                payload = {'idempotency_key': 'physical-example-' + color,
                           'purchase_invoice_id': 'isolated-invoice', 'purchase_invoice_line_id': color,
                           'product_id': 'isolated-necklace', 'location_id': 'isolated-location',
                           'scanned_barcode': 'UAT', 'quantity': quantity,
                           'preparation_state': state, 'specifications': specs}
                for _ in range(2):
                    response = await client.post('/api/inventory-v2/purchase-receipts', json=payload)
                    assert response.status_code == 409, response.text
                    assert response.json()['detail']['code'] == 'purchase_full_approval_required'
                    assert (await db.warehouse_locations.find_one({'id': initial['id']}))['occupancy']['total_quantity'] == 3

        after = {name: await db[name].find({}).to_list(1000)
                 for name in await db.list_collection_names()}
        assert after == before  # No receipt, cost, journal or stock side effects.
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
        assert await db.mz2_inventory_cost_states.count_documents({}) == 0

    run(scenario)
