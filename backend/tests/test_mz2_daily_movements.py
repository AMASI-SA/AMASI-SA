"""Real-Mongo contract for MZ2 bank-statement / daily movement intake."""
import io
import os
from uuid import uuid4
import unittest

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from openpyxl import Workbook

from accounting_atomic import atomic_owner
from accounting_daily_movements import (
    ManualIncomingMovementIn,
    ProviderConfirmIn,
    confirm_daily_movement_provider,
    create_manual_incoming_movement,
    import_daily_movement_file,
    parse_daily_movement_xlsx,
)


def workbook_bytes(rows, headers=None):
    wb = Workbook()
    ws = wb.active
    ws.append(headers or [
        "تاريخ الحركة",
        "إيداع",
        "سحب",
        "البيان",
        "رقم المرجع",
        "المنصة",
    ])
    for row in rows:
        ws.append(row)
    stream = io.BytesIO()
    wb.save(stream)
    wb.close()
    return stream.getvalue()


class DailyMovementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(
            os.environ["MZ2_TEST_MONGO_URI"],
            serverSelectionTimeoutMS=5000,
        )
        self.db = self.mongo["mz2_daily_movements_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.actor = {
            "id": "owner",
            "role": "owner",
            "name": "Synthetic owner",
        }
        await self.db.users.insert_one({**self.actor, "is_active": True})
        await self.db.accounts.insert_one({
            "id": "bank-inma",
            "user_id": "owner",
            "name": "Synthetic Alinma",
            "account_type": "bank",
            "status": "active",
        })
        await self.db.accounting_provider_bank_bindings_v2.insert_many([
            {
                "user_id": "owner",
                "provider": "tabby",
                "bank_account_id": "bank-inma",
                "verification_status": "verified",
                "source_kind": "owner_confirmed",
            },
            {
                "user_id": "owner",
                "provider": "tamara",
                "bank_account_id": "bank-inma",
                "verification_status": "verified",
                "source_kind": "owner_confirmed",
            },
        ])

    async def asyncTearDown(self):
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def tx(self, callback):
        return await atomic_owner(self.db, "owner", callback)

    async def import_file(self, content, filename="bank.xlsx"):
        parsed = parse_daily_movement_xlsx(content)
        self.assertEqual(parsed["errors"], [])
        return await self.tx(lambda scoped: import_daily_movement_file(
            scoped,
            owner="owner",
            actor=self.actor,
            bank_account_id="bank-inma",
            filename=filename,
            content=content,
            parsed=parsed,
        ))

    async def test_explicit_provider_auto_receipt_suggestion_requires_confirmation(self):
        content = workbook_bytes([
            ["2026-09-21", 41133.07, 0, "Tabby settlement transfer", "TBY-001", "tabby"],
            ["2026-09-21", 2500, 0, "Tamara Merchant payout", "TAM-001", ""],
            ["2026-09-21", 0, 4000, "Payroll payment", "PAY-001", ""],
        ])
        result = await self.import_file(content)
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["provider_receipts_created"], 1)
        self.assertEqual(result["needs_review"], 1)
        self.assertEqual(result["unclassified"], 1)

        rows = {row["reference"]: row for row in result["items"]}
        self.assertEqual(rows["TBY-001"]["status"], "provider_receipt_created")
        self.assertEqual(rows["TBY-001"]["confirmed_provider"], "tabby")
        self.assertTrue(rows["TBY-001"]["receipt_id"])
        self.assertEqual(rows["TAM-001"]["status"], "needs_review")
        self.assertEqual(rows["TAM-001"]["suggested_provider"], "tamara")
        self.assertIsNone(rows["TAM-001"]["receipt_id"])
        self.assertEqual(rows["PAY-001"]["status"], "unclassified")

        receipts = await self.db.mz2_bank_receipts.find({}).to_list(10)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["provider"], "tabby")
        self.assertEqual(receipts[0]["bank_account_id"], "bank-inma")
        self.assertEqual(receipts[0]["amount"], "41133.07")
        self.assertEqual(receipts[0]["source"], "bank_statement_import")
        self.assertEqual(receipts[0]["source_daily_movement_id"], rows["TBY-001"]["id"])
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

        payload = ProviderConfirmIn(provider="tamara", reason="Verified sender in bank evidence")
        confirmed = await self.tx(lambda scoped: confirm_daily_movement_provider(
            scoped,
            owner="owner",
            actor=self.actor,
            movement_id=rows["TAM-001"]["id"],
            payload=payload,
        ))
        self.assertEqual(confirmed["status"], "provider_receipt_created")
        self.assertEqual(confirmed["confirmed_provider"], "tamara")
        self.assertEqual(confirmed["receipt"]["provider"], "tamara")
        self.assertEqual(await self.db.mz2_bank_receipts.count_documents({}), 2)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

    async def test_duplicate_file_is_idempotent_and_reference_is_cross_file_unique(self):
        first_content = workbook_bytes([
            ["2026-09-21", 100, 0, "Incoming no provider", "BANK-REF-1", ""],
        ])
        first = await self.import_file(first_content, "first.xlsx")
        counts = {
            "files": await self.db.mz2_daily_movement_files.count_documents({}),
            "rows": await self.db.mz2_daily_movements.count_documents({}),
            "sources": await self.db.accounting_source_files.count_documents({}),
        }
        duplicate = await self.import_file(first_content, "renamed.xlsx")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(
            {
                "files": await self.db.mz2_daily_movement_files.count_documents({}),
                "rows": await self.db.mz2_daily_movements.count_documents({}),
                "sources": await self.db.accounting_source_files.count_documents({}),
            },
            counts,
        )
        self.assertEqual(duplicate["file"]["id"], first["file"]["id"])

        second_content = workbook_bytes([
            ["2026-09-22", 101, 0, "Different file same bank reference", "BANK-REF-1", ""],
        ])
        parsed = parse_daily_movement_xlsx(second_content)
        before = {
            "files": await self.db.mz2_daily_movement_files.count_documents({}),
            "rows": await self.db.mz2_daily_movements.count_documents({}),
            "sources": await self.db.accounting_source_files.count_documents({}),
        }
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: import_daily_movement_file(
                scoped,
                owner="owner",
                actor=self.actor,
                bank_account_id="bank-inma",
                filename="second.xlsx",
                content=second_content,
                parsed=parsed,
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "bank_reference_already_imported")
        self.assertEqual(
            {
                "files": await self.db.mz2_daily_movement_files.count_documents({}),
                "rows": await self.db.mz2_daily_movements.count_documents({}),
                "sources": await self.db.accounting_source_files.count_documents({}),
            },
            before,
        )

    async def test_provider_binding_mismatch_never_creates_receipt(self):
        await self.db.accounts.insert_one({
            "id": "bank-other",
            "user_id": "owner",
            "name": "Other bank",
            "account_type": "bank",
            "status": "active",
        })
        content = workbook_bytes([
            ["2026-09-21", 500, 0, "Tabby settlement", "TBY-OTHER", "tabby"],
        ])
        parsed = parse_daily_movement_xlsx(content)
        result = await self.tx(lambda scoped: import_daily_movement_file(
            scoped,
            owner="owner",
            actor=self.actor,
            bank_account_id="bank-other",
            filename="other.xlsx",
            content=content,
            parsed=parsed,
        ))
        self.assertEqual(result["provider_receipts_created"], 0)
        self.assertEqual(result["unclassified"], 1)
        self.assertEqual(await self.db.mz2_bank_receipts.count_documents({}), 0)

        row = result["items"][0]
        payload = ProviderConfirmIn(provider="tabby", reason="Try wrong bank")
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: confirm_daily_movement_provider(
                scoped,
                owner="owner",
                actor=self.actor,
                movement_id=row["id"],
                payload=payload,
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "provider_bank_binding_mismatch")
        self.assertEqual(await self.db.mz2_bank_receipts.count_documents({}), 0)

    def test_parser_supports_amount_direction_and_rejects_bad_rows(self):
        content = workbook_bytes(
            [
                ["2026-09-21", 123.45, "وارد", "Manual incoming", "REF-A", "salla"],
                ["2026-09-21", 80, "صادر", "Manual outgoing", "REF-B", ""],
            ],
            headers=["date", "amount", "direction", "description", "reference", "provider"],
        )
        parsed = parse_daily_movement_xlsx(content)
        self.assertEqual(parsed["errors"], [])
        self.assertEqual([row["direction"] for row in parsed["rows"]], ["in", "out"])
        self.assertEqual(parsed["rows"][0]["amount"], "123.45")

        bad = workbook_bytes([
            ["2026-09-21", 100, 50, "Both credit and debit", "BAD-1", ""],
        ])
        parsed_bad = parse_daily_movement_xlsx(bad)
        self.assertEqual(parsed_bad["rows"], [])
        self.assertEqual(parsed_bad["errors"][0]["code"], "movement_credit_debit_conflict")


    async def test_manual_incoming_transfer_keeps_bank_amount_sender_and_date_without_gl(self):
        payload = ManualIncomingMovementIn(
            bank_account_id="bank-inma",
            amount="750.25",
            sender_name="أحمد محمد",
            movement_date="2026-09-21",
            reference="manual-ref-001",
            notes="تحويل عميل",
            request_id="REQ-MANUAL-001",
        )
        first = await self.tx(lambda scoped: create_manual_incoming_movement(
            scoped,
            owner="owner",
            actor=self.actor,
            payload=payload,
        ))
        self.assertEqual(first["bank_account_id"], "bank-inma")
        self.assertEqual(first["amount"], "750.25")
        self.assertEqual(first["sender_name"], "أحمد محمد")
        self.assertEqual(first["movement_date"], "2026-09-21")
        self.assertEqual(first["reference"], "MANUAL-REF-001")
        self.assertEqual(first["direction"], "in")
        self.assertEqual(first["source"], "manual_accountant")
        self.assertEqual(first["status"], "unclassified")
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

        duplicate = await self.tx(lambda scoped: create_manual_incoming_movement(
            scoped,
            owner="owner",
            actor=self.actor,
            payload=payload,
        ))
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["id"], first["id"])
        self.assertEqual(await self.db.mz2_daily_movements.count_documents({}), 1)

        changed = payload.model_copy(update={"amount": "751.25"})
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: create_manual_incoming_movement(
                scoped,
                owner="owner",
                actor=self.actor,
                payload=changed,
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "manual_movement_request_conflict")
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

    async def test_manual_platform_sender_can_follow_same_provider_receipt_flow(self):
        payload = ManualIncomingMovementIn(
            bank_account_id="bank-inma",
            amount="2300.00",
            sender_name="شركة تابي",
            movement_date="2026-09-21",
            reference="TBY-MANUAL-001",
            notes="تحويل تسوية",
            request_id="REQ-MANUAL-TABBY",
        )
        movement = await self.tx(lambda scoped: create_manual_incoming_movement(
            scoped,
            owner="owner",
            actor=self.actor,
            payload=payload,
        ))
        self.assertEqual(movement["status"], "needs_review")
        self.assertEqual(movement["suggested_provider"], "tabby")
        self.assertEqual(await self.db.mz2_bank_receipts.count_documents({}), 0)

        confirmed = await self.tx(lambda scoped: confirm_daily_movement_provider(
            scoped,
            owner="owner",
            actor=self.actor,
            movement_id=movement["id"],
            payload=ProviderConfirmIn(
                provider="tabby",
                reason="اسم المحول وتفاصيل البنك تؤكد تابي",
            ),
        ))
        self.assertEqual(confirmed["status"], "provider_receipt_created")
        self.assertEqual(confirmed["confirmed_provider"], "tabby")
        self.assertEqual(confirmed["receipt"]["amount"], "2300.00")
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

    async def test_later_statement_confirms_same_manual_reference_without_duplicate_row(self):
        payload = ManualIncomingMovementIn(
            bank_account_id="bank-inma",
            amount="510.00",
            sender_name="عميل تجريبي",
            movement_date="2026-09-21",
            reference="BANK-MANUAL-RECON",
            notes="",
            request_id="REQ-MANUAL-RECON",
        )
        manual = await self.tx(lambda scoped: create_manual_incoming_movement(
            scoped,
            owner="owner",
            actor=self.actor,
            payload=payload,
        ))
        self.assertEqual(await self.db.mz2_daily_movements.count_documents({}), 1)

        content = workbook_bytes([
            ["2026-09-21", 510, 0, "Incoming customer transfer", "BANK-MANUAL-RECON", ""],
        ])
        result = await self.import_file(content, "later-statement.xlsx")
        self.assertEqual(result["status"], "imported")
        self.assertEqual(await self.db.mz2_daily_movements.count_documents({}), 1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], manual["id"])
        self.assertEqual(result["items"][0]["source"], "manual_reconciled_bank_statement")
        self.assertEqual(
            set(result["items"][0]["evidence_sources"]),
            {"manual_accountant", "bank_statement_import"},
        )
        self.assertEqual(result["items"][0]["sender_name"], "عميل تجريبي")
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)

    async def test_later_statement_with_same_manual_reference_but_changed_amount_fails_closed(self):
        payload = ManualIncomingMovementIn(
            bank_account_id="bank-inma",
            amount="510.00",
            sender_name="عميل تجريبي",
            movement_date="2026-09-21",
            reference="BANK-MANUAL-CONFLICT",
            notes="",
            request_id="REQ-MANUAL-CONFLICT",
        )
        await self.tx(lambda scoped: create_manual_incoming_movement(
            scoped,
            owner="owner",
            actor=self.actor,
            payload=payload,
        ))
        content = workbook_bytes([
            ["2026-09-21", 511, 0, "Changed bank amount", "BANK-MANUAL-CONFLICT", ""],
        ])
        parsed = parse_daily_movement_xlsx(content)
        with self.assertRaises(HTTPException) as ctx:
            await self.tx(lambda scoped: import_daily_movement_file(
                scoped,
                owner="owner",
                actor=self.actor,
                bank_account_id="bank-inma",
                filename="conflict.xlsx",
                content=content,
                parsed=parsed,
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "manual_movement_bank_statement_conflict")
        self.assertEqual(await self.db.mz2_daily_movements.count_documents({}), 1)
        self.assertEqual(await self.db.mz2_daily_movement_files.count_documents({}), 0)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 0)


if __name__ == "__main__":
    unittest.main()
