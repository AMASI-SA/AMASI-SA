"""Real-Mongo contract for MZ2-native salary, advance and custody flows."""
import io
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook

from accounting_atomic import atomic_owner
from accounting_daily_movements import import_daily_movement_file, parse_daily_movement_xlsx
from accounting_employee_finance import (
    MovementClassifyIn,
    PayrollAccrualIn,
    accrue_payroll_period,
    classify_employee_movement,
)
from accounting_module_opening_balances import (
    OpeningActivateIn,
    OpeningApproveIn,
    OpeningLineIn,
    OpeningPreviewIn,
    activate_p01,
    approve_opening_preview,
    create_opening_preview,
)
from accounting_mz2_reports import read_mz2_ledger
from accounting_periods import PeriodChange, set_period


def bank_xlsx(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["تاريخ الحركة", "إيداع", "سحب", "البيان", "رقم المرجع", "المنصة"])
    for row in rows:
        ws.append(row)
    stream = io.BytesIO()
    wb.save(stream)
    wb.close()
    return stream.getvalue()


class MZ2EmployeeFinanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_employee_finance_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.owner = "owner"
        self.employee = "employee-1"
        self.actor = {
            "id": self.owner,
            "role": "owner",
            "name": "Synthetic owner",
            "email": "owner@example.invalid",
        }
        await self.db.users.insert_one({**self.actor, "is_active": True})
        await self.db.settings.insert_one({"user_id": self.owner})
        await self.db.accounts.insert_one({
            "id": "bank-main",
            "user_id": self.owner,
            "name": "Synthetic bank",
            "account_type": "bank",
            "status": "active",
        })
        await self.db.operating_salaries.insert_one({
            "id": self.employee,
            "user_id": self.owner,
            "name": "Synthetic employee",
            "category": "employee",
            "monthly_amount": 4000,
            "status": "active",
        })
        await self._open_and_activate()

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def tx(self, callback):
        return await atomic_owner(self.db, self.owner, callback)

    async def _open_and_activate(self):
        refs = {
            "banks_cash": "SYN-BANKS",
            "providers": "SYN-PROVIDERS-ZERO",
            "couriers_cod": "SYN-COURIERS-ZERO",
            "inventory": "SYN-INVENTORY-ZERO",
            "suppliers": "SYN-SUPPLIERS-ZERO",
            "payroll_obligations": "SYN-PAYROLL",
            "equity": "SYN-EQUITY",
        }
        payload = OpeningPreviewIn(
            cutover_at="2026-09-20T00:00:00+03:00",
            evidence_sheet_ref="SYN-OPENING",
            evidence_sections=refs,
            lines=[
                OpeningLineIn(category="bank", entity_id="bank-main", amount="10000"),
                OpeningLineIn(category="employee_salary_payable", entity_id=self.employee, amount="3000"),
                OpeningLineIn(category="employee_advance", entity_id=self.employee, amount="500"),
            ],
        )
        preview = await self.tx(lambda scoped: create_opening_preview(
            scoped, owner=self.owner, actor=self.actor, payload=payload,
        ))
        self.assertIn(
            ("employee", self.employee, "custody"),
            {
                (row["entity_type"], row["entity_id"], row["sub_account"])
                for row in preview["zero_scope"]
            },
        )
        await self.tx(lambda scoped: approve_opening_preview(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=OpeningApproveIn(
                preview_id=preview["id"],
                confirmation="APPROVE_OPENING_BALANCE",
            ),
        ))
        await self.tx(lambda scoped: activate_p01(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=OpeningActivateIn(
                activation_ref="SYN-UAT",
                confirmation="ACTIVATE_MZ2_P01",
            ),
        ))

    async def import_movement(self, *, credit=0, debit=0, description, reference):
        content = bank_xlsx([
            ["2026-09-21", credit, debit, description, reference, ""],
        ])
        parsed = parse_daily_movement_xlsx(content)
        self.assertEqual(parsed["errors"], [])
        result = await self.tx(lambda scoped: import_daily_movement_file(
            scoped,
            owner=self.owner,
            actor=self.actor,
            bank_account_id="bank-main",
            filename=reference + ".xlsx",
            content=content,
            parsed=parsed,
        ))
        self.assertEqual(len(result["items"]), 1)
        return result["items"][0]

    async def balances(self):
        scope = await read_mz2_ledger(self.db, owner=self.owner)
        self.assertEqual(scope["status"], "available", scope)
        nets = {}
        for row in scope["items"]:
            key = (row["entity_type"], row["entity_id"], row.get("sub_account") or "")
            nets[key] = nets.get(key, 0.0) + (row["amount"] if row["side"] == "debit" else -row["amount"])
        return nets

    async def classify(self, movement_id, action, *, apply=True, reason="SYN verified"):
        payload = MovementClassifyIn(
            employee_id=self.employee,
            action=action,
            apply_open_advances=apply,
            reason=reason,
        )
        return await self.tx(lambda scoped: classify_employee_movement(
            scoped,
            owner=self.owner,
            actor=self.actor,
            movement_id=movement_id,
            payload=payload,
        ))

    async def test_net_salary_payment_consumes_bank_movement_and_advance_once(self):
        movement = await self.import_movement(
            debit=2500,
            description="Salary payment",
            reference="SAL-001",
        )
        before_legs = await self.db.general_ledger.count_documents({})
        result = await self.classify(movement["id"], "salary_payment")
        self.assertEqual(result["cash_amount"], "2500.00")
        self.assertEqual(result["advance_offset"], "500.00")
        self.assertEqual(result["salary_settled"], "3000.00")

        nets = await self.balances()
        self.assertEqual(round(nets[("bank", "bank-main", "main")], 2), 7500.0)
        self.assertEqual(round(nets[("employee", self.employee, "salary_payable")], 2), 0.0)
        self.assertEqual(round(nets[("employee", self.employee, "advance")], 2), 0.0)

        after_first = await self.db.general_ledger.count_documents({})
        self.assertEqual(after_first - before_legs, 3)
        again = await self.classify(movement["id"], "salary_payment")
        self.assertEqual(again["state"], "already_posted")
        self.assertEqual(again["txn_group_id"], result["txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), after_first)

        stored = await self.db.mz2_daily_movements.find_one({"id": movement["id"]})
        self.assertEqual(stored["status"], "accounting_posted")
        self.assertEqual(stored["accounting_action"], "salary_payment")

    async def test_advance_and_custody_cash_flows_follow_actual_bank_rows(self):
        salary = await self.import_movement(
            debit=2500, description="Salary", reference="SAL-FIRST",
        )
        await self.classify(salary["id"], "salary_payment")

        advance_out = await self.import_movement(
            debit=400, description="Employee advance", reference="ADV-OUT",
        )
        await self.classify(advance_out["id"], "advance_grant")
        advance_in = await self.import_movement(
            credit=150, description="Employee returned advance", reference="ADV-IN",
        )
        await self.classify(advance_in["id"], "advance_repayment")

        custody_out = await self.import_movement(
            debit=300, description="Employee custody", reference="CUS-OUT",
        )
        await self.classify(custody_out["id"], "custody_grant")
        custody_in = await self.import_movement(
            credit=100, description="Employee custody return", reference="CUS-IN",
        )
        await self.classify(custody_in["id"], "custody_return")

        nets = await self.balances()
        self.assertEqual(round(nets[("employee", self.employee, "advance")], 2), 250.0)
        self.assertEqual(round(nets[("employee", self.employee, "custody")], 2), 200.0)
        # 10,000 - 2,500 - 400 + 150 - 300 + 100
        self.assertEqual(round(nets[("bank", "bank-main", "main")], 2), 7050.0)

    async def test_salary_accrual_is_period_idempotent_and_reportable(self):
        payload = PayrollAccrualIn(
            period="2026-09",
            accrued_at="2026-09-21T00:30:00+03:00",
            employee_id=self.employee,
            reason="SYN September payroll",
        )
        first = await self.tx(lambda scoped: accrue_payroll_period(
            scoped, owner=self.owner, actor=self.actor, payload=payload,
        ))
        self.assertEqual(first["posted"], 1)
        self.assertEqual(first["items"][0]["amount"], "4000.00")
        before = await self.db.general_ledger.count_documents({})

        second = await self.tx(lambda scoped: accrue_payroll_period(
            scoped, owner=self.owner, actor=self.actor, payload=payload,
        ))
        self.assertEqual(second["posted"], 0)
        self.assertEqual(second["already_posted"], 1)
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)

        nets = await self.balances()
        # Opening payable 3000 + September accrual 4000.
        self.assertEqual(round(nets[("employee", self.employee, "salary_payable")], 2), -7000.0)
        self.assertEqual(round(nets[("expense", "salary", "")], 2), 4000.0)

    async def test_salary_cash_more_than_payable_fails_without_consuming_movement(self):
        movement = await self.import_movement(
            debit=3500,
            description="Too large salary payment",
            reference="SAL-TOO-HIGH",
        )
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as ctx:
            await self.classify(movement["id"], "salary_payment", apply=False)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "salary_payment_exceeds_payable")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        stored = await self.db.mz2_daily_movements.find_one({"id": movement["id"]})
        self.assertEqual(stored["status"], "unclassified")
        self.assertFalse(stored.get("accounting_event_id"))

    async def test_closed_period_rolls_back_employee_posting_and_movement_consumption(self):
        movement = await self.import_movement(
            debit=2500, description="Closed month salary", reference="SAL-CLOSED",
        )
        await set_period(
            self.db,
            self.owner,
            self.owner,
            PeriodChange(
                month="2026-09",
                closed=True,
                revision=0,
                reason="SYN close",
                evidence_ref="SYN close approval",
            ),
        )
        before_legs = await self.db.general_ledger.count_documents({})
        before_events = await self.db.mz2_employee_financial_events.count_documents({})
        with self.assertRaises(HTTPException) as ctx:
            await self.classify(movement["id"], "salary_payment")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "accounting_period_closed")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before_legs)
        self.assertEqual(await self.db.mz2_employee_financial_events.count_documents({}), before_events)
        stored = await self.db.mz2_daily_movements.find_one({"id": movement["id"]})
        self.assertEqual(stored["status"], "unclassified")
        self.assertFalse(stored.get("accounting_event_id"))

    async def test_provider_like_movement_cannot_be_reclassified_as_employee_cash(self):
        content = bank_xlsx([
            ["2026-09-21", 100, 0, "Tabby settlement", "TBY-NOT-EMP", ""],
        ])
        parsed = parse_daily_movement_xlsx(content)
        result = await self.tx(lambda scoped: import_daily_movement_file(
            scoped,
            owner=self.owner,
            actor=self.actor,
            bank_account_id="bank-main",
            filename="provider-suggestion.xlsx",
            content=content,
            parsed=parsed,
        ))
        row = result["items"][0]
        self.assertEqual(row["status"], "unclassified")
        # The suggestion is discarded when the provider is not bound to this
        # bank, but the explicit text remains bank evidence; employee workflow
        # only sees no provider marker. Add an explicit provider marker to prove
        # the hard boundary instead.
        await self.db.mz2_daily_movements.update_one(
            {"id": row["id"]},
            {"$set": {"explicit_provider": "tabby"}},
        )
        with self.assertRaises(HTTPException) as ctx:
            await self.classify(row["id"], "advance_repayment")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "provider_movement_cannot_be_employee_cash")


if __name__ == "__main__":
    unittest.main()
