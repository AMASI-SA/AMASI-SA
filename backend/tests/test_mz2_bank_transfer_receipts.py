"""Real-Mongo contract for customer bank-transfer receipt review."""
import io
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook

from accounting_atomic import atomic_owner
from accounting_bank_transfer_receipts import (
    BankTransferError,
    approve_receipt,
    bank_transfer_candidates,
    bank_transfer_queue,
    convert_confirmed_deliveries,
    save_receipt_draft,
)
from accounting_daily_movements import (
    import_daily_movement_file,
    parse_daily_movement_xlsx,
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
from accounting_sales_tax_service import save_policy
from accounting_salla_order_evidence import (
    import_salla_order_evidence,
    parse_salla_order_xlsx,
)
from accounting_mz2_reports import read_mz2_ledger


ORDER_HEADERS = [
    "رقم الطلب", "حالة الطلب", "طريقة الدفع", "رقم مرجع عملية الدفع",
    "صافي المبيعات", "تاريخ الطلب", "تاريخ آخر تحديث للطلب",
    "إجمالي الطلب بالعملة الأصلية", "العملة الأصلية للطلب", "المبلغ المسترجع",
    "تفاصيل طرق الدفع", "تاريخ التسليم", "شركة الشحن / الفرع",
    "تكلفة الشحن", "عمولة الدفع عند الاستلام", "رقم البوليصة",
    "رقم مرجع الطلب", "إجمالي المبيعات", "عملة الطلب", "الضريبة",
]


def order_xlsx(order_number, *, status="تم التنفيذ", delivery="", amount=230.0, updated="2026-09-21 10:00"):
    row = {
        "رقم الطلب": order_number,
        "حالة الطلب": status,
        "طريقة الدفع": "حوالة بنكيةمصرف الراجحي",
        "رقم مرجع عملية الدفع": "",
        "صافي المبيعات": amount,
        "تاريخ الطلب": "2026-09-20 09:00",
        "تاريخ آخر تحديث للطلب": updated,
        "إجمالي الطلب بالعملة الأصلية": amount,
        "العملة الأصلية للطلب": "SAR",
        "المبلغ المسترجع": 0,
        "تفاصيل طرق الدفع": "حوالة بنكية",
        "تاريخ التسليم": delivery,
        "شركة الشحن / الفرع": "iMile للتوصيل",
        "تكلفة الشحن": 24.07,
        "عمولة الدفع عند الاستلام": 0,
        "رقم البوليصة": "WB-" + order_number,
        "رقم مرجع الطلب": "REF-" + order_number,
        "إجمالي المبيعات": amount,
        "عملة الطلب": "SAR",
        "الضريبة": 16.0,
    }
    wb = Workbook()
    ws = wb.active
    ws.append(ORDER_HEADERS)
    ws.append([row.get(header) for header in ORDER_HEADERS])
    stream = io.BytesIO()
    wb.save(stream)
    wb.close()
    return stream.getvalue()


def bank_xlsx(*, amount=230.0, reference="BANK-001", date="2026-09-21", description="حوالة عميل"):
    wb = Workbook()
    ws = wb.active
    ws.append(["تاريخ الحركة", "إيداع", "سحب", "البيان", "رقم المرجع", "المنصة"])
    ws.append([date, amount, 0, description, reference, ""])
    stream = io.BytesIO()
    wb.save(stream)
    wb.close()
    return stream.getvalue()


class BankTransferReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_bank_transfer_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.owner = "owner"
        self.actor = {
            "id": self.owner,
            "role": "owner",
            "name": "Synthetic owner",
            "email": "owner@example.invalid",
        }
        await self.db.users.insert_one({**self.actor, "is_active": True})
        await self.db.settings.insert_one({"user_id": self.owner})
        await self.db.accounts.insert_one({
            "id": "rajhi-bank",
            "user_id": self.owner,
            "name": "حساب الراجحي",
            "account_type": "bank",
            "status": "active",
        })
        await self._open_activate_tax()

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def tx(self, callback):
        return await atomic_owner(self.db, self.owner, callback)

    async def _open_activate_tax(self):
        refs = {
            "banks_cash": "SYN-BANKS",
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
                evidence_sheet_ref="SYN-BANK-TRANSFER-OPENING",
                evidence_sections=refs,
                lines=[OpeningLineIn(
                    category="bank",
                    entity_id="rajhi-bank",
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
        await save_policy(
            self.db,
            owner=self.owner,
            actor_id=self.owner,
            rate="15",
            effective_at="2026-09-01T00:00:00+03:00",
            revision=0,
            reason="Synthetic UAT tax",
        )

    async def import_order(self, order_number, **kwargs):
        content = order_xlsx(order_number, **kwargs)
        parsed = parse_salla_order_xlsx(content)
        self.assertEqual(parsed["errors"], [])
        result = await self.tx(lambda scoped: import_salla_order_evidence(
            scoped,
            owner=self.owner,
            actor=self.actor,
            filename=order_number + ".xlsx",
            content=content,
            parsed=parsed,
        ))
        current = await self.db.mz2_salla_order_evidence.find_one(
            {"user_id": self.owner, "order_number": order_number},
            {"_id": 0},
        )
        return result, current

    async def import_bank(self, **kwargs):
        content = bank_xlsx(**kwargs)
        parsed = parse_daily_movement_xlsx(content)
        self.assertEqual(parsed["errors"], [])
        result = await self.tx(lambda scoped: import_daily_movement_file(
            scoped,
            owner=self.owner,
            actor=self.actor,
            bank_account_id="rajhi-bank",
            filename="rajhi.xlsx",
            content=content,
            parsed=parsed,
        ))
        return result["items"][0]

    async def upload_receipt(self, evidence):
        return await self.tx(lambda scoped: save_receipt_draft(
            scoped,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence["id"],
            filename="receipt.pdf",
            content_type="application/pdf",
            content=b"%PDF-1.4\n% synthetic bank transfer receipt\n",
            notes="Reviewed visually",
        ))

    async def ledger_nets(self):
        scope = await read_mz2_ledger(self.db, owner=self.owner)
        self.assertEqual(scope["status"], "available", scope)
        nets = {}
        for row in scope["items"]:
            key = (row["entity_type"], row["entity_id"], row.get("sub_account") or "")
            nets[key] = nets.get(key, 0.0) + (
                row["amount"] if row["side"] == "debit" else -row["amount"]
            )
        return nets

    async def test_bank_and_amount_come_from_order_then_reviewer_selects_actual_bank_movement(self):
        _, evidence = await self.import_order("ORD-BANK-1")
        self.assertEqual(evidence["status"], "needs_bank_transfer_evidence")

        queue = await bank_transfer_queue(self.db, owner=self.owner)
        item = queue["items"][0]
        self.assertEqual(item["selected_bank"], "مصرف الراجحي")
        self.assertEqual(item["bank_resolution"]["bank_account_id"], "rajhi-bank")
        self.assertEqual(item["expected_amount"], "230.00")
        self.assertEqual(item["state"], "waiting_receipt")

        review = await self.upload_receipt(evidence)
        self.assertEqual(review["status"], "pending_approval")
        self.assertEqual(review["expected_amount"], "230.00")
        self.assertEqual(review["selected_bank_from_order"], "مصرف الراجحي")
        self.assertNotIn("received_amount", review)
        self.assertEqual(await self.db.general_ledger.count_documents({
            "entry_type": {"$in": ["bank_transfer_advance", "bank_transfer_sale"]}
        }), 0)

        movement = await self.import_bank()
        candidates = await bank_transfer_candidates(
            self.db, owner=self.owner, review_id=review["id"]
        )
        self.assertEqual(candidates["exact_count"], 1)
        self.assertEqual(candidates["items"][0]["id"], movement["id"])
        self.assertTrue(candidates["items"][0]["amount_matches"])

        approved = await approve_receipt(
            self.db,
            owner=self.owner,
            actor=self.actor,
            review_id=review["id"],
            movement_id=movement["id"],
        )
        self.assertEqual(approved["status"], "confirmed_waiting_delivery")
        self.assertEqual(approved["received_amount"], "230.00")
        self.assertEqual(approved["bank_movement_id"], movement["id"])

        stored_movement = await self.db.mz2_daily_movements.find_one({"id": movement["id"]})
        self.assertEqual(stored_movement["status"], "accounting_posted")
        self.assertEqual(stored_movement["accounting_action"], "bank_transfer_customer_receipt")

        nets = await self.ledger_nets()
        self.assertEqual(round(nets[("bank", "rajhi-bank", "main")], 2), 1230.0)
        advance_key = ("liability", approved["advance_id"], "customer_advance")
        self.assertEqual(round(nets[advance_key], 2), -230.0)

    async def test_delivery_reupload_preserves_review_then_converts_advance_to_sale(self):
        _, evidence = await self.import_order("ORD-BANK-2")
        review = await self.upload_receipt(evidence)
        movement = await self.import_bank(reference="BANK-002")
        approved = await approve_receipt(
            self.db,
            owner=self.owner,
            actor=self.actor,
            review_id=review["id"],
            movement_id=movement["id"],
        )
        self.assertEqual(approved["status"], "confirmed_waiting_delivery")

        second, current = await self.import_order(
            "ORD-BANK-2",
            status="تم التوصيل",
            delivery="2026-09-21 16:00:00",
            updated="2026-09-21 16:10",
        )
        self.assertEqual(current["bank_transfer_receipt_review_id"], review["id"])
        self.assertEqual(current["bank_transfer_receipt_status"], "confirmed_waiting_delivery")
        self.assertEqual(current["status"], "bank_transfer_confirmed_waiting_delivery")

        converted = await convert_confirmed_deliveries(
            self.db,
            owner=self.owner,
            actor=self.actor,
            file_id=second["file"]["id"],
            dry_run=False,
        )
        self.assertEqual(converted["posted_count"], 1)
        current = await self.db.mz2_salla_order_evidence.find_one(
            {"user_id": self.owner, "order_number": "ORD-BANK-2"},
            {"_id": 0},
        )
        self.assertEqual(current["status"], "recognized_bank_transfer")

        nets = await self.ledger_nets()
        advance_key = ("liability", approved["advance_id"], "customer_advance")
        self.assertEqual(round(nets[advance_key], 2), 0.0)
        self.assertEqual(round(nets[("revenue", "bnpl_sales", "")], 2), -200.0)
        self.assertEqual(round(nets[("tax", "sales_vat_payable", "")], 2), -30.0)
        self.assertEqual(round(nets[("bank", "rajhi-bank", "main")], 2), 1230.0)

    async def test_wrong_amount_cannot_be_approved_and_movement_remains_available(self):
        _, evidence = await self.import_order("ORD-BANK-3")
        review = await self.upload_receipt(evidence)
        movement = await self.import_bank(amount=229.0, reference="BANK-003")

        candidates = await bank_transfer_candidates(
            self.db, owner=self.owner, review_id=review["id"]
        )
        self.assertEqual(candidates["exact_count"], 0)
        self.assertFalse(candidates["items"][0]["amount_matches"])

        before = await self.db.general_ledger.count_documents({})
        with self.assertRaises(BankTransferError) as ctx:
            await approve_receipt(
                self.db,
                owner=self.owner,
                actor=self.actor,
                review_id=review["id"],
                movement_id=movement["id"],
            )
        self.assertEqual(str(ctx.exception), "bank_movement_amount_mismatch")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before)
        stored = await self.db.mz2_daily_movements.find_one({"id": movement["id"]})
        self.assertEqual(stored["status"], "unclassified")
        self.assertFalse(stored.get("accounting_event_id"))

    async def test_same_bank_movement_cannot_confirm_two_orders(self):
        _, first_evidence = await self.import_order("ORD-BANK-4")
        first_review = await self.upload_receipt(first_evidence)
        movement = await self.import_bank(reference="BANK-004")
        await approve_receipt(
            self.db,
            owner=self.owner,
            actor=self.actor,
            review_id=first_review["id"],
            movement_id=movement["id"],
        )

        _, second_evidence = await self.import_order(
            "ORD-BANK-5",
            updated="2026-09-21 10:30",
        )
        # Use a different receipt; duplicate-file evidence is independently guarded.
        second_review = await self.tx(lambda scoped: save_receipt_draft(
            scoped,
            owner=self.owner,
            actor=self.actor,
            evidence_id=second_evidence["id"],
            filename="receipt-2.pdf",
            content_type="application/pdf",
            content=b"%PDF-1.4\n% a different receipt\n",
            notes="Second order",
        ))
        with self.assertRaises(BankTransferError) as ctx:
            await approve_receipt(
                self.db,
                owner=self.owner,
                actor=self.actor,
                review_id=second_review["id"],
                movement_id=movement["id"],
            )
        self.assertIn(
            str(ctx.exception),
            {"bank_movement_already_classified", "bank_movement_already_used_for_another_order"},
        )


if __name__ == "__main__":
    unittest.main()
