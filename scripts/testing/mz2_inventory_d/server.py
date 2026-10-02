"""Loopback-only Stage 10 browser proof, real API and isolated Mongo database."""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'backend'))
from accounting_onboarding import install_onboarding_routes
from tests.test_financial_accounts_real_mongo import mongo_db
if urlparse(os.environ.get('MZ2_TEST_MONGO_URI', '')).hostname not in {'127.0.0.1', 'localhost'}:
    raise RuntimeError('Disposable loopback Mongo required')

@asynccontextmanager
async def lifespan(app):
    fixture = mongo_db.__wrapped__()
    db = await anext(fixture)
    actor = {'id': 'inventory-test-owner', 'role': 'owner', 'is_active': True, 'disabled': False}
    await db.users.insert_one(actor)
    owner = {'user_id': actor['id']}
    await db.mezan_products_v2.insert_one({**owner, 'mezan_product_id': 'p1', 'name': 'عباية اختبار', 'sku': 'ABAYA-54', 'barcode': '628001', 'main_image': '/fixture-product.svg', 'options': [{'id': 'color', 'name': 'اللون', 'values': [{'id': 'black', 'name': 'أسود'}, {'id': 'blue', 'name': 'أزرق'}]}], 'variants': [{'id': 'black-54', 'sku': 'BLK-54', 'barcode': '628002', 'selections': [{'name': 'اللون', 'value': 'أسود'}, {'name': 'المقاس', 'value': '54'}]}, {'id': 'blue-54', 'sku': 'BLUE-54', 'barcode': '628003', 'selections': [{'name': 'اللون', 'value': 'أزرق'}, {'name': 'المقاس', 'value': '54'}]}]})
    await db.mezan_cost_resources_v2.insert_one({**owner, 'id': 'fabric', 'name': 'قماش اختبار', 'code': 'FAB', 'unit': 'meter', 'category_ids': ['fabric-cat'], 'status': 'active', 'track_inventory': True, 'kind': 'material'})
    await db.mezan_component_categories_v2.insert_one({**owner, 'id': 'fabric-cat', 'name': 'أقمشة'})
    await db.products.insert_one({**owner, 'id': 'legacy-only', 'name': 'Legacy only'})
    await db.warehouse_locations.insert_one({**owner, 'id': 'location', 'warehouse_id': 'مستودع اختبار', 'purpose': 'permanent_storage', 'code': 'A-01', 'barcode_value': 'BIN-01'})
    async def user(): return actor
    router = APIRouter()
    install_onboarding_routes(router, db, user, {})
    app.include_router(router, prefix='/api')
    @app.get('/api/financial-provider-apps/accounting-module/financial-accounts')
    async def accounts(): return {'items': []}
    baseline = {name: await db[name].count_documents({}) for name in await db.list_collection_names()}
    @app.get('/__test/proof')
    async def proof():
        counts = {name: await db[name].count_documents({}) for name in await db.list_collection_names() if name != 'mz2_onboarding_sessions'}
        return {'non_session_collections_unchanged': counts == baseline, 'database': db.name}
    @app.get('/fixture-product.svg')
    async def image(): return Response('<svg xmlns="http://www.w3.org/2000/svg" width="160" height="180"><rect width="160" height="180" fill="#f1f5f9"/><path d="M55 25 L105 25 L140 90 L117 102 L106 75 L115 163 L45 163 L54 75 L43 102 L20 90 Z" fill="#172b26"/><text x="80" y="176" text-anchor="middle" font-size="10">TEST FIXTURE</text></svg>', media_type='image/svg+xml')
    app.mount('/', StaticFiles(directory=os.environ['MZ2_AB_DIST'], html=True))
    try: yield
    finally: await fixture.aclose()
app = FastAPI(lifespan=lifespan)
