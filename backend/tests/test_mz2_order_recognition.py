"""Real-Mongo contract for safe MZ2 recognition from Salla order evidence."""
import io
import json
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook

from accounting_atomic import atomic_owner
from accounting_module_opening_balances import (
    OpeningActivateIn,
    OpeningApproveIn,
    OpeningLineIn,
    OpeningPreviewIn,
    activate_p01,
    approve_opening_preview,
    create_opening_preview,
)
from accounting_order_recognition import (
    execute_order_recognition,
    prepare_order_recognition,
    recognition_queue,
)
from accounting_periods import PeriodChange, set_period
from accounting_recognition_evidence import EvidenceError
from accounting_sales_tax_service import save_policy
from accounting_salla_order_evidence import (
    import_salla_order_evidence,
    parse_salla_order_xlsx,
)
from accounting_mz2_reports import read_mz2_ledger


HEADERS = [
    "رقم الطلب",
    "حالة الطلب",
    "طريقة الدفع",
    "رقم مرجع عملية الدفع",
    "صافي المبيعات",
    "تاريخ الطلب",
    "تاريخ آخر تحديث للطلب",
    "إجمالي الطلب بالعملة الأصلية",
    "العملة الأصلية للطلب",
    "المبلغ المسترجع",
    "تفاصيل طرق الدفع",
    "تاريخ التسليم",
    "شركة الشحن / الفرع",
    "تكلفة الشحن",
    "عمولة الدفع عند الاستلام",
    "رقم البوليصة",
    "رقم مرجع الطلب",
    "إجمالي المبيعات",
    "عملة الطلب",
    "الضريبة",
]


def payment_ref(provider, reference, amount):
    return json.dumps([{
        "provider": provider,
        "reference": reference,
        "amount": amount,
    }], ensure_ascii=False)


def workbook_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(HEADERS)
    for row in rows:
        ws.append([row.get(header) for header in HEADERS])
    stream = io.BytesIO()
    wb.save(stream)
    wb.close()
    return stream.getvalue()


def sale_row(
    order_number="ORD-SALLA-1",
    *,
    method="مدى",
    payment_provider="applepay",
    payment_id="PAY-SALLA-1",
    payment_amount=115.00,
    current=115.00,
    refunded=0,
    status="تم التوصيل",
    delivery="2026-09-21 10:00:00",
    updated="2026-09-21 10:05",
    original_amount=115.00,
    original_currency="SAR",
    shipping="iMile للتوصيل",
):
    return {
        "رقم الطلب": order_number,
        "حالة الطلب": status,
        "طريقة الدفع": method,
        "رقم مرجع عملية الدفع": payment_ref(
            payment_provider, payment_id, payment_amount
        ),
        "صافي المبيعات": current,
        "تاريخ الطلب": "2026-09-20 10:00",
        "تاريخ آخر تحديث للطلب": updated,
        "إجمالي الطلب بالعملة الأصلية": original_amount,
        "العملة الأصلية للطلب": original_currency,
        "المبلغ المسترجع": refunded,
        "تفاصيل طرق الدفع": method,
        "تاريخ التسليم": delivery,
        "شركة الشحن / الفرع": shipping,
        "تكلفة الشحن": 17.25,
        "عمولة الدفع عند الاستلام": 0,
        "رقم البوليصة": "WAY-" + order_number,
        "رقم مرجع الطلب": "REF-" + order_number,
        "إجمالي المبيعات": current,
        "عملة الطلب": "SAR",
        # Deliberately not 15% of gross. The MZ2 policy must win.
        "الضريبة": 8.52,
    }


