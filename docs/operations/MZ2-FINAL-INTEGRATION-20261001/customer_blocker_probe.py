"""Synthetic customer blocker evidence; NOT successful end-to-end acceptance.

Run with PYTHONPATH=backend;backend/tests and MZ2_TEST_MONGO_URI pointing at
an unauthenticated loopback replica set. No historical activation or mocks.
"""
import asyncio
import json
import os
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_atomic import atomic_owner
from accounting_daily_movements import import_daily_movement_file, parse_daily_movement_xlsx
from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes, post_opening_journal_v2, verify_active_opening_v2
from accounting_module_contract import OPERATION_ID, EVIDENCE_SECTIONS
from accounting_module_readiness import build_accounting_module_status
from accounting_mz2_reports import read_mz2_ledger
from accounting_shipping_native import _leg

OWNER = "synthetic-customer-blocker-owner"
CUT = "2026-09-01T00:00:00.000000Z"

async def snapshot(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(None)
            for name in sorted(await db.list_collection_names())
            if await db[name].count_documents({})}

async def main():
    uri = os.environ["MZ2_TEST_MONGO_URI"]
    parsed = urlsplit(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not parsed.username and not parsed.password and parsed.path in {"", "/"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    name = "mz2_customer_blocker_probe_" + uuid4().hex
    db = client[name]
    actor = {"id": OWNER, "role": "owner", "name": "Synthetic owner", "is_active": True}
    try:
        assert (await db.command("hello")).get("setName")
        await ensure_accounting_ledger_v2_indexes(db)
        await db.users.insert_one(actor)
        await db.mz2_atomic_owners.insert_one({"_id": OWNER, "revision": 0, "writes_paused": False,
            "control_revision": 0, "ledger_backend_state": "v2_active", "ledger_backend_revision": 1,
            "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "synthetic-only"})
        await db.mz2_financial_accounts.insert_one({"user_id": OWNER, "id": "bank-f", "name": "Synthetic bank",
            "account_type": "bank", "status": "active", "currency": "SAR"})
        # Same sealed native opening pattern as shipping_p02_native_fixture.py.
        async with await client.start_session() as session:
            async def opening(active):
                return await post_opening_journal_v2(db, user_id=OWNER, actor_id=OWNER, actor_name=OWNER,
                    opening_operation_id="synthetic-customer-opening", approved_preview_hash="a" * 64,
                    effective_at=CUT, entries=[
                        _leg(("bank", "bank-f", "main"), "debit", Decimal("1000"), "bank", "opening_balance"),
                        _leg(("equity", "opening_balance_equity", "main"), "credit", Decimal("1000"), "equity", "opening_balance")],
                    mongo_session=active)
            result = await session.with_transaction(opening)
        group = result["group"]["txn_group_id"]
        state = {"operation_id": OPERATION_ID, "status": "active", "cutover_at": CUT,
            "ledger_source": "accounting_v2_operation_scoped", "opening_active_txn_group_id": group,
            "opening_root_txn_group_id": group, "opening_balance_txn_group_id": group,
            "evidence_sheet_ref": "synthetic", "evidence_sections": {s["id"]: "synthetic" for s in EVIDENCE_SECTIONS},
            "opening_balance_preview_id": "synthetic", "opening_balance_preview_balanced": True,
            "opening_balance_approved_at": CUT, "opening_balance_approved_by": OWNER,
            "opening_balance_zero_accounts": [{"entity_type": "payment_gateway", "entity_id": "tamara",
                "sub_account": "receivable", "evidence_ref": "synthetic-zero", "accounting_at": CUT,
                "opening_balance_txn_group_id": group}]}
        await db.settings.insert_one({"user_id": OWNER, "mezan2_financial_cutover": state})
        verified = await verify_active_opening_v2(db, user_id=OWNER, cutover=state)
        assert verified
        assert build_accounting_module_status(state, opening_posted_verified=verified)["cutover"]["safe_active"]
        scope = await read_mz2_ledger(db, owner=OWNER)
        assert scope["status"] == "available", scope
        print(json.dumps({"native_opening_verified": True, "safe_active": True, "readiness": scope["status"]}))

        async def blocked(label, callback):
            before = await snapshot(db)
            try:
                await callback()
            except HTTPException as exc:
                code = exc.detail.get("code") if isinstance(exc.detail, dict) else exc.detail
                assert exc.status_code == 423 and code == "accounting_legacy_writer_disabled", (label, exc.detail)
                assert await snapshot(db) == before, label + " mutated persisted documents"
                print(json.dumps({"attempt": label, "status": exc.status_code, "code": code,
                    "persisted_documents_unchanged": True, "customer_acceptance": False}))
            else:
                raise AssertionError(label + " unexpectedly succeeded; reassess blocker")

        from accounting_customer_advances import recognize_advance
        # Source schema follows test_mz2_customer_advances / receivable workflow.
        # Synthetic capture facts only; no provider or Production request.
        await db.payment_transactions.insert_one(dict(id="SYN-CAPTURE-LOCAL", user_id=OWNER,
            provider="tamara", provider_id="SYN-CAPTURE", order_reference_id="SYN-ADVANCE-ORDER",
            amount="115.00", captured_amount="115.00", currency="SAR", status="captured",
            source="synthetic_import", captured_at="2026-09-21T11:00:00Z"))
        await db.orders_db.insert_one(dict(id="SYN-ADVANCE-ORDER", user_id=OWNER,
            order_number="SYN-ADVANCE-ORDER", total_amount="115.00", currency="SAR",
            payment_method="tamara", order_status="pending", order_created_at="2026-09-20T10:00:00Z"))
        await blocked("customer_advance_capture", lambda: recognize_advance(db, owner=OWNER, actor=actor,
            provider="tamara", payment_id="SYN-CAPTURE", evidence_ref="SYN-CAPTURE-DOCUMENT",
            original_tax_amount="0", tax_review_ref="SYN-REVIEW-NO-ORIGINAL-TAX"))

        from accounting_bank_transfer_bindings import BankTransferBindingIn, save_bank_transfer_binding
        from accounting_bank_transfer_receipts import approve_receipt
        from test_mz2_bank_transfer_receipts import BankTransferReceiptTests
        await atomic_owner(db, OWNER, lambda scoped: save_bank_transfer_binding(scoped, OWNER, actor,
            BankTransferBindingIn(upstream_source="salla.payment_method_bank", upstream_value="rajhi-bank",
                financial_account_id="bank-f", confirmation="CONFIRM_MZ2_BANK_TRANSFER_BINDING",
                evidence_ref="synthetic-explicit-binding")))
        # Reuse existing real XLSX parsing/import and receipt-draft helpers only;
        # deliberately do not invoke their legacy opening or activation setup.
        case = BankTransferReceiptTests()
        case.db, case.owner, case.actor = db, OWNER, actor
        _, evidence = await case.import_order("SYN-BANK-ORDER")
        review = await case.upload_receipt(evidence)
        from test_mz2_bank_transfer_receipts import bank_xlsx as transfer_xlsx
        content = transfer_xlsx(reference="SYN-CUSTOMER-BANK")
        parsed_sheet = parse_daily_movement_xlsx(content)
        assert not parsed_sheet["errors"]
        imported = await atomic_owner(db, OWNER, lambda scoped: import_daily_movement_file(scoped,
            owner=OWNER, actor=actor, bank_account_id="bank-f", filename="SYN-customer-bank.xlsx",
            content=content, parsed=parsed_sheet))
        movement = imported["items"][0]
        await blocked("bank_transfer_receipt_approval", lambda: approve_receipt(db, owner=OWNER, actor=actor,
            review_id=review["id"], movement_id=movement["id"]))
        from accounting_daily_movements import OutgoingMovementClassifyIn, classify_outgoing_movement
        from test_mz2_employee_finance import bank_xlsx as outgoing_xlsx
        content = outgoing_xlsx([["2026-09-21", 0, 50, "Synthetic office rent", "SYN-RENT", ""]])
        parsed_sheet = parse_daily_movement_xlsx(content)
        assert not parsed_sheet["errors"]
        imported = await atomic_owner(db, OWNER, lambda scoped: import_daily_movement_file(scoped,
            owner=OWNER, actor=actor, bank_account_id="bank-f", filename="SYN-rent.xlsx",
            content=content, parsed=parsed_sheet))
        movement = imported["items"][0]
        await blocked("daily_general_expense_rent", lambda: atomic_owner(db, OWNER,
            lambda scoped: classify_outgoing_movement(scoped, owner=OWNER, actor=actor,
                movement_id=movement["id"], payload=OutgoingMovementClassifyIn(action="expense",
                    expense_category="rent", reason="Synthetic documented office rent"))))
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.accounting_journal_groups_v2.count_documents({}) == 1
        assert await db.accounting_general_ledger_v2.count_documents({}) == 2
        print(json.dumps({"not_exercised": ["advance_cancel", "advance_refund_payment", "bank_transfer_delivery_conversion"],
            "reason": "initial capture and receipt approval cannot commit; no fabricated posted provenance"}))
    finally:
        assert db.name == name and name.startswith("mz2_customer_blocker_probe_")
        await client.drop_database(name)
        client.close()
        print(json.dumps({"synthetic_database_removed": True}))

if __name__ == "__main__":
    asyncio.run(main())
