"""Explicit synthetic-only API for review of the real compiled frontend.

Never import the production server or read MONGO_URL/.env. Only a validated
loopback disposable Mongo URI is accepted; all writers target a new UUID DB.
"""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import unittest
import uuid
import os

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/tests')]
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
import test_g47_purchase_approval_integration as fixture
from test_purchase_configuration_contract import product
from inventory_stocktake_routes import make_inventory_stocktake_router
from operational_inventory_projection import projection


def app_for_preview():
    uri=fixture.local_uri('MZ2_TEST_MONGO_URI','mongodb://127.0.0.1:27653/?replicaSet=inventoryLocal')
    mongo=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=3000)
    database_name='inventory_preview_'+uuid.uuid4().hex
    db=mongo[database_name]
    seed=unittest.TestCase()
    seed.actor={'id':'owner','role':'owner','is_active':True,'name':'بيانات اختبار محلية',
                'accounting_permissions':['accounting.inventory.adjustments.review','accounting.inventory.adjustments.post']}
    app=fixture.app_for(db,seed.actor)
    async def identity(): return dict(seed.actor)
    app.include_router(make_inventory_stocktake_router(db,identity),prefix='/api')
    @app.get('/api/operational-balances/inventory')
    async def physical_inventory(): return await projection(db,'owner')
    @app.get('/api/fixture-health')
    async def fixture_health(): return {'synthetic_only':True,'production_modified':False,'backend':'native_count_and_purchase_routes'}
    @app.get('/api/counterparties')
    async def counterparties():
        return {'items':await db.counterparties.find({'user_id':'owner'},{'_id':0}).to_list(100)}
    @asynccontextmanager
    async def lifespan(_):
        if not (await mongo.admin.command('hello')).get('setName'): raise RuntimeError('Disposable replica required')
        await fixture.PurchaseApprovalMongoIntegration.seed(seed,db)
        await db.warehouse_locations_warehouses.insert_one({'id':'warehouse','user_id':'owner','name':'مستودع اختبار محلي','status':'active'})
        await db.expense_categories.insert_one({'user_id':'owner','code':'inventory-variance','name':'فروقات مخزون تجريبية','status':'active'})
        await db.mezan_products_v2.update_one({'mezan_product_id':'product-A'},{'$set':{'name':'بلوفر أساسي تجريبي','unit':'piece'}})
        await db.mezan_cost_resources_v2.update_one({'id':'component-A'},{'$set':{'name':'خامة تجريبية','unit':'gram'}})
        pullover=product();pullover.update(id='pullover',main_image='http://127.0.0.1:4175/fixture-assets/product.svg',name='بلوفر خيارات تجريبي')
        await db.mezan_products_v2.insert_one(pullover)
        await db.warehouse_locations.insert_one({'id':'location-options','user_id':'owner','warehouse_id':'warehouse',
            'purpose':'permanent_storage','code':'LOCAL-OPTIONS','barcode_value':'LOCAL-OPTIONS','display_code':'د1 / ر1 / خ3',
            'state':'empty','max_items':100,'occupancy':{'items':[],'total_quantity':0}})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.local') as client:
            body=fixture.PurchaseApprovalMongoIntegration.payload(seed)
            invoice=(await client.post('/api/purchase-invoices',json=body)).json()
            receipt=await client.post(f'/api/purchase-invoices/{invoice["id"]}/approve-receive',json=fixture.PurchaseApprovalMongoIntegration.request_for(seed,invoice))
            if receipt.status_code!=200: raise RuntimeError('Synthetic baseline receipt could not be seeded')
        try: yield
        finally:
            assert database_name.startswith('inventory_preview_')
            await mongo.drop_database(database_name);mongo.close()
    app.router.lifespan_context=lifespan
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--isolated-test-data',action='store_true',required=True)
    parser.parse_args()
    # Child process only; this cannot activate a real owner or a production DB.
    os.environ['MEZAN_STOCKTAKE_POSTING_ENABLED']='1'
    import uvicorn
    uvicorn.run(app_for_preview(),host='127.0.0.1',port=4176,access_log=False)
