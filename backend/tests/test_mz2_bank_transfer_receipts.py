"""Real-Mongo contract for customer bank-transfer receipt review."""
import io
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook

from accounting_bank_transfer_bindings import BankTransferBindingIn, save_bank_transfer_binding
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
from customer_native_fixture import native_customer_opening
from decimal import Decimal
from unittest.mock import patch
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


def order_xlsx(order_number, *, status="تم التنفيذ", delivery="", amount=230.0, updated="2026-09-21 10:00", selected_bank="rajhi-bank"):
    row = {
        "رقم الطلب": order_number,
        "حالة الطلب": status,
        "طريقة الدفع": "حوالة بنكية" + selected_bank,
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
        # This successful-write fixture has explicit owner write-control state.
        await self.db.mz2_atomic_owners.insert_one({
            "_id": "owner", "revision": 0, "writes_paused": False, "control_revision": 0,
        })
        self.owner = "owner"
        self.actor = {
            "id": self.owner,
            "role": "owner",
            "name": "Synthetic owner",
            "email": "owner@example.invalid",
        }
        await self.db.users.insert_one({**self.actor, "is_active": True})
        await self.db.settings.insert_one({"user_id": self.owner})
        await self.db.mz2_financial_accounts.insert_one({
            "id": "rajhi-bank", "user_id": self.owner, "account_type": "bank",
            "name": "Canonical synthetic bank", "status": "active", "currency": "SAR",
            "idempotency_key": "fixture-rajhi-bank",
        })
        await atomic_owner(self.db, self.owner, lambda scoped: save_bank_transfer_binding(
            scoped, self.owner, self.actor, BankTransferBindingIn(
                upstream_source="salla.payment_method_bank", upstream_value="rajhi-bank",
                financial_account_id="rajhi-bank", confirmation="CONFIRM_MZ2_BANK_TRANSFER_BINDING",
                evidence_ref="synthetic-explicit-binding")))
        await self._open_activate_tax()

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def tx(self, callback):
        return await atomic_owner(self.db, self.owner, callback)

    async def _open_activate_tax(self):
        await native_customer_opening(self.db, bank="rajhi-bank", amount="1000.00",
            cutover="2026-09-20T00:00:00+03:00")
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
            nets[key] = nets.get(key, Decimal(0)) + (
                Decimal(row["amount"]) if row["side"] == "debit" else -Decimal(row["amount"])
            )
        return nets

    async def test_legacy_display_name_does_not_resolve_bank_or_write_financial_rows(self):
        before = await self.db.accounting_general_ledger_v2.count_documents({})
        await self.import_order("ORD-LEGACY-NAME", selected_bank="مصرف الراجحي")
        queue = await bank_transfer_queue(self.db, owner=self.owner)
        resolution = queue["items"][0]["bank_resolution"]
        self.assertEqual(resolution["state"], "unresolved")
        self.assertEqual(resolution["code"], "MZ2_LINK_REQUIRED")
        self.assertIsNone(resolution["bank_account_id"])
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}), before)

    async def test_bank_and_amount_come_from_order_then_reviewer_selects_actual_bank_movement(self):
        _, evidence = await self.import_order("ORD-BANK-1")
        self.assertEqual(evidence["status"], "needs_bank_transfer_evidence")

        queue = await bank_transfer_queue(self.db, owner=self.owner)
        item = queue["items"][0]
        self.assertEqual(item["selected_bank"], "rajhi-bank")
        self.assertEqual(item["bank_resolution"]["bank_account_id"], "rajhi-bank")
        self.assertEqual(item["expected_amount"], "230.00")
        self.assertEqual(item["state"], "waiting_receipt")

        review = await self.upload_receipt(evidence)
        self.assertEqual(review["status"], "pending_approval")
        self.assertEqual(review["expected_amount"], "230.00")
        self.assertEqual(review["selected_bank_from_order"], "rajhi-bank")
        self.assertNotIn("received_amount", review)
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({
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

    async def financial_snapshot(self):
        return {name: await self.db[name].find({}).to_list(100) for name in (
            "general_ledger", "accounting_audit_log", "accounting_general_ledger_v2",
            "accounting_journal_groups_v2", "accounting_audit_log_v2", "mz2_recognition_events",
            "mz2_bank_transfer_receipt_events", "mz2_bank_transfer_receipts",
            "mz2_daily_movements", "mz2_bank_transfer_receipt_audit", "mz2_salla_order_evidence",
        )}

    async def test_order_creation_cutover_rejects_receipt_without_financial_changes(self):
        _, evidence = await self.import_order("ORD-CUTOVER-RECEIPT")
        review = await self.upload_receipt(evidence)
        movement = await self.import_bank(reference="BANK-CUTOVER-RECEIPT")
        for created, code in (
            (None, "order_creation_timestamp_required"),
            ("invalid", "order_creation_timestamp_invalid"),
            ("2026-09-19 23:59:59", "pre_cutover_order"),
        ):
            with self.subTest(created=created):
                await self.db.mz2_salla_order_evidence.update_one(
                    {"user_id": self.owner, "id": evidence["id"]},
                    {"$set": {"order_date_source_text": created}},
                )
                before = await self.financial_snapshot()
                with self.assertRaisesRegex(BankTransferError, "^" + code + "$"):
                    await approve_receipt(self.db, owner=self.owner, actor=self.actor,
                        review_id=review["id"], movement_id=movement["id"])
                self.assertEqual(await self.financial_snapshot(), before)

        await self.db.mz2_salla_order_evidence.update_one(
            {"user_id": self.owner, "id": evidence["id"]},
            {"$set": {"order_date_source_text": "2026-09-20 00:00:00"}},
        )
        approved = await approve_receipt(self.db, owner=self.owner, actor=self.actor,
            review_id=review["id"], movement_id=movement["id"])
        self.assertEqual(approved["status"], "confirmed_waiting_delivery")
        self.assertIsNone(approved["sale_txn_group_id"])

    async def test_order_creation_cutover_rechecked_on_confirmed_delivery_conversion(self):
        _, evidence = await self.import_order("ORD-CUTOVER-CONVERT")
        review = await self.upload_receipt(evidence)
        movement = await self.import_bank(reference="BANK-CUTOVER-CONVERT")
        await approve_receipt(self.db, owner=self.owner, actor=self.actor,
            review_id=review["id"], movement_id=movement["id"])
        await self.import_order("ORD-CUTOVER-CONVERT", status="تم التوصيل",
            delivery="2026-09-21 16:00:00", updated="2026-09-21 16:10")
        for created, code in (
            (None, "order_creation_timestamp_required"),
            ("invalid", "order_creation_timestamp_invalid"),
            ("2026-09-19 23:59:59", "pre_cutover_order"),
        ):
            for dry_run in (True, False):
                with self.subTest(created=created, dry_run=dry_run):
                    await self.db.mz2_salla_order_evidence.update_one(
                        {"user_id": self.owner, "id": evidence["id"]},
                        {"$set": {"order_date_source_text": created}},
                    )
                    before = await self.financial_snapshot()
                    result = await convert_confirmed_deliveries(self.db, owner=self.owner,
                        actor=self.actor, dry_run=dry_run)
                    self.assertEqual(result["blocked_count"], 1)
                    self.assertEqual(result["items"][0]["reason"], code)
                    self.assertEqual(await self.financial_snapshot(), before)

        await self.db.mz2_salla_order_evidence.update_one(
            {"user_id": self.owner, "id": evidence["id"]},
            {"$set": {"order_date_source_text": "2026-09-20 00:00:00"}},
        )
        result = await convert_confirmed_deliveries(self.db, owner=self.owner,
            actor=self.actor, dry_run=False)
        self.assertEqual(result["posted_count"], 1)

    async def test_wrong_amount_cannot_be_approved_and_movement_remains_available(self):
        _, evidence = await self.import_order("ORD-BANK-3")
        review = await self.upload_receipt(evidence)
        movement = await self.import_bank(amount=229.0, reference="BANK-003")

        candidates = await bank_transfer_candidates(
            self.db, owner=self.owner, review_id=review["id"]
        )
        self.assertEqual(candidates["exact_count"], 0)
        self.assertFalse(candidates["items"][0]["amount_matches"])

        before = await self.db.accounting_general_ledger_v2.count_documents({})
        with self.assertRaises(BankTransferError) as ctx:
            await approve_receipt(
                self.db,
                owner=self.owner,
                actor=self.actor,
                review_id=review["id"],
                movement_id=movement["id"],
            )
        self.assertEqual(str(ctx.exception), "bank_movement_amount_mismatch")
        self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}), before)
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


    async def test_native_receipt_delivery_legacy_isolation_and_atomic_retry(self):
        from customer_native_fixture import CustomerLegacyAccess
        import accounting_customer_native
        _, evidence = await self.import_order("SYN-NATIVE-ISOLATION")
        review = await self.upload_receipt(evidence)
        movement = await self.import_bank(reference="SYN-NATIVE-ISOLATION")
        await self.db.general_ledger.insert_one(dict(id="SYN-LEGACY", user_id=self.owner,
            status="posted", entity_type="bank", entity_id="rajhi-bank", side="debit", amount=999999,
            entry_type="bank_transfer_sale", metadata={"order_reference_id": "SYN-NATIVE-ISOLATION"}))
        legacy = await self.db.general_ledger.find({}).to_list(None)
        listener = CustomerLegacyAccess()
        client = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], event_listeners=[listener])
        observed = client[self.db.name]
        try:
            before = await self.financial_snapshot()
            original = accounting_customer_native.post_journal_v2
            async def fail_after_journal(*args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError("SYN after sealed receipt journal")
            with patch.object(accounting_customer_native, "post_journal_v2", side_effect=fail_after_journal):
                with self.assertRaisesRegex(RuntimeError, "sealed receipt"):
                    await approve_receipt(observed, owner=self.owner, actor=self.actor,
                        review_id=review["id"], movement_id=movement["id"])
            self.assertEqual(await self.financial_snapshot(), before)
            # Another owner's actor cannot approve this owner's selected receipt.
            await self.db.users.insert_one(dict(id="other", role="owner", is_active=True))
            with self.assertRaises(HTTPException) as denied:
                await approve_receipt(observed, owner=self.owner, actor={"id": "other"},
                    review_id=review["id"], movement_id=movement["id"])
            self.assertEqual(denied.exception.status_code, 403)
            result = await approve_receipt(observed, owner=self.owner, actor=self.actor,
                review_id=review["id"], movement_id=movement["id"])
            again = await approve_receipt(observed, owner=self.owner, actor=self.actor,
                review_id=review["id"], movement_id=movement["id"])
            self.assertEqual(again["receipt_txn_group_id"], result["receipt_txn_group_id"])
            await self.import_order("SYN-NATIVE-ISOLATION", status="تم التوصيل",
                delivery="2026-09-21 16:00:00", updated="2026-09-21 16:10")
            converted = await convert_confirmed_deliveries(observed, owner=self.owner, actor=self.actor, dry_run=False)
            self.assertEqual(converted["posted_count"], 1, converted)
            self.assertEqual((await convert_confirmed_deliveries(observed, owner=self.owner, actor=self.actor, dry_run=False))["posted_count"], 0)
            self.assertEqual(listener.accesses, [])
            self.assertEqual(await self.db.general_ledger.find({}).to_list(None), legacy)
            self.assertEqual(await self.db.accounting_general_ledger_v2.count_documents({}), 7)
        finally:
            client.close()


    async def test_native_receipt_closed_period_pause_and_revoked_permission(self):
        from accounting_periods import PeriodChange, set_period
        _, evidence = await self.import_order("SYN-NATIVE-GATES")
        review = await self.upload_receipt(evidence)
        movement = await self.import_bank(reference="SYN-NATIVE-GATES")
        async def approve():
            return await approve_receipt(self.db, owner=self.owner, actor=self.actor,
                review_id=review["id"], movement_id=movement["id"])
        await self.db.users.update_one({"id": self.owner}, {"$set": {"role": "employee",
            "created_by": self.owner, "accounting_permissions": ["accounting.movements.view"]}})
        before = await self.financial_snapshot()
        with self.assertRaises(HTTPException) as denied:
            await approve()
        self.assertEqual(denied.exception.status_code, 403)
        self.assertEqual(await self.financial_snapshot(), before)
        await self.db.users.update_one({"id": self.owner}, {"$set": {"role": "owner"}})
        await self.db.mz2_atomic_owners.update_one({"_id": self.owner}, {"$set": {"writes_paused": True}})
        with self.assertRaises(HTTPException) as denied:
            await approve()
        self.assertEqual(denied.exception.status_code, 423)
        self.assertEqual(await self.financial_snapshot(), before)
        await self.db.mz2_atomic_owners.update_one({"_id": self.owner}, {"$set": {"writes_paused": False}})
        await set_period(self.db, self.owner, self.owner, PeriodChange(month="2026-09", closed=True,
            revision=0, reason="Synthetic close", evidence_ref="SYN-close"))
        with self.assertRaises(HTTPException) as denied:
            await approve()
        self.assertEqual(denied.exception.detail["code"], "accounting_period_closed")
        self.assertEqual(await self.financial_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
