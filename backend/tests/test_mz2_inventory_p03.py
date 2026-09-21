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
    require_p03_inventory_financial_writes,
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


if __name__ == "__main__":
    unittest.main()
