"""Real-Mongo contract for MZ2 Salla order-export evidence intake."""
import io
import os
from uuid import uuid4
import unittest

from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook

from accounting_atomic import atomic_owner
from accounting_salla_order_evidence import (
    import_salla_order_evidence,
    parse_salla_order_xlsx,
)


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
    import json
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


def source_rows():
    return [
        {
            "رقم الطلب": "ORD-MADA",
            "حالة الطلب": "تم التوصيل",
            "طريقة الدفع": "مدى",
            "رقم مرجع عملية الدفع": payment_ref("applepay", "SALLA-MADA-1", 180.55),
            "صافي المبيعات": 180.55,
            "تاريخ الطلب": "2026-09-20 10:00",
            "تاريخ آخر تحديث للطلب": "2026-09-21 10:00",
            "إجمالي الطلب بالعملة الأصلية": 180.55,
            "العملة الأصلية للطلب": "SAR",
            "المبلغ المسترجع": 0,
            "تفاصيل طرق الدفع": "mada: 180.55",
            "تاريخ التسليم": "2026-09-21 09:45:00",
            "شركة الشحن / الفرع": "iMile للتوصيل",
            "تكلفة الشحن": 24.07,
            "رقم البوليصة": "WAY-MADA",
            "رقم مرجع الطلب": "REF-MADA",
            "إجمالي المبيعات": 196.45,
            "عملة الطلب": "SAR",
            "الضريبة": 13.38,
        },
        {
            "رقم الطلب": "ORD-TABBY",
            "حالة الطلب": "تم التوصيل",
            "طريقة الدفع": "تابي",
            "رقم مرجع عملية الدفع": payment_ref("Tabby", "TABBY-1", 189.13),
            "صافي المبيعات": 189.13,
            "تاريخ الطلب": "2026-09-20 10:10",
            "تاريخ آخر تحديث للطلب": "2026-09-21 10:10",
            "إجمالي الطلب بالعملة الأصلية": 189.13,
            "العملة الأصلية للطلب": "SAR",
            "المبلغ المسترجع": 0,
            "تاريخ التسليم": "2026-09-21 09:50:00",
        },
        {
            "رقم الطلب": "ORD-REFUND",
            "حالة الطلب": "تم التوصيل",
            "طريقة الدفع": "البطاقة الإئتمانية",
            "رقم مرجع عملية الدفع": payment_ref("applepay", "SALLA-REFUND-1", 306.80),
            "صافي المبيعات": 166.40,
            "تاريخ الطلب": "2026-09-20 10:20",
            "تاريخ آخر تحديث للطلب": "2026-09-21 10:20",
            "إجمالي الطلب بالعملة الأصلية": 166.40,
            "العملة الأصلية للطلب": "SAR",
            "المبلغ المسترجع": 140.40,
            "تاريخ التسليم": "2026-09-21 09:55:00",
        },
        {
            "رقم الطلب": "ORD-CONFLICT",
            "حالة الطلب": "تم التوصيل",
            "طريقة الدفع": "تابي",
            "رقم مرجع عملية الدفع": payment_ref("Tabby", "TABBY-CONFLICT", 176.12),
            "صافي المبيعات": 177.12,
            "تاريخ الطلب": "2026-09-20 10:30",
            "تاريخ آخر تحديث للطلب": "2026-09-21 10:30",
            "إجمالي الطلب بالعملة الأصلية": 177.12,
            "العملة الأصلية للطلب": "SAR",
            "المبلغ المسترجع": 0,
            "تاريخ التسليم": "2026-09-21 10:00:00",
        },
        {
            "رقم الطلب": "ORD-EXECUTED",
            "حالة الطلب": "تم التنفيذ",
            "طريقة الدفع": "تمارا",
            "رقم مرجع عملية الدفع": payment_ref("Tamara", "TAMARA-EXEC", 208.52),
            "صافي المبيعات": 208.52,
            "تاريخ الطلب": "2026-09-20 10:40",
            "تاريخ آخر تحديث للطلب": "2026-09-21 10:40",
            "إجمالي الطلب بالعملة الأصلية": 208.52,
            "العملة الأصلية للطلب": "SAR",
            "المبلغ المسترجع": 0,
            "تاريخ التسليم": "",
        },
        {
            "رقم الطلب": "ORD-COD",
            "حالة الطلب": "تم التوصيل",
            "طريقة الدفع": "دفع عند الإستلام",
            "رقم مرجع عملية الدفع": "",
            "صافي المبيعات": 150.00,
            "تاريخ الطلب": "2026-09-20 10:50",
            "تاريخ آخر تحديث للطلب": "2026-09-21 10:50",
            "إجمالي الطلب بالعملة الأصلية": 150.00,
            "العملة الأصلية للطلب": "SAR",
            "المبلغ المسترجع": 0,
            "تاريخ التسليم": "2026-09-21 10:10:00",
            "عمولة الدفع عند الاستلام": 7.25,
        },
        {
            "رقم الطلب": "ORD-BANK",
            "حالة الطلب": "تم التنفيذ",
            "طريقة الدفع": "حوالة بنكيةمصرف الراجحي",
            "رقم مرجع عملية الدفع": "",
            "صافي المبيعات": 175.12,
            "تاريخ الطلب": "2026-09-20 11:00",
            "تاريخ آخر تحديث للطلب": "2026-09-21 11:00",
            "إجمالي الطلب بالعملة الأصلية": 175.12,
            "العملة الأصلية للطلب": "SAR",
            "المبلغ المسترجع": 0,
        },
        {
            "رقم الطلب": "ORD-FX",
            "حالة الطلب": "تم التوصيل",
            "طريقة الدفع": "البطاقة الإئتمانية",
            "رقم مرجع عملية الدفع": payment_ref("applepay", "SALLA-FX-1", 351.00),
            "صافي المبيعات": 351.00,
            "تاريخ الطلب": "2026-09-20 11:10",
            "تاريخ آخر تحديث للطلب": "2026-09-21 11:10",
            "إجمالي الطلب بالعملة الأصلية": 343.38,
            "العملة الأصلية للطلب": "AED",
            "المبلغ المسترجع": 0,
            "تاريخ التسليم": "2026-09-21 10:20:00",
            "عملة الطلب": "SAR",
        },
        {
            "رقم الطلب": "ORD-UNKNOWN",
            "حالة الطلب": "بإنتظار الدفع",
            "طريقة الدفع": "\\N",
            "رقم مرجع عملية الدفع": "",
            "صافي المبيعات": 100.00,
            "تاريخ الطلب": "2026-09-20 11:20",
            "تاريخ آخر تحديث للطلب": "2026-09-21 11:20",
            "إجمالي الطلب بالعملة الأصلية": 100.00,
            "العملة الأصلية للطلب": "SAR",
            "المبلغ المسترجع": 0,
        },
    ]


class SallaOrderEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_salla_order_evidence_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.owner = "owner"
        self.actor = {"id": "owner", "role": "owner", "name": "Synthetic owner"}

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def tx(self, callback):
        return await atomic_owner(self.db, self.owner, callback)

    def test_parser_classifies_real_export_patterns_without_customer_pii(self):
        parsed = parse_salla_order_xlsx(workbook_bytes(source_rows()))
        self.assertEqual(parsed["errors"], [])
        rows = {row["order_number"]: row for row in parsed["rows"]}

        self.assertEqual(rows["ORD-MADA"]["accounting_provider"], "salla")
        self.assertEqual(rows["ORD-MADA"]["status"], "ready_for_provider_resolution")
        self.assertEqual(rows["ORD-TABBY"]["accounting_provider"], "tabby")
        self.assertEqual(rows["ORD-TABBY"]["status"], "ready_for_provider_resolution")

        self.assertEqual(rows["ORD-REFUND"]["status"], "ready_sale_refund_pending_evidence")
        self.assertIn("refund_provider_identity_required", rows["ORD-REFUND"]["review_reasons"])

        self.assertEqual(rows["ORD-CONFLICT"]["status"], "needs_review")
        self.assertIn("payment_amount_conflict", rows["ORD-CONFLICT"]["review_reasons"])

        self.assertEqual(rows["ORD-EXECUTED"]["status"], "needs_review")
        self.assertIn("fulfilment_timestamp_required", rows["ORD-EXECUTED"]["review_reasons"])

        self.assertEqual(rows["ORD-COD"]["status"], "waiting_p02_cod")
        self.assertEqual(rows["ORD-BANK"]["status"], "needs_bank_transfer_evidence")

        self.assertEqual(rows["ORD-FX"]["status"], "ready_for_provider_resolution")
        self.assertEqual(rows["ORD-FX"]["original_currency"], "AED")
        self.assertEqual(rows["ORD-FX"]["original_amount"], "343.38")
        self.assertEqual(rows["ORD-FX"]["current_net_sar"], "351.00")
        self.assertEqual(rows["ORD-FX"]["payment_reference"]["amount"], "351.00")

        self.assertEqual(rows["ORD-UNKNOWN"]["status"], "needs_review")
        self.assertIn("payment_method_unknown", rows["ORD-UNKNOWN"]["review_reasons"])

        forbidden = {"اسم العميل", "رقم الجوال", "عنوان العميل", "بريد العميل"}
        for row in parsed["rows"]:
            self.assertTrue(forbidden.isdisjoint(row))

    async def test_import_is_nonfinancial_and_duplicate_file_is_idempotent(self):
        content = workbook_bytes(source_rows())
        parsed = parse_salla_order_xlsx(content)
        first = await self.tx(lambda scoped: import_salla_order_evidence(
            scoped,
            owner=self.owner,
            actor=self.actor,
            filename="salla-orders.xlsx",
            content=content,
            parsed=parsed,
        ))
        self.assertEqual(first["status"], "imported")
        self.assertEqual(first["file"]["row_count"], len(source_rows()))
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)
        self.assertEqual(await self.db.payment_transactions.count_documents({}), 0)
        self.assertEqual(await self.db.orders_db.count_documents({}), 0)
        self.assertEqual(await self.db.mz2_salla_order_evidence.count_documents({}), len(source_rows()))
        self.assertEqual(await self.db.mz2_salla_order_snapshots.count_documents({}), len(source_rows()))
        self.assertEqual(await self.db.accounting_source_files.count_documents({}), 1)

        before = {
            "files": await self.db.mz2_salla_order_files.count_documents({}),
            "current": await self.db.mz2_salla_order_evidence.count_documents({}),
            "snapshots": await self.db.mz2_salla_order_snapshots.count_documents({}),
            "source_files": await self.db.accounting_source_files.count_documents({}),
        }
        second = await self.tx(lambda scoped: import_salla_order_evidence(
            scoped,
            owner=self.owner,
            actor=self.actor,
            filename="renamed-same-file.xlsx",
            content=content,
            parsed=parsed,
        ))
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(
            {
                "files": await self.db.mz2_salla_order_files.count_documents({}),
                "current": await self.db.mz2_salla_order_evidence.count_documents({}),
                "snapshots": await self.db.mz2_salla_order_snapshots.count_documents({}),
                "source_files": await self.db.accounting_source_files.count_documents({}),
            },
            before,
        )
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

    async def test_newer_export_updates_current_projection_but_preserves_history(self):
        rows = source_rows()[:1]
        first_content = workbook_bytes(rows)
        first = await self.tx(lambda scoped: import_salla_order_evidence(
            scoped,
            owner=self.owner,
            actor=self.actor,
            filename="day1.xlsx",
            content=first_content,
            parsed=parse_salla_order_xlsx(first_content),
        ))
        first_snapshot = first["items"][0]["id"]

        newer = [dict(rows[0])]
        newer[0]["تاريخ آخر تحديث للطلب"] = "2026-09-22 12:00"
        newer[0]["شركة الشحن / الفرع"] = "سمسا"
        newer[0]["رقم البوليصة"] = "WAY-NEW"
        second_content = workbook_bytes(newer)
        second = await self.tx(lambda scoped: import_salla_order_evidence(
            scoped,
            owner=self.owner,
            actor=self.actor,
            filename="day2.xlsx",
            content=second_content,
            parsed=parse_salla_order_xlsx(second_content),
        ))
        self.assertEqual(second["status"], "imported")
        current = await self.db.mz2_salla_order_evidence.find_one(
            {"user_id": self.owner, "order_number": "ORD-MADA"},
            {"_id": 0},
        )
        self.assertEqual(current["shipping_company"], "سمسا")
        self.assertEqual(current["waybill"], "WAY-NEW")
        self.assertNotEqual(current["latest_snapshot_id"], first_snapshot)
        self.assertEqual(await self.db.mz2_salla_order_snapshots.count_documents({}), 2)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

    async def test_same_source_version_changed_becomes_review_conflict(self):
        rows = source_rows()[:1]
        first_content = workbook_bytes(rows)
        await self.tx(lambda scoped: import_salla_order_evidence(
            scoped,
            owner=self.owner,
            actor=self.actor,
            filename="first.xlsx",
            content=first_content,
            parsed=parse_salla_order_xlsx(first_content),
        ))

        changed = [dict(rows[0])]
        changed[0]["صافي المبيعات"] = 181.55
        changed[0]["إجمالي الطلب بالعملة الأصلية"] = 181.55
        # Keep exact same last-update source version to prove conflict.
        second_content = workbook_bytes(changed)
        second = await self.tx(lambda scoped: import_salla_order_evidence(
            scoped,
            owner=self.owner,
            actor=self.actor,
            filename="changed.xlsx",
            content=second_content,
            parsed=parse_salla_order_xlsx(second_content),
        ))
        self.assertEqual(second["file"]["conflict_count"], 1)
        current = await self.db.mz2_salla_order_evidence.find_one(
            {"user_id": self.owner, "order_number": "ORD-MADA"},
            {"_id": 0},
        )
        self.assertTrue(current["conflict"])
        self.assertEqual(current["status"], "needs_review")
        self.assertIn("same_source_version_changed", current["review_reasons"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)


if __name__ == "__main__":
    unittest.main()
