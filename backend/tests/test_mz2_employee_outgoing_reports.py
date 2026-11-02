"""Real native producers must remain consumable by classified V2 reports."""
import unittest
import os
from copy import deepcopy
from decimal import Decimal
from motor.motor_asyncio import AsyncIOMotorClient

import test_mz2_employee_finance as payroll_fixture
import test_mz2_daily_movements as daily_fixture
from accounting_employee_finance import PayrollAccrualIn, accrue_payroll_period
from accounting_mz2_reports import mz2_financial_position, mz2_trial_balance
from employee_outgoing_native_fixture import NoLegacyFinancial


class NoLegacyReports(NoLegacyFinancial):
    def started(self, event):
        super().started(event)
        collection = event.command.get(event.command_name)
        if isinstance(collection, str) and collection in {"operating_salaries", "financial_provider_apps_legacy"}:
            self.accesses.append((event.command_name, collection))


async def reports(test):
    position = await mz2_financial_position(test.db, owner="owner")
    trial = await mz2_trial_balance(test.db, owner="owner")
    test.assertEqual(position["status"], "available", position)
    test.assertEqual(trial["status"], "available", trial)
    test.assertEqual(sum(Decimal(str(row["net"])) for row in trial["items"]), Decimal(0))
    return position, {(row["entity_type"], row["entity_id"], row.get("sub_account") or ""): Decimal(str(row["net"]))
                      for row in trial["items"]}


class PayrollReportTests(unittest.IsolatedAsyncioTestCase):
    # Keep the actual producer fixture's historical alias: financial legs use
    # employee.id, and report readiness must verify that same native identity.
    asyncSetUp = payroll_fixture.MZ2EmployeeFinanceTests.asyncSetUp
    asyncTearDown = payroll_fixture.MZ2EmployeeFinanceTests.asyncTearDown
    _open_and_activate = payroll_fixture.MZ2EmployeeFinanceTests._open_and_activate
    tx = payroll_fixture.MZ2EmployeeFinanceTests.tx
    import_movement = payroll_fixture.MZ2EmployeeFinanceTests.import_movement
    classify = payroll_fixture.MZ2EmployeeFinanceTests.classify

    async def test_accrual_salary_advance_and_custody_remain_reportable(self):
        await self.tx(lambda scoped: accrue_payroll_period(scoped, owner=self.owner, actor=self.actor,
            payload=PayrollAccrualIn(period="2026-09", accrued_at="2026-09-21T00:30:00+03:00",
                employee_id=self.employee, reason="Synthetic report payroll")))
        for index, (action, amount, direction) in enumerate([
            ("salary_payment", 2500, "out"), ("advance_grant", 100, "out"),
            ("advance_repayment", 40, "in"), ("custody_grant", 80, "out"),
            ("custody_return", 30, "in"),
        ]):
            movement = await self.import_movement(credit=amount if direction == "in" else 0,
                debit=amount if direction == "out" else 0, description=action, reference=f"REPORT-{index}")
            await self.classify(movement["id"], action)
            await reports(self)
        position, balances = await reports(self)
        self.assertEqual(Decimal(str(position["assets"]["banks"])), Decimal("7390"))
        self.assertEqual(Decimal(str(position["assets"]["employee_advance"])), Decimal("60"))
        self.assertEqual(Decimal(str(position["assets"]["employee_custody"])), Decimal("50"))
        self.assertEqual(Decimal(str(position["liabilities"]["salaries_unpaid"])), Decimal("266.67"))
        self.assertEqual(balances[("expense", "salary", "")], Decimal("266.67"))

    async def test_reports_use_native_id_without_alias_and_never_access_legacy(self):
        original_db = self.db
        listener = NoLegacyReports()
        client = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], event_listeners=[listener])
        self.db = client[original_db.name]
        try:
            for mapping in (None, "unconfirmed-alias"):
                with self.subTest(financial_entity_id=mapping):
                    await self.db.mezan_employees_v2.update_one({"user_id": self.owner, "id": self.employee},
                        {"$set": {"financial_entity_id": mapping}})
                    before = {name: await original_db[name].find({}).to_list(None)
                              for name in await original_db.list_collection_names()}
                    position, balances = await reports(self)
                    self.assertEqual(position["totals"], {
                        "total_assets": 10500.0, "total_liabilities": 3000.0, "net_position": 7500.0})
                    self.assertEqual(balances[("employee", self.employee, "salary_payable")], Decimal("-3000"))
                    self.assertEqual(balances[("employee", self.employee, "advance")], Decimal("500"))
                    self.assertEqual(balances[("employee", self.employee, "custody")], Decimal("0"))
                    self.assertFalse(position["legacy_financial_data_included"])
                    self.assertEqual(listener.accesses, [])
                    self.assertEqual({name: await original_db[name].find({}).to_list(None)
                                      for name in await original_db.list_collection_names()}, before)
        finally:
            self.db = original_db
            client.close()

    async def test_reports_block_missing_foreign_archived_or_alias_only_native_employee(self):
        original = await self.db.mezan_employees_v2.find_one({"user_id": self.owner, "id": self.employee})
        for invalid in (None, {"user_id": "other-owner"}, {"archived": True},
                        {"id": "different-native-id", "financial_entity_id": self.employee}):
            with self.subTest(invalid=invalid):
                await self.db.mezan_employees_v2.replace_one({"_id": original["_id"]}, deepcopy(original), upsert=True)
                if invalid is None:
                    await self.db.mezan_employees_v2.delete_one({"_id": original["_id"]})
                else:
                    await self.db.mezan_employees_v2.update_one({"_id": original["_id"]}, {"$set": invalid})
                for reader in (mz2_financial_position, mz2_trial_balance):
                    result = await reader(self.db, owner=self.owner)
                    self.assertEqual(result["status"], "not_ready", result)
                    self.assertEqual(result["reason"], "unresolved_mz2_identity")
                    self.assertTrue(result["readiness_blockers"])
                    self.assertEqual({row["entity_id"] for row in result["readiness_blockers"]}, {self.employee})
                    self.assertEqual({row["reason"] for row in result["readiness_blockers"]}, {"native_identity_missing"})
                    if reader is mz2_financial_position:
                        self.assertIsNone(result["totals"])
                    else:
                        self.assertEqual(result["items"], [])


class DailyOutgoingReportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = daily_fixture.DailyMovementTests.asyncSetUp
    asyncTearDown = daily_fixture.DailyMovementTests.asyncTearDown
    tx = daily_fixture.DailyMovementTests.tx
    import_file = daily_fixture.DailyMovementTests.import_file
    activate_outgoing_opening = daily_fixture.DailyMovementTests.activate_outgoing_opening
    classify_outgoing = daily_fixture.DailyMovementTests.classify_outgoing

    async def movement(self, amount, reference):
        return (await self.import_file(daily_fixture.workbook_bytes([
            ["2026-09-21", 0, amount, "Report proof", reference, ""],
        ]), reference + ".xlsx"))["items"][0]

    async def test_daily_rent_expense_remains_reportable(self):
        await self.activate_outgoing_opening()
        row = await self.movement(1200, "REPORT-RENT")
        await self.classify_outgoing(row["id"], action="expense", expense_category="rent", reason="Actual rent movement")
        position, balances = await reports(self)
        self.assertEqual(Decimal(str(position["assets"]["banks"])), Decimal("8800"))
        self.assertEqual(balances[("expense", "rent", "")], Decimal("1200"))

    async def test_daily_supplier_payment_remains_reportable_without_advance(self):
        await self.activate_outgoing_opening(supplier_payable="800")
        row = await self.movement(200, "REPORT-SUPPLIER")
        await self.classify_outgoing(row["id"], action="supplier_payment", supplier_id="supplier-1", reason="Actual supplier movement")
        position, balances = await reports(self)
        self.assertEqual(Decimal(str(position["assets"]["banks"])), Decimal("9800"))
        self.assertEqual(Decimal(str(position["liabilities"]["supplier_payable"])), Decimal("600"))
        self.assertEqual(balances.get(("supplier", "supplier-1", "advance"), Decimal(0)), Decimal(0))

    async def test_existing_daily_expense_categories_remain_reportable(self):
        from accounting_daily_movements import GENERAL_EXPENSE_CATEGORIES
        await self.activate_outgoing_opening()
        await self.db.expense_categories.insert_one({"user_id": "owner", "code": "approved_custom",
            "name": "Owner-approved expense", "status": "active"})
        categories = list(GENERAL_EXPENSE_CATEGORIES) + ["approved_custom"]
        for index, category in enumerate(categories):
            row = await self.movement(10, f"REPORT-EXPENSE-{index}")
            await self.classify_outgoing(row["id"], action="expense", expense_category=category,
                reason="Existing approved expense classification")
        position, balances = await reports(self)
        self.assertEqual(Decimal(str(position["assets"]["banks"])), Decimal(10000 - 10 * len(categories)))
        for category in categories:
            self.assertEqual(balances[("expense", category, "")], Decimal("10"))
