"""Opening consumes the actual Employee OS creator and canonical writer identity."""
from copy import deepcopy

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from accounting_onboarding_identities import identities, verify_mappings
from employee_payroll_status import require_employee_v2_identity
from tests.test_employee_salary_contract_management import db, create


def compiled(employee_id):
    return {"lines": [{"financial_account_id": None, "entity_type": "employee", "entity_id": employee_id,
                       "category": category, "sub_account": sub, "evidence_file_id": "owner-evidence"}
                      for category, sub in (("employee_salary_payable", "salary_payable"),
                                            ("employee_advance", "advance"), ("employee_custody", "custody"))],
            "section_evidence_file_ids": {}}


@pytest.mark.asyncio
async def test_native_created_employee_uses_writer_identity_without_historical_financial_alias(db):
    result = await create(db)
    identity = result["employee_id"]
    employee = await require_employee_v2_identity(db, "owner", identity)
    assert employee["financial_entity_id"] is None
    before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    catalog = await identities(db, "owner", "employee")
    assert catalog == [{"id": identity, "label": employee["display_name"], "kind": "employee",
                        "version": employee["version"], "financial_identity_ready": True,
                        "currency": None, "external_ref": None}]
    assert {row["id"] for row in await verify_mappings(db, "owner", compiled(identity), [])} == {identity}
    assert {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["foreign", "native:alias", "", " absent "])
async def test_opening_employee_does_not_accept_foreign_alias_or_unresolved_identity(db, invalid):
    await create(db)
    await db.mezan_employees_v2.insert_one({"id": "foreign", "user_id": "other", "status": "active"})
    with pytest.raises(HTTPException) as denied:
        await verify_mappings(db, "owner", compiled(invalid), [])
    assert denied.value.status_code == 409


@pytest.mark.asyncio
async def test_native_identity_unique_constraint_and_archived_employee_fail_closed(db):
    result = await create(db)
    identity = result["employee_id"]
    original = await db.mezan_employees_v2.find_one({"id": identity})
    duplicate = deepcopy(original)
    duplicate.pop("_id")
    with pytest.raises(DuplicateKeyError):
        await db.mezan_employees_v2.insert_one(duplicate)
    await db.mezan_employees_v2.update_one({"id": identity}, {"$set": {"archived": True}})
    assert await identities(db, "owner", "employee") == []
    with pytest.raises(HTTPException):
        await verify_mappings(db, "owner", compiled(identity), [])


@pytest.mark.asyncio
async def test_catalog_rejects_duplicate_native_source_before_unique_index_exists(db):
    row = {"id": "native", "user_id": "owner", "status": "active"}
    await db.mezan_employees_v2.insert_many([deepcopy(row), deepcopy(row)])
    with pytest.raises(HTTPException) as denied:
        await identities(db, "owner", "employee")
    assert denied.value.status_code == 409
