"""Actual native shipment handoff consumes physical stock and MZ2 cost atomically."""
import os
import httpx
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorCollection
import fulfillment_v2_routes as fulfillment
from product_inventory_receipt_routes import INVENTORY_RECEIPTS
from warehouse_location_routes import LOCATIONS
from ledger_core import ensure_indexes as ensure_ledger, post_txn_group
from mezan_special_orders.inventory_costs import approve_valuation, InventoryValuation, VALUATIONS
from mezan_special_orders.tests.test_core import OWNER
from mezan_special_orders.tests.test_financial_integration import run, gl_balance
from mezan_special_orders.tests.test_native_supplier_integration import reviewed_order
from mezan_special_orders.ledger_adapter import OPERATION_ID, EVENTS

pytestmark=pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'), reason='Dedicated replica set required')


async def inventory(h,order,*,approve=True):
    await h.raw[INVENTORY_RECEIPTS].insert_one({'user_id':OWNER.tenant_id,'id':'inventory-receipt-demo',
        'status':'posted','salla_product_id':'p-demo','salla_variant_id':None,'quantity':2})
    await h.raw[LOCATIONS].insert_one({'user_id':OWNER.tenant_id,'id':'shelf-demo','state':'occupied',
        'occupancy':{'total_quantity':2,'items':[{'receipt_id':'inventory-receipt-demo','quantity':2,'product_id':'p-demo'}]}})
    await ensure_ledger(h.raw)
    group=await post_txn_group(h.raw,user_id=OWNER.tenant_id,actor_id=OWNER.actor_id,actor_name='Synthetic',
        txn_type='synthetic_inventory_opening',metadata={'operation_id':OPERATION_ID},entries=[
            {'entity_type':'inventory','entity_id':'inventory-demo','side':'debit','amount':42.01,'entry_type':'opening_balance'},
            {'entity_type':'equity','entity_id':'opening','side':'credit','amount':42.01,'entry_type':'opening_balance'}])
    entry=next(r for r in group['entries'] if r['side']=='debit')
    if approve:
        await approve_valuation(h.db,OWNER,InventoryValuation(receipt_id='inventory-receipt-demo',
            inventory_entry_id=entry['id'],quantity=2,cost_sar_minor=4201,
            evidence=h.policy_ev,reason='Synthetic existing inventory carrying cost'))
    await h.raw[fulfillment.INVENTORY_RESERVATIONS].insert_one({'user_id':OWNER.tenant_id,'id':'reservation-demo',
        'order_number':order['order_number'],'status':'active','line_key':order['items'][0]['order_item_id'],
        'quantity':1,'allocations':[{'location_id':'shelf-demo','receipt_id':'inventory-receipt-demo','item_index':0,'quantity':1}]})
    await h.raw[fulfillment.BATCHES].insert_one({'user_id':OWNER.tenant_id,'id':'shipment-demo','status':'packed',
        'print_count':1,'order_numbers':[order['order_number']],'claimed_by':OWNER.tenant_id})
    # Component test begins at native handoff; review/freeze were executed by the real endpoint.
    await h.raw.order_review_workflows.update_one({'order_number':order['order_number']},
        {'$set':{'stage':'ready_to_ship','claim_batch_id':'shipment-demo','assembly_status':'completed'}})


def client(h):
    app=FastAPI();app.include_router(fulfillment.make_fulfillment_v2_router(h.db,lambda:{'id':OWNER.tenant_id,'role':'owner'}))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test')


def test_native_handoff_consumes_verified_carrying_cost_once_not_catalog_price():
    async def scenario(h):
        order=await reviewed_order(h);await inventory(h,order)
        async with client(h) as http:
            response=await http.post('/fulfillment-v2/batches/shipment-demo/handoff',json={})
            assert response.status_code==200,response.text
            count=await h.raw.general_ledger.count_documents({})
            replay=await http.post('/fulfillment-v2/batches/shipment-demo/handoff',json={})
            assert replay.status_code==409,replay.text
            assert await h.raw.general_ledger.count_documents({})==count
        assert (await h.raw[LOCATIONS].find_one({'id':'shelf-demo'}))['occupancy']['total_quantity']==1
        assert (await h.raw[VALUATIONS].find_one({}))['issued_quantity']==1
        assert (await h.raw[fulfillment.INVENTORY_RESERVATIONS].find_one({}))['status']=='consumed'
        doc=await h.doc(order)
        assert len(doc['costs'])==1 and doc['costs'][0]['cost_sar_minor']==2101
        assert await gl_balance(h,'inventory','inventory-demo')==2100
        assert await gl_balance(h,'expense','special_orders:marketing')==2101
        assert await h.raw.general_ledger.count_documents({'entity_type':'supplier'})==0
    run(scenario)


@pytest.mark.parametrize('failure',['missing_valuation','physical_stock_changed','write_after_cost'])
def test_native_handoff_failure_rolls_back_cost_stock_reservation_and_batch(monkeypatch,failure):
    original=AsyncIOMotorCollection.update_one
    async def fail(self,selector,update,*args,**kwargs):
        if self.name==LOCATIONS and 'occupancy.items.$[stock].quantity' in update.get('$inc',{}):
            await original(self,selector,update,*args,**kwargs)
            raise RuntimeError('synthetic physical write failure')
        return await original(self,selector,update,*args,**kwargs)
    async def scenario(h):
        order=await reviewed_order(h);await inventory(h,order,approve=failure!='missing_valuation')
        if failure=='physical_stock_changed':
            await h.raw[LOCATIONS].update_one({'id':'shelf-demo'},{'$set':{'occupancy.items.0.quantity':0,'occupancy.total_quantity':0}})
        before=await h.doc(order)
        stock=await h.raw[LOCATIONS].find_one({'id':'shelf-demo'})
        entries=await h.raw.general_ledger.find({}).sort('id',1).to_list(100)
        if failure=='write_after_cost':monkeypatch.setattr(AsyncIOMotorCollection,'update_one',fail)
        async with client(h) as http:
            if failure=='write_after_cost':
                with pytest.raises(RuntimeError,match='synthetic physical write failure'):
                    await http.post('/fulfillment-v2/batches/shipment-demo/handoff',json={})
            else:
                response=await http.post('/fulfillment-v2/batches/shipment-demo/handoff',json={})
                assert response.status_code==409,response.text
        assert await h.doc(order)==before
        assert await h.raw[LOCATIONS].find_one({'id':'shelf-demo'})==stock
        assert await h.raw.general_ledger.find({}).sort('id',1).to_list(100)==entries
        valuation=await h.raw[VALUATIONS].find_one({})
        assert valuation is None or valuation['issued_quantity']==0
        assert (await h.raw[fulfillment.BATCHES].find_one({}))['status']=='packed'
        assert (await h.raw[fulfillment.INVENTORY_RESERVATIONS].find_one({}))['status']=='active'
        assert await h.raw[EVENTS].count_documents({})==0
    run(scenario)
