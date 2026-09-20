"""Real replica: closed periods reject atomically and serialize with writes."""
import asyncio
import unittest
from fastapi import APIRouter
from accounting_atomic import atomic_owner
from accounting_periods import install_period_routes, set_period, PeriodChange
from ledger_core import post_txn_group
import test_mz2_daily_refunds as daily

class ClosedPeriodTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = daily.DailyRefundTests.asyncTearDown
    configure = daily.DailyRefundTests.configure
    source = daily.DailyRefundTests.source
    payload = daily.DailyRefundTests.payload
    preview_and_post = daily.DailyRefundTests.preview_and_post
    post = daily.DailyRefundTests.post
    setup_sale = daily.DailyRefundTests.setup_sale
    bank = daily.DailyRefundTests.bank
    case = daily.DailyRefundTests.case
    confirm = daily.DailyRefundTests.confirm

    async def asyncSetUp(self):
        await daily.DailyRefundTests.asyncSetUp(self)
        router = APIRouter()
        async def actor(): return {"id": self.actor}
        install_period_routes(router, self.db, actor)
        self.app.include_router(router)

    async def change(self, month, closed=True, revision=0):
        return await self.client.put("/accounting-module/periods", json=dict(
            month=month, closed=closed, revision=revision, reason="SYN close", evidence_ref="SYN approved close"))

    async def snapshot(self):
        # Compare all persisted collections, including sequence and audit rows.
        names = sorted(await self.db.list_collection_names())
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(None) for name in names}

    async def test_closed_entitlement_and_payment_no_partial_or_automatic_redating(self):
        await self.bank(); key = await self.setup_sale(gross="115")
        row = await self.case(key, "SYN-CLOSED", "115")
        self.assertEqual((await self.change("2020-01")).status_code, 200)
        before = await self.snapshot()
        denied = await self.client.post(daily.BASE+"/"+row["id"]+"/recognize", json=dict(
            amount="115", recognized_at="2020-01-31T23:30:00+03:00", reason="SYN right", evidence_ref="SYN NOTE"))
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertIn("accounting_period_closed", denied.text)
        self.assertEqual(await self.snapshot(), before)
        confirmed = await self.confirm(row, "2020-02-01T12:00:00+03:00")
        self.assertEqual(confirmed["remaining"], "115.00")
        payment = await self.post("/bank-payments", dict(original_key=key, case_reference=row["case_reference"], amount="40",
            paid_at="2020-02-02T10:00:00+03:00", bank_account_id="bank", bank_reference="SYN FEB", execution_channel="bank"))
        self.assertEqual((await self.change("2020-02")).status_code, 200)
        before = await self.snapshot()
        denied = await self.client.post(daily.BASE+"/bank-payments/"+payment["id"]+"/approve")
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertIn("accounting_period_closed", denied.text)
        self.assertEqual(await self.snapshot(), before)
        periods = (await self.client.get("/accounting-module/periods")).json()["items"]
        self.assertTrue(all(p["closed"] for p in periods))

    async def test_owner_only_close_reopen_and_stale_revision(self):
        self.actor = "viewer"
        for closed in (True, False):
            denied = await self.change("2020-01", closed)
            self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(await self.db.mz2_accounting_periods.count_documents({}), 0)
        self.actor = "owner"
        self.assertEqual((await self.change("2020-01")).status_code, 200)
        before = await self.snapshot()
        self.assertEqual((await self.change("2020-01", False, 0)).status_code, 409)
        self.assertEqual(await self.snapshot(), before)
        reopened = await self.change("2020-01", False, 1)
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertFalse(reopened.json()["closed"])
        self.assertEqual(reopened.json()["revision"], 2)

    async def test_close_waits_for_inflight_balanced_group(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def operation(scoped):
            entered.set()
            await release.wait()
            return await post_txn_group(scoped, user_id="owner", actor_id="owner", actor_name="SYN",
                entries=[dict(entity_type="bank", entity_id="SYN", sub_account="main", side="debit", amount=10, entry_type="bank_transfer"),
                         dict(entity_type="equity", entity_id="SYN", side="credit", amount=10, entry_type="bank_transfer")],
                txn_type="bank_transfer", metadata={"accounting_at": "2020-01-05T12:00:00Z"})
        pending = asyncio.create_task(atomic_owner(self.db, "owner", operation))
        await asyncio.wait_for(entered.wait(), 5)
        closing = asyncio.create_task(set_period(self.db, "owner", "owner", PeriodChange(
            month="2020-01", closed=True, revision=0, reason="SYN", evidence_ref="SYN")))
        await asyncio.sleep(0.1)
        self.assertFalse(closing.done())
        release.set()
        group, closed = await asyncio.wait_for(asyncio.gather(pending, closing), 20)
        self.assertTrue(closed["closed"])
        legs = await self.db.general_ledger.find({"txn_group_id": group["txn_group_id"]}).to_list(None)
        self.assertEqual(len(legs), 2)
        self.assertEqual(sum(r["amount"] if r["side"] == "debit" else -r["amount"] for r in legs), 0)
