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
        # This successful-write fixture has explicit owner write-control state.
        await self.db.mz2_atomic_owners.insert_one({
            "_id": "owner", "revision": 0, "writes_paused": False, "control_revision": 0,
        })
        self.owner = "owner"
        self.employee = "employee-v2-1"
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
        await self.db.mezan_employees_v2.insert_one({
            "id": "employee-v2-1",
            "user_id": self.owner,
            "display_name": "Synthetic employee",
            "status": "active",
            "hire_date": "2026-09-01",
            "legacy_employee_id": "legacy-person-1",
            "financial_entity_id": "legacy-financial-1",
        })
        await self.db.mezan_employee_salary_contracts_v2.insert_one({
            "id": "contract-v2-1",
            "user_id": self.owner,
            "employee_id": "employee-v2-1",
            "legacy_salary_id": "employee-1",
            "contract_type": "monthly",
            "monthly_amount": 4000,
            "currency": "SAR",
            "effective_from": "2026-09-01",
            "effective_to": None,
            "status": "active",
            "payroll_state": "active",
            "suspension_periods": [],
            "source_authority": "mezan_employee_salary_contracts_v2",
            "version": 1,
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

    async def classify(self, movement_id, action, *, apply=True, reason="SYN verified", employee_id=None):
        payload = MovementClassifyIn(
            employee_id=employee_id or self.employee,
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

    async def test_legacy_alias_resolves_to_v2_canonical_identity(self):
        from accounting_employee_finance import _employee
        from employee_payroll_status import find_employee_salary
        for alias in (self.employee, "employee-1", "legacy-person-1", "legacy-financial-1", "contract-v2-1"):
            resolved = await _employee(self.db, self.owner, alias)
            self.assertEqual(resolved["canonical_id"], self.employee)
            row = await find_employee_salary(self.db, self.owner, alias)
            self.assertEqual(row["id"], row["employee_v2_id"])
            self.assertEqual(row["employee_v2_id"], self.employee)
            self.assertEqual(row["contract_id"], "contract-v2-1")

    async def test_all_employee_financial_legs_and_events_are_v2_canonical(self):
        from accounting_employee_finance import payroll_context
        # Single and bulk accrual both use V2, even when lookup is historical.
        for lookup in ("legacy-person-1", None):
            await self.tx(lambda tx: accrue_payroll_period(tx, owner=self.owner, actor=self.actor,
                payload=PayrollAccrualIn(period="2026-09", accrued_at="2026-09-21T00:30:00+03:00", employee_id=lookup)))
        for index, action in enumerate(("salary_payment", "advance_grant", "advance_repayment", "custody_grant", "custody_return")):
            inbound = action in {"advance_repayment", "custody_return"}
            movement = await self.import_movement(credit=100 if inbound else 0, debit=0 if inbound else 100,
                description=action, reference=f"CANON-{index}")
            result = await self.classify(movement["id"], action, apply=False, employee_id="employee-1")
            self.assertEqual(result["employee_id"], self.employee)
            # Repeating with canonical id must not create another event.
            again = await self.classify(movement["id"], action, apply=False)
            self.assertEqual(again["state"], "already_posted")
        rows = await self.db.general_ledger.find({"entity_type": "employee"}).to_list(100)
        self.assertEqual({row["entity_id"] for row in rows}, {self.employee})
        self.assertEqual({row["sub_account"] for row in rows}, {"salary_payable", "advance", "custody"})
        self.assertTrue(any(row["entry_type"] == "opening_balance" for row in rows))
        events = await self.db.mz2_employee_financial_events.find({}).to_list(100)
        self.assertEqual({row["employee_id"] for row in events}, {self.employee})
        context = await payroll_context(self.db, self.owner)
        self.assertEqual(context["employees"][0]["id"], self.employee)
        self.assertEqual(context["employees"][0]["employee_v2_id"], self.employee)
        self.assertEqual(context["employees"][0]["custody"], 0)
        self.assertEqual(await self.db.operating_salaries.count_documents({}), 0)

    async def test_without_contract_alias_still_writes_v2_custody(self):
        await self.db.mezan_employee_salary_contracts_v2.delete_many({})
        movement = await self.import_movement(debit=100, description="custody", reference="NO-SALARY")
        await self.classify(movement["id"], "custody_grant", employee_id="legacy-financial-1")
        rows = await self.db.general_ledger.find({"entity_type": "employee", "sub_account": "custody"}).to_list(10)
        self.assertEqual({row["entity_id"] for row in rows}, {self.employee})

    async def test_legacy_only_and_orphan_contract_fail_closed(self):
        await self.db.mezan_employees_v2.delete_many({})
        await self.db.operating_salaries.insert_one({"id": "employee-1", "user_id": self.owner, "monthly_amount": 99999})
        movement = await self.import_movement(debit=100, description="salary", reference="LEGACY-ONLY")
        before = await self.db.general_ledger.find({}).to_list(100)
        for alias in ("employee-1", self.employee, "missing"):
            with self.assertRaises(HTTPException):
                await self.classify(movement["id"], "salary_payment", employee_id=alias)
            with self.assertRaises(HTTPException):
                await self.tx(lambda tx: accrue_payroll_period(tx, owner=self.owner, actor=self.actor,
                    payload=PayrollAccrualIn(period="2026-09", accrued_at="2026-09-21T00:30:00+03:00", employee_id=alias)))
        self.assertEqual(await self.db.general_ledger.find({}).to_list(100), before)
        self.assertEqual(await self.db.mz2_employee_financial_events.count_documents({}), 0)
        self.assertEqual((await self.db.mz2_daily_movements.find_one({"id": movement["id"]}))["status"], "unclassified")

    async def test_ambiguous_alias_and_cross_tenant_fail_closed(self):
        from accounting_employee_finance import _employee
        await self.db.mezan_employees_v2.insert_one({"id": "other-v2", "user_id": self.owner, "legacy_employee_id": "employee-1"})
        with self.assertRaises(HTTPException) as exc:
            await _employee(self.db, self.owner, "employee-1")
        self.assertEqual(exc.exception.detail, "employee_v2_alias_ambiguous")
        with self.assertRaises(HTTPException):
            await _employee(self.db, "foreign-owner", self.employee)

    async def test_general_ledger_rejects_unresolved_aliases_for_every_employee_writer(self):
        from ledger_core import post_txn_group
        from ledger_double_write import mirror_account_txn_to_ledger
        before = await self.db.general_ledger.find({}).to_list(100)
        for alias in ("employee-1", "legacy-person-1", "legacy-financial-1", "contract-v2-1", "missing"):
            for source in ("accounting_payroll_p01", "accounting_opening_balance_p01", "other_writer"):
                with self.subTest(alias=alias, source=source), self.assertRaises(HTTPException) as exc:
                    await self.tx(lambda tx: post_txn_group(tx, user_id=self.owner, actor_id=self.owner,
                        actor_name="Synthetic", txn_type="salary_accrual", entries=[
                            {"entity_type": "employee", "entity_id": alias, "sub_account": "salary_payable", "entry_type": "salary_accrual", "amount": 10, "side": "credit", "metadata": {"source": source}},
                            {"entity_type": "expense", "entity_id": "salary", "entry_type": "salary_accrual", "amount": 10, "side": "debit"},
                        ]))
                self.assertEqual(exc.exception.detail, "employee_v2_identity_required")
            # Batch mirror and direct transactional inserts cannot bypass the
            # common persistence guard by avoiding post_ledger_entry.
            with self.assertRaises(HTTPException) as exc:
                await self.tx(lambda tx: mirror_account_txn_to_ledger(tx, user_id=self.owner,
                    account_id="bank-main", account_transaction_id=f"bad-{alias}", amount=10,
                    direction="out", transaction_type="salary", counter_entity_type="employee", counter_entity_id=alias))
            self.assertEqual(exc.exception.detail, "employee_v2_identity_required")
            with self.assertRaises(HTTPException) as exc:
                await self.tx(lambda tx: tx.general_ledger.insert_one({"user_id": self.owner,
                    "entity_type": "employee", "entity_id": alias, "amount": 10}))
            self.assertEqual(exc.exception.detail, "employee_v2_identity_required")
        self.assertEqual(await self.db.general_ledger.find({}).to_list(100), before)

    async def test_native_v2_opening_writer_requires_exact_employee_id(self):
        from accounting_ledger_v2 import post_opening_journal_v2, GENERAL_LEDGER_COLLECTION, GROUPS_COLLECTION
        # Test-only new-book activation; never a production control operation.
        await self.db.mz2_atomic_owners.update_one({"_id": self.owner}, {"$set": {
            "ledger_backend_state": "v2_active", "ledger_backend_revision": 1,
            "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "synthetic-native-book",
        }})
        async def opening(employee_id):
            entries = [{"entity_type": "employee", "entity_id": employee_id, "sub_account": sub,
                "leg_key": sub, "entry_type": "opening_balance", "amount": "100.00", "side": "credit"}
                for sub in ("salary_payable", "advance", "custody")]
            entries.append({"entity_type": "equity", "entity_id": "opening", "leg_key": "equity",
                "entry_type": "opening_balance", "amount": "300.00", "side": "debit"})
            async with await self.mongo.start_session() as session:
                return await session.with_transaction(lambda active: post_opening_journal_v2(self.db,
                    user_id=self.owner, actor_id=self.owner, actor_name="Synthetic", opening_operation_id="canonical-opening",
                    approved_preview_hash="a" * 64, effective_at="2026-09-20T00:00:00+03:00", entries=entries,
                    mongo_session=active))
        for alias in ("employee-1", "legacy-person-1", "legacy-financial-1", "contract-v2-1"):
            with self.assertRaises(HTTPException) as exc:
                await opening(alias)
            self.assertEqual(exc.exception.detail, "employee_v2_identity_required")
            self.assertEqual(await self.db[GENERAL_LEDGER_COLLECTION].count_documents({}), 0)
            self.assertEqual(await self.db[GROUPS_COLLECTION].count_documents({}), 0)
        await opening(self.employee)
        rows = await self.db[GENERAL_LEDGER_COLLECTION].find({"entity_type": "employee"}).to_list(10)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row["entity_id"] for row in rows}, {self.employee})

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
        self.assertEqual(first["items"][0]["amount"], "266.67")
        before = await self.db.general_ledger.count_documents({})

        second = await self.tx(lambda scoped: accrue_payroll_period(
            scoped, owner=self.owner, actor=self.actor, payload=payload,
        ))
        self.assertEqual(second["posted"], 0)
        self.assertEqual(second["already_posted"], 1)
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)

        nets = await self.balances()
        # Opening payable 3000 + two covered post-cutover days (20-21).
        self.assertEqual(round(nets[("employee", self.employee, "salary_payable")], 2), -3266.67)
        self.assertEqual(round(nets[("expense", "salary", "")], 2), 266.67)

    async def test_salary_cash_more_than_payable_splits_explicit_advance(self):
        movement = await self.import_movement(debit=3500, description="Salary and advance", reference="SAL-OVER")
        before = await self.db.general_ledger.count_documents({})
        result = await self.classify(movement["id"], "salary_payment", apply=False)
        self.assertEqual(result["salary_settled"], "3000.00")
        self.assertEqual(result["advance_granted"], "500.00")
        self.assertEqual(result["advance_offset"], "0.00")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 4)
        nets = await self.balances()
        self.assertEqual(nets[("employee", self.employee, "salary_payable")], 0)
        self.assertEqual(nets[("employee", self.employee, "advance")], 1000)
        again = await self.classify(movement["id"], "salary_payment", apply=False)
        self.assertEqual(again["state"], "already_posted")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 4)

    async def test_partial_salary_does_not_implicitly_net_advances(self):
        movement = await self.import_movement(debit=700, description="Partial salary", reference="SAL-PARTIAL")
        payload = MovementClassifyIn(employee_id=self.employee, action="salary_payment", reason="Synthetic partial salary")
        self.assertFalse(payload.apply_open_advances)
        result = await self.tx(lambda scoped: classify_employee_movement(scoped, owner=self.owner, actor=self.actor, movement_id=movement["id"], payload=payload))
        self.assertEqual(result["salary_settled"], "700.00")
        self.assertEqual(result["advance_offset"], "0.00")
        nets = await self.balances()
        self.assertEqual(nets[("employee", self.employee, "salary_payable")], -2300)
        self.assertEqual(nets[("employee", self.employee, "advance")], 500)

    async def test_daily_accrual_posts_only_increment_and_preserves_first_journal(self):
        async def accrue(day):
            payload = PayrollAccrualIn(period="2026-09", accrued_at=f"2026-09-{day}T00:30:00+03:00", employee_id=self.employee)
            return await self.tx(lambda scoped: accrue_payroll_period(scoped, owner=self.owner, actor=self.actor, payload=payload))
        first = await accrue("21")
        original = await self.db.general_ledger.find({"txn_group_id": first["items"][0]["txn_group_id"]}).to_list(10)
        second = await accrue("22")
        self.assertEqual(second["items"][0]["amount"], "133.33")
        self.assertEqual(await self.db.general_ledger.find({"txn_group_id": first["items"][0]["txn_group_id"]}).to_list(10), original)
        self.assertEqual((await accrue("22"))["already_posted"], 1)

    async def test_967_74_payable_with_700_cash_leaves_267_74(self):
        prior = await self.import_movement(debit=2032.26, description="Prior salary", reference="EX-PRE-700")
        await self.classify(prior["id"], "salary_payment", apply=False)
        movement = await self.import_movement(debit=700, description="Partial salary", reference="EX-700")
        result = await self.classify(movement["id"], "salary_payment", apply=False)
        self.assertEqual(result["salary_settled"], "700.00")
        self.assertEqual(result["advance_granted"], "0.00")
        nets = await self.balances()
        self.assertEqual(round(nets[("employee", self.employee, "salary_payable")], 2), -267.74)
        self.assertEqual(nets[("employee", self.employee, "advance")], 500)

    async def test_967_74_payable_with_1200_cash_creates_232_26_advance(self):
        prior = await self.import_movement(debit=2032.26, description="Prior salary", reference="EX-PRE-1200")
        await self.classify(prior["id"], "salary_payment", apply=False)
        movement = await self.import_movement(debit=1200, description="Salary and advance", reference="EX-1200")
        result = await self.classify(movement["id"], "salary_payment", apply=False)
        self.assertEqual(result["salary_settled"], "967.74")
        self.assertEqual(result["advance_granted"], "232.26")
        nets = await self.balances()
        self.assertEqual(round(nets[("employee", self.employee, "salary_payable")], 2), 0)
        self.assertEqual(round(nets[("employee", self.employee, "advance")], 2), 732.26)

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
