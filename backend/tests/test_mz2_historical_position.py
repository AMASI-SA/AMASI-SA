"""Real Mongo report regression using approved refund and payment routes."""
import unittest
import test_mz2_daily_refunds as daily
from accounting_mz2_reports import mz2_financial_position, install_mz2_report_routes
from accounting_atomic import atomic_owner
from accounting_ledger_v2 import reverse_journal_v2

class HistoricalPositionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = daily.DailyRefundTests.asyncSetUp
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

    async def test_august_115_september_75_then_zero_and_august_unchanged(self):
        await self.bank()
        key = await self.setup_sale(gross="115")
        row = await self.case(key, "SYN-HISTORICAL", "115")
        await self.confirm(row, "2020-08-31T23:30:00+03:00")
        async def check(day, liability, bank):
            report = await mz2_financial_position(self.db, owner="owner", as_of=day)
            self.assertEqual(report["status"], "available", report)
            self.assertEqual(report["liabilities"]["customer_refund_payable"], liability)
            self.assertEqual(report["assets"]["banks"], bank)
            self.assertEqual(report["timezone"], "Asia/Riyadh")
        await check("2020-08-31", 115, 1000)
        for day, amount, due, bank in (("2020-09-02", "40", 75, 960), ("2020-09-05", "75", 0, 885)):
            payment = await self.post("/bank-payments", dict(original_key=key, case_reference=row["case_reference"], amount=amount,
                paid_at=day+"T10:00:00+03:00", bank_account_id="bank", bank_reference="SYN-"+day, execution_channel="bank"))
            await self.post("/bank-payments/"+payment["id"]+"/approve")
            await check(day, due, bank)
            await check("2020-08-31", 115, 1000)
        await check("2020-09-02", 75, 960)
        # A mutable bank balance must never leak into a historical report.
        await self.db.accounts.update_one({"id": "bank"}, {"$set": {"current_balance": 999999}})
        await check("2020-08-31", 115, 1000)
        other = await mz2_financial_position(self.db, owner="other", as_of="2020-08-31")
        self.assertEqual(other["status"], "not_ready")
        self.assertIsNone(other["liabilities"])

    async def test_later_reversal_does_not_erase_historical_liability(self):
        await self.bank()
        key = await self.setup_sale(gross="115")
        case = await self.case(key, "SYN-HISTORICAL-REVERSED", "115")
        await self.confirm(case, "2020-08-31T23:30:00+03:00")
        recognized = await self.db.mz2_customer_refunds.find_one({"id": case["id"], "user_id": "owner"})
        original = await self.db.accounting_journal_groups_v2.find_one(
            {"user_id": "owner", "txn_group_id": recognized["due_txn_group_id"]})
        async def reverse(scoped):
            return await reverse_journal_v2(scoped._db, user_id="owner", actor_id="owner",
                actor_name="Synthetic owner", original_txn_group_id=recognized["due_txn_group_id"],
                effective_at="2020-09-02T12:00:00Z", reason="Synthetic historical reversal",
                mongo_session=scoped._session)
        reversal = await atomic_owner(self.db, "owner", reverse)
        self.assertNotEqual(reversal["group"]["txn_group_id"], recognized["due_txn_group_id"])
        self.assertEqual(await self.db.accounting_journal_groups_v2.find_one({"_id": original["_id"]}), original)
        sentinel = {"user_id": "owner", "status": "posted", "entity_type": "liability",
                    "sub_account": "customer_refund_payable", "side": "credit", "amount": 999999,
                    "metadata": {"accounting_at": "2020-08-31T20:30:00Z"}}
        await self.db.general_ledger.insert_one(sentinel)
        for day, expected in (("2020-08-31", 115), ("2020-09-02", 0)):
            report = await mz2_financial_position(self.db, owner="owner", as_of=day)
            self.assertEqual(report["status"], "available", report)
            self.assertEqual(report["liabilities"]["customer_refund_payable"], expected)
        self.assertEqual(await self.db.general_ledger.find_one({"_id": sentinel["_id"]}), sentinel)

    async def test_report_route_owner_delegated_and_revoked_permissions(self):
        from fastapi import APIRouter, HTTPException, Request
        router = APIRouter()
        install_mz2_report_routes(router, self.db, lambda: None)
        route = next(r for r in router.routes if r.path.endswith("/financial-position"))
        request = Request({"type": "http", "query_string": b""})
        async def endpoint(**kwargs):
            return await route.endpoint(request=request, **kwargs)
        await self.bank()
        key = await self.setup_sale(gross="115")
        case = await self.case(key, "SYN-HISTORICAL-PERMISSIONS", "115")
        await self.confirm(case, "2020-08-31T23:30:00+03:00")
        owner = await endpoint(as_of="2020-08-31", user={"id": "owner"})
        self.assertEqual(owner["liabilities"]["customer_refund_payable"], 115)
        await self.db.users.update_one({"id": "viewer"}, {"$set": {"accounting_permissions": ["accounting.journals_reports.view"]}})
        staff = await endpoint(as_of="2020-08-31", user={"id": "viewer"})
        self.assertEqual(staff["liabilities"]["customer_refund_payable"], 115)
        await self.db.users.update_one({"id": "viewer"}, {"$set": {"accounting_permissions": ["accounting.settlements.view"]}})
        with self.assertRaises(HTTPException) as denied:
            await endpoint(as_of="2020-08-31", user={"id": "viewer", "role": "owner"})
        self.assertEqual(denied.exception.status_code, 403)
        await self.db.users.update_one({"id": "viewer"}, {"$set": {"accounting_permissions": ["accounting.journals_reports.view"]}, "$unset": {"created_by": ""}})
        with self.assertRaises(HTTPException) as unlinked:
            await endpoint(as_of="2020-08-31", user={"id": "viewer"})
        self.assertEqual(unlinked.exception.status_code, 403)
        isolated = await endpoint(as_of="2020-08-31", user={"id": "other"})
        self.assertEqual(isolated["status"], "not_ready")
        self.assertIsNone(isolated["liabilities"])
