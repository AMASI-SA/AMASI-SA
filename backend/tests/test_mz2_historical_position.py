"""Real Mongo report regression using approved refund and payment routes."""
import unittest
import test_mz2_daily_refunds as daily
from financial_position_ssot import compute_financial_position

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
        # Fixture opening is explicitly dated; immutable accounting date differs
        # from today's audit timestamp just as historical migrated openings do.
        await self.db.general_ledger.update_many({}, {"$set": {"metadata.accounting_at": "2020-01-01T00:00:00+03:00"}})
        key = await self.setup_sale(gross="115")
        row = await self.case(key, "SYN-HISTORICAL", "115")
        await self.confirm(row, "2020-08-31T23:30:00+03:00")
        async def check(day, liability, bank):
            report = await compute_financial_position(self.db, "owner", as_of=day)
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
        other = await compute_financial_position(self.db, "other", as_of="2020-08-31")
        self.assertEqual(other["totals"]["total_liabilities"], 0)

    async def test_later_reversal_does_not_erase_historical_liability(self):
        # Read-model regression: original immutable journal plus later reversal.
        await self.db.general_ledger.insert_many([
            {"user_id": "owner", "status": "reversed", "entity_type": "liability", "sub_account": "customer_refund_payable",
             "side": "credit", "amount": 115, "entry_type": "bnpl_refund", "metadata": {"accounting_at": "2020-08-31T23:30:00+03:00"}},
            {"user_id": "owner", "status": "posted", "entity_type": "liability", "sub_account": "customer_refund_payable",
             "side": "debit", "amount": 115, "entry_type": "reversal", "posted_at": "2020-09-02T12:00:00Z"},
        ])
        for day, expected in (("2020-08-31", 115), ("2020-09-02", 0)):
            report = await compute_financial_position(self.db, "owner", as_of=day)
            self.assertEqual(report["liabilities"]["customer_refund_payable"], expected)

    async def test_report_route_owner_delegated_and_revoked_permissions(self):
        from universal_accounting_routes import make_universal_router
        from fastapi import HTTPException
        route = next(r for r in make_universal_router(self.db).routes if r.path.endswith("/financial-position"))
        endpoint = route.endpoint
        await self.db.general_ledger.insert_one({"user_id": "owner", "status": "posted", "entity_type": "liability",
            "sub_account": "customer_refund_payable", "side": "credit", "amount": 115,
            "entry_type": "bnpl_refund", "metadata": {"accounting_at": "2020-08-31T20:30:00Z"}})
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
        self.assertEqual(isolated["liabilities"]["customer_refund_payable"], 0)
