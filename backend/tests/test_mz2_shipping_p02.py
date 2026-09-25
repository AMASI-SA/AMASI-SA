"""Real-replica contract for isolated MZ2 P02 shipping/COD accounting."""
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_module_opening_balances import (
    OpeningActivateIn,
    OpeningApproveIn,
    OpeningLineIn,
    OpeningPreviewIn,
    activate_p01,
    approve_opening_preview,
    create_opening_preview,
)
from accounting_mz2_reports import mz2_financial_position, read_mz2_ledger
from accounting_periods import PeriodChange, set_period
from accounting_sales_tax_service import save_policy
from accounting_shipping_p02 import (
    ShippingAccountingError,
    ShippingRateInput,
    post_courier_fee,
    post_store_driver_cod,
    save_shipping_rate,
)
from accounting_atomic import atomic_owner


class MZ2ShippingP02Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_shipping_p02_" + uuid4().hex]
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
        await self.db.store_drivers.insert_one({
            "id": "driver-1",
            "user_id": self.owner,
            "name": "Synthetic driver",
            "status": "active",
            "delivery_fee": 20,
        })

        await save_shipping_rate(
            self.db,
            owner=self.owner,
            actor=self.actor,
            payload=ShippingRateInput(
                courier_id="imile",
                name="iMile",
                aliases=["iMile للتوصيل", "iMile"],
                total_fee="17.25",
                effective_at="2026-09-01T00:00:00+03:00",
                evidence_ref="SYN-I-MILE-CONTRACT",
                revision=0,
                reason="Synthetic UAT approved rate",
            ),
        )
        await save_shipping_rate(
            self.db,
            owner=self.owner,
            actor=self.actor,
            payload=ShippingRateInput(
                courier_id="smsa",
                name="SMSA",
                aliases=["سمسا", "SMSA"],
                total_fee="17.25",
                effective_at="2026-09-01T00:00:00+03:00",
                evidence_ref="SYN-SMSA-CONTRACT",
                revision=1,
                reason="Synthetic UAT approved rate",
            ),
        )
        await self._open_activate()
        await save_policy(
            self.db,
            owner=self.owner,
            actor_id=self.owner,
            rate="15",
            effective_at="2026-09-01T00:00:00+03:00",
            revision=0,
            reason="Synthetic UAT sales tax",
        )
        await self.db.settings.update_one(
            {"user_id": self.owner},
            {"$set": {
                "mezan2_financial_cutover.p02_shipping_cod_enabled": True,
                "mezan2_financial_cutover.p02_shipping_cod_activation_ref": "SYN-P02-UAT",
            }},
        )

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def tx(self, callback):
        return await atomic_owner(self.db, self.owner, callback)

    async def _open_activate(self):
        refs = {
            "banks_cash": "SYN-BANKS",
            "providers": "SYN-PROVIDERS-ZERO",
            "couriers_cod": "SYN-COURIERS-DRIVERS-ZERO",
            "inventory": "SYN-INVENTORY-ZERO",
            "suppliers": "SYN-SUPPLIERS-ZERO",
            "payroll_obligations": "SYN-PAYROLL-ZERO",
            "equity": "SYN-EQUITY",
        }
        opening = OpeningPreviewIn(
            cutover_at="2026-09-20T00:00:00+03:00",
            evidence_sheet_ref="SYN-OPENING",
            evidence_sections=refs,
            lines=[
                OpeningLineIn(
                    category="bank",
                    entity_id="bank-main",
                    amount="1000",
                ),
            ],
        )
        preview = await self.tx(lambda scoped: create_opening_preview(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=opening,
        ))
        zeros = {
            (row["entity_type"], row["entity_id"], row["sub_account"])
            for row in preview["zero_scope"]
        }
        self.assertIn(("courier", "imile", "payable"), zeros)
        self.assertIn(("courier", "imile", "cod_receivable"), zeros)
        self.assertIn(("courier", "smsa", "payable"), zeros)
        self.assertIn(("store_driver", "driver-1", "cod_receivable"), zeros)
        self.assertIn(("store_driver", "driver-1", "delivery_fee_payable"), zeros)

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

    async def add_courier_order(
        self,
        order="ORD-IMILE-1",
        evidence_id="E-IMILE-1",
        waybill="WB-IMILE-1",
        company="iMile للتوصيل",
        salla_shipping_charge="24.07",
    ):
        await self.db.mz2_salla_order_evidence.insert_one({
            "_id": evidence_id,
            "id": evidence_id,
            "user_id": self.owner,
            "order_number": order,
            "conflict": False,
            "delivery_source_text": "2026-09-21 10:00:00",
            "shipping_company": company,
            "waybill": waybill,
            "shipping_cost_source": salla_shipping_charge,
            "source": "salla_orders_export",
        })

    async def add_driver_cod(
        self,
        *,
        assignment="ASSIGN-COD-1",
        order="ORD-COD-1",
        amount=150,
        fee=20,
        collected_at="2026-09-21T10:00:00+00:00",
    ):
        evidence_id = "E-" + order
        await self.db.mz2_salla_order_evidence.insert_one({
            "_id": evidence_id,
            "id": evidence_id,
            "user_id": self.owner,
            "order_number": order,
            "conflict": False,
            "accounting_provider": "cod",
            "status": "waiting_p02_cod",
            "current_net_sar": f"{amount:.2f}",
            "refunded_sar": "0.00",
            "source_tax_sar": "8.52",
            "source": "salla_orders_export",
        })
        await self.db.store_delivery_collections.insert_one({
            "id": "COLL-" + assignment,
            "user_id": self.owner,
            "assignment_id": assignment,
            "order_id": order,
            "order_number": order,
            "driver_id": "driver-1",
            "amount": amount,
            "amount_source": "unified_orders.remaining_amount",
            "payment_method": "cash",
            "cod_custody_amount": amount,
            "review_status": "not_required",
            "accounting_status": "pending",
            "collected_at": collected_at,
        })
        await self.db.store_delivery_driver_earnings.insert_one({
            "id": "EARN-" + assignment,
            "user_id": self.owner,
            "assignment_id": assignment,
            "order_id": order,
            "order_number": order,
            "driver_id": "driver-1",
            "amount": fee,
            "status": "due",
            "accounting_status": "pending",
            "earned_at": collected_at,
        })
        return evidence_id

    async def test_external_courier_fee_uses_verified_rate_not_salla_charge_and_no_revenue(self):
        await self.add_courier_order()
        before = await self.db.general_ledger.count_documents({})
        result = await post_courier_fee(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id="E-IMILE-1",
        )
        self.assertEqual(result["state"], "posted")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 2)

        legs = await self.db.general_ledger.find(
            {"txn_group_id": result["txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {(row["entity_type"], row["entity_id"], row.get("sub_account"), row["side"], row["amount"])
             for row in legs},
            {
                ("expense", "shipping", None, "debit", 17.25),
                ("courier", "imile", "payable", "credit", 17.25),
            },
        )
        self.assertTrue(all(
            row["metadata"]["salla_shipping_charge_for_review"] == "24.07"
            and row["metadata"]["tax_treatment"] == "gross_expense_no_input_vat"
            for row in legs
        ))
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "txn_group_id": result["txn_group_id"],
                "entity_type": {"$in": ["revenue", "tax"]},
            }),
            0,
        )

        retry = await post_courier_fee(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id="E-IMILE-1",
        )
        self.assertEqual(retry["state"], "already_posted")
        self.assertEqual(retry["txn_group_id"], result["txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 2)

    async def test_store_driver_cash_cod_posts_sale_once_and_fee_separately(self):
        await self.add_driver_cod()
        before = await self.db.general_ledger.count_documents({})
        result = await post_store_driver_cod(
            self.db,
            owner=self.owner,
            actor=self.actor,
            assignment_id="ASSIGN-COD-1",
        )
        self.assertEqual(result["state"], "posted")
        self.assertEqual(result["tax"]["gross"], "150.00")
        self.assertEqual(result["tax"]["net"], "130.43")
        self.assertEqual(result["tax"]["tax"], "19.57")
        self.assertNotEqual(result["sale_txn_group_id"], result["fee_txn_group_id"])

        sale = await self.db.general_ledger.find(
            {"txn_group_id": result["sale_txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {(row["entity_type"], row["entity_id"], row.get("sub_account"), row["side"], row["amount"])
             for row in sale},
            {
                ("store_driver", "driver-1", "cod_receivable", "debit", 150.0),
                ("revenue", "bnpl_sales", None, "credit", 130.43),
                ("tax", "sales_vat_payable", None, "credit", 19.57),
            },
        )
        fee = await self.db.general_ledger.find(
            {"txn_group_id": result["fee_txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {(row["entity_type"], row["entity_id"], row.get("sub_account"), row["side"], row["amount"])
             for row in fee},
            {
                ("expense", "store_delivery", None, "debit", 20.0),
                ("store_driver", "driver-1", "delivery_fee_payable", "credit", 20.0),
            },
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "metadata.order_reference_id": "ORD-COD-1",
                "entity_type": "revenue",
            }),
            1,
        )
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 5)

        current = await self.db.mz2_salla_order_evidence.find_one(
            {"id": "E-ORD-COD-1"},
            {"_id": 0},
        )
        self.assertEqual(current["status"], "recognized_cod")
        self.assertEqual(current["recognized_gross_sar"], "150.00")

        retry = await post_store_driver_cod(
            self.db,
            owner=self.owner,
            actor=self.actor,
            assignment_id="ASSIGN-COD-1",
        )
        self.assertEqual(retry["state"], "already_posted")
        self.assertEqual(retry["sale_txn_group_id"], result["sale_txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 5)

        scope = await read_mz2_ledger(self.db, owner=self.owner)
        self.assertEqual(scope["status"], "available", scope)
        fp = await mz2_financial_position(self.db, owner=self.owner)
        self.assertEqual(fp["status"], "available", fp)
        self.assertEqual(fp["assets"]["store_driver_cod_receivable"], 150.0)
        self.assertEqual(fp["liabilities"]["store_driver_payable"], 20.0)

    async def test_non_cash_or_mismatched_cod_never_posts(self):
        await self.add_driver_cod(
            assignment="ASSIGN-BLOCK-1",
            order="ORD-BLOCK-1",
            amount=100,
        )
        await self.db.store_delivery_collections.update_one(
            {"assignment_id": "ASSIGN-BLOCK-1"},
            {"$set": {
                "payment_method": "card_terminal",
                "cod_custody_amount": 0,
                "review_status": "pending_accountant_review",
            }},
        )
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(ShippingAccountingError):
            await post_store_driver_cod(
                self.db,
                owner=self.owner,
                actor=self.actor,
                assignment_id="ASSIGN-BLOCK-1",
            )
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)

        await self.add_driver_cod(
            assignment="ASSIGN-BLOCK-2",
            order="ORD-BLOCK-2",
            amount=100,
        )
        await self.db.store_delivery_collections.update_one(
            {"assignment_id": "ASSIGN-BLOCK-2"},
            {"$set": {"cod_custody_amount": 99}},
        )
        with self.assertRaises(ShippingAccountingError):
            await post_store_driver_cod(
                self.db,
                owner=self.owner,
                actor=self.actor,
                assignment_id="ASSIGN-BLOCK-2",
            )
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)

    async def test_p02_lock_blocks_financial_write_without_partial_event(self):
        await self.add_courier_order(
            order="ORD-LOCK",
            evidence_id="E-LOCK",
            waybill="WB-LOCK",
        )
        await self.db.settings.update_one(
            {"user_id": self.owner},
            {"$set": {"mezan2_financial_cutover.p02_shipping_cod_enabled": False}},
        )
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as denied:
            await post_courier_fee(
                self.db,
                owner=self.owner,
                actor=self.actor,
                evidence_id="E-LOCK",
            )
        self.assertEqual(denied.exception.status_code, 423)
        self.assertEqual(denied.exception.detail["code"], "p02_shipping_cod_locked")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        self.assertEqual(
            await self.db.mz2_shipping_accounting_events.count_documents({
                "facts.order_number": "ORD-LOCK",
            }),
            0,
        )

    async def test_closed_period_rolls_back_cod_sale_fee_and_source_marks(self):
        await self.add_driver_cod(
            assignment="ASSIGN-CLOSED",
            order="ORD-CLOSED",
            amount=115,
            fee=20,
            collected_at="2026-09-21T10:00:00+00:00",
        )
        await set_period(
            self.db,
            self.owner,
            self.owner,
            PeriodChange(
                month="2026-09",
                closed=True,
                revision=0,
                reason="Synthetic close",
                evidence_ref="Synthetic approved close",
            ),
        )
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as denied:
            await post_store_driver_cod(
                self.db,
                owner=self.owner,
                actor=self.actor,
                assignment_id="ASSIGN-CLOSED",
            )
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(denied.exception.detail["code"], "accounting_period_closed")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        self.assertEqual(
            await self.db.mz2_shipping_accounting_events.count_documents({
                "facts.assignment_id": "ASSIGN-CLOSED",
            }),
            0,
        )
        evidence = await self.db.mz2_salla_order_evidence.find_one(
            {"order_number": "ORD-CLOSED"},
            {"_id": 0},
        )
        self.assertEqual(evidence["status"], "waiting_p02_cod")
        collection = await self.db.store_delivery_collections.find_one(
            {"assignment_id": "ASSIGN-CLOSED"},
            {"_id": 0},
        )
        self.assertFalse(collection.get("mz2_p02_event_id"))

    async def test_changed_cod_facts_after_post_conflict_instead_of_second_sale(self):
        await self.add_driver_cod(
            assignment="ASSIGN-CHANGE",
            order="ORD-CHANGE",
            amount=115,
        )
        first = await post_store_driver_cod(
            self.db,
            owner=self.owner,
            actor=self.actor,
            assignment_id="ASSIGN-CHANGE",
        )
        before = await self.db.general_ledger.count_documents({})
        await self.db.store_delivery_driver_earnings.update_one(
            {"assignment_id": "ASSIGN-CHANGE"},
            {"$set": {"amount": 25}},
        )
        with self.assertRaises(ShippingAccountingError) as conflict:
            await post_store_driver_cod(
                self.db,
                owner=self.owner,
                actor=self.actor,
                assignment_id="ASSIGN-CHANGE",
            )
        self.assertEqual(str(conflict.exception), "shipping_event_source_conflict")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        self.assertTrue(first["sale_txn_group_id"])


if __name__ == "__main__":
    unittest.main()
