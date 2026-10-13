"""Real physical writer acceptance on disposable replica-set databases."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from tests import test_g47_purchase_approval_integration as g47
from operational_physical_receipts import receive, return_pending
from operational_inventory_projection import projection
from purchase_receiving_service import assert_opening_inventory_initialized


class PhysicalWriterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f=g47.PurchaseApprovalMongoIntegration()
        self.f.addAsyncCleanup=self.addAsyncCleanup
        await self.f.asyncSetUp()
        self.db=self.f.db
        await self.db.warehouse_locations_warehouses.insert_one({"id":"warehouse","user_id":"owner","status":"active"})
        for key in ("gold","silver"):
            await self.db.warehouse_locations.insert_one({"id":key,"user_id":"owner","warehouse_id":"warehouse","purpose":"permanent_storage","barcode_value":key.upper(),"state":"empty","max_items":1000,"occupancy":{"items":[],"total_quantity":0}})

    def payload(self, color="gold", quantity=10):
        return {"supplier_id":"supplier","source_document_id":"PHYSICAL-001","source_line_id":color,"line":{"item_type":"PRODUCT","product_id":"product-A","variant_id":"variant-A" if color=="gold" else "variant-B","quantity":quantity},"unit":"piece","operational_unit_cost":"15","location_id":color,"scanned_location_barcode":color.upper(),"preparation_state":"requires_preparation" if color=="gold" else "ready_complete","specifications":{} if color=="gold" else {"\u0627\u0644\u0627\u0633\u0645":"\u0639\u0628\u064a\u0631"}}

    async def snapshot_financial(self):
        names=[n for n in await self.db.list_collection_names() if n.startswith("accounting_") or n.startswith("mz2_opening") or n in {"mz2_inventory_cost_states","general_ledger","liabilities"}]
        return {n:await self.db[n].find({}).sort("_id",1).to_list(1000) for n in names}

    async def test_real_110_cycle_raw_customized_supplier_returns_and_no_financial_writes(self):
        # Existing3 units entered through the real approved contract; before new intake.
        invoice=await self.f.draft(self.f.payload(lines=[{"id":"historical","item_type":"PRODUCT","product_id":"product-B","quantity":3,"unit_cost":7}]))
        result=await self.f.approve(invoice)
        self.assertEqual(result.status_code,200,result.text)
        before=await self.snapshot_financial()
        rows=(await projection(self.db,"owner"))["items"]
        self.assertEqual(sum(x["physical"] for x in rows),3)
        gold=await receive(self.db,user=self.f.actor,payload=self.payload())
        rows=(await projection(self.db,"owner"))["items"]
        self.assertEqual(sum(x["physical"] for x in rows),13)
        silver=await receive(self.db,user=self.f.actor,payload=self.payload("silver",100))
        rows=(await projection(self.db,"owner"))["items"]
        self.assertEqual(sum(x["physical"] for x in rows),113)
        await return_pending(self.db,user=self.f.actor,receipt_id=gold["id"],source_return_id="RETURN-G",quantity=2)
        self.assertEqual(sum(x["physical"] for x in (await projection(self.db,"owner"))["items"]),111)
        await return_pending(self.db,user=self.f.actor,receipt_id=silver["id"],source_return_id="RETURN-S",quantity=1)
        rows=(await projection(self.db,"owner"))["items"]
        self.assertEqual(sum(x["physical"] for x in rows),110)
        self.assertEqual(sum(x["financially_pending"] for x in rows),107)
        self.assertEqual(sum(x["available"] for x in rows),3)
        byloc={x["location_id"]:x for x in rows}
        self.assertEqual(byloc["gold"]["physical"],8)
        self.assertEqual(byloc["silver"]["physical"],99)
        self.assertEqual(byloc["silver"]["specifications"],{"\u0627\u0644\u0627\u0633\u0645":"\u0639\u0628\u064a\u0631"})
        self.assertIsNone(byloc["gold"]["inventory_value"])
        self.assertEqual(await self.snapshot_financial(),before)

    async def test_concurrent_intake_retry_and_return_are_once(self):
        results=await asyncio.gather(*(receive(self.db,user=self.f.actor,payload=self.payload()) for _ in range(4)),return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                self.assertIsInstance(result, HTTPException)
                self.assertEqual(result.status_code, 429)
        self.assertTrue(any(not isinstance(r, Exception) for r in results))
        results=[await receive(self.db,user=self.f.actor,payload=self.payload()) if isinstance(r, Exception) else r for r in results]
        self.assertEqual(len({r["id"] for r in results}),1)
        rid=results[0]["id"]
        returns=await asyncio.gather(*(return_pending(self.db,user=self.f.actor,receipt_id=rid,source_return_id="same-return",quantity=2) for _ in range(4)),return_exceptions=True)
        for result in returns:
            if isinstance(result, Exception):
                self.assertIsInstance(result, HTTPException)
                self.assertEqual(result.status_code, 429)
                await return_pending(self.db,user=self.f.actor,receipt_id=rid,source_return_id="same-return",quantity=2)
        self.assertEqual((await self.db.warehouse_locations.find_one({"id":"gold"}))["occupancy"]["total_quantity"],8)
        with self.assertRaises(HTTPException):await receive(self.db,user=self.f.actor,payload=self.payload(quantity=11))
        with self.assertRaises(HTTPException):await return_pending(self.db,user=self.f.actor,receipt_id=rid,source_return_id="same-return",quantity=3)

    async def test_failure_after_placement_rolls_back_then_retry_commits(self):
        from inventory_receipt_service import place_inventory_receipt
        async def crash(*a,**kw):
            await place_inventory_receipt(*a,**kw)
            raise RuntimeError("injected after occupancy before event")
        with patch("operational_physical_receipts.place_inventory_receipt",crash):
            with self.assertRaises(RuntimeError):await receive(self.db,user=self.f.actor,payload=self.payload())
        self.assertEqual(await self.db.mezan_inventory_receipts_v2.count_documents({}),0)
        self.assertEqual((await self.db.warehouse_locations.find_one({"id":"gold"}))["occupancy"]["total_quantity"],0)
        await receive(self.db,user=self.f.actor,payload=self.payload())
        self.assertEqual((await self.db.warehouse_locations.find_one({"id":"gold"}))["occupancy"]["total_quantity"],10)

    async def test_component_without_sku_pending_and_return(self):
        payload=self.payload();payload.update(source_line_id="component",line={"item_type":"STOCK_COMPONENT","resource_id":"component-A","category_id":"metal","quantity":10},location_id="location-component",scanned_location_barcode="LOC-C")
        before=await self.snapshot_financial()
        receipt=await receive(self.db,user=self.f.actor,payload=payload)
        await return_pending(self.db,user=self.f.actor,receipt_id=receipt["id"],source_return_id="component-return",quantity=2)
        rows=(await projection(self.db,"owner"))["items"]
        row=next(r for r in rows if r["resource_id"]=="component-A")
        self.assertEqual((row["physical"],row["available"],row["financially_pending"]),(8,0,8))
        self.assertEqual(await self.snapshot_financial(),before)

    async def test_forged_pending_cannot_bypass_opening_guard(self):
        await self.db.warehouse_locations.update_one({"id":"gold"},{"$set":{"occupancy":{"items":[{"product_id":"product-A","quantity":10,"valuation_status":"PENDING_FINANCIAL_VALUATION"}],"total_quantity":10}}})
        with self.assertRaises(HTTPException):await assert_opening_inventory_initialized(self.db,"owner",{})


    async def test_http_permission_enforcement_and_server_forced_pending(self):
        import httpx
        from fastapi import FastAPI
        from product_inventory_receipt_routes import make_product_inventory_receipt_router
        actor=dict(self.f.actor)
        async def current():return actor
        app=FastAPI();app.include_router(make_product_inventory_receipt_router(self.db,current),prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://local.test") as client:
            payload=self.payload()
            response=await client.post("/api/inventory-v2/physical-receipts",json={**payload,"valuation_status":"approved"})
            self.assertEqual(response.status_code,422)
            response=await client.post("/api/inventory-v2/physical-receipts",json=payload)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()["valuation_status"],"PENDING_FINANCIAL_VALUATION")
            actor.clear();actor.update({"id":"outsider","role":"staff"})
            await self.db.users.insert_one({"id":"outsider","role":"staff","is_active":True})
            denied=await client.post("/api/inventory-v2/physical-receipts",json=self.payload("silver",100))
            self.assertIn(denied.status_code,(403,409))
            self.assertEqual((await self.db.warehouse_locations.find_one({"id":"silver"}))["occupancy"]["total_quantity"],0)

    async def test_operational_profile_poison_rejects_financial_write_and_rolls_back(self):
        from operational_atomic import operational_owner
        async def attempt(db):
            await db.warehouse_location_events.insert_one({"_id":"attempt","id":"attempt","user_id":"owner","event_type":"physical_receipt","receipt_id":"fake","quantity":1,"occurred_at":"now","recorded_by":"owner"})
            try:await db.mz2_inventory_cost_states.insert_one({"user_id":"owner","average_cost":"0","authoritative":True})
            except HTTPException:pass
        with self.assertRaises(HTTPException):await operational_owner(self.db,"owner",attempt,profile="physical_receipt")
        self.assertEqual(await self.db.warehouse_location_events.count_documents({"id":"attempt"}),0)
        self.assertEqual(await self.db.mz2_inventory_cost_states.count_documents({}),0)


    async def test_return_rechecks_warehouse_assignment(self):
        receipt=await receive(self.db,user=self.f.actor,payload=self.payload())
        with patch("fulfillment_v2_routes._warehouse_allowed",return_value=False):
            with self.assertRaises(HTTPException) as error:
                await return_pending(self.db,user=self.f.actor,receipt_id=receipt["id"],source_return_id="denied",quantity=1)
        self.assertEqual(error.exception.status_code,403)
        self.assertEqual((await self.db.warehouse_locations.find_one({"id":"gold"}))["occupancy"]["total_quantity"],10)
