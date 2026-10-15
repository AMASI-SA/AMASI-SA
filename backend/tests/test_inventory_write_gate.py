"""Real Replica Set admission across independent Python processes."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import test_g47_purchase_approval_integration as g47
from operational_physical_receipts import receive, return_pending
from inventory_write_gate import admitted, set_pause
from fastapi import HTTPException


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = g47.PurchaseApprovalMongoIntegration()
        self.f.addAsyncCleanup = self.addAsyncCleanup
        await self.f.asyncSetUp()
        self.db = self.f.db
        await self.db.warehouse_locations_warehouses.insert_one({'id':'warehouse','user_id':'owner','status':'active'})
        self.payload = {'source_document_id':'GATE','source_line_id':'physical-line','supplier_id':'supplier',
            'line':{'item_type':'PRODUCT','product_id':'product-A','variant_id':'variant-A','quantity':10},
            'unit':'piece','operational_unit_cost':15,'location_id':'location-product',
            'scanned_location_barcode':'LOC-P','preparation_state':'ready_complete','specifications':{}}

    async def child(self, mode):
        p = await asyncio.create_subprocess_exec(sys.executable, __file__, mode, self.db.name,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.addAsyncCleanup(self.stop_child, p)
        ready = await asyncio.wait_for(p.stdout.readline(), 10)
        self.assertEqual(ready.strip(), b'READY', (ready, await p.stderr.read() if p.returncode is not None else b''))
        return p

    async def stop_child(self, p):
        if p.returncode is None:
            p.kill()
        await p.wait()

    async def test_other_process_busy_worker_death_and_expiry(self):
        p = await self.child('crash')
        await p.wait()
        with self.assertRaises(HTTPException) as e:
            await receive(self.db, user=self.f.actor, payload=self.payload)
        self.assertEqual(e.exception.status_code, 429)
        await asyncio.sleep(1.2)
        await receive(self.db, user=self.f.actor, payload=self.payload)
        self.assertEqual((await self.db.warehouse_locations.find_one({'id':'location-product'}))['occupancy']['total_quantity'],10)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}),0)

    async def test_expiry_does_not_steal_live_transaction_and_pause_drains(self):
        p = await self.child('transaction')
        await asyncio.sleep(1.1)
        with self.assertRaises(HTTPException) as e:
            await receive(self.db, user=self.f.actor, payload=self.payload)
        self.assertEqual(e.exception.status_code,429)
        control = asyncio.create_task(set_pause(self.db,user=self.f.actor,paused=True,revision=1,reason='emergency'))
        await asyncio.sleep(.1)
        self.assertFalse(control.done())
        await asyncio.wait_for(p.wait(),10)
        self.assertEqual(p.returncode,0,(await p.stderr.read()).decode())
        await control
        with self.assertRaises(HTTPException) as e:
            await receive(self.db,user=self.f.actor,payload=self.payload)
        self.assertEqual(e.exception.status_code,423)

    async def test_pause_return_adoption_resume_authority_and_replay(self):
        receipt = await receive(self.db,user=self.f.actor,payload=self.payload)
        invoice = await self.f.draft(self.f.payload(invoice_number='GATE',lines=[{'id':'line-product','item_type':'PRODUCT','product_id':'product-A','variant_id':'variant-A','quantity':10,'unit_cost':15}]))
        request = self.f.request_for(invoice)
        request['receipts'][0].update(physical_receipt_id=receipt['id'],physical_source_line_id='physical-line')
        before = await self.db.warehouse_locations.find_one({'id':'location-product'})
        await set_pause(self.db,user=self.f.actor,paused=True,revision=1,reason='pause')
        with self.assertRaises(HTTPException) as e:
            await return_pending(self.db,user=self.f.actor,receipt_id=receipt['id'],source_return_id='return',quantity=1)
        self.assertEqual(e.exception.status_code,423)
        self.assertEqual((await self.f.approve(invoice,request)).status_code,423)
        self.assertEqual(await self.db.warehouse_locations.find_one({'id':'location-product'}),before)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}),0)
        self.assertEqual(await self.db.mz2_inventory_cost_states.count_documents({}),0)
        with self.assertRaises(HTTPException):
            await set_pause(self.db,user={'id':'outsider'},paused=False,revision=1,reason='denied')
        with self.assertRaises(HTTPException):
            await set_pause(self.db,user=self.f.actor,paused=False,revision=0,reason='stale')
        await set_pause(self.db,user=self.f.actor,paused=False,revision=2,reason='resume')
        for _ in range(2):
            self.assertEqual((await self.f.approve(invoice,request)).status_code,200)
        self.assertEqual((await self.db.warehouse_locations.find_one({'id':'location-product'}))['occupancy']['total_quantity'],10)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}),1)
        self.assertEqual(await self.db.mz2_inventory_control_audit.count_documents({}),3)

    async def test_stale_worker_cannot_start_transaction_after_expiry(self):
        from operational_atomic import operational_owner
        async def delayed():
            await self.db.mz2_inventory_admission.update_one({'_id':'owner'},
                [{'$set':{'expires_at':{'$subtract':['$$NOW',1]}}}])
            async def forbidden(scoped):
                raise AssertionError('stale worker entered transaction')
            with self.assertRaises(HTTPException) as error:
                await operational_owner(self.db,'owner',forbidden,profile='physical_receipt')
            self.assertEqual(error.exception.status_code,409)
        await admitted(self.db,'owner','stale',delayed)
        await receive(self.db,user=self.f.actor,payload=self.payload)

    async def test_control_http_requires_owner_and_exposes_revision(self):
        import httpx
        from fastapi import FastAPI
        from operational_physical_receipts import attach_physical_routes
        from fastapi import APIRouter
        actor=dict(self.f.actor)
        async def current():return actor
        app=FastAPI();router=APIRouter();attach_physical_routes(router,self.db,current);app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.local') as client:
            self.assertEqual((await client.get('/physical-write-control')).json(),{'paused':False,'revision':1})
            response=await client.put('/physical-write-control',json={'paused':True,'revision':1,'reason':'HTTP pause'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json(),{'paused':True,'revision':2})
            await self.db.users.insert_one({'id':'staff','role':'staff','created_by':'owner','is_active':True,'accounting_permissions':['accounting.inventory.view']})
            actor.clear();actor.update({'id':'staff'})
            response=await client.put('/physical-write-control',json={'paused':False,'revision':1,'reason':'not owner'})
            self.assertEqual(response.status_code,403,response.text)
            self.assertTrue((await self.db.mz2_atomic_owners.find_one({'_id':'owner'}))['physical_inventory_writes_paused'])

    async def test_lost_committed_response_replay_is_once(self):
        import httpx
        r=await receive(self.db,user=self.f.actor,payload=self.payload)
        invoice=await self.f.draft(self.f.payload(invoice_number='GATE',lines=[{'id':'line-product','item_type':'PRODUCT','product_id':'product-A','variant_id':'variant-A','quantity':10,'unit_cost':15}]))
        request=self.f.request_for(invoice)
        request['receipts'][0].update(physical_receipt_id=r['id'],physical_source_line_id='physical-line')
        async with httpx.AsyncClient(transport=g47.LoseCommittedResponse(self.f.app),base_url='http://synthetic.local') as client:
            with self.assertRaises(httpx.ReadError):
                await self.f.approve(invoice,request,client)
        self.assertEqual((await self.f.approve(invoice,request)).status_code,200)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}),1)
        self.assertEqual((await self.db.warehouse_locations.find_one({'id':'location-product'}))['occupancy']['total_quantity'],10)


async def worker(mode, name):
    from motor.motor_asyncio import AsyncIOMotorClient
    import inventory_write_gate as gate
    from operational_atomic import operational_owner
    client=AsyncIOMotorClient(g47.local_uri('MZ2_TEST_MONGO_URI'))
    assert name.startswith('g47_purchase_')
    db=client[name]
    gate.LEASE_MS=1000
    async def work():
        if mode=='crash':
            print('READY',flush=True)
            os._exit(0)
        async def tx(scoped):
            print('READY',flush=True)
            await asyncio.sleep(4)
        await operational_owner(db,'owner',tx,profile='physical_receipt')
    try:
        await admitted(db,'owner','worker',work)
    finally:
        client.close()

if __name__=='__main__':
    asyncio.run(worker(sys.argv[1],sys.argv[2]))