class MZ2OrderRecognitionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_order_recognition_" + uuid4().hex]
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
            "id": "bank-main",
            "user_id": self.owner,
            "name": "Synthetic bank",
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
            "providers": "SYN-PROVIDERS",
            "couriers_cod": "SYN-COURIERS-ZERO",
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
                activation_ref="SYN-ORDER-UAT",
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
            reason="Synthetic UAT tax policy",
        )

    async def import_rows(self, rows, filename="orders.xlsx"):
        content = workbook_bytes(rows)
        parsed = parse_salla_order_xlsx(content)
        self.assertEqual(parsed["errors"], [])
        result = await self.tx(lambda scoped: import_salla_order_evidence(
            scoped,
            owner=self.owner,
            actor=self.actor,
            filename=filename,
            content=content,
            parsed=parsed,
        ))
        return result

    async def current(self, order_number):
        return await self.db.mz2_salla_order_evidence.find_one(
            {"user_id": self.owner, "order_number": order_number},
            {"_id": 0},
        )

    async def test_salla_sale_posts_manual_tax_and_provider_receivable_once(self):
        await self.import_rows([sale_row()])
        evidence = await self.current("ORD-SALLA-1")
        self.assertEqual(evidence["status"], "ready_for_provider_resolution")

        preview = await prepare_order_recognition(
            self.db, owner=self.owner, evidence_id=evidence["id"]
        )
        self.assertEqual(preview["state"], "eligible")
        self.assertEqual(preview["tax"]["gross"], "115.00")
        self.assertEqual(preview["tax"]["net"], "100.00")
        self.assertEqual(preview["tax"]["tax"], "15.00")
        # Salla source value is evidence only.
        self.assertEqual(preview["tax"]["source_tax_for_review"]["tax_amount"], "8.52")

        before = await self.db.general_ledger.count_documents({})
        posted = await execute_order_recognition(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence["id"],
            preview_hash=preview["preview_hash"],
        )
        self.assertEqual(posted["state"], "posted")
        self.assertEqual(posted["evidence_status_after"], "recognized")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 3)

        legs = await self.db.general_ledger.find(
            {"txn_group_id": posted["txn_group_id"]},
            {"_id": 0},
        ).to_list(10)
        self.assertEqual(len(legs), 3)
        self.assertEqual(
            {(row["entity_type"], row["entity_id"], row.get("sub_account"), row["side"], row["amount"])
             for row in legs},
            {
                ("payment_gateway", "salla", "receivable", "debit", 115.0),
                ("revenue", "bnpl_sales", None, "credit", 100.0),
                ("tax", "sales_vat_payable", None, "credit", 15.0),
            },
        )
        self.assertTrue(all(
            row["metadata"]["recognition_source"] == "salla_order_evidence"
            and row["metadata"]["operation_id"] == "MZ2-FIN-CUTOVER-001"
            for row in legs
        ))
        self.assertEqual(await self.db.payment_transactions.count_documents({}), 0)
        self.assertEqual(await self.db.orders_db.count_documents({}), 0)

        retry = await execute_order_recognition(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence["id"],
        )
        self.assertEqual(retry["state"], "already_posted")
        self.assertEqual(retry["txn_group_id"], posted["txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), before + 3)

        scope = await read_mz2_ledger(self.db, owner=self.owner)
        self.assertEqual(scope["status"], "available", scope)
        salla = [
            row for row in scope["items"]
            if row["entity_type"] == "payment_gateway"
            and row["entity_id"] == "salla"
            and row.get("sub_account") == "receivable"
        ]
        self.assertEqual(sum(row["amount"] for row in salla), 115.0)

    async def test_tabby_needs_provider_capture_then_posts_same_order(self):
        row = sale_row(
            "ORD-TABBY-1",
            method="تابي",
            payment_provider="Tabby",
            payment_id="TABBY-PAY-1",
        )
        await self.import_rows([row])
        evidence = await self.current("ORD-TABBY-1")
        with self.assertRaises(EvidenceError) as missing:
            await prepare_order_recognition(
                self.db, owner=self.owner, evidence_id=evidence["id"]
            )
        self.assertEqual(str(missing.exception), "provider_payment_evidence_missing")
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({}), 0)

        await self.db.payment_transactions.insert_one({
            "id": "local-tabby-1",
            "user_id": self.owner,
            "provider": "tabby",
            "provider_id": "TABBY-PAY-1",
            "order_reference_id": "ORD-TABBY-1",
            "amount": "115.00",
            "captured_amount": "115.00",
            "currency": "SAR",
            "status": "closed",
            "source": "tabby_api",
            "captured_at": "2026-09-21T06:00:00+00:00",
        })
        preview = await prepare_order_recognition(
            self.db, owner=self.owner, evidence_id=evidence["id"]
        )
        self.assertEqual(preview["event"]["provider"], "tabby")
        # Delivery 10:00 Riyadh = 07:00Z, later than provider capture 06:00Z.
        self.assertEqual(preview["event"]["recognized_at"], "2026-09-21T07:00:00+00:00")

        posted = await execute_order_recognition(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence["id"],
        )
        self.assertEqual(posted["state"], "posted")
        leg = await self.db.general_ledger.find_one({
            "txn_group_id": posted["txn_group_id"],
            "entity_type": "payment_gateway",
        })
        self.assertEqual(leg["entity_id"], "tabby")
        self.assertEqual(leg["amount"], 115.0)

    async def test_partial_refund_posts_original_sale_only_and_waits_for_refund_evidence(self):
        await self.import_rows([sale_row(
            "ORD-REFUND-1",
            payment_id="PAY-REFUND-1",
            payment_amount=306.80,
            current=166.40,
            refunded=140.40,
            original_amount=166.40,
        )])
        evidence = await self.current("ORD-REFUND-1")
        self.assertEqual(evidence["status"], "ready_sale_refund_pending_evidence")

        posted = await execute_order_recognition(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence["id"],
        )
        self.assertEqual(posted["tax"]["gross"], "306.80")
        self.assertEqual(posted["evidence_status_after"], "recognized_refund_pending_evidence")
        self.assertEqual(
            await self.db.general_ledger.count_documents({
                "txn_group_id": posted["txn_group_id"],
                "entry_type": "bnpl_refund",
            }),
            0,
        )
        current = await self.current("ORD-REFUND-1")
        self.assertEqual(current["refunded_sar"], "140.40")
        self.assertEqual(current["recognized_gross_sar"], "306.80")

    async def test_foreign_currency_source_posts_explicit_sar_payment_and_preserves_original(self):
        await self.import_rows([sale_row(
            "ORD-FX-1",
            payment_id="PAY-FX-1",
            payment_amount=351.00,
            current=351.00,
            original_amount=343.38,
            original_currency="AED",
        )])
        evidence = await self.current("ORD-FX-1")
        posted = await execute_order_recognition(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence["id"],
        )
        self.assertEqual(posted["tax"]["gross"], "351.00")
        leg = await self.db.general_ledger.find_one({
            "txn_group_id": posted["txn_group_id"],
            "entity_type": "payment_gateway",
        })
        self.assertEqual(leg["amount"], 351.0)
        self.assertEqual(leg["metadata"]["original_currency"], "AED")
        self.assertEqual(leg["metadata"]["original_amount"], "343.38")

    async def test_daily_reupload_preserves_posted_sale_then_surfaces_later_refund(self):
        first_row = sale_row("ORD-DAILY-1", payment_id="PAY-DAILY-1")
        await self.import_rows([first_row], "day1.xlsx")
        evidence = await self.current("ORD-DAILY-1")
        posted = await execute_order_recognition(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence["id"],
        )

        day2 = dict(first_row)
        day2["تاريخ آخر تحديث للطلب"] = "2026-09-22 10:00"
        day2["شركة الشحن / الفرع"] = "سمسا"
        day2["رقم البوليصة"] = "WAY-DAY2"
        await self.import_rows([day2], "day2.xlsx")
        current = await self.current("ORD-DAILY-1")
        self.assertEqual(current["status"], "recognized")
        self.assertEqual(current["recognition_txn_group_id"], posted["txn_group_id"])
        self.assertEqual(current["shipping_company"], "سمسا")

        day3 = dict(day2)
        day3["تاريخ آخر تحديث للطلب"] = "2026-09-23 10:00"
        day3["صافي المبيعات"] = 100.00
        day3["إجمالي المبيعات"] = 100.00
        day3["إجمالي الطلب بالعملة الأصلية"] = 100.00
        day3["المبلغ المسترجع"] = 15.00
        await self.import_rows([day3], "day3.xlsx")
        current = await self.current("ORD-DAILY-1")
        self.assertEqual(current["status"], "recognized_refund_pending_evidence")
        self.assertEqual(current["recognition_txn_group_id"], posted["txn_group_id"])
        self.assertEqual(current["review_reasons"], ["refund_provider_identity_required"])
        self.assertEqual(
            await self.db.mz2_recognition_events.count_documents({}),
            1,
        )

    async def test_post_recognition_payment_identity_change_is_review_conflict_not_repost(self):
        row = sale_row("ORD-CHANGE-1", payment_id="PAY-CHANGE-1")
        await self.import_rows([row], "before.xlsx")
        evidence = await self.current("ORD-CHANGE-1")
        posted = await execute_order_recognition(
            self.db,
            owner=self.owner,
            actor=self.actor,
            evidence_id=evidence["id"],
        )
        before_legs = await self.db.general_ledger.count_documents({})

        changed = dict(row)
        changed["تاريخ آخر تحديث للطلب"] = "2026-09-22 11:00"
        changed["رقم مرجع عملية الدفع"] = payment_ref(
            "applepay", "PAY-CHANGED", 115.00
        )
        result = await self.import_rows([changed], "changed.xlsx")
        self.assertEqual(result["file"]["conflict_count"], 1)
        current = await self.current("ORD-CHANGE-1")
        self.assertEqual(current["status"], "needs_review")
        self.assertTrue(current["conflict"])
        self.assertIn("recognized_payment_identity_changed", current["review_reasons"])
        self.assertEqual(current["recognition_txn_group_id"], posted["txn_group_id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), before_legs)

    async def test_closed_period_rolls_back_recognition_and_keeps_evidence_ready(self):
        await self.import_rows([sale_row(
            "ORD-CLOSED-1", payment_id="PAY-CLOSED-1"
        )])
        evidence = await self.current("ORD-CLOSED-1")
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
        before_legs = await self.db.general_ledger.count_documents({})
        before_events = await self.db.mz2_recognition_events.count_documents({})
        with self.assertRaises(HTTPException) as denied:
            await execute_order_recognition(
                self.db,
                owner=self.owner,
                actor=self.actor,
                evidence_id=evidence["id"],
            )
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(denied.exception.detail["code"], "accounting_period_closed")
        self.assertEqual(await self.db.general_ledger.count_documents({}), before_legs)
        self.assertEqual(await self.db.mz2_recognition_events.count_documents({}), before_events)
        current = await self.current("ORD-CLOSED-1")
        self.assertEqual(current["status"], "ready_for_provider_resolution")
        self.assertFalse(current.get("recognition_txn_group_id"))

    async def test_queue_separates_ready_and_provider_waiting(self):
        await self.import_rows([
            sale_row("ORD-Q-SALLA", payment_id="PAY-Q-SALLA"),
            sale_row(
                "ORD-Q-TAMARA",
                method="تمارا",
                payment_provider="Tamara",
                payment_id="PAY-Q-TAMARA",
                updated="2026-09-21 10:06",
            ),
        ])
        queue = await recognition_queue(self.db, owner=self.owner, limit=20)
        by_order = {item["order_number"]: item for item in queue["items"]}
        self.assertEqual(by_order["ORD-Q-SALLA"]["state"], "eligible")
        self.assertEqual(by_order["ORD-Q-TAMARA"]["state"], "waiting")
        self.assertEqual(
            by_order["ORD-Q-TAMARA"]["reasons"],
            ["provider_payment_evidence_missing"],
        )


if __name__ == "__main__":
    unittest.main()
