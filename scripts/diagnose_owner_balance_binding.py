"""Read-only owner payroll-to-balance binding diagnostic.

Run on the trusted Mezan server, never expose owner/employee IDs, emails,
salary amounts, movement rows, tokens, or database credentials in stdout.
This tool makes no writes, creates no employee/financial identity, and never
resolves a ledger party from a person's display name.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient


def active(row):
    return bool(row) and not any(row.get(key) for key in (
        "disabled", "deleted", "is_deleted", "deleted_at", "archived", "is_archived"
    )) and row.get("is_active") is not False and row.get("active") is not False


def classify(direct, legacy, v2, contracts, ledger):
    if len(direct) > 1 or len(legacy) > 1 or len(v2) > 1:
        return "AMBIGUOUS_IDENTITY_REQUIRES_OWNER_REVIEW"
    if len(direct) == 1 and active(direct[0]) and direct[0].get("status") == "active":
        return "DIRECT_V2_ACCOUNT_LINK_PRESENT_CHECK_SESSION_PERMISSION"
    if len(legacy) == 1 and len(v2) == 1 and len(contracts) >= 1 and ledger:
        return "EXPLICIT_LEGACY_OWNER_LINK_WITH_V2_CONTRACT_REQUIRES_AUTHENTICATED_UAT"
    if len(legacy) == 0 and len(direct) == 0:
        return "NO_VERIFIED_OWNER_EMPLOYEE_LINK_DO_NOT_INFER_FROM_NAME"
    if not ledger:
        return "OPERATIONAL_LEDGER_NOT_FOUND"
    return "OWNER_FINANCIAL_SOURCE_INCOMPLETE_REQUIRES_DATA_REVIEW"


async def diagnose(db, email):
    # Exactly one enabled account. No fuzzy name matching or broad tenant scan.
    owners = await db.users.find({
        "email": email, "$or": [{"role": "owner"}, {"is_owner": True}],
    }, {"_id": 0, "id": 1, "role": 1, "is_owner": 1, "active": 1,
        "is_active": 1, "disabled": 1}).limit(2).to_list(2)
    if len(owners) != 1 or not active(owners[0]):
        return {"read_only": True, "result": "OWNER_NOT_FOUND_OR_NOT_UNIQUE"}
    owner_id = owners[0]["id"]
    direct = await db.mezan_employees_v2.find({
        "user_id": owner_id, "account_user_id": owner_id,
    }, {"_id": 0, "id": 1, "status": 1, "active": 1, "is_active": 1,
        "disabled": 1, "archived": 1}).limit(3).to_list(3)
    legacy = await db.operating_salaries.find({
        "user_id": owner_id, "category": "employee",
        "$or": [{"account_user_id": owner_id}, {"linked_user_id": owner_id},
                {"user_account_id": owner_id}],
    }, {"_id": 0, "id": 1}).limit(3).to_list(3)
    legacy_ids = [row.get("id") for row in legacy if row.get("id")]
    v2 = await db.mezan_employees_v2.find({
        "user_id": owner_id,
        "$or": [{"legacy_employee_id": {"$in": legacy_ids}},
                {"financial_entity_id": {"$in": legacy_ids}}],
    }, {"_id": 0, "id": 1, "status": 1, "account_user_id": 1}).limit(3).to_list(3) if legacy_ids else []
    ids = [row.get("id") for row in v2 if row.get("id")]
    contracts = await db.mezan_employee_salary_contracts_v2.find({
        "user_id": owner_id, "employee_id": {"$in": ids},
    }, {"_id": 0, "employee_id": 1, "status": 1}).limit(3).to_list(3) if ids else []
    ledger = await db.operational_balance_states_v1.find_one({
        "_id": owner_id, "owner_id": owner_id,
    }, {"_id": 1, "status": 1})
    return {
        "read_only": True,
        "direct_v2_owner_link_count": len(direct),
        "explicit_legacy_owner_link_count": len(legacy),
        "matching_v2_employee_count": len(v2),
        "matching_v2_salary_contract_count": len(contracts),
        "operational_ledger_present": bool(ledger),
        "result": classify(direct, legacy, v2, contracts, bool(ledger)),
    }


async def run(email):
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env", override=False)
    except ImportError:
        pass
    uri, db_name = os.getenv("MONGO_URL"), os.getenv("DB_NAME")
    if not uri or not db_name:
        return {"read_only": True, "result": "DB_CONFIGURATION_UNAVAILABLE"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=4000)
    try:
        return await asyncio.wait_for(diagnose(client[db_name], email), timeout=12)
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-email", required=True)
    args = parser.parse_args()
    if not args.owner_email or "@" not in args.owner_email:
        raise SystemExit("OWNER_EMAIL_INVALID")
    try:
        result = asyncio.run(run(args.owner_email.strip()))
    except Exception:
        # No raw exceptions: database drivers may embed sensitive connection info.
        result = {"read_only": True, "result": "DIAGNOSTIC_UNAVAILABLE"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
