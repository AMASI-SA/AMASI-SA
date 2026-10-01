"""Synthetic runtime blocker evidence; NOT successful payroll acceptance.

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
from accounting_employee_finance import (
    PayrollAccrualIn, MovementClassifyIn, accrue_payroll_period, classify_employee_movement,
)
from accounting_daily_movements import import_daily_movement_file, parse_daily_movement_xlsx
from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes, post_opening_journal_v2, verify_active_opening_v2
from accounting_module_contract import OPERATION_ID, EVIDENCE_SECTIONS
from accounting_module_readiness import build_accounting_module_status
from accounting_mz2_reports import read_mz2_ledger
from accounting_shipping_native import _leg
from test_mz2_employee_finance import bank_xlsx

OWNER = "synthetic-payroll-blocker-owner"
EMPLOYEE = "synthetic-payroll-employee"
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
    name = "mz2_payroll_blocker_probe_" + uuid4().hex
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
        await db.mezan_employees_v2.insert_one({"user_id": OWNER, "id": EMPLOYEE,
            "display_name": "Synthetic employee", "status": "active", "hire_date": "2026-09-01"})
        await db.mezan_employee_salary_contracts_v2.insert_one({"user_id": OWNER, "id": "synthetic-contract",
            "employee_id": EMPLOYEE, "contract_type": "monthly", "monthly_amount": 4000, "currency": "SAR",
            "effective_from": "2026-09-01", "effective_to": None, "status": "active", "payroll_state": "active",
            "suspension_periods": [], "source_authority": "mezan_employee_salary_contracts_v2", "version": 1})
        # Same sealed native opening pattern as shipping_p02_native_fixture.py.
        async with await client.start_session() as session:
            async def opening(active):
                return await post_opening_journal_v2(db, user_id=OWNER, actor_id=OWNER, actor_name=OWNER,
                    opening_operation_id="synthetic-payroll-opening", approved_preview_hash="a" * 64,
                    effective_at=CUT, entries=[
                        _leg(("bank", "bank-f", "main"), "debit", Decimal("1000"), "bank", "opening_balance"),
                        _leg(("employee", EMPLOYEE, "salary_payable"), "credit", Decimal("500"), "payable", "opening_balance"),
                        _leg(("equity", "opening_balance_equity", "main"), "credit", Decimal("500"), "equity", "opening_balance")],
                    mongo_session=active)
            result = await session.with_transaction(opening)
        group = result["group"]["txn_group_id"]
        state = {"operation_id": OPERATION_ID, "status": "active", "cutover_at": CUT,
            "ledger_source": "accounting_v2_operation_scoped", "opening_active_txn_group_id": group,
            "opening_root_txn_group_id": group, "opening_balance_txn_group_id": group,
            "evidence_sheet_ref": "synthetic", "evidence_sections": {s["id"]: "synthetic" for s in EVIDENCE_SECTIONS},
            "opening_balance_preview_id": "synthetic", "opening_balance_preview_balanced": True,
            "opening_balance_approved_at": CUT, "opening_balance_approved_by": OWNER,
            "opening_balance_zero_accounts": [{"entity_type": "employee", "entity_id": EMPLOYEE,
                "sub_account": sub, "evidence_ref": "synthetic-zero", "accounting_at": CUT,
                "opening_balance_txn_group_id": group} for sub in ("advance", "custody")]}
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
                await atomic_owner(db, OWNER, callback)
            except HTTPException as exc:
                code = exc.detail.get("code") if isinstance(exc.detail, dict) else exc.detail
                assert exc.status_code == 423 and code == "accounting_legacy_writer_disabled", (label, exc.detail)
                assert await snapshot(db) == before, label + " mutated persisted documents"
                print(json.dumps({"attempt": label, "status": exc.status_code, "code": code,
                    "persisted_documents_unchanged": True, "payroll_acceptance": False}))
            else:
                raise AssertionError(label + " unexpectedly succeeded; reassess blocker")

        await blocked("payroll_accrue", lambda scoped: accrue_payroll_period(scoped, owner=OWNER, actor=actor,
            payload=PayrollAccrualIn(period="2026-09", accrued_at="2026-09-21T00:30:00+03:00", employee_id=EMPLOYEE)))
        for action in ("salary_payment", "advance_grant", "custody_grant"):
            content = bank_xlsx([["2026-09-21", 0, 50, "Synthetic " + action, "SYN-" + action, ""]])
            parsed_sheet = parse_daily_movement_xlsx(content)
            assert not parsed_sheet["errors"]
            imported = await atomic_owner(db, OWNER, lambda scoped: import_daily_movement_file(scoped,
                owner=OWNER, actor=actor, bank_account_id="bank-f", filename="SYN-"+action+".xlsx",
                content=content, parsed=parsed_sheet))
            movement = imported["items"][0]
            await blocked(action, lambda scoped: classify_employee_movement(scoped, owner=OWNER, actor=actor,
                movement_id=movement["id"], payload=MovementClassifyIn(employee_id=EMPLOYEE,
                action=action, apply_open_advances=True, reason="Synthetic confirmed payroll blocker probe")))
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.accounting_journal_groups_v2.count_documents({}) == 1
        assert await db.accounting_general_ledger_v2.count_documents({}) == 3
    finally:
        assert db.name == name and name.startswith("mz2_payroll_blocker_probe_")
        await client.drop_database(name)
        client.close()
        print(json.dumps({"synthetic_database_removed": True}))

if __name__ == "__main__":
    asyncio.run(main())
