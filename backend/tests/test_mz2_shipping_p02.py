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
    post_store_driver_fee,
    process_pending_store_driver_accounting,
    save_shipping_rate,
)
from accounting_shipping_settlements import (
    ShippingSettlementIn,
    post_shipping_settlement,
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

    async def add_movement(
        self,
        *,
        movement_id,
        direction,
        amount,
        reference,
        movement_date="2026-09-21",
    ):
        await self.db.mz2_daily_movements.insert_one({
            "_id": "ROW-" + movement_id,
            "id": movement_id,
            "user_id": self.owner,
            "file_id": "SYN-BANK-FILE",
            "file_hash": "SYN-HASH",
            "row_no": 1,
            "movement_date": movement_date,
            "direction": direction,
            "amount": f"{amount:.2f}",
            "currency": "SAR",
            "description": reference,
            "reference": reference,
            "bank_account_id": "bank-main",
            "bank_account_name": "Synthetic bank",
            "explicit_provider": None,
            "suggested_provider": None,
            "status": "unclassified",
            "receipt_id": None,
            "created_at": "2026-09-21T10:00:00+00:00",
            "source": "bank_statement_import",
        })

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

    async def test_non_cash_or_mismatched_cod_never_posts_sale_but_fee_is_independent(self):
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
        revenue_before = await self.db.general_ledger.count_documents({
            "entity_type": "revenue",
        })
        cod_before = await self.db.general_ledger.count_documents({
            "entity_type": "store_driver",
            "sub_account": "cod_receivable",
        })
        with self.assertRaises(ShippingAccountingError):
            await post_store_driver_cod(
                self.db,
                owner=self.owner,
                actor=self.actor,
                assignment_id="ASSIGN-BLOCK-1",
            )
        self.assertEqual(
            await self.db.general_ledger.count_documents({"entity_type": "revenue"}),
            revenue_before,
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "entity_type": "store_driver",
                "sub_account": "cod_receivable",
            }),
            cod_before,
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "metadata.assignment_id": "ASSIGN-BLOCK-1",
                "entry_type": "shipping_fee_accrual",
            }),
            2,
        )

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
        self.assertEqual(
            await self.db.general_ledger.count_documents({"entity_type": "revenue"}),
            revenue_before,
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "entity_type": "store_driver",
                "sub_account": "cod_receivable",
            }),
            cod_before,
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "metadata.assignment_id": "ASSIGN-BLOCK-2",
                "entry_type": "shipping_fee_accrual",
            }),
            2,
        )

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

    async def test_driver_net_settlement_uses_one_inbound_bank_row_and_clears_both_balances(self):
        await self.add_driver_cod(
            assignment="ASSIGN-NET",
            order="ORD-NET",
            amount=150,
            fee=20,
        )
        cod = await post_store_driver_cod(
            self.db,
            owner=self.owner,
            actor=self.actor,
            assignment_id="ASSIGN-NET",
        )
        revenue_before = await self.db.general_ledger.count_documents({
            "entity_type": "revenue",
        })
        expense_before = await self.db.general_ledger.count_documents({
            "entity_type": "expense",
        })
        await self.add_movement(
            movement_id="MOVE-NET",
            direction="in",
            amount=130,
            reference="DRIVER-NET-130",
        )
        payload = ShippingSettlementIn(
            movement_id="MOVE-NET",
            counterparty_type="store_driver",
            counterparty_id="driver-1",
            settlement_type="net_settlement",
            offset_amount="20",
            reason="Synthetic driver net COD settlement",
        )
        settled = await post_shipping_settlement(
            self.db,
            owner=self.owner,
            actor=self.actor,
            payload=payload,
        )
        self.assertEqual(settled["state"], "posted")
        self.assertEqual(settled["gross_cod_cleared"], "150.00")
        self.assertEqual(settled["payable_cleared"], "20.00")

        legs = await self.db.general_ledger.find(
            {"txn_group_id": settled["txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {(row["entity_type"], row["entity_id"], row.get("sub_account"), row["side"], row["amount"])
             for row in legs},
            {
                ("bank", "bank-main", "main", "debit", 130.0),
                ("store_driver", "driver-1", "delivery_fee_payable", "debit", 20.0),
                ("store_driver", "driver-1", "cod_receivable", "credit", 150.0),
            },
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({"entity_type": "revenue"}),
            revenue_before,
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({"entity_type": "expense"}),
            expense_before,
        )
        fp = await mz2_financial_position(self.db, owner=self.owner)
        self.assertEqual(fp["assets"]["banks"], 1130.0)
        self.assertEqual(fp["assets"]["store_driver_cod_receivable"], 0.0)
        self.assertEqual(fp["liabilities"]["store_driver_payable"], 0.0)

        retry = await post_shipping_settlement(
            self.db,
            owner=self.owner,
            actor=self.actor,
            payload=payload,
        )
        self.assertEqual(retry["state"], "already_posted")
        self.assertEqual(retry["txn_group_id"], settled["txn_group_id"])
        self.assertTrue(cod["sale_txn_group_id"])

    async def test_courier_fee_payment_consumes_outbound_bank_row_without_second_expense(self):
        await self.add_courier_order(
            order="ORD-COURIER-PAY",
            evidence_id="E-COURIER-PAY",
            waybill="WB-COURIER-PAY",
        )
        accrual = await post_courier_fee(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id="E-COURIER-PAY",
        )
        expense_before = await self.db.general_ledger.count_documents({
            "entity_type": "expense",
        })
        await self.add_movement(
            movement_id="MOVE-COURIER-PAY",
            direction="out",
            amount=17.25,
            reference="IMILE-INVOICE-PAYMENT",
        )
        payload = ShippingSettlementIn(
            movement_id="MOVE-COURIER-PAY",
            counterparty_type="courier",
            counterparty_id="imile",
            settlement_type="fee_payment",
            reason="Synthetic iMile invoice payment",
        )
        settled = await post_shipping_settlement(
            self.db,
            owner=self.owner,
            actor=self.actor,
            payload=payload,
        )
        self.assertEqual(settled["state"], "posted")
        legs = await self.db.general_ledger.find(
            {"txn_group_id": settled["txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {(row["entity_type"], row["entity_id"], row.get("sub_account"), row["side"], row["amount"])
             for row in legs},
            {
                ("courier", "imile", "payable", "debit", 17.25),
                ("bank", "bank-main", "main", "credit", 17.25),
            },
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({"entity_type": "expense"}),
            expense_before,
        )
        fp = await mz2_financial_position(self.db, owner=self.owner)
        self.assertEqual(fp["liabilities"]["courier_payable"], 0.0)
        self.assertEqual(fp["assets"]["banks"], 982.75)
        self.assertTrue(accrual["txn_group_id"])

    async def test_shipping_movement_cannot_be_consumed_twice_or_over_settle(self):
        await self.add_driver_cod(
            assignment="ASSIGN-DOUBLE",
            order="ORD-DOUBLE",
            amount=100,
            fee=20,
        )
        await post_store_driver_cod(
            self.db,
            owner=self.owner,
            actor=self.actor,
            assignment_id="ASSIGN-DOUBLE",
        )
        await self.add_movement(
            movement_id="MOVE-DOUBLE",
            direction="in",
            amount=80,
            reference="DRIVER-NET-80",
        )
        first_payload = ShippingSettlementIn(
            movement_id="MOVE-DOUBLE",
            counterparty_type="store_driver",
            counterparty_id="driver-1",
            settlement_type="net_settlement",
            offset_amount="20",
            reason="Synthetic driver net settlement",
        )
        await post_shipping_settlement(
            self.db,
            owner=self.owner,
            actor=self.actor,
            payload=first_payload,
        )
        other_payload = ShippingSettlementIn(
            movement_id="MOVE-DOUBLE",
            counterparty_type="courier",
            counterparty_id="imile",
            settlement_type="cod_remittance",
            reason="Try reusing consumed bank evidence",
        )
        with self.assertRaises(ShippingAccountingError) as consumed:
            await post_shipping_settlement(
                self.db,
                owner=self.owner,
                actor=self.actor,
                payload=other_payload,
            )
        self.assertEqual(str(consumed.exception), "daily_movement_already_consumed")

        await self.add_movement(
            movement_id="MOVE-OVER",
            direction="in",
            amount=1,
            reference="OVER-SETTLE",
        )
        over_payload = ShippingSettlementIn(
            movement_id="MOVE-OVER",
            counterparty_type="store_driver",
            counterparty_id="driver-1",
            settlement_type="cod_remittance",
            reason="Try excess remittance after COD cleared",
        )
        with self.assertRaises(HTTPException) as over:
            await post_shipping_settlement(
                self.db,
                owner=self.owner,
                actor=self.actor,
                payload=over_payload,
            )
        self.assertEqual(
            over.exception.detail["code"],
            "shipping_cod_remittance_exceeds_receivable",
        )
        move = await self.db.mz2_daily_movements.find_one(
            {"id": "MOVE-OVER"},
            {"_id": 0},
        )
        self.assertEqual(move["status"], "unclassified")

    async def test_closed_period_rolls_back_shipping_payment_and_bank_evidence_consumption(self):
        await self.add_courier_order(
            order="ORD-CLOSED-PAY",
            evidence_id="E-CLOSED-PAY",
            waybill="WB-CLOSED-PAY",
        )
        await post_courier_fee(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id="E-CLOSED-PAY",
        )
        await self.add_movement(
            movement_id="MOVE-CLOSED-PAY",
            direction="out",
            amount=17.25,
            reference="CLOSED-COURIER-PAY",
        )
        await set_period(
            self.db,
            self.owner,
            self.owner,
            PeriodChange(
                month="2026-09",
                closed=True,
                revision=0,
                reason="Synthetic close before courier payment",
                evidence_ref="Synthetic close approval",
            ),
        )
        before = await self.db.general_ledger.count_documents({})
        payload = ShippingSettlementIn(
            movement_id="MOVE-CLOSED-PAY",
            counterparty_type="courier",
            counterparty_id="imile",
            settlement_type="fee_payment",
            reason="Payment in closed month",
        )
        with self.assertRaises(HTTPException) as denied:
            await post_shipping_settlement(
                self.db,
                owner=self.owner,
                actor=self.actor,
                payload=payload,
            )
        self.assertEqual(denied.exception.detail["code"], "accounting_period_closed")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        movement = await self.db.mz2_daily_movements.find_one(
            {"id": "MOVE-CLOSED-PAY"},
            {"_id": 0},
        )
        self.assertEqual(movement["status"], "unclassified")
        self.assertFalse(movement.get("accounting_event_id"))


    async def test_non_cash_delivery_posts_fee_only_without_salla_order_or_revenue(self):
        await self.db.store_delivery_collections.insert_one({
            "id": "COLL-NONCASH",
            "user_id": self.owner,
            "assignment_id": "ASSIGN-NONCASH",
            "order_id": "ORD-NONCASH",
            "order_number": "ORD-NONCASH",
            "driver_id": "driver-1",
            "amount": 200,
            "amount_source": "unified_orders.remaining_amount",
            "payment_method": "card_terminal",
            "cod_custody_amount": 0,
            "review_status": "pending_accountant_review",
            "accounting_status": "pending",
            "collected_at": "2026-09-21T10:00:00+00:00",
        })
        await self.db.store_delivery_driver_earnings.insert_one({
            "id": "EARN-NONCASH",
            "user_id": self.owner,
            "assignment_id": "ASSIGN-NONCASH",
            "order_id": "ORD-NONCASH",
            "order_number": "ORD-NONCASH",
            "driver_id": "driver-1",
            "amount": 20,
            "status": "due",
            "accounting_status": "pending",
            "earned_at": "2026-09-21T10:00:00+00:00",
        })
        before_revenue = await self.db.general_ledger.count_documents({
            "entity_type": "revenue",
        })
        result = await post_store_driver_fee(
            self.db,
            owner=self.owner,
            actor=self.actor,
            assignment_id="ASSIGN-NONCASH",
        )
        self.assertEqual(result["state"], "posted")
        legs = await self.db.general_ledger.find(
            {"txn_group_id": result["txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {(row["entity_type"], row["entity_id"], row.get("sub_account"), row["side"], row["amount"])
             for row in legs},
            {
                ("expense", "store_delivery", None, "debit", 20.0),
                ("store_driver", "driver-1", "delivery_fee_payable", "credit", 20.0),
            },
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({"entity_type": "revenue"}),
            before_revenue,
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "txn_group_id": result["txn_group_id"],
                "entity_type": "tax",
            }),
            0,
        )

    async def test_pending_processor_waits_for_cod_order_evidence_then_posts_idempotently(self):
        assignment = "ASSIGN-PENDING-COD"
        order = "ORD-PENDING-COD"
        await self.db.store_delivery_collections.insert_one({
            "id": "COLL-" + assignment,
            "user_id": self.owner,
            "assignment_id": assignment,
            "order_id": order,
            "order_number": order,
            "driver_id": "driver-1",
            "amount": 115,
            "amount_source": "unified_orders.remaining_amount",
            "payment_method": "cash",
            "cod_custody_amount": 115,
            "review_status": "not_required",
            "accounting_status": "pending",
            "mz2_p02_accounting_status": "pending_evidence",
            "collected_at": "2026-09-21T10:00:00+00:00",
        })
        await self.db.store_delivery_driver_earnings.insert_one({
            "id": "EARN-" + assignment,
            "user_id": self.owner,
            "assignment_id": assignment,
            "order_id": order,
            "order_number": order,
            "driver_id": "driver-1",
            "amount": 20,
            "status": "due",
            "accounting_status": "pending",
            "mz2_p02_accounting_status": "pending_evidence",
            "earned_at": "2026-09-21T10:00:00+00:00",
        })

        dry = await process_pending_store_driver_accounting(
            self.db,
            owner=self.owner,
            actor=self.actor,
            limit=20,
            dry_run=True,
        )
        row = next(item for item in dry["items"] if item["assignment_id"] == assignment)
        self.assertEqual(row["state"], "waiting")
        self.assertEqual(row["reasons"], ["unique_cod_order_evidence_required"])
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "metadata.assignment_id": assignment,
            }),
            0,
        )

        evidence_id = "E-" + order
        await self.db.mz2_salla_order_evidence.insert_one({
            "_id": evidence_id,
            "id": evidence_id,
            "user_id": self.owner,
            "order_number": order,
            "conflict": False,
            "accounting_provider": "cod",
            "status": "waiting_p02_cod",
            "current_net_sar": "115.00",
            "refunded_sar": "0.00",
            "source_tax_sar": "8.52",
            "source": "salla_orders_export",
        })

        executed = await process_pending_store_driver_accounting(
            self.db,
            owner=self.owner,
            actor=self.actor,
            limit=20,
            dry_run=False,
        )
        row = next(item for item in executed["items"] if item["assignment_id"] == assignment)
        self.assertEqual(row["state"], "posted")
        self.assertTrue(row["sale_txn_group_id"])
        self.assertTrue(row["fee_txn_group_id"])

        again = await process_pending_store_driver_accounting(
            self.db,
            owner=self.owner,
            actor=self.actor,
            limit=20,
            dry_run=False,
        )
        self.assertFalse(any(
            item["assignment_id"] == assignment
            and item["state"] == "posted"
            for item in again["items"]
        ))
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "metadata.assignment_id": assignment,
                "entity_type": "revenue",
            }),
            1,
        )


if __name__ == "__main__":
    unittest.main()
