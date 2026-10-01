"""Current carrier intake and fee review on isolated real Mongo transactions.

No production fallback or transaction mock. The existing G47 CI runs this file
against loopback replica/standalone fixtures and rejects skipped cases.
"""
import asyncio
import os
import unittest
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_atomic import atomic_owner
from accounting_shipping_p02 import ShippingAccountingError, prepare_courier_fee
from operational_atomic import operational_owner
from orders_db import upsert_order
from order_engine.mapper import map_salla_order
from order_engine.repository import MongoOrderRepository
from salla_integration.sync import _salla_order_to_doc


class CurrentShippingTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client["shipping_current_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.owner = "SYN-OWNER"
        await self.db.mz2_atomic_owners.insert_one({"_id": self.owner, "revision": 0, "writes_paused": False})
        await self.db.unified_orders.create_index([("user_id", 1), ("order_number", 1)], unique=True)

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            await self.client.drop_database(self.db.name)
            self.client.close()

    def payload(self, company="iMile", version="2026-10-01T09:00:00Z"):
        return {"id": "9001", "reference_id": "3001", "date": "2026-10-01T08:00:00Z", "updated_at": version,
                "shipping": {"company_name": company}, "items": []}

    async def ingest(self, payload, db=None):
        return await upsert_order(db if db is not None else self.db, self.owner, "3001", _salla_order_to_doc(payload), "salla_direct", raw=payload)

    async def assert_no_financial_events(self):
        for name in ("general_ledger", "mz2_shipping_accounting_events", "mz2_ledger_entries", "liabilities"):
            self.assertEqual(await self.db[name].count_documents({}), 0, name)

    async def test_carrier_update_remains_operational_when_finance_is_paused(self):
        await self.ingest(self.payload())
        await self.db.mz2_atomic_owners.update_one({"_id": self.owner}, {"$set": {"writes_paused": True}})
        await self.db.unified_orders.update_one({"user_id": self.owner}, {"$set": {"last_make_update_at": "earlier"}})
        await self.ingest(self.payload("مندوب الرياض", "2026-10-01T10:00:00Z"))
        raw = await MongoOrderRepository(self.db).get_salla_order(user_id=self.owner, order_number="3001")
        self.assertEqual(map_salla_order(raw.salla_raw).shipping.company, "مندوب الرياض")
        self.assertTrue((await self.db.mz2_atomic_owners.find_one({"_id": self.owner}))["writes_paused"])
        await self.assert_no_financial_events()

    async def test_concurrent_first_intake_keeps_latest_carrier(self):
        await asyncio.gather(self.ingest(self.payload()), self.ingest(self.payload("مندوب الرياض", "2026-10-01T10:00:00Z")))
        row = await self.db.unified_orders.find_one({"user_id": self.owner, "order_number": "3001"})
        self.assertEqual(row["shipping_company"], "مندوب الرياض")
        self.assertEqual(await self.db.unified_orders.count_documents({"user_id": self.owner}), 1)
        await self.assert_no_financial_events()

    async def test_fee_preparation_waits_for_current_carrier_commit_then_reviews_old_import(self):
        await self.ingest(self.payload())
        evidence = {"id": "SYN-EVIDENCE", "user_id": self.owner, "order_number": "3001", "conflict": False,
                    "delivery_source_text": "2026-09-21 10:00:00", "shipping_company": "iMile للتوصيل",
                    "waybill": "SYN-AWB", "shipping_cost_source": "24.07"}
        await self.db.mz2_salla_order_evidence.insert_one(evidence)
        await self.db.mz2_shipping_rate_policies.insert_one({"_id": self.owner, "user_id": self.owner, "revision": 1,
            "versions": [{"id": "SYN-RATE", "courier_id": "imile", "name": "iMile", "aliases_normalized": ["imile", "imile للتوصيل"],
                "effective_at": "2026-09-01T00:00:00+00:00", "revision": 1, "verification_status": "approved", "total_fee": "17.25",
                "evidence_ref": "SYN-RATE-EVIDENCE", "tax_treatment": "gross_expense_no_input_vat"}], "audit": []})
        persisted, release = asyncio.Event(), asyncio.Event()
        async def intake(scoped):
            await self.ingest(self.payload("مندوب الرياض", "2026-10-01T10:00:00Z"), db=scoped)
            persisted.set()
            await asyncio.wait_for(release.wait(), 2)
        carrier_task = asyncio.create_task(operational_owner(self.db, self.owner, intake))
        await asyncio.wait_for(persisted.wait(), 2)
        async def fee(scoped):
            return await prepare_courier_fee(scoped, owner=self.owner, evidence_id="SYN-EVIDENCE")
        fee_task = asyncio.create_task(atomic_owner(self.db, self.owner, fee))
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(fee_task), 0.15)
        finally:
            release.set()
            await carrier_task
        with self.assertRaisesRegex(ShippingAccountingError, "shipping_current_carrier_conflict_review_required"):
            await fee_task
        self.assertEqual((await self.db.mz2_salla_order_evidence.find_one({"id": "SYN-EVIDENCE"}))["shipping_company"], "iMile للتوصيل")
        await self.assert_no_financial_events()

    async def test_standalone_rejects_current_carrier_write_without_partial_order(self):
        uri = os.environ.get("MZ2_TEST_STANDALONE_URI")
        if not uri:
            self.skipTest("MZ2_TEST_STANDALONE_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        database = client["shipping_standalone_" + uuid4().hex]
        try:
            with self.assertRaises(HTTPException) as caught:
                await self.ingest(self.payload(), db=database)
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(await database.unified_orders.count_documents({}), 0)
        finally:
            await client.drop_database(database.name)
            client.close()
