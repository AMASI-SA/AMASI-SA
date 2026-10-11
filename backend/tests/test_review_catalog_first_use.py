"""Catalog/Sync/intake and first-use regressions on real synthetic Mongo fixtures."""
import os, uuid, unittest, asyncio, copy
from unittest.mock import patch, AsyncMock
from pymongo import ReturnDocument, monitoring
from pymongo.errors import OperationFailure, ConnectionFailure
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import HTTPException, FastAPI
from httpx import AsyncClient, ASGITransport
import review_acceptance_config_guard as guard
import order_review_completion as completion
import test_review_displayed_approval as display_tests
events=[]
http_records=[]
class Monitor(monitoring.CommandListener):
    def started(self,event): pass
    def succeeded(self,event): pass
    def failed(self,event): events.append({'command':event.command_name,'failure':event.failure})
monitoring.register(Monitor())

class Writers(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo=AsyncIOMotorClient(os.environ['MZ2_TEST_MONGO_URI'],serverSelectionTimeoutMS=3000, event_listeners=[Monitor()])
        self.raw=self.mongo['readback_final_'+uuid.uuid4().hex]
        self.db=guard.AcceptanceConfigDatabase(self.raw)
        await guard.ensure_acceptance_config_storage(self.db)
    async def asyncTearDown(self):
        assert self.raw.name.startswith('readback_final_');await self.mongo.drop_database(self.raw.name);self.mongo.close()
    async def seed(self,name):
        await self.raw[name].insert_one({'_id':'p','user_id':'owner','id':'p','salla_product_id':'p',
          'product_id':'p','parent_product_id':'parent','sku':'S','barcode':'B',
          'variants':[{'id':'v','sku':'VS','barcode':'VB','cost':1}]})
    async def version(self,owner='owner'):
        return (await self.raw[guard.FENCES].find_one({'_id':owner}) or {}).get('version',0)
    async def test_setup_first_use_no_business_rows(self):
        await guard.ensure_acceptance_config_storage(self.db)
        await asyncio.gather(*(guard.ensure_acceptance_config_storage(self.db) for _ in range(4)))
        self.assertIn(guard.FENCES,await self.raw.list_collection_names())
        self.assertEqual(await self.raw[guard.FENCES].count_documents({}),0)
        self.assertIn('_id_',await self.raw[guard.FENCES].index_information())
    async def test_setup_permission_failure_propagates(self):
        failing=AsyncMock(side_effect=OperationFailure('synthetic denied',13))
        class Collection:
            create_index=failing
            def with_options(inner, *, write_concern):
                self.assertEqual(write_concern.document, {'w':'majority','j':True})
                return inner
        class DB:
            def __getitem__(self,name):return Collection()
        with self.assertRaises(OperationFailure):await guard.ensure_acceptance_config_storage(DB())
        self.assertEqual(failing.await_count,1)
    async def test_rollback_identity_and_version(self):
        await self.seed('products')
        async with await self.mongo.start_session() as session:
            with self.assertRaisesRegex(RuntimeError,'abort'):
                async with session.start_transaction():
                    await self.db.products.update_one({'user_id':'owner','id':'p'},{'$set':{'barcode':'new'}},session=session)
                    raise RuntimeError('abort')
        self.assertEqual((await self.raw.products.find_one({'id':'p'}))['barcode'],'B')
        self.assertEqual(await self.version(),0)
    async def test_cross_owner_replacement_rejected(self):
        await self.seed('products')
        with self.assertRaises(HTTPException):await self.db.products.replace_one({'user_id':'owner','id':'p'},{'user_id':'other','id':'p'})
        self.assertEqual(await self.version(),0)
    async def test_other_owner_isolation(self):
        await self.db.products.insert_one({'user_id':'other','id':'p','barcode':'X'})
        self.assertEqual(await self.version(),0);self.assertEqual(await self.version('other'),1)
    async def test_real_order_ingestion_update_and_repeat(self):
        from orders_db import _ensure_order_products_catalogued
        row={'products':[{'product_id':'p','sku':'S','barcode':'NEW','name':'Synthetic product','quantity':1}]}
        await self.raw.products.insert_one({'_id':'legacy','user_id':'owner','product_id':'p','sku':'S'})
        first=await _ensure_order_products_catalogued(self.db,'owner','TEST',row,'synthetic')
        self.assertEqual(first['updated'],1);self.assertEqual(await self.version(),1)
        second=await _ensure_order_products_catalogued(self.db,'owner','TEST',row,'synthetic')
        self.assertEqual(second['updated'],1);self.assertEqual(await self.version(),1)
        self.assertEqual(await self.raw.products.count_documents({'user_id':'owner'}),1)
    async def test_import_insert_and_nonmaterial_update(self):
        result=await self.db.products.insert_one({'user_id':'owner','product_id':'p','name':'Synthetic','barcode':None})
        self.assertIsNotNone(result.inserted_id);self.assertEqual(await self.version(),1)
        await self.db.products.update_one({'user_id':'owner','_id':result.inserted_id},{'$set':{'name':'New name','cost_current':5,'images':['synthetic']}})
        self.assertEqual(await self.version(),1)

def material_case(name,label,update,options=None):
    async def case(self):
        await self.seed(name)
        result=await self.db[name].update_one({'user_id':'owner','id':'p'},update,**(options or {}))
        self.assertEqual(result.matched_count,1);self.assertEqual(result.modified_count,1)
        self.assertEqual(await self.version(),1)
    setattr(Writers,'test_'+name+'_'+label,case)
for name in ('mezan_products_v2','salla_products','products'):
    for label,update,opt in [
      ('barcode',{'$set':{'barcode':'NEW'}},None),
      ('product_identity',{'$set':{'product_id':'OTHER','parent_product_id':'OTHER-PARENT'}},None),
      ('variant_index_barcode',{'$set':{'variants.0.barcode':'NEW'}},None),
      ('variant_all_sku',{'$set':{'variants.$[].sku':'NEW'}},None),
      ('variant_filtered_id',{'$set':{'variants.$[v].id':'NEW'}},{'array_filters':[{'v.id':'v'}]}),
      ('variant_replace',{'$set':{'variants':[{'id':'new','sku':'new','barcode':'new'}]}},None),
      ('variant_push',{'$push':{'variants':{'id':'v2','sku':'S2'}}},None),
      ('variant_pull',{'$pull':{'variants':{'id':'v'}}},None),
      ('rename_barcode',{'$rename':{'sku':'barcode'}},None)]:material_case(name,label,update,opt)
    def make_crud(name,method):
        async def case(self):
            await self.seed(name);coll=self.db[name];selector={'user_id':'owner','id':'p'}
            if method=='delete':self.assertEqual((await coll.delete_one(selector)).deleted_count,1)
            elif method=='replace':self.assertEqual((await coll.replace_one(selector,{'user_id':'owner','id':'p','barcode':'NEW'})).modified_count,1)
            elif method=='find_update':self.assertEqual((await coll.find_one_and_update(selector,{'$set':{'barcode':'NEW'}},return_document=ReturnDocument.AFTER))['barcode'],'NEW')
            elif method=='upsert':self.assertIsNotNone((await coll.update_one({'user_id':'owner','id':'absent'},{'$set':{'barcode':'NEW'}},upsert=True)).upserted_id)
            elif method=='delete_many':self.assertEqual((await coll.delete_many(selector)).deleted_count,1)
            self.assertEqual(await self.version(),1)
        return case
    for method in ('delete','replace','find_update','upsert','delete_many'):setattr(Writers,'test_'+name+'_'+method,make_crud(name,method))
    def nonmaterial(name):
        async def case(self):
            await self.seed(name)
            await self.db[name].update_one({'user_id':'owner','id':'p'},{'$set':{'cost':10,'images':['new'],'name':'New','variants.0.cost':8}})
            self.assertEqual(await self.version(),0)
        return case
    setattr(Writers,'test_'+name+'_nonmaterial_preserved',nonmaterial(name))

class Approval(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = display_tests.DisplayedApprovalTests.asyncSetUp
    asyncTearDown = display_tests.DisplayedApprovalTests.asyncTearDown
    source_payload = display_tests.DisplayedApprovalTests.source_payload
    webhook = display_tests.DisplayedApprovalTests.webhook
    saved = display_tests.DisplayedApprovalTests.saved
    assert_completed = display_tests.DisplayedApprovalTests.assert_completed
    assert_no_completion = display_tests.DisplayedApprovalTests.assert_no_completion
    display = display_tests.DisplayedApprovalTests.display
    submit = display_tests.DisplayedApprovalTests.submit
    state = display_tests.DisplayedApprovalTests.state
    async def race(self,collection,update,*,initialized=True,expected=409):
        if initialized:await guard.ensure_acceptance_config_storage(self.db)
        detail=await self.display();before=await self.state();real=completion.acceptance_snapshot;injected=False;posts=0
        async def after(scoped,**kwargs):
            nonlocal injected
            value=await real(scoped,**kwargs)
            if not injected:
                injected=True
                await guard.AcceptanceConfigDatabase(self.db)[collection].update_one(
                    {'user_id':'owner','id':'recipe-material' if collection==display_tests.local.fixture.PRODUCT_BINDINGS else 'p'},update)
            return value
        start=len(events)
        with patch.object(completion,'acceptance_snapshot',after):
            posts+=1;response=await self.submit(detail)
        self.assertEqual(response.status_code,expected,response.text)
        self.assertEqual(await self.state(),before)
        await self.assert_no_completion();self.assertEqual(posts,1)
        if expected==503:
            self.assertEqual(response.json()['detail']['state'],'unknown')
            self.assertIs(response.json()['detail']['retry_post'],False)
            self.assertTrue(any(e['failure'].get('code')==112 for e in events[start:]))
        http_records.append({'test':self._testMethodName,'status':response.status_code,
          'code':response.json().get('detail',{}).get('code'),'POST':posts,'zero_completion_effects':True,
          'driver_errors':events[start:]})
    async def test_first_use_without_setup_commit112_typed_unknown(self):
        await self.race(display_tests.local.fixture.PRODUCT_BINDINGS,{'$set':{'quantity':3}},initialized=False,expected=503)
    async def test_first_use_with_setup_stale_recipe_409(self):
        await self.race(display_tests.local.fixture.PRODUCT_BINDINGS,{'$set':{'quantity':3}})
    async def test_barcode_after_snapshot_rejected(self):
        await self.race('mezan_products_v2',{'$set':{'barcode':'NEW'}})
    async def test_variants_after_snapshot_rejected(self):
        await self.race('mezan_products_v2',{'$set':{'variants':[{'id':'new','sku':'new'}]}})
    async def test_cache_insert_after_snapshot_rejected(self):
        await guard.ensure_acceptance_config_storage(self.db)
        detail=await self.display();before=await self.state();real=completion.acceptance_snapshot;injected=False
        async def after(scoped,**kwargs):
            nonlocal injected
            snap=await real(scoped,**kwargs)
            if not injected:
                injected=True
                await guard.AcceptanceConfigDatabase(self.db).salla_products.insert_one(
                    {'user_id':'owner','id':'p','product_id':'p','barcode':'new'})
            return snap
        with patch.object(completion,'acceptance_snapshot',after):response=await self.submit(detail)
        self.assertEqual(response.status_code,409,response.text);self.assertEqual(await self.state(),before);await self.assert_no_completion()
    async def test_completion_first_then_guarded_sync_preserved(self):
        await guard.ensure_acceptance_config_storage(self.db)
        detail=await self.display();await self.assert_completed(await self.submit(detail))
        await guard.AcceptanceConfigDatabase(self.db).mezan_products_v2.update_one(
            {'user_id':'owner','id':'p'},{'$set':{'barcode':'new'}})
        self.assertGreaterEqual((await self.db[guard.FENCES].find_one({'_id':'owner'}))['version'],1)

class Errors(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_commit_is_not_rejected_or_replayed(self):
        error=OperationFailure('synthetic lost ACK',91,{'errorLabels':['UnknownTransactionCommitResult']})
        fn=AsyncMock(side_effect=error)
        with patch.object(completion,'complete_review_operation',fn):
            with self.assertRaises(HTTPException) as got:await completion.complete_local_review_operation(None)
        self.assertEqual(fn.await_count,1);self.assertEqual(got.exception.status_code,503)
        self.assertEqual(got.exception.detail['execution_outcome'],'unknown');self.assertFalse(got.exception.detail['retry_post'])
    async def test_network_error_no_resubmit(self):
        fn=AsyncMock(side_effect=ConnectionFailure('synthetic'))
        with patch.object(completion,'complete_review_operation',fn):
            with self.assertRaises(HTTPException) as got:await completion.complete_local_review_operation(None)
        self.assertEqual(fn.await_count,1);self.assertEqual(got.exception.detail['state'],'unknown')
    async def test_existing_business_409_unchanged(self):
        original=HTTPException(409,detail={'code':'component_source_event_stale'})
        with patch.object(completion,'complete_review_operation',AsyncMock(side_effect=original)):
            with self.assertRaises(HTTPException) as got:await completion.complete_local_review_operation(None)
        self.assertIs(got.exception,original)
    async def test_readiness_blocks_before_or_failed_setup(self):
        from boot_runtime import readiness_traffic_gate
        app=FastAPI();app.middleware('http')(readiness_traffic_gate);hits=[]
        @app.post('/api/review')
        async def write():hits.append(1);return {'ok':True}
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as api:
            for state in ('starting','failed'):
                app.state.readiness=state;self.assertEqual((await api.post('/api/review')).status_code,503)
            self.assertEqual(hits,[])
            app.state.readiness='ready';self.assertEqual((await api.post('/api/review')).status_code,200)
            self.assertEqual(hits,[1])


class Supplemental(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Writers.asyncSetUp
    asyncTearDown = Writers.asyncTearDown
    seed = Writers.seed
    version = Writers.version
    async def intake(self, operational):
        from orders_db import upsert_order
        await self.raw.products.insert_one({'_id':'legacy','user_id':'owner','product_id':'p','sku':'S'})
        await self.raw.create_collection('unified_orders')
        await self.raw.mz2_atomic_owners.insert_one({'_id':'owner','revision':0,'writes_paused':False,'control_revision':0})
        incoming={'products':[{'product_id':'p','sku':'S','barcode':'NEW','name':'Synthetic product','quantity':1}]}
        opts={'shipping_snapshot':{'test_only_observation':True}} if operational else {}
        for _ in range(2):
            result=await upsert_order(self.db,'owner','TEST-ONLY',incoming,'synthetic',**opts)
            self.assertIn('doc',result)
            actual=await self.raw.products.find_one({'_id':'legacy'})
            self.assertEqual(actual['barcode'],'NEW')
            self.assertEqual(await self.version(),1)
            self.assertEqual(await self.raw.products.count_documents({'user_id':'owner'}),1)
            self.assertEqual(await self.raw.unified_orders.count_documents({'user_id':'owner','order_number':'TEST-ONLY'}),1)
    async def test_actual_upsert_direct(self):await self.intake(False)
    async def test_actual_upsert_operational_branch(self):await self.intake(True)
    async def sync(self, rows):
        import product_v2_sync_hotfix as sync
        provider=AsyncMock(side_effect=[{'data':copy.deepcopy(rows)},{'data':[]}])
        with patch.object(sync,'call_salla',provider):
            result=await sync.run_product_v2_sync_fixed(self.db,'owner')
        self.assertEqual(result['errors_count'],0,result)
        self.assertEqual(result['status'],'completed',result)
        self.assertTrue(result['traversal_complete'])
        self.assertEqual(result['pages_fetched'],2)
        self.assertEqual(provider.await_count,2)
        for call in provider.await_args_list:
            self.assertEqual(call.args[2:4],('GET','/products'))
        return result
    async def test_actual_v2_sync_create_unchanged_update_archive(self):
        one={'id':'p','name':'Synthetic','sku':'S','barcode':'B','variants':[{'id':'v','sku':'V','barcode':'VB'}]}
        two={'id':'q','name':'Other synthetic','sku':'Q','barcode':'QB'}
        first=await self.sync([one,two]);self.assertEqual(first['created'],2)
        version=await self.version();self.assertEqual(version,2)
        second=await self.sync([one,two]);self.assertEqual(second['unchanged'],2)
        self.assertEqual(await self.version(),version)
        one['barcode']='B2';one['variants'][0]['barcode']='VB2'
        third=await self.sync([one]);self.assertEqual(third['updated'],1)
        self.assertEqual(await self.version(),version+1)
        self.assertEqual(await self.raw.mezan_product_change_log_v2.count_documents({'user_id':'owner'}),1)
        q=await self.raw.mezan_products_v2.find_one({'user_id':'owner','salla_product_id':'q'})
        self.assertTrue(q['archived'])
        p=await self.raw.mezan_products_v2.find_one({'user_id':'owner','salla_product_id':'p'})
        self.assertEqual(p['barcode'],'B2');self.assertEqual(p['variants'][0]['barcode'],'VB2')
    async def test_full_array_nonmaterial_keeps_fence(self):
        await self.seed('products')
        await self.db.products.update_one({'user_id':'owner','id':'p'},
            {'$set':{'variants':[{'id':'v','sku':'VS','barcode':'VB','cost':99,'image':'different'}]}})
        self.assertEqual(await self.version(),0)
