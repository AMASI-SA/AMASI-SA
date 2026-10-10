"""Real Mongo assignment reconciliation; only provider transport/context are isolated."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock, patch
from motor.motor_asyncio import AsyncIOMotorClient
import preparation_piece_operations as p

class AssignmentSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get('MZ2_TEST_MONGO_URI', '')
        if not uri: self.skipTest('isolated real Mongo required')
        assert uri.startswith('mongodb://127.0.0.1:')
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client['assignment_sync_' + uuid4().hex]
        hello = await self.db.command('hello')
        self.assertTrue(hello.get('setName') and hello.get('isWritablePrimary'))
        self.assertEqual((await self.client.server_info())['version'], '8.0.12')
        self.order = SimpleNamespace(order_number='synthetic', source=SimpleNamespace(source_order_id='synthetic'),
            items=[SimpleNamespace(order_item_id='line', quantity=2)])
        self.workflow = dict(user_id='tenant', order_number='synthetic', stage='reviewed', revision=0,
            completion_mode=p.LOCAL_COMPLETION_MODE, review_completion_operation_id='approval', items=[])
        await self.db[p.WORKFLOWS].insert_one(dict(self.workflow))
        self.status = 'reviewed'; self.posts = 0; self.gets = 0; self.fail_post = False; self.readback_stale = False
        self.patches = [
            patch.object(p, 'load_reviewed_product_context', AsyncMock(return_value={'pairs':[(self.order,self.workflow)]})),
            patch.object(p, 'load_local_review_workflows', AsyncMock(return_value={'synthetic': self.workflow})),
            patch.object(p, 'local_review_stage_eligible', return_value=True),
            patch.object(p, 'call_salla', side_effect=self.transport),
        ]
        for x in self.patches: x.start()
    async def asyncTearDown(self):
        for x in reversed(self.patches): x.stop()
        await self.client.drop_database(self.db.name); self.client.close()
    async def transport(self, db, user, method, path, **kw):
        if path == '/orders/statuses': return {'data':[{'id':7,'name':'قيد التنفيذ'}]}
        if method == 'POST':
            self.posts += 1
            await asyncio.sleep(.025)
            if self.fail_post: raise TimeoutError('uncertain isolated transport')
            if not self.readback_stale: self.status='in_progress'
            return {'success':True}
        self.gets += 1
        return {'data':{'status':{'slug':self.status}}}
    async def allocate(self, indices=(1,2), item='line'):
        for i in indices:
            await self.db[p.PREPARATION_UNIT_ALLOCATIONS].insert_one(dict(
                user_id='tenant',order_number='synthetic',order_item_id=item,unit_index=i,status='committed'))
    async def reconcile(self):
        return await p._assigned_reconcile_order_stage(self.db,user_id='tenant',order_number='synthetic',
            batch_id='batch',actor={'id':'actor'})
    async def state(self): return await self.db[p.WORKFLOWS].find_one({'order_number':'synthetic'})
    async def test_full_readback_duplicate(self):
        await self.allocate(); self.assertEqual(await self.reconcile(),(True,0))
        self.assertEqual((await self.state())['salla_status_sync_state'],'sent')
        await self.reconcile(); self.assertEqual(self.posts,1)
        self.assertEqual(await self.db[p.EVENTS].count_documents({'event_type':'order_moved_to_in_progress'}),1)
    async def test_partial(self):
        await self.allocate((1,)); self.assertEqual(await self.reconcile(),(False,1)); self.assertEqual(self.posts,0)
        self.assertEqual((await self.state())['stage'],'reviewed')
    async def test_multiple_lines(self):
        self.order.items.append(SimpleNamespace(order_item_id='other',quantity=2))
        await self.allocate(); await self.allocate((1,), 'other')
        self.assertEqual(await self.reconcile(),(False,1)); self.assertEqual(self.posts,0)
        await self.allocate((2,), 'other'); await self.reconcile(); self.assertEqual(self.posts,1)
    async def test_already_in_progress(self):
        self.status='in_progress'; await self.allocate(); await self.reconcile(); self.assertEqual(self.posts,0)
    async def test_concurrent(self):
        await self.allocate()
        results=await asyncio.gather(*(self.reconcile() for _ in range(10)),return_exceptions=True)
        self.assertEqual(self.posts,1)
        await self.reconcile()
        self.assertEqual(await self.db[p.EVENTS].count_documents({'event_type':'order_moved_to_in_progress'}),1)
        self.assertEqual(await self.db[p.PREPARATION_UNIT_ALLOCATIONS].count_documents({}),2)
    async def test_timeout_preserves_assignment_no_redispatch(self):
        await self.allocate(); self.fail_post=True
        with self.assertRaises(TimeoutError): await self.reconcile()
        with self.assertRaises(RuntimeError): await self.reconcile()
        self.assertEqual(self.posts,1); self.assertEqual((await self.state())['stage'],'reviewed')
        self.assertEqual(await self.db[p.PREPARATION_UNIT_ALLOCATIONS].count_documents({}),2)
        self.status='in_progress'; await self.reconcile(); self.assertEqual(self.posts,1)
    async def test_post_success_stale_readback(self):
        await self.allocate(); self.readback_stale=True
        with self.assertRaises(RuntimeError): await self.reconcile()
        with self.assertRaises(RuntimeError): await self.reconcile()
        self.assertEqual(self.posts,1)
        self.status='in_progress'; await self.reconcile(); self.assertEqual(self.posts,1)
    async def test_mixed_already_local_in_progress(self):
        await self.db[p.WORKFLOWS].update_one({}, {'$set':{'stage':'in_progress','in_progress_at':'original'}})
        await self.allocate((1,)); self.assertEqual(await self.reconcile(),(False,1))
        await self.allocate((2,)); self.assertEqual(await self.reconcile(),(True,0))
        self.assertEqual(self.posts,1); self.assertEqual((await self.state())['in_progress_at'],'original')
        self.assertEqual(await self.db[p.EVENTS].count_documents({'event_type':'order_moved_to_in_progress'}),0)
    async def test_provider_backed(self):
        await self.db[p.WORKFLOWS].update_one({}, {'$unset':{'completion_mode':''}})
        await self.allocate(); await self.reconcile(); self.assertEqual(self.posts,1)
    async def test_out_of_range_not_coverage(self):
        await self.allocate((1,99)); self.assertEqual(await self.reconcile(),(False,1)); self.assertEqual(self.posts,0)

