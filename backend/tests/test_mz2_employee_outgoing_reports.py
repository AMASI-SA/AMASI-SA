"""Real native producers must remain consumable by classified V2 reports."""
import unittest
from decimal import Decimal

import test_mz2_employee_finance as payroll_fixture
import test_mz2_daily_movements as daily_fixture
from accounting_employee_finance import PayrollAccrualIn, accrue_payroll_period
from accounting_mz2_reports import mz2_financial_position, mz2_trial_balance


async def reports(test):
    position = await mz2_financial_position(test.db, owner="owner")
    trial = await mz2_trial_balance(test.db, owner="owner")
    test.assertEqual(position["status"], "available", position)
    test.assertEqual(trial["status"], "available", trial)
    test.assertEqual(sum(Decimal(str(row["net"])) for row in trial["items"]), Decimal(0))
    return position, {(row["entity_type"], row["entity_id"], row.get("sub_account") or ""): Decimal(str(row["net"]))
                      for row in trial["items"]}


class PayrollReportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await payroll_fixture.MZ2EmployeeFinanceTests.asyncSetUp(self)
        # The reused producer fixture deliberately carries a historical alias.
        # Report readiness additionally requires the confirmed onboarding FK.
        await self.db.mezan_employees_v2.update_one({"user_id": self.owner, "id": self.employee},
            {"$set": {"financial_entity_id": self.employee}})
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

    async def test_reports_keep_unconfirmed_employee_financial_identity_blocked(self):
        await self.db.mezan_employees_v2.update_one({"user_id": self.owner, "id": self.employee},
            {"$set": {"financial_entity_id": "unconfirmed-alias"}})
        for reader in (mz2_financial_position, mz2_trial_balance):
            result = await reader(self.db, owner=self.owner)
            self.assertEqual(result["status"], "not_ready")
            self.assertTrue(any(row["reason"] == "onboarding_employee_financial_identity_dependency"
                for row in result["readiness_blockers"]))


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
