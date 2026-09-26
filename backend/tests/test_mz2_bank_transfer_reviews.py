"""Real-replica contract for reviewed Salla bank-transfer receipts."""
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_atomic import atomic_owner
from accounting_bank_transfer_reviews import (
    BankTransferReviewError,
    approve_bank_transfer,
    bank_transfer_review_queue,
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
from accounting_mz2_reports import mz2_financial_position
from accounting_periods import PeriodChange, set_period
from accounting_sales_tax_service import save_policy


class MZ2BankTransferReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_bank_transfer_review_" + uuid4().hex]
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
            "name": "مصرف الراجحي",
            "account_type": "bank",
            "status": "active",
        })
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

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def tx(self, callback):
        return await atomic_owner(self.db, self.owner, callback)

    async def _open_activate(self):
        refs = {
            "banks_cash": "SYN-BANK",
            "providers": "SYN-PROVIDERS-ZERO",
            "couriers_cod": "SYN-COURIERS-ZERO",
            "inventory": "SYN-INVENTORY-ZERO",
            "suppliers": "SYN-SUPPLIERS-ZERO",
            "payroll_obligations": "SYN-PAYROLL-ZERO",
            "equity": "SYN-EQUITY",
        }
        preview = await self.tx(lambda scoped: create_opening_preview(
            scoped,
            owner=self.owner,
            actor=self.actor,
            payload=OpeningPreviewIn(
                cutover_at="2026-09-20T00:00:00+03:00",
                evidence_sheet_ref="SYN-OPENING",
                evidence_sections=refs,
                lines=[OpeningLineIn(
                    category="bank",
                    entity_id="bank-main",
                    amount="1000",
                )],
            ),
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
                activation_ref="SYN-BANK-TRANSFER-UAT",
                confirmation="ACTIVATE_MZ2_P01",
            ),
        ))

    async def add_case(
        self,
        *,
        order="ORD-BANK-1",
        amount=115,
        movement_id="MOVE-BANK-1",
        movement_date="2026-09-21",
        delivery="2026-09-21 15:00:00",
        receipt=True,
        bank_name="مصرف الراجحي",
        movement_amount=None,
    ):
        evidence_id = "E-" + order
        await self.db.mz2_salla_order_evidence.insert_one({
            "_id": evidence_id,
            "id": evidence_id,
            "user_id": self.owner,
            "order_number": order,
            "order_status": "تم التوصيل",
            "payment_method_raw": "تحويل بنكي",
            "accounting_provider": "bank_transfer",
            "current_net_sar": f"{amount:.2f}",
            "refunded_sar": "0.00",
            "source_tax_sar": "8.52",
            "delivery_source_text": delivery,
            "status": "needs_bank_transfer_evidence",
            "review_reasons": [],
            "conflict": False,
            "source": "salla_orders_export",
        })
        await self.db.unified_orders.insert_one({
            "id": "U-" + order,
            "user_id": self.owner,
            "order_number": order,
            "payment_method": "bank",
            "receiving_bank_name": bank_name,
            "payment_receipt_url": (
                "https://cdn.salla.sa/example/" + order + ".jpg"
                if receipt else ""
            ),
            "total_amount": amount,
        })
        await self.db.mz2_daily_movements.insert_one({
            "_id": "ROW-" + movement_id,
            "id": movement_id,
            "user_id": self.owner,
            "file_id": "SYN-BANK-FILE",
            "file_hash": "SYN-BANK-HASH",
            "row_no": 1,
            "movement_date": movement_date,
            "direction": "in",
            "amount": f"{(movement_amount if movement_amount is not None else amount):.2f}",
            "currency": "SAR",
            "description": "تحويل عميل " + order,
            "reference": "BANK-" + order,
            "bank_account_id": "bank-main",
            "bank_account_name": "مصرف الراجحي",
            "explicit_provider": None,
            "suggested_provider": None,
            "status": "unclassified",
            "receipt_id": None,
            "source": "bank_statement_import",
        })
        return evidence_id

    async def test_queue_shows_receipt_amount_bank_and_actual_transfer_for_explicit_review(self):
        evidence_id = await self.add_case()
        queue = await bank_transfer_review_queue(
            self.db, owner=self.owner, limit=20
        )
        self.assertEqual(queue["ready_count"], 1)
        row = queue["items"][0]
        self.assertEqual(row["evidence_id"], evidence_id)
        self.assertEqual(row["order_amount"], "115.00")
        self.assertEqual(row["receipt_bank_name"], "مصرف الراجحي")
        self.assertTrue(row["receipt_url"].endswith("ORD-BANK-1.jpg"))
        self.assertEqual(len(row["bank_movements"]), 1)
        self.assertEqual(row["bank_movements"][0]["id"], "MOVE-BANK-1")
        self.assertEqual(row["bank_movements"][0]["amount"], "115.00")
        self.assertEqual(
            row["bank_movements"][0]["bank_account_name"],
            "مصرف الراجحي",
        )

    async def test_approve_posts_advance_then_sale_once_and_consumes_selected_movement(self):
        evidence_id = await self.add_case()
        before = await self.db.general_ledger.count_documents({})
        result = await approve_bank_transfer(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence_id,
            movement_id="MOVE-BANK-1",
        )
        self.assertEqual(result["status"], "posted")
        self.assertTrue(result["advance_txn_group_id"])
        self.assertTrue(result["sale_txn_group_id"])
        self.assertNotEqual(
            result["advance_txn_group_id"],
            result["sale_txn_group_id"],
        )
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 5)

        advance = await self.db.general_ledger.find(
            {"txn_group_id": result["advance_txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {(row["entity_type"], row["sub_account"], row["side"], row["amount"])
             for row in advance},
            {
                ("bank", "main", "debit", 115.0),
                ("liability", "customer_advance", "credit", 115.0),
            },
        )

        sale = await self.db.general_ledger.find(
            {"txn_group_id": result["sale_txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(
            {(row["entity_type"], row["entity_id"], row.get("sub_account"), row["side"], row["amount"])
             for row in sale},
            {
                ("liability", result["id"], "customer_advance", "debit", 115.0),
                ("revenue", "bnpl_sales", None, "credit", 100.0),
                ("tax", "sales_vat_payable", None, "credit", 15.0),
            },
        )
        self.assertTrue(all(
            row["metadata"]["receipt_bank_name"] == "مصرف الراجحي"
            and row["metadata"]["receipt_url"].endswith("ORD-BANK-1.jpg")
            for row in sale
        ))

        movement = await self.db.mz2_daily_movements.find_one(
            {"id": "MOVE-BANK-1"}, {"_id": 0}
        )
        self.assertEqual(movement["status"], "accounting_posted")
        self.assertEqual(
            movement["accounting_action"],
            "bank_transfer_order_sale",
        )
        evidence = await self.db.mz2_salla_order_evidence.find_one(
            {"id": evidence_id}, {"_id": 0}
        )
        self.assertEqual(evidence["status"], "recognized")
        self.assertEqual(evidence["recognized_provider"], "bank_transfer")
        self.assertEqual(evidence["recognized_gross_sar"], "115.00")
        self.assertEqual(evidence["recognized_tax_sar"], "15.00")

        duplicate = await approve_bank_transfer(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence_id,
            movement_id="MOVE-BANK-1",
        )
        self.assertEqual(duplicate["sale_txn_group_id"], result["sale_txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 5)

        fp = await mz2_financial_position(self.db, owner=self.owner)
        self.assertEqual(fp["status"], "available", fp)
        self.assertEqual(fp["assets"]["banks"], 1115.0)
        self.assertEqual(fp["liabilities"]["customer_advance"], 0.0)
        self.assertEqual(fp["liabilities"]["sales_vat_payable"], 15.0)

    async def test_missing_receipt_or_actual_bank_amount_keeps_review_waiting(self):
        await self.add_case(
            order="ORD-NO-RECEIPT",
            movement_id="MOVE-NO-RECEIPT",
            receipt=False,
        )
        await self.add_case(
            order="ORD-AMOUNT-MISMATCH",
            amount=117,
            movement_id="MOVE-AMOUNT-MISMATCH",
            movement_amount=114,
        )
        queue = await bank_transfer_review_queue(
            self.db, owner=self.owner, limit=20
        )
        by_order = {row["order_number"]: row for row in queue["items"]}
        self.assertEqual(by_order["ORD-NO-RECEIPT"]["state"], "waiting")
        self.assertIn(
            "customer_transfer_receipt_missing",
            by_order["ORD-NO-RECEIPT"]["reasons"],
        )
        self.assertEqual(by_order["ORD-AMOUNT-MISMATCH"]["state"], "waiting")
        self.assertIn(
            "matching_bank_movement_missing",
            by_order["ORD-AMOUNT-MISMATCH"]["reasons"],
        )
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "metadata.source": "accounting_bank_transfer_review"
            }),
            0,
        )

    async def test_bank_movement_after_delivery_is_blocked_without_consumption(self):
        evidence_id = await self.add_case(
            order="ORD-LATE-BANK",
            movement_id="MOVE-LATE-BANK",
            movement_date="2026-09-22",
            delivery="2026-09-21 15:00:00",
        )
        with self.assertRaises(BankTransferReviewError) as blocked:
            await approve_bank_transfer(
                self.db,
                owner=self.owner,
                actor=self.actor,
                evidence_id=evidence_id,
                movement_id="MOVE-LATE-BANK",
            )
        self.assertEqual(
            str(blocked.exception),
            "bank_transfer_after_delivery_requires_receivable_workflow",
        )
        movement = await self.db.mz2_daily_movements.find_one(
            {"id": "MOVE-LATE-BANK"}, {"_id": 0}
        )
        self.assertEqual(movement["status"], "unclassified")
        self.assertFalse(movement.get("accounting_event_id"))
        self.assertEqual(
            await self.db.mz2_bank_transfer_reviews.count_documents({}),
            0,
        )

    async def test_closed_period_rolls_back_review_and_movement_consumption(self):
        evidence_id = await self.add_case(
            order="ORD-CLOSED-BANK",
            movement_id="MOVE-CLOSED-BANK",
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
                evidence_ref="Synthetic close evidence",
            ),
        )
        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(HTTPException) as denied:
            await approve_bank_transfer(
                self.db,
                owner=self.owner,
                actor=self.actor,
                evidence_id=evidence_id,
                movement_id="MOVE-CLOSED-BANK",
            )
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(denied.exception.detail["code"], "accounting_period_closed")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        self.assertEqual(
            await self.db.mz2_bank_transfer_reviews.count_documents({}),
            0,
        )
        movement = await self.db.mz2_daily_movements.find_one(
            {"id": "MOVE-CLOSED-BANK"}, {"_id": 0}
        )
        self.assertEqual(movement["status"], "unclassified")


if __name__ == "__main__":
    unittest.main()
