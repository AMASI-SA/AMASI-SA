"""Default-closed acceptance against real Mongo and independent backend workers."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from pymongo.errors import AutoReconnect
from tests import test_g47_purchase_approval_integration as g47
from inventory_write_gate import admitted, set_pause
from operational_physical_receipts import receive, return_pending, attach_physical_routes


def payload():
    return {'source_document_id':'DEFAULT-CLOSED', 'source_line_id':'physical-line',
            'supplier_id':'supplier', 'line':{'item_type':'PRODUCT','product_id':'product-A',
            'variant_id':'variant-A','quantity':10}, 'unit':'piece', 'operational_unit_cost':15,
            'location_id':'location-product','scanned_location_barcode':'LOC-P',
            'preparation_state':'ready_complete','specifications':{}}


class DefaultClosedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f = g47.PurchaseApprovalMongoIntegration()
        self.f.addAsyncCleanup = self.addAsyncCleanup
        await self.f.asyncSetUp()
        self.db = self.f.db
        await self.db.warehouse_locations_warehouses.insert_one(
            {'id':'warehouse','user_id':'owner','status':'active'})

    async def unknown(self):
        await self.db.mz2_atomic_owners.update_one({'_id':'owner'}, {'$unset':{
            'physical_inventory_writes_paused':'', 'inventory_control_revision':''}})

    async def snapshot(self):
        names = ['warehouse_locations','warehouse_location_events','mezan_inventory_receipts_v2',
                 'mz2_inventory_cost_states','accounting_journal_groups_v2','accounting_general_ledger_v2',
                 'liabilities','purchase_invoices']
        return {n:await self.db[n].find({}).sort('_id',1).to_list(1000) for n in names}

    async def test_missing_control_rejects_before_admission_or_callback(self):
        await self.unknown()
        before = await self.snapshot()
        callback = AsyncMock()
        with self.assertRaises(HTTPException) as error:
            await admitted(self.db,'owner','physical_receive',callback)
        self.assertEqual(error.exception.status_code,423)
        callback.assert_not_awaited()
        self.assertEqual(await self.db.mz2_inventory_admission.count_documents({}),0)
        self.assertEqual(await self.db.mz2_inventory_attempts.count_documents({}),0)
        self.assertEqual(await self.snapshot(),before)

    async def test_missing_malformed_and_unrevisioned_controls_stay_closed(self):
        for fields in ({}, {'physical_inventory_writes_paused':False},
                       {'physical_inventory_writes_paused':False,'inventory_control_revision':True},
                       {'physical_inventory_writes_paused':'false','inventory_control_revision':1},
                       {'physical_inventory_writes_paused':None,'inventory_control_revision':1}):
            await self.unknown()
            if fields:
                await self.db.mz2_atomic_owners.update_one({'_id':'owner'},{'$set':fields})
            with self.assertRaises(HTTPException) as error:
                await receive(self.db,user=self.f.actor,payload=payload())
            self.assertEqual(error.exception.status_code,423)
        await self.db.mz2_atomic_owners.delete_one({'_id':'owner'})
        with self.assertRaises(HTTPException) as error:
            await receive(self.db,user=self.f.actor,payload=payload())
        self.assertEqual(error.exception.status_code,423)

    async def test_receipt_return_and_adoption_closed_preserve_data_and_read_projection(self):
        from operational_inventory_projection import projection
        receipt = await receive(self.db,user=self.f.actor,payload=payload())
        invoice = await self.f.draft(self.f.payload(invoice_number='DEFAULT-CLOSED',lines=[{
            'id':'line-product','item_type':'PRODUCT','product_id':'product-A','variant_id':'variant-A',
            'quantity':10,'unit_cost':15}]))
        request = self.f.request_for(invoice)
        request['receipts'][0].update(physical_receipt_id=receipt['id'],physical_source_line_id='physical-line')
        await self.unknown()
        before = await self.snapshot()
        for operation in (lambda:receive(self.db,user=self.f.actor,payload=payload()),
                          lambda:return_pending(self.db,user=self.f.actor,receipt_id=receipt['id'],
                                                source_return_id='RETURN',quantity=1)):
            with self.assertRaises(HTTPException) as error:
                await operation()
            self.assertEqual(error.exception.status_code,423)
        response = await self.f.approve(invoice,request)
        self.assertEqual(response.status_code,423,response.text)
        self.assertEqual(await self.snapshot(),before)
        rows = (await projection(self.db,'owner'))['items']
        self.assertEqual(sum(row['physical'] for row in rows),10)
        self.assertEqual(sum(row['available'] for row in rows),0)

    async def test_control_read_failure_is_closed_without_callback(self):
        # Fault only the authoritative control read; do not replace business services.
        from motor.motor_asyncio import AsyncIOMotorCollection
        callback = AsyncMock()
        with patch.object(AsyncIOMotorCollection,'find_one',side_effect=AutoReconnect('synthetic outage')):
            with self.assertRaises(HTTPException) as error:
                await admitted(self.db,'owner','physical_receive',callback)
        self.assertEqual(error.exception.status_code,503)
        callback.assert_not_awaited()
        self.assertEqual(await self.db.mz2_inventory_attempts.count_documents({}),0)

    async def test_pause_between_admission_and_transaction_rejects_and_rolls_back(self):
        from operational_atomic import operational_owner
        before = await self.snapshot()
        async def delayed():
            # Simulate a separately committed pause after initial admission.
            await self.db.mz2_atomic_owners.update_one({'_id':'owner'},
                {'$set':{'physical_inventory_writes_paused':True}})
            async def forbidden(scoped):
                self.fail('paused writer entered transaction body')
            await operational_owner(self.db,'owner',forbidden,profile='physical_receipt')
        with self.assertRaises(HTTPException) as error:
            await admitted(self.db,'owner','race',delayed)
        self.assertEqual(error.exception.status_code,423)
        self.assertEqual(await self.snapshot(),before)

    async def test_owner_only_activation_audit_and_default_control_read(self):
        import httpx
        from fastapi import FastAPI, APIRouter
        await self.unknown()
        actor = dict(self.f.actor)
        async def current(): return dict(actor)
        app=FastAPI(); router=APIRouter(); attach_physical_routes(router,self.db,current)
        app.include_router(router)
        audits = await self.db.mz2_inventory_control_audit.count_documents({})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.local') as http:
            self.assertEqual((await http.get('/physical-write-control')).json(),{'paused':True,'revision':0})
            await self.db.users.insert_one({'id':'staff','role':'staff','created_by':'owner','is_active':True})
            actor.clear(); actor['id']='staff'
            result=await http.put('/physical-write-control',json={'paused':False,'revision':0,'reason':'denied'})
            self.assertEqual(result.status_code,403)
            self.assertEqual(await self.db.mz2_inventory_control_audit.count_documents({}),audits)
            actor.clear(); actor.update(self.f.actor)
            result=await http.put('/physical-write-control',json={'paused':False,'revision':0,'reason':'approved test activation'})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json(),{'paused':False,'revision':1})
        audit=await self.db.mz2_inventory_control_audit.find_one({'reason':'approved test activation'})
        self.assertEqual(audit['actor_id'],'owner')
        self.assertFalse(audit['paused'])

    async def child(self):
        process=await asyncio.create_subprocess_exec(sys.executable,__file__,'probe',self.db.name,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            out,err=await asyncio.wait_for(process.communicate(),15)
        finally:
            if process.returncode is None:
                process.kill(); await process.wait()
        self.assertEqual(process.returncode,0,err.decode())
        return json.loads(out)

    async def test_new_workers_share_default_activation_and_pause_without_duplicate_stock(self):
        await self.unknown()
        self.assertEqual(await asyncio.gather(self.child(),self.child()),[423,423])
        await set_pause(self.db,user=self.f.actor,paused=False,revision=0,reason='synthetic activation')
        results=await asyncio.gather(self.child(),self.child())
        self.assertIn(200,results)
        self.assertTrue(all(code in (200,429) for code in results),results)
        self.assertEqual(await self.child(),200)  # fresh process / retry retains source identity
        self.assertEqual((await self.db.warehouse_locations.find_one({'id':'location-product'}))['occupancy']['total_quantity'],10)
        await set_pause(self.db,user=self.f.actor,paused=True,revision=1,reason='synthetic stop')
        self.assertEqual(await asyncio.gather(self.child(),self.child()),[423,423])
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}),0)


async def probe(name):
    from motor.motor_asyncio import AsyncIOMotorClient
    assert name.startswith('g47_purchase_')
    client=AsyncIOMotorClient(g47.local_uri('MZ2_TEST_MONGO_URI'))
    try:
        await receive(client[name],user={'id':'owner','role':'owner'},payload=payload())
        print(json.dumps(200))
    except HTTPException as exc:
        print(json.dumps(exc.status_code))
    finally:
        client.close()


if __name__ == '__main__':
    asyncio.run(probe(sys.argv[2]))
