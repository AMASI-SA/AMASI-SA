"""Real-Mongo contract for the MZ2-native opening-balance workflow."""
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_atomic import atomic_owner
from accounting_module_contract import OPERATION_ID
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


class OpeningBalanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_opening_" + uuid4().hex]
        hello = await self.db.command("hello")
        self.assertTrue(hello.get("setName"))
        self.actor = {
            "id": "owner",
            "role": "owner",
            "name": "Synthetic owner",
            "email": "owner@example.invalid",
        }
        await self.db.users.insert_one({
            **self.actor,
            "is_active": True,
        })
        await self.db.settings.insert_one({"user_id": "owner"})
        await self.db.accounts.insert_many([
            {
                "id": "bank-inma",
                "user_id": "owner",
                "name": "Synthetic bank",
                "account_type": "bank",
                "status": "active",
            },
            {
                "id": "cash-main",
                "user_id": "owner",
                "name": "Synthetic cash",
                "account_type": "cash",
                "status": "active",
            },
            {
                "id": "legacy-provider-account",
                "user_id": "owner",
                "name": "Synthetic platform account",
                "account_type": "payment_platform",
                "status": "active",
            },
        ])
        await self.db.operating_salaries.insert_one({
            "id": "employee-1",
            "user_id": "owner",
            "name": "Synthetic employee",
            "status": "active",
        })

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    def preview_payload(self):
        refs = {
            "banks_cash": "UAT-BANKS",
            "providers": "UAT-PROVIDERS",
            "couriers_cod": "UAT-COURIERS",
            "inventory": "UAT-INVENTORY-ZERO",
            "suppliers": "UAT-SUPPLIERS",
            "payroll_obligations": "UAT-PAYROLL",
            "equity": "UAT-EQUITY",
        }
        lines = [
            OpeningLineIn(category="bank", entity_id="bank-inma", label="Bank", amount="1000"),
            OpeningLineIn(category="provider_receivable", entity_id="tamara", amount="115"),
            OpeningLineIn(category="courier_cod_receivable", entity_id="smsa", amount="100"),
            OpeningLineIn(category="employee_advance", entity_id="employee-1", amount="50"),
            OpeningLineIn(category="employee_salary_payable", entity_id="employee-1", amount="300"),
            OpeningLineIn(category="supplier_payable", entity_id="supplier-1", amount="200"),
        ]
        return OpeningPreviewIn(
            cutover_at="2026-09-20T21:00:00+03:00",
            evidence_sheet_ref="UAT-SIGNED-OPENING",
            evidence_sections=refs,
            lines=lines,
            notes="Synthetic isolated UAT",
        )

    async def tx(self, callback):
        return await atomic_owner(self.db, "owner", callback)

    async def create(self):
        payload = self.preview_payload()
        return await self.tx(
            lambda scoped: create_opening_preview(
                scoped,
                owner="owner",
                actor=self.actor,
                payload=payload,
            )
        )

    async def approve(self, preview_id):
        payload = OpeningApproveIn(
            preview_id=preview_id,
            confirmation="APPROVE_OPENING_BALANCE",
        )
        return await self.tx(
            lambda scoped: approve_opening_preview(
                scoped,
                owner="owner",
                actor=self.actor,
                payload=payload,
            )
        )

    async def activate(self):
        payload = OpeningActivateIn(
            activation_ref="UAT-ACTIVATION-001",
            confirmation="ACTIVATE_MZ2_P01",
        )
        return await self.tx(
            lambda scoped: activate_p01(
                scoped,
                owner="owner",
                actor=self.actor,
                payload=payload,
            )
        )

    async def test_preview_post_activate_is_atomic_balanced_and_reportable(self):
        preview = await self.create()
        self.assertTrue(preview["totals"]["balanced"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        self.assertEqual(preview["status"], "previewed")
        # Debit 1265 vs natural liabilities 500 => an automatic credit equity plug 765.
        plug = [row for row in preview["lines"] if row["category"] == "equity_plug"]
        self.assertEqual(len(plug), 1)
        self.assertEqual(plug[0]["side"], "credit")
        self.assertEqual(plug[0]["amount"], "765.00")

        posted = await self.approve(preview["id"])
        self.assertEqual(posted["state"], "posted")
        self.assertEqual(posted["debit_total"], posted["credit_total"])
        group = posted["txn_group_id"]
        rows = await self.db.general_ledger.find({"txn_group_id": group}).to_list(100)
        self.assertEqual(len(rows), len(preview["lines"]))
        self.assertEqual(
            sum(row["amount"] for row in rows if row["side"] == "debit"),
            sum(row["amount"] for row in rows if row["side"] == "credit"),
        )
        self.assertTrue(all(
            row["entry_type"] == "opening_balance"
            and row["status"] == "posted"
            and row["metadata"]["operation_id"] == OPERATION_ID
            and row["metadata"]["accounting_at"] == "2026-09-20T18:00:00+00:00"
            for row in rows
        ))

        settings = await self.db.settings.find_one({"user_id": "owner"})
        cutover = settings["mezan2_financial_cutover"]
        self.assertEqual(cutover["status"], "prepared")
        self.assertEqual(cutover["opening_balance_txn_group_id"], group)
        zero = {
            (row["entity_type"], row["entity_id"], row["sub_account"])
            for row in cutover["opening_balance_zero_accounts"]
        }
        self.assertIn(("bank", "cash-main", "main"), zero)
        self.assertIn(("bank", "legacy-provider-account", "main"), zero)
        self.assertIn(("payment_gateway", "salla", "receivable"), zero)
        self.assertIn(("payment_gateway", "tabby", "receivable"), zero)
        self.assertIn(("payment_gateway", "emkan", "receivable"), zero)
        self.assertNotIn(("payment_gateway", "tamara", "receivable"), zero)

        activation = await self.activate()
        self.assertEqual(activation["state"], "active")
        self.assertFalse(activation["p02_shipping_cod_enabled"])
        scope = await read_mz2_ledger(self.db, owner="owner")
        self.assertEqual(scope["status"], "available", scope)
        self.assertTrue(any(
            row["entity_type"] == "payment_gateway"
            and row["entity_id"] == "tamara"
            and row["sub_account"] == "receivable"
            for row in scope["items"]
        ))

    async def test_duplicate_approval_returns_same_group_without_new_legs(self):
        preview = await self.create()
        first = await self.approve(preview["id"])
        before = await self.db.general_ledger.count_documents({})
        second = await self.approve(preview["id"])
        self.assertEqual(second["state"], "already_posted")
        self.assertEqual(second["txn_group_id"], first["txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)

    async def test_account_scope_change_after_preview_blocks_without_journal(self):
        preview = await self.create()
        await self.db.accounts.insert_one({
            "id": "bank-new",
            "user_id": "owner",
            "name": "Changed after preview",
            "account_type": "bank",
            "status": "active",
        })
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as ctx:
            await self.approve(preview["id"])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("opening_account_scope_changed", str(ctx.exception.detail))
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)

    async def test_opening_must_be_first_mz2_journal(self):
        preview = await self.create()
        await self.db.general_ledger.insert_one({
            "id": "bad-before-opening",
            "user_id": "owner",
            "entry_no": 1,
            "entity_type": "bank",
            "entity_id": "bank-inma",
            "sub_account": "main",
            "entry_type": "bnpl_sale",
            "side": "debit",
            "amount": 1.0,
            "status": "posted",
            "txn_group_id": "existing-mz2",
            "metadata": {
                "operation_id": OPERATION_ID,
                "accounting_at": "2026-09-20T18:01:00+00:00",
            },
        })
        with self.assertRaises(HTTPException) as ctx:
            await self.approve(preview["id"])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "opening_must_precede_all_mz2_journals")

    async def test_p02_flag_blocks_preview_and_activation(self):
        await self.db.settings.update_one(
            {"user_id": "owner"},
            {"$set": {"mezan2_financial_cutover.p02_shipping_cod_enabled": True}},
        )
        with self.assertRaises(HTTPException) as ctx:
            await self.create()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("p02_must_remain_locked", str(ctx.exception.detail))

    async def test_missing_evidence_section_and_unknown_employee_fail_before_gl(self):
        payload = self.preview_payload()
        payload.evidence_sections.pop("inventory")
        with self.assertRaises(HTTPException):
            await self.tx(
                lambda scoped: create_opening_preview(
                    scoped, owner="owner", actor=self.actor, payload=payload
                )
            )
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

        payload = self.preview_payload()
        payload.lines.append(
            OpeningLineIn(
                category="employee_advance",
                entity_id="missing-employee",
                amount="10",
            )
        )
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(
                lambda scoped: create_opening_preview(
                    scoped, owner="owner", actor=self.actor, payload=payload
                )
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("opening_employee_missing", str(ctx.exception.detail))
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)


if __name__ == "__main__":
    unittest.main()
