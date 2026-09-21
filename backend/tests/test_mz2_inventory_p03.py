"""Real-Mongo guard contract for MZ2 P03 inventory/purchase phase."""
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_atomic import atomic_owner
from accounting_inventory_p03 import (
    P03ActivateIn,
    activate_p03,
    inventory_p03_workspace,
    post_supplier_invoice,
    prepare_supplier_invoice_post,
    require_p03_inventory_financial_writes,
)
from accounting_mz2_reports import mz2_financial_position, read_mz2_ledger
from accounting_periods import PeriodChange, set_period
from accounting_module_opening_balances import (
    OpeningActivateIn,
    OpeningApproveIn,
    OpeningLineIn,
    OpeningPreviewIn,
    activate_p01,
    approve_opening_preview,
    create_opening_preview,
)


class MZ2InventoryP03PhaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_inventory_p03_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.owner = "owner"
        self.actor = {
            "id": self.owner,
            "role": "owner",
            "name": "Synthetic owner",
            "email": "owner@example.invalid",
            "is_owner": True,
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
        await self.db.mezan_suppliers_v2.insert_one({
            "id": "msv2-supplier-1",
            "user_id": self.owner,
            "company_name": "Synthetic supplier V2",
            "status": "active",
        })

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def tx(self, callback):
        return await atomic_owner(self.db, self.owner, callback)

    def opening(self, *, extra_lines=None):
        return OpeningPreviewIn(
            cutover_at="2026-09-20T00:00:00+03:00",
            evidence_sheet_ref="SYN-P03-OPENING",
            evidence_sections={
                "banks_cash": "SYN-BANKS",
                "providers": "SYN-PROVIDERS-ZERO",
                "couriers_cod": "SYN-P02-ZERO",
                "inventory": "SYN-INVENTORY",
                "suppliers": "SYN-SUPPLIERS",
                "payroll_obligations": "SYN-PAYROLL-ZERO",
                "equity": "SYN-EQUITY",
            },
            lines=[
                OpeningLineIn(
                    category="bank",
                    entity_id="bank-main",
                    amount="1000",
                ),
                *(extra_lines or []),
            ],
        )

    async def activate_full_p03(self):
        await self.activate_p01_only()
        await self.db.settings.update_one(
            {"user_id": self.owner},
            {"$set": {
                "mezan2_financial_cutover.p02_shipping_cod_enabled": True,
                "mezan2_financial_cutover.p02_shipping_cod_activation_ref": "SYN-P02-UAT",
            }},
        )
        await self.tx(lambda scoped: activate_p03(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P03ActivateIn(
                activation_ref="SYN-P03-UAT",
                confirmation="ACTIVATE_MZ2_P03",
            ),
        ))

    async def add_supplier_invoice(self, *, invoice_id="msiv2-post-1", total_halalas=25000):
        await self.db.mezan_supplier_invoices_v2.insert_one({
            "id": invoice_id,
            "user_id": self.owner,
            "supplier_id": "msv2-supplier-1",
            "supplier_snapshot": {"company_name": "Synthetic supplier V2"},
            "invoice_number": "SI-P03-1",
            "session_id": "session-p03-1",
            "status": "awaiting_accounting",
            "payment_status": "unposted",
            "paid_halalas": 0,
            "outstanding_halalas": 0,
            "total_halalas": total_halalas,
            "experiment_mode": False,
            "approved_at": "2026-09-21T08:00:00+00:00",
            "financial_invoice_created": True,
            "liability_created": False,
            "ledger_txn_group_id": None,
            "ledger_entry_ids": [],
        })

    async def activate_p01_only(self):
        preview = await self.tx(lambda scoped: create_opening_preview(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=self.opening(),
        ))
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
                activation_ref="SYN-P01-UAT",
                confirmation="ACTIVATE_MZ2_P01",
            ),
        ))
        return preview

    async def test_opening_zero_scope_covers_v2_suppliers_inventory_and_input_vat(self):
        preview = await self.tx(lambda scoped: create_opening_preview(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=self.opening(),
        ))
        zeros = {
            (row["entity_type"], row["entity_id"], row["sub_account"])
            for row in preview["zero_scope"]
        }
        self.assertIn(("supplier", "msv2-supplier-1", "payable"), zeros)
        self.assertIn(("asset", "inventory", "inventory"), zeros)
        self.assertIn(("tax", "input_vat", "input_vat"), zeros)

    async def test_supplier_opening_requires_mezan_v2_supplier_identity(self):
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: create_opening_preview(
                scoped,
                owner=self.owner,
                actor=self.actor,
                payload=self.opening(extra_lines=[
                    OpeningLineIn(
                        category="supplier_payable",
                        entity_id="legacy-supplier",
                        amount="100",
                    ),
                ]),
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "opening_supplier_missing")

    async def test_p03_activation_is_fail_closed_and_requires_p02_evidence(self):
        await self.activate_p01_only()
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: activate_p03(
                scoped,
                owner=self.owner,
                actor=self.actor,
                payload=P03ActivateIn(
                    activation_ref="SYN-P03-UAT",
                    confirmation="ACTIVATE_MZ2_P03",
                ),
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("p02_shipping_cod_enabled", ctx.exception.detail["missing"])

        with self.assertRaises(HTTPException) as locked:
            await require_p03_inventory_financial_writes(
                self.db,
                owner=self.owner,
                event_at="2026-09-21T10:00:00+03:00",
            )
        self.assertEqual(locked.exception.status_code, 423)
        self.assertEqual(
            locked.exception.detail["code"],
            "p03_inventory_purchases_locked",
        )

        await self.db.settings.update_one(
            {"user_id": self.owner},
            {"$set": {
                "mezan2_financial_cutover.p02_shipping_cod_enabled": True,
                "mezan2_financial_cutover.p02_shipping_cod_activation_ref": "SYN-P02-UAT",
            }},
        )
        result = await self.tx(lambda scoped: activate_p03(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P03ActivateIn(
                activation_ref="SYN-P03-UAT",
                confirmation="ACTIVATE_MZ2_P03",
            ),
        ))
        self.assertEqual(result["state"], "active")
        self.assertTrue(result["p03_inventory_purchases_enabled"])

        await require_p03_inventory_financial_writes(
            self.db,
            owner=self.owner,
            event_at="2026-09-21T10:00:00+03:00",
        )
        with self.assertRaises(HTTPException) as before_cutover:
            await require_p03_inventory_financial_writes(
                self.db,
                owner=self.owner,
                event_at="2026-09-19T10:00:00+03:00",
            )
        self.assertEqual(before_cutover.exception.status_code, 423)

        duplicate = await self.tx(lambda scoped: activate_p03(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P03ActivateIn(
                activation_ref="IGNORED-DUPLICATE",
                confirmation="ACTIVATE_MZ2_P03",
            ),
        ))
        self.assertEqual(duplicate["state"], "already_active")
        audit = await self.db.mz2_inventory_p03_audit.count_documents({
            "user_id": self.owner,
            "action": "p03_activated",
        })
        self.assertEqual(audit, 1)

    async def test_workspace_is_read_only_and_uses_v2_sources(self):
        await self.db.mezan_supplier_invoices_v2.insert_one({
            "id": "msiv2-1",
            "user_id": self.owner,
            "supplier_id": "msv2-supplier-1",
            "supplier_snapshot": {"company_name": "Synthetic supplier V2"},
            "invoice_number": "SI-1",
            "status": "awaiting_accounting",
            "payment_status": "unposted",
            "total_halalas": 25000,
            "outstanding_halalas": 0,
            "experiment_mode": False,
            "approved_at": "2026-09-21T08:00:00+00:00",
        })
        await self.db.mezan_inventory_receipts_v2.insert_one({
            "id": "receipt-1",
            "user_id": self.owner,
            "status": "posted",
            "purchase_invoice_id": "purchase-1",
            "purchase_invoice_line_id": "line-1",
            "quantity": 5,
            "posted_at": "2026-09-21T09:00:00+00:00",
        })
        before_ledger = await self.db.general_ledger.count_documents({})
        before_settings = await self.db.settings.find_one({"user_id": self.owner})

        workspace = await inventory_p03_workspace(self.db, owner=self.owner)

        self.assertEqual(workspace["rules"]["supplier_identity_source"], "mezan_suppliers_v2")
        self.assertFalse(workspace["rules"]["legacy_liabilities_are_accounting_authority"])
        self.assertTrue(workspace["rules"]["inventory_v2_remains_operational_stock_authority"])
        self.assertEqual(workspace["summary"]["active_suppliers"], 1)
        self.assertEqual(workspace["summary"]["supplier_invoices"], 1)
        self.assertEqual(workspace["summary"]["posted_inventory_receipts"], 1)
        self.assertEqual(workspace["summary"]["inventory_receipts_without_mz2_accounting"], 1)
        self.assertEqual(await self.db.general_ledger.count_documents({}), before_ledger)
        self.assertEqual(
            await self.db.settings.find_one({"user_id": self.owner}),
            before_settings,
        )


    async def test_supplier_invoice_posts_only_through_active_p03_and_is_idempotent(self):
        await self.activate_full_p03()
        await self.add_supplier_invoice()

        preview = await prepare_supplier_invoice_post(
            self.db,
            owner=self.owner,
            invoice_id="msiv2-post-1",
        )
        self.assertEqual(preview["state"], "eligible")
        self.assertEqual(preview["facts"]["amount"], "250.00")

        before = await self.db.general_ledger.count_documents({})
        result = await self.tx(lambda scoped: post_supplier_invoice(
            scoped,
            owner=self.owner,
            actor=self.actor,
            invoice_id="msiv2-post-1",
            reason="اعتماد فاتورة المورد بعد الاستلام",
        ))
        self.assertEqual(result["state"], "posted")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 2)

        legs = await self.db.general_ledger.find(
            {"txn_group_id": result["txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {
                (
                    row["entity_type"],
                    row["entity_id"],
                    row.get("sub_account"),
                    row["side"],
                    row["entry_type"],
                )
                for row in legs
            },
            {
                ("expense", "supplier_fulfillment", None, "debit", "supplier_invoice"),
                ("supplier", "msv2-supplier-1", "payable", "credit", "supplier_invoice"),
            },
        )
        self.assertTrue(all(
            (row.get("metadata") or {}).get("source") == "accounting_inventory_p03"
            for row in legs
        ))
        self.assertTrue(all(
            (row.get("metadata") or {}).get("p03_event_id")
            for row in legs
        ))

        invoice = await self.db.mezan_supplier_invoices_v2.find_one(
            {"id": "msiv2-post-1"},
            {"_id": 0},
        )
        self.assertEqual(invoice["status"], "payable_posted")
        self.assertEqual(invoice["payment_status"], "unpaid")
        self.assertEqual(invoice["outstanding_halalas"], 25000)
        self.assertTrue(invoice["liability_created"])
        self.assertEqual(invoice["ledger_txn_group_id"], result["txn_group_id"])

        again = await self.tx(lambda scoped: post_supplier_invoice(
            scoped,
            owner=self.owner,
            actor=self.actor,
            invoice_id="msiv2-post-1",
            reason="إعادة إرسال آمنة",
        ))
        self.assertEqual(again["state"], "already_posted")
        self.assertEqual(again["txn_group_id"], result["txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 2)

        ledger = await read_mz2_ledger(self.db, owner=self.owner)
        self.assertEqual(ledger["status"], "available")
        position = await mz2_financial_position(self.db, owner=self.owner)
        self.assertAlmostEqual(position["liabilities"]["supplier_payable"], 250.0)

    async def test_supplier_invoice_post_is_blocked_while_p03_locked(self):
        await self.activate_p01_only()
        await self.add_supplier_invoice(invoice_id="msiv2-locked")
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: post_supplier_invoice(
                scoped,
                owner=self.owner,
                actor=self.actor,
                invoice_id="msiv2-locked",
                reason="يجب أن تبقى المرحلة مقفلة",
            ))
        self.assertEqual(ctx.exception.status_code, 423)
        self.assertEqual(ctx.exception.detail["code"], "p03_inventory_purchases_locked")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        invoice = await self.db.mezan_supplier_invoices_v2.find_one(
            {"id": "msiv2-locked"},
            {"_id": 0},
        )
        self.assertFalse(invoice["liability_created"])
        self.assertFalse(invoice.get("ledger_txn_group_id"))

    async def test_closed_period_rolls_back_p03_supplier_invoice(self):
        await self.activate_full_p03()
        await self.add_supplier_invoice(invoice_id="msiv2-closed")
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
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: post_supplier_invoice(
                scoped,
                owner=self.owner,
                actor=self.actor,
                invoice_id="msiv2-closed",
                reason="اختبار الفترة المقفلة",
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "accounting_period_closed")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        self.assertEqual(
            await self.db.mz2_inventory_p03_events.count_documents({
                "user_id": self.owner,
                "facts.invoice_id": "msiv2-closed",
            }),
            0,
        )

    async def test_existing_non_p03_supplier_ledger_is_not_reposted(self):
        await self.activate_full_p03()
        await self.add_supplier_invoice(invoice_id="msiv2-legacy")
        await self.db.mezan_supplier_invoices_v2.update_one(
            {"id": "msiv2-legacy"},
            {"$set": {"ledger_txn_group_id": "legacy-direct-group"}},
        )
        preview = await prepare_supplier_invoice_post(
            self.db,
            owner=self.owner,
            invoice_id="msiv2-legacy",
        )
        self.assertEqual(preview["state"], "blocked")
        self.assertEqual(
            preview["reasons"],
            ["supplier_invoice_existing_non_p03_ledger"],
        )


if __name__ == "__main__":
    unittest.main()
