"""Bounded, read-only tenant-scope diagnostic for the owner's personal balance.

This diagnoses whether the selected account is attached to a financial dataset
before considering owner-self authorization. No salary, name, private customer
data, Mongo connection string, or employee identifier is emitted.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path


def _active(row):
    return bool(row) and all(not row.get(k) for k in ("deleted", "deleted_at", "disabled",
        "archived", "is_archived", "is_deleted")) and row.get("active") is not False and row.get("is_active") is not False


async def _upto2(collection, query):
    """Only check no record, one record or multiple; never scan/project PII."""
    cursor = collection.find(query, {"_id": 1}).limit(2).max_time_ms(2000)
    try:
        return len(await cursor.to_list(length=2))
    finally:
        await cursor.close()


async def audit(db, email):
    owner_candidates = await db.users.find({
        "email": email,
        "$or": [{"role": "owner"}, {"is_owner": True}],
    }, {"_id": 0, "id": 1, "role": 1, "is_owner": 1, "disabled": 1,
        "active": 1, "is_active": 1, "linked_employee_id": 1}
    ).limit(2).max_time_ms(2000).to_list(length=2)
    if len(owner_candidates) != 1 or not _active(owner_candidates[0]):
        return {"read_only": True, "result": "OWNER_ACCOUNT_NOT_UNIQUELY_RESOLVED"}
    row = owner_candidates[0]
    owner = row["id"]

    # The financial reader requires an EXACT owner/employee identity. Inspect
    # the presence of each existing data source, never infer one from a name.
    counts = {
        "v2_employee_records": await _upto2(db.mezan_employees_v2, {"user_id": owner}),
        "v2_salary_contracts": await _upto2(db.mezan_employee_salary_contracts_v2, {"user_id": owner}),
        "legacy_employee_salary_records": await _upto2(db.operating_salaries,
            {"user_id": owner, "category": "employee"}),
        "legacy_other_salary_records": await _upto2(db.operating_salaries,
            {"user_id": owner, "category": {"$ne": "employee"}}),
        "owner_direct_v2_links": await _upto2(db.mezan_employees_v2,
            {"user_id": owner, "account_user_id": owner}),
        "owner_reverse_v2_links": await _upto2(db.mezan_employees_v2,
            {"user_id": owner, "id": row.get("linked_employee_id")})
            if row.get("linked_employee_id") else 0,
        "owner_ledger_state_exact": await _upto2(db.operational_balance_states_v1,
            {"_id": owner, "owner_id": owner}),
        "owner_ledger_by_owner_id": await _upto2(db.operational_balance_states_v1,
            {"owner_id": owner}),
        "any_ledger_state_in_this_database": await _upto2(db.operational_balance_states_v1, {}),
        "owner_general_ledger_entries": await _upto2(db.general_ledger, {"user_id": owner}),
        "owner_employee_financial_events": await _upto2(db.mz2_employee_financial_events,
            {"user_id": owner}),
    }
    ledger = bool(counts["owner_ledger_state_exact"])
    if not counts["any_ledger_state_in_this_database"]:
        decision = "DATABASE_HAS_NO_OPERATIONAL_STATES_VERIFY_ACTIVE_RUNTIME_DB"
    elif not ledger:
        decision = "OWNER_LEDGER_NOT_FOUND_IN_SELECTED_DATABASE"
    elif (counts["v2_salary_contracts"] or counts["legacy_employee_salary_records"]
          or counts["legacy_other_salary_records"]) and not counts["owner_direct_v2_links"]:
        decision = "SALARY_SOURCE_PRESENT_OWNER_ACCOUNT_LINK_UNVERIFIED"
    elif not (counts["v2_salary_contracts"] or counts["legacy_employee_salary_records"]
              or counts["legacy_other_salary_records"]):
        decision = "NO_SALARY_SOURCE_IN_SELECTED_OWNER_SCOPE"
    else:
        decision = "OWNER_FINANCIAL_SCOPE_REQUIRES_AUTHENTICATED_SELF_TEST"

    # Count 2 means 2+; not a comprehensive tenant inventory.
    return {"read_only": True, "counts_are_capped_at": 2,
            "owner_account_unique": True,
            "owner_reverse_link_field_present": bool(row.get("linked_employee_id")),
            "sources": counts, "result": decision}


async def run(email):
    try:
        from dotenv import load_dotenv
        load_dotenv(Path.cwd() / "backend" / ".env", override=False)
    except ImportError:
        pass
    uri, database = os.getenv("MONGO_URL"), os.getenv("DB_NAME")
    if not uri or not database:
        return {"read_only": True, "result": "DB_CONFIGURATION_UNAVAILABLE"}
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ModuleNotFoundError:
        return {"read_only": True, "result": "MONGO_DRIVER_UNAVAILABLE"}
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=4000)
    try:
        return await asyncio.wait_for(audit(client[database], email), timeout=15)
    finally:
        client.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner-email", required=True)
    args = ap.parse_args()
    try:
        if "@" not in args.owner_email:
            raise ValueError("bad account")
        result = asyncio.run(run(args.owner_email.strip()))
    except Exception:
        result = {"read_only": True, "result": "SCOPE_PROBE_UNAVAILABLE"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
