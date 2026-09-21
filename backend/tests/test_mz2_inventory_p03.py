"""Real-Mongo guard contract for MZ2 P03 inventory/purchase phase."""
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_atomic import atomic_owner
from accounting_inventory_p03 import (
    P03ActivateIn,
    P03OpeningInventoryCostApproveIn,
    P03OpeningInventoryCostLineIn,
    P03PurchaseInvoiceCreateIn,
    P03PurchaseLineIn,
    activate_p03,
    approve_opening_inventory_cost_snapshot,
    create_p03_purchase_invoice,
    inventory_p03_workspace,
    opening_inventory_cost_workspace,
    post_inventory_cogs,
    post_inventory_receipt,
    post_supplier_invoice,
    prepare_inventory_cogs_post,
    prepare_inventory_receipt_post,
    prepare_supplier_invoice_post,
    require_p03_inventory_financial_writes,
)
from accounting_mz2_reports import mz2_financial_position, read_mz2_ledger
from accounting_periods import PeriodChange, set_period
from ledger_core import post_txn_group
from accounting_shipping_p02 import P02ActivateIn, activate_p02
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
        await self.db.mezan_products_v2.insert_one({
            "id": "product-row-1",
            "mezan_product_id": "MZP-1",
            "salla_product_id": "SALLA-1",
            "user_id": self.owner,
            "name": "Synthetic inventory product",
            "sku": "SKU-1",
            "archived": False,
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
        await self.tx(lambda scoped: activate_p02(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P02ActivateIn(
                activation_ref="SYN-P02-UAT",
                confirmation="ACTIVATE_MZ2_P02",
            ),
        ))
        await self.tx(lambda scoped: activate_p03(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P03ActivateIn(
                activation_ref="SYN-P03-UAT",
                confirmation="ACTIVATE_MZ2_P03",
            ),
        ))

    async def activate_p03_with_opening_inventory(
        self,
        *,
        opening_amount="300.00",
        quantity=3,
        unit_cost="100.00",
        lot_id="opening-lot-1",
    ):
        await self.db.warehouse_locations.insert_one({
            "id": "LOC-OPENING",
            "code": "OPEN-01",
            "warehouse_id": "WH-OPENING",
            "user_id": self.owner,
            "state": "occupied",
            "occupancy": {
                "total_quantity": quantity,
                "items": [{
                    "receipt_id": None,
                    "product_id": "SALLA-OPENING",
                    "mezan_product_id": "MZP-OPENING",
                    "product_name": "Opening inventory product",
                    "sku": "SKU-OPENING",
                    "quantity": quantity,
                    "lot_id": lot_id,
                    "configuration_key": "opening-default",
                    "placed_at": "2026-09-19T18:00:00+00:00",
                }],
            },
        })
        preview = await self.tx(lambda scoped: create_opening_preview(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=self.opening(extra_lines=[
                OpeningLineIn(
                    category="inventory_asset",
                    entity_id="inventory",
                    amount=opening_amount,
                ),
            ]),
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
                activation_ref="SYN-P01-OPENING-INVENTORY",
                confirmation="ACTIVATE_MZ2_P01",
            ),
        ))
        await self.tx(lambda scoped: activate_p02(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P02ActivateIn(
                activation_ref="SYN-P02-OPENING-INVENTORY",
                confirmation="ACTIVATE_MZ2_P02",
            ),
        ))
        workspace = await opening_inventory_cost_workspace(
            self.db,
            owner=self.owner,
        )
        self.assertEqual(workspace["blockers"], [])
        self.assertEqual(workspace["opening_inventory_amount"], opening_amount)
        self.assertEqual(workspace["target_count"], 1)
        self.assertEqual(workspace["targets"][0]["target_key"], "lot:" + lot_id)
        approved = await self.tx(lambda scoped: approve_opening_inventory_cost_snapshot(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P03OpeningInventoryCostApproveIn(
                inventory_fingerprint=workspace["inventory_fingerprint"],
                evidence_ref="SYN-OPENING-INVENTORY-COST-SHEET",
                reason="Opening inventory lot cost evidence",
                lines=[
                    P03OpeningInventoryCostLineIn(
                        target_key="lot:" + lot_id,
                        unit_cost=unit_cost,
                    ),
                ],
            ),
        ))
        self.assertEqual(approved["state"], "approved")
        await self.tx(lambda scoped: activate_p03(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P03ActivateIn(
                activation_ref="SYN-P03-OPENING-INVENTORY",
                confirmation="ACTIVATE_MZ2_P03",
            ),
        ))
        return workspace, approved

    async def create_purchase_invoice(
        self,
        *,
        request_id="REQ-P03-PURCHASE-001",
        tax_treatment="recoverable_input_vat",
    ):
        payload = P03PurchaseInvoiceCreateIn(
            request_id=request_id,
            supplier_id="msv2-supplier-1",
            invoice_number="PINV-P03-001",
            invoice_date="2026-09-21",
            due_date="2026-10-21",
            lines=[
                P03PurchaseLineIn(
                    product_id="MZP-1",
                    product_name="Synthetic inventory product",
                    sku="SKU-1",
                    quantity=3,
                    unit_price="100.00",
                ),
            ],
            tax_amount="45.00",
            tax_treatment=tax_treatment,
            tax_evidence_ref=(
                "SYN-TAX-INVOICE-001"
                if tax_treatment == "recoverable_input_vat"
                else None
            ),
            notes="Synthetic P03 purchase",
        )
        return await self.tx(lambda scoped: create_p03_purchase_invoice(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=payload,
        ))

    async def add_inventory_receipt(
        self,
        *,
        receipt_id,
        purchase_invoice_id,
        line_id,
        quantity,
        posted_at,
    ):
        await self.db.mezan_inventory_receipts_v2.insert_one({
            "id": receipt_id,
            "user_id": self.owner,
            "status": "posted",
            "purchase_invoice_id": purchase_invoice_id,
            "purchase_invoice_line_id": line_id,
            "invoice_number": "PINV-P03-001",
            "supplier_name": "Synthetic supplier V2",
            "mezan_product_id": "MZP-1",
            "product_name": "Synthetic inventory product",
            "sku": "SKU-1",
            "quantity": quantity,
            "warehouse_id": "WH-1",
            "location_id": "LOC-1",
            "posted_at": posted_at,
        })

    async def add_inventory_consumption(
        self,
        *,
        event_id,
        order_number,
        receipt_id,
        quantity,
        lot_id=None,
        location_id="LOC-1",
        item_index=0,
        consumed_at="2026-09-21T14:00:00+00:00",
    ):
        await self.db.mezan_inventory_consumption_events_v2.insert_one({
            "id": event_id,
            "user_id": self.owner,
            "order_number": order_number,
            "batch_id": "batch-cogs-1",
            "status": "consumed",
            "allocations": [{
                "location_id": location_id,
                "receipt_id": receipt_id,
                "lot_id": lot_id,
                "item_index": item_index,
                "quantity": quantity,
                "product_id": "SALLA-1",
                "mezan_product_id": "MZP-1",
                "sku": "SKU-1",
            }],
            "reservation_ids": ["reservation-cogs-1"],
            "economic_hash": "synthetic-consumption",
            "prepared_at": consumed_at,
            "consumed_at": consumed_at,
            "accounting_status": "waiting_sale_recognition",
        })

    async def recognize_synthetic_sale(
        self,
        *,
        order_number,
        recognized_at="2026-09-21T15:00:00+00:00",
    ):
        result = await self.tx(lambda scoped: post_txn_group(
            scoped,
            user_id=self.owner,
            actor_id=self.owner,
            actor_name="Synthetic owner",
            txn_type="synthetic_bnpl_sale_for_cogs",
            notes=f"Synthetic sale {order_number}",
            metadata={
                "operation_id": "MZ2-FIN-CUTOVER-001",
                "recognition_event_key": f"sale:{order_number}",
                "recognized_at": recognized_at,
                "accounting_at": recognized_at,
                "order_reference_id": order_number,
            },
            entries=[
                {
                    "entity_type": "payment_gateway",
                    "entity_id": "tabby",
                    "sub_account": "receivable",
                    "side": "debit",
                    "amount": "115.00",
                    "entry_type": "bnpl_sale",
                },
                {
                    "entity_type": "revenue",
                    "entity_id": "bnpl_sales",
                    "side": "credit",
                    "amount": "100.00",
                    "entry_type": "bnpl_sale",
                },
                {
                    "entity_type": "tax",
                    "entity_id": "sales_vat_payable",
                    "side": "credit",
                    "amount": "15.00",
                    "entry_type": "bnpl_sale",
                },
            ],
        ))
        await self.db.mz2_salla_order_evidence.insert_one({
            "id": f"evidence-{order_number}",
            "user_id": self.owner,
            "order_number": order_number,
            "status": "recognized",
            "recognition_txn_group_id": result["txn_group_id"],
            "recognized_provider": "tabby",
            "recognized_at": recognized_at,
        })
        return result

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

    async def test_p03_canonical_opening_account_ids_are_enforced(self):
        for line, expected in [
            (
                OpeningLineIn(
                    category="inventory_asset",
                    entity_id="inventory-main",
                    amount="100",
                ),
                "inventory",
            ),
            (
                OpeningLineIn(
                    category="input_vat",
                    entity_id="vat-input-custom",
                    amount="15",
                ),
                "input_vat",
            ),
        ]:
            with self.assertRaises(HTTPException) as ctx:
                await self.tx(lambda scoped, line=line: create_opening_preview(
                    scoped,
                    owner=self.owner,
                    actor=self.actor,
                    payload=self.opening(extra_lines=[line]),
                ))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(
                ctx.exception.detail["code"],
                "opening_canonical_account_required",
            )
            self.assertEqual(ctx.exception.detail["entity_id"], expected)

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

        await self.tx(lambda scoped: activate_p02(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P02ActivateIn(
                activation_ref="SYN-P02-UAT",
                confirmation="ACTIVATE_MZ2_P02",
            ),
        ))
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


    async def test_native_purchase_invoice_is_idempotent_and_never_creates_legacy_liability(self):
        first = await self.create_purchase_invoice()
        self.assertEqual(first["supplier_id"], "msv2-supplier-1")
        self.assertEqual(first["accounting_authority"], "accounting_inventory_p03")
        self.assertFalse(first["legacy_liability_used"])
        self.assertIsNone(first["liability_id"])
        self.assertEqual(first["subtotal_halalas"], 30000)
        self.assertEqual(first["tax_halalas"], 4500)
        self.assertEqual(first["total_halalas"], 34500)
        self.assertEqual(first["lines"][0]["line_tax_halalas"], 4500)
        self.assertEqual(await self.db.liabilities.count_documents({}), 0)

        duplicate = await self.create_purchase_invoice()
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["id"], first["id"])
        self.assertEqual(
            await self.db.purchase_invoices.count_documents({
                "user_id": self.owner,
                "accounting_authority": "accounting_inventory_p03",
            }),
            1,
        )

    async def test_partial_inventory_receipts_post_cost_input_vat_and_supplier_payable(self):
        await self.activate_full_p03()
        invoice = await self.create_purchase_invoice()
        line_id = invoice["lines"][0]["id"]
        await self.add_inventory_receipt(
            receipt_id="receipt-p03-1",
            purchase_invoice_id=invoice["id"],
            line_id=line_id,
            quantity=1,
            posted_at="2026-09-21T09:00:00+00:00",
        )
        await self.add_inventory_receipt(
            receipt_id="receipt-p03-2",
            purchase_invoice_id=invoice["id"],
            line_id=line_id,
            quantity=2,
            posted_at="2026-09-21T10:00:00+00:00",
        )

        first_preview = await prepare_inventory_receipt_post(
            self.db,
            owner=self.owner,
            receipt_id="receipt-p03-1",
        )
        self.assertEqual(first_preview["state"], "eligible")
        self.assertEqual(first_preview["facts"]["net_halalas"], 10000)
        self.assertEqual(first_preview["facts"]["tax_halalas"], 1500)
        self.assertEqual(first_preview["facts"]["gross_halalas"], 11500)

        first = await self.tx(lambda scoped: post_inventory_receipt(
            scoped,
            owner=self.owner,
            actor=self.actor,
            receipt_id="receipt-p03-1",
            reason="استلام الدفعة الأولى",
        ))
        first_legs = await self.db.general_ledger.find(
            {"txn_group_id": first["txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {
                (
                    row["entity_type"],
                    row["entity_id"],
                    row.get("sub_account"),
                    row["side"],
                    round(float(row["amount"]), 2),
                )
                for row in first_legs
            },
            {
                ("asset", "inventory", "inventory", "debit", 100.00),
                ("tax", "input_vat", "input_vat", "debit", 15.00),
                ("supplier", "msv2-supplier-1", "payable", "credit", 115.00),
            },
        )

        second_preview = await prepare_inventory_receipt_post(
            self.db,
            owner=self.owner,
            receipt_id="receipt-p03-2",
        )
        self.assertEqual(second_preview["facts"]["net_halalas"], 20000)
        self.assertEqual(second_preview["facts"]["tax_halalas"], 3000)
        self.assertEqual(second_preview["facts"]["gross_halalas"], 23000)

        second = await self.tx(lambda scoped: post_inventory_receipt(
            scoped,
            owner=self.owner,
            actor=self.actor,
            receipt_id="receipt-p03-2",
            reason="استلام الدفعة الثانية",
        ))
        self.assertNotEqual(first["txn_group_id"], second["txn_group_id"])

        duplicate = await self.tx(lambda scoped: post_inventory_receipt(
            scoped,
            owner=self.owner,
            actor=self.actor,
            receipt_id="receipt-p03-1",
            reason="إعادة آمنة",
        ))
        self.assertEqual(duplicate["state"], "already_posted")
        self.assertEqual(duplicate["txn_group_id"], first["txn_group_id"])

        stored_invoice = await self.db.purchase_invoices.find_one(
            {"id": invoice["id"]},
            {"_id": 0},
        )
        self.assertEqual(stored_invoice["status"], "fully_received")
        self.assertEqual(stored_invoice["recognized_payable_halalas"], 34500)

        position = await mz2_financial_position(self.db, owner=self.owner)
        self.assertEqual(position["status"], "available")
        self.assertAlmostEqual(position["assets"]["inventory"], 300.0)
        self.assertAlmostEqual(position["assets"]["input_vat"], 45.0)
        self.assertAlmostEqual(position["liabilities"]["supplier_payable"], 345.0)

    async def test_tax_can_be_capitalized_into_inventory_cost(self):
        await self.activate_full_p03()
        invoice = await self.create_purchase_invoice(
            request_id="REQ-P03-CAPITALIZE-001",
            tax_treatment="included_in_inventory_cost",
        )
        line_id = invoice["lines"][0]["id"]
        await self.add_inventory_receipt(
            receipt_id="receipt-capitalized",
            purchase_invoice_id=invoice["id"],
            line_id=line_id,
            quantity=3,
            posted_at="2026-09-21T11:00:00+00:00",
        )
        result = await self.tx(lambda scoped: post_inventory_receipt(
            scoped,
            owner=self.owner,
            actor=self.actor,
            receipt_id="receipt-capitalized",
            reason="ضريبة غير مستردة تضاف للتكلفة",
        ))
        legs = await self.db.general_ledger.find(
            {"txn_group_id": result["txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(len(legs), 2)
        self.assertEqual(
            {
                (
                    row["entity_type"],
                    row["entity_id"],
                    row.get("sub_account"),
                    row["side"],
                    round(float(row["amount"]), 2),
                )
                for row in legs
            },
            {
                ("asset", "inventory", "inventory", "debit", 345.00),
                ("supplier", "msv2-supplier-1", "payable", "credit", 345.00),
            },
        )

    async def test_legacy_purchase_invoice_is_never_posted_by_p03(self):
        await self.activate_full_p03()
        await self.db.purchase_invoices.insert_one({
            "id": "legacy-purchase-1",
            "user_id": self.owner,
            "supplier_name": "Legacy supplier",
            "lines": [{
                "id": "legacy-line-1",
                "quantity": 1,
                "unit_price": 100,
            }],
        })
        await self.add_inventory_receipt(
            receipt_id="receipt-legacy",
            purchase_invoice_id="legacy-purchase-1",
            line_id="legacy-line-1",
            quantity=1,
            posted_at="2026-09-21T12:00:00+00:00",
        )
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as ctx:
            await prepare_inventory_receipt_post(
                self.db,
                owner=self.owner,
                receipt_id="receipt-legacy",
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "p03_purchase_invoice_not_mz2_native")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)

    async def test_closed_period_rolls_back_inventory_receipt_accounting(self):
        await self.activate_full_p03()
        invoice = await self.create_purchase_invoice(
            request_id="REQ-P03-CLOSED-001",
        )
        line_id = invoice["lines"][0]["id"]
        await self.add_inventory_receipt(
            receipt_id="receipt-closed",
            purchase_invoice_id=invoice["id"],
            line_id=line_id,
            quantity=1,
            posted_at="2026-09-21T13:00:00+00:00",
        )
        await set_period(
            self.db,
            self.owner,
            self.owner,
            PeriodChange(
                month="2026-09",
                closed=True,
                revision=0,
                reason="SYN P03 close",
                evidence_ref="SYN P03 close approval",
            ),
        )
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: post_inventory_receipt(
                scoped,
                owner=self.owner,
                actor=self.actor,
                receipt_id="receipt-closed",
                reason="اختبار إقفال P03",
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "accounting_period_closed")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        receipt = await self.db.mezan_inventory_receipts_v2.find_one(
            {"id": "receipt-closed"},
            {"_id": 0},
        )
        self.assertFalse(receipt.get("accounting_event_id"))


    async def test_recoverable_input_vat_requires_tax_evidence(self):
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: create_p03_purchase_invoice(
                scoped,
                owner=self.owner,
                actor=self.actor,
                payload=P03PurchaseInvoiceCreateIn(
                    request_id="REQ-P03-NO-VAT-EVIDENCE",
                    supplier_id="msv2-supplier-1",
                    invoice_number="PINV-NO-EVIDENCE",
                    invoice_date="2026-09-21",
                    lines=[
                        P03PurchaseLineIn(
                            product_id="MZP-1",
                            product_name="Synthetic inventory product",
                            sku="SKU-1",
                            quantity=1,
                            unit_price="100.00",
                        ),
                    ],
                    tax_amount="15.00",
                    tax_treatment="recoverable_input_vat",
                    tax_evidence_ref=None,
                ),
            ))
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(
            ctx.exception.detail["code"],
            "p03_input_vat_evidence_required",
        )
        self.assertEqual(
            await self.db.purchase_invoices.count_documents({
                "user_id": self.owner,
                "invoice_number": "PINV-NO-EVIDENCE",
            }),
            0,
        )


    async def test_cogs_waits_for_sale_then_uses_original_receipt_cost_basis(self):
        await self.activate_full_p03()
        invoice = await self.create_purchase_invoice(
            request_id="REQ-P03-COGS-001",
        )
        line_id = invoice["lines"][0]["id"]
        await self.add_inventory_receipt(
            receipt_id="receipt-cogs-1",
            purchase_invoice_id=invoice["id"],
            line_id=line_id,
            quantity=3,
            posted_at="2026-09-21T10:00:00+00:00",
        )
        await self.tx(lambda scoped: post_inventory_receipt(
            scoped,
            owner=self.owner,
            actor=self.actor,
            receipt_id="receipt-cogs-1",
            reason="إثبات مخزون COGS",
        ))
        receipt = await self.db.mezan_inventory_receipts_v2.find_one(
            {"id": "receipt-cogs-1"},
            {"_id": 0},
        )
        self.assertEqual(receipt["accounting_inventory_cost_halalas"], 30000)

        await self.add_inventory_consumption(
            event_id="consume-cogs-1",
            order_number="ORD-COGS-1",
            receipt_id="receipt-cogs-1",
            quantity=1,
        )
        waiting = await prepare_inventory_cogs_post(
            self.db,
            owner=self.owner,
            consumption_event_id="consume-cogs-1",
        )
        self.assertEqual(waiting["state"], "waiting")
        self.assertEqual(waiting["reasons"], ["sale_recognition_required"])

        sale = await self.recognize_synthetic_sale(order_number="ORD-COGS-1")
        ready = await prepare_inventory_cogs_post(
            self.db,
            owner=self.owner,
            consumption_event_id="consume-cogs-1",
        )
        self.assertEqual(ready["state"], "eligible")
        self.assertEqual(ready["facts"]["total_cost_halalas"], 10000)
        self.assertEqual(ready["facts"]["total_cost"], "100.00")
        self.assertEqual(
            ready["facts"]["sale_txn_group_id"],
            sale["txn_group_id"],
        )

        result = await self.tx(lambda scoped: post_inventory_cogs(
            scoped,
            owner=self.owner,
            actor=self.actor,
            consumption_event_id="consume-cogs-1",
            reason="مطابقة تكلفة البضاعة المباعة مع البيع",
        ))
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
                    round(float(row["amount"]), 2),
                )
                for row in legs
            },
            {
                ("expense", "cogs", None, "debit", 100.00),
                ("asset", "inventory", "inventory", "credit", 100.00),
            },
        )
        self.assertTrue(all(
            (row.get("metadata") or {}).get("sale_recognition_txn_group_id")
            == sale["txn_group_id"]
            for row in legs
        ))
        duplicate = await self.tx(lambda scoped: post_inventory_cogs(
            scoped,
            owner=self.owner,
            actor=self.actor,
            consumption_event_id="consume-cogs-1",
            reason="إعادة آمنة",
        ))
        self.assertEqual(duplicate["state"], "already_posted")
        self.assertEqual(duplicate["txn_group_id"], result["txn_group_id"])

        position = await mz2_financial_position(self.db, owner=self.owner)
        self.assertAlmostEqual(position["assets"]["inventory"], 200.0)

    async def test_cogs_allocates_final_receipt_remainder_without_overstating_cost(self):
        await self.activate_full_p03()
        invoice = await self.create_purchase_invoice(
            request_id="REQ-P03-COGS-REMAINDER",
        )
        line_id = invoice["lines"][0]["id"]
        await self.add_inventory_receipt(
            receipt_id="receipt-cogs-r",
            purchase_invoice_id=invoice["id"],
            line_id=line_id,
            quantity=3,
            posted_at="2026-09-21T10:00:00+00:00",
        )
        await self.tx(lambda scoped: post_inventory_receipt(
            scoped,
            owner=self.owner,
            actor=self.actor,
            receipt_id="receipt-cogs-r",
            reason="إثبات مخزون لاختبار توزيع COGS",
        ))
        costs = []
        for index, quantity in enumerate((1, 2), start=1):
            order_number = f"ORD-COGS-R-{index}"
            event_id = f"consume-cogs-r-{index}"
            await self.add_inventory_consumption(
                event_id=event_id,
                order_number=order_number,
                receipt_id="receipt-cogs-r",
                quantity=quantity,
                consumed_at=f"2026-09-21T1{index}:00:00+00:00",
            )
            await self.recognize_synthetic_sale(
                order_number=order_number,
                recognized_at=f"2026-09-21T1{index+2}:00:00+00:00",
            )
            preview = await prepare_inventory_cogs_post(
                self.db,
                owner=self.owner,
                consumption_event_id=event_id,
            )
            costs.append(preview["facts"]["total_cost_halalas"])
            await self.tx(lambda scoped, event_id=event_id: post_inventory_cogs(
                scoped,
                owner=self.owner,
                actor=self.actor,
                consumption_event_id=event_id,
                reason="توزيع تكلفة الدفعة",
            ))
        self.assertEqual(costs, [10000, 20000])
        self.assertEqual(sum(costs), 30000)
        position = await mz2_financial_position(self.db, owner=self.owner)
        self.assertAlmostEqual(position["assets"]["inventory"], 0.0)

    async def test_cogs_refuses_mutable_catalog_cost_and_waits_for_receipt_cost_basis(self):
        await self.activate_full_p03()
        await self.db.mezan_inventory_consumption_events_v2.insert_one({
            "id": "consume-no-receipt",
            "user_id": self.owner,
            "order_number": "ORD-NO-RECEIPT",
            "batch_id": "batch-no-receipt",
            "status": "consumed",
            "allocations": [{
                "location_id": "LOC-OPENING",
                "receipt_id": None,
                "item_index": 0,
                "quantity": 1,
                "sku": "SKU-OPENING",
            }],
            "consumed_at": "2026-09-21T14:00:00+00:00",
        })
        await self.db.product_costs.insert_one({
            "user_id": self.owner,
            "sku": "SKU-OPENING",
            "product_name": "Mutable current cost must not be used",
            "cost_price": 999,
            "currency": "SAR",
            "is_active": True,
        })
        await self.recognize_synthetic_sale(order_number="ORD-NO-RECEIPT")
        preview = await prepare_inventory_cogs_post(
            self.db,
            owner=self.owner,
            consumption_event_id="consume-no-receipt",
        )
        self.assertEqual(preview["state"], "waiting")
        self.assertEqual(
            preview["reasons"],
            ["inventory_cost_basis_missing"],
        )
        before = await self.db.general_ledger.count_documents({
            "entry_type": "inventory_cogs",
        })
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: post_inventory_cogs(
                scoped,
                owner=self.owner,
                actor=self.actor,
                consumption_event_id="consume-no-receipt",
                reason="يجب ألا يستخدم تكلفة الكتالوج الحالية",
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "p03_cogs_not_eligible")
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "entry_type": "inventory_cogs",
            }),
            before,
        )


    async def test_opening_inventory_snapshot_must_equal_gl_and_drives_cogs(self):
        workspace, approved = await self.activate_p03_with_opening_inventory()
        self.assertEqual(workspace["opening_inventory_halalas"], 30000)
        self.assertEqual(approved["total_cost_halalas"], 30000)

        await self.add_inventory_consumption(
            event_id="consume-opening-lot",
            order_number="ORD-OPENING-LOT",
            receipt_id=None,
            lot_id="opening-lot-1",
            location_id="LOC-OPENING",
            item_index=0,
            quantity=1,
        )
        await self.db.product_costs.insert_one({
            "user_id": self.owner,
            "sku": "SKU-OPENING",
            "product_name": "Mutable catalogue cost",
            "cost_price": 999,
            "currency": "SAR",
            "is_active": True,
        })
        sale = await self.recognize_synthetic_sale(
            order_number="ORD-OPENING-LOT",
        )
        preview = await prepare_inventory_cogs_post(
            self.db,
            owner=self.owner,
            consumption_event_id="consume-opening-lot",
        )
        self.assertEqual(preview["state"], "eligible")
        self.assertEqual(preview["facts"]["total_cost"], "100.00")
        self.assertEqual(
            preview["facts"]["cost_allocations"][0]["source_kind"],
            "opening_inventory_snapshot",
        )
        self.assertEqual(
            preview["facts"]["cost_allocations"][0]["target_key"],
            "lot:opening-lot-1",
        )
        self.assertEqual(
            preview["facts"]["cost_allocations"][0]["evidence_ref"],
            "SYN-OPENING-INVENTORY-COST-SHEET",
        )
        self.assertEqual(
            preview["facts"]["sale_txn_group_id"],
            sale["txn_group_id"],
        )

        posted = await self.tx(lambda scoped: post_inventory_cogs(
            scoped,
            owner=self.owner,
            actor=self.actor,
            consumption_event_id="consume-opening-lot",
            reason="Opening lot COGS matched to recognized sale",
        ))
        self.assertEqual(posted["facts"]["total_cost"], "100.00")
        position = await mz2_financial_position(self.db, owner=self.owner)
        self.assertAlmostEqual(position["assets"]["inventory"], 200.0)

    async def test_opening_inventory_snapshot_rejects_total_not_equal_to_gl(self):
        await self.db.warehouse_locations.insert_one({
            "id": "LOC-OPENING-BAD",
            "code": "OPEN-BAD",
            "warehouse_id": "WH-OPENING",
            "user_id": self.owner,
            "state": "occupied",
            "occupancy": {
                "total_quantity": 3,
                "items": [{
                    "receipt_id": None,
                    "product_id": "SALLA-OPENING",
                    "mezan_product_id": "MZP-OPENING",
                    "product_name": "Opening inventory product",
                    "sku": "SKU-OPENING",
                    "quantity": 3,
                    "lot_id": "opening-lot-bad",
                    "configuration_key": "opening-default",
                    "placed_at": "2026-09-19T18:00:00+00:00",
                }],
            },
        })
        preview = await self.tx(lambda scoped: create_opening_preview(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=self.opening(extra_lines=[
                OpeningLineIn(
                    category="inventory_asset",
                    entity_id="inventory",
                    amount="300.00",
                ),
            ]),
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
                activation_ref="SYN-P01-BAD-OPENING-COST",
                confirmation="ACTIVATE_MZ2_P01",
            ),
        ))
        await self.tx(lambda scoped: activate_p02(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=P02ActivateIn(
                activation_ref="SYN-P02-BAD-OPENING-COST",
                confirmation="ACTIVATE_MZ2_P02",
            ),
        ))
        workspace = await opening_inventory_cost_workspace(
            self.db,
            owner=self.owner,
        )
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: approve_opening_inventory_cost_snapshot(
                scoped,
                owner=self.owner,
                actor=self.actor,
                payload=P03OpeningInventoryCostApproveIn(
                    inventory_fingerprint=workspace["inventory_fingerprint"],
                    evidence_ref="SYN-BAD-COST-SHEET",
                    reason="Mismatch must fail",
                    lines=[
                        P03OpeningInventoryCostLineIn(
                            target_key="lot:opening-lot-bad",
                            unit_cost="90.00",
                        ),
                    ],
                ),
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(
            ctx.exception.detail["code"],
            "p03_opening_inventory_cost_total_mismatch",
        )
        self.assertEqual(ctx.exception.detail["opening_inventory_halalas"], 30000)
        self.assertEqual(ctx.exception.detail["snapshot_total_halalas"], 27000)

        with self.assertRaises(HTTPException) as activation:
            await self.tx(lambda scoped: activate_p03(
                scoped,
                owner=self.owner,
                actor=self.actor,
                payload=P03ActivateIn(
                    activation_ref="SYN-P03-MUST-NOT-ACTIVATE",
                    confirmation="ACTIVATE_MZ2_P03",
                ),
            ))
        self.assertEqual(activation.exception.status_code, 409)
        self.assertEqual(
            activation.exception.detail["code"],
            "p03_activation_opening_inventory_cost_snapshot_required",
        )


if __name__ == "__main__":
    unittest.main()
