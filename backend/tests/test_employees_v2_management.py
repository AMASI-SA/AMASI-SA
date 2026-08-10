from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest
from fastapi import HTTPException

import employees_v2_routes as employee_routes
from employees_v2_routes import (
    PILOT_ACCOUNT_LINK_CONFIRMATION,
    PILOT_CREATE_CONFIRMATION,
    PILOT_ROLE_ASSIGNMENT_CONFIRMATION,
    PILOT_SOURCE_SYSTEM,
    _require_pilot_employee,
    build_employee_management_snapshot,
    make_employees_v2_router,
    normalize_pilot_employee_payload,
)


ROOT = Path(__file__).resolve().parents[2]


def _nested(document, key):
    value = document
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None, False
        value = value[part]
    return value, True


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, item) for item in expected):
                return False
            continue
        actual, exists = _nested(document, key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$exists" in expected and exists is not bool(expected["$exists"]):
                return False
            if "$type" in expected and expected["$type"] == "string" and not isinstance(actual, str):
                return False
            continue
        if actual != expected:
            return False
    return True


def _set_nested(document, key, value):
    target = document
    parts = key.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


class _WriteResult:
    def __init__(self, *, matched_count=0, upserted_id=None):
        self.matched_count = matched_count
        self.modified_count = matched_count
        self.upserted_id = upserted_id


class _Collection:
    def __init__(self):
        self.rows = []

    async def create_index(self, *_args, **_kwargs):
        return "index"

    async def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)

    async def insert_one(self, document):
        self.rows.append(copy.deepcopy(document))
        return _WriteResult(upserted_id=document.get("id"))

    async def find_one(self, query, _projection=None):
        for row in self.rows:
            if _matches(row, query):
                return copy.deepcopy(row)
        return None

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if not _matches(row, query):
                continue
            for key, value in (update.get("$set") or {}).items():
                _set_nested(row, key, copy.deepcopy(value))
            for key, value in (update.get("$inc") or {}).items():
                current, _exists = _nested(row, key)
                _set_nested(row, key, (current or 0) + value)
            return _WriteResult(matched_count=1)
        if upsert:
            document = {
                key: copy.deepcopy(value)
                for key, value in query.items()
                if not key.startswith("$") and not isinstance(value, dict)
            }
            document.update(copy.deepcopy(update.get("$setOnInsert") or {}))
            document.update(copy.deepcopy(update.get("$set") or {}))
            self.rows.append(document)
            return _WriteResult(upserted_id=document.get("id") or "upsert")
        return _WriteResult()


class _Database:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())

    def __getattr__(self, name):
        return self[name]


def _route_endpoint(router, path, method):
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in (route.methods or set())
    )


def test_pilot_payload_normalizes_employee_fields_without_enabling_payroll():
    result = normalize_pilot_employee_payload({
        "name": "  موظف تجريبي  ",
        "phone": "0500000000",
        "contact_email": "Pilot@Example.com",
        "job_title": "تجربة النظام",
        "department": "العمليات",
        "hire_date": "2026-08-10",
        "monthly_salary": "2500.125",
        "status": "draft",
        "notes": "لا يُفعّل ماليًا",
    })

    assert result == {
        "display_name": "موظف تجريبي",
        "phone": "0500000000",
        "contact_email": "pilot@example.com",
        "job_title": "تجربة النظام",
        "department": "العمليات",
        "notes": "لا يُفعّل ماليًا",
        "hire_date": "2026-08-10",
        "status": "draft",
        "monthly_salary": 2500.12,
    }


def test_pilot_never_accepts_an_active_payroll_status():
    with pytest.raises(ValueError, match="employee_pilot_status_invalid"):
        normalize_pilot_employee_payload({
            "name": "موظف تجريبي",
            "status": "active",
            "monthly_salary": 1000,
        })


def test_migrated_employee_is_not_mutable_during_pilot():
    with pytest.raises(HTTPException) as exc:
        _require_pilot_employee({
            "id": "empv2-migrated",
            "source": {"system": "mezan_legacy"},
            "management": {"mode": "shadow_read_only"},
        })

    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "employee_management_pilot_only",
        "migrated_employee_writes_enabled": False,
    }


def test_management_snapshot_reserves_arafat_and_excludes_used_or_privileged_accounts():
    snapshot = build_employee_management_snapshot(
        pilot_employees=[{
            "id": "pilot-1",
            "display_name": "موظف تجريبي",
            "status": "draft",
            "version": 1,
            "source": {"system": PILOT_SOURCE_SYSTEM},
            "management": {"mode": "pilot_only"},
            "created_at": "2026-08-10T10:00:00+00:00",
        }],
        salary_contracts=[{
            "id": "contract-1",
            "employee_id": "pilot-1",
            "monthly_amount": 1000,
            "payroll_enabled": False,
            "source_authority": "employees_v2_pilot_only",
        }],
        team_users=[
            {"id": "owner", "name": "المالك", "email": "owner@example.com", "role": "owner"},
            {"id": "arafat", "name": "عرفات", "email": "arafat@example.com", "role": "viewer"},
            {"id": "used", "name": "مستخدم", "email": "used@example.com", "role": "viewer"},
            {"id": "assigned", "name": "له دور", "email": "assigned@example.com", "role": "viewer"},
            {"id": "free", "name": "حساب تجربة", "email": "free@example.com", "role": "viewer"},
        ],
        all_employee_links=[{"id": "legacy-1", "account_user_id": "used"}],
        role_assignments=[{
            "user_id": "assigned",
            "role_key": "warehouse_operator",
            "enabled": True,
        }],
        preview_employees=[{
            "employee_id": "legacy-arafat",
            "account": {
                "status": "review_required",
                "suggested_account": {
                    "id": "arafat",
                    "name": "عرفات",
                    "email": "arafat@example.com",
                },
            },
        }],
        latest_events=[],
    )

    assert snapshot["rollout_mode"] == "pilot_only"
    assert snapshot["pilot_limit"] == 1
    assert snapshot["can_create_pilot"] is False
    assert snapshot["migrated_employee_writes_enabled"] is False
    assert snapshot["legacy_payroll_writes_enabled"] is False
    assert snapshot["general_ledger_writes_enabled"] is False
    assert snapshot["reserved_review_accounts"] == 1
    assert snapshot["login_account_candidates"] == [{
        "id": "free",
        "name": "حساب تجربة",
        "email": "free@example.com",
        "account_role": "viewer",
    }]
    assert snapshot["employees"][0]["payroll_enabled"] is False
    assert snapshot["employees"][0]["salary_contract"]["monthly_amount"] == 1000


def test_unlinked_disabled_pilot_assignment_can_be_reused_for_the_same_employee():
    snapshot = build_employee_management_snapshot(
        pilot_employees=[{
            "id": "pilot-1",
            "display_name": "موظف تجريبي",
            "status": "draft",
            "version": 3,
            "source": {"system": PILOT_SOURCE_SYSTEM},
            "management": {"mode": "pilot_only"},
        }],
        salary_contracts=[],
        team_users=[{
            "id": "reusable",
            "name": "حساب تجربة سابق",
            "email": "reusable@example.com",
            "role": "viewer",
        }],
        all_employee_links=[],
        role_assignments=[{
            "user_id": "reusable",
            "employee_v2_id": "pilot-1",
            "assignment_scope": "employee_pilot",
            "role_key": "warehouse_operator",
            "enabled": False,
        }],
        preview_employees=[],
        latest_events=[],
    )

    assert snapshot["login_account_candidates"] == [{
        "id": "reusable",
        "name": "حساب تجربة سابق",
        "email": "reusable@example.com",
        "account_role": "viewer",
    }]


def test_management_routes_and_exact_confirmations_are_part_of_the_contract():
    router = make_employees_v2_router(object(), lambda: {"id": "owner", "role": "owner"})
    paths = {(route.path, next(iter(route.methods or []), None)) for route in router.routes}
    route_paths = {path for path, _method in paths}

    assert "/employees-v2/management" in route_paths
    assert "/employees-v2/management/pilot" in route_paths
    assert "/employees-v2/management/pilot/{employee_id}" in route_paths
    assert "/employees-v2/management/pilot/{employee_id}/account" in route_paths
    assert "/employees-v2/management/pilot/{employee_id}/role" in route_paths
    assert "/employees-v2/management/pilot/{employee_id}/events" in route_paths
    assert PILOT_CREATE_CONFIRMATION == "CREATE_EMPLOYEE_V2_PILOT"
    assert PILOT_ACCOUNT_LINK_CONFIRMATION == "LINK_EMPLOYEE_V2_PILOT_ACCOUNT"
    assert PILOT_ROLE_ASSIGNMENT_CONFIRMATION == "ASSIGN_EMPLOYEE_V2_PILOT_ROLE"


def test_full_pilot_api_flow_is_audited_and_never_touches_financial_sources(monkeypatch):
    db = _Database()
    owner = {"id": "owner-1", "role": "owner", "name": "المالك"}

    async def response(_db, *, owner_id):
        return {"mode": "pilot_management", "owner_id": owner_id}

    async def preview(_db, owner_id):
        return {"employees": [], "owner_id": owner_id}

    monkeypatch.setattr(employee_routes, "_employee_management_response", response)
    monkeypatch.setattr(employee_routes, "_preview_from_db", preview)
    router = make_employees_v2_router(db, lambda: owner)

    create = _route_endpoint(router, "/employees-v2/management/pilot", "POST")
    created = asyncio.run(create(payload={
        "confirmation": PILOT_CREATE_CONFIRMATION,
        "name": "موظف تجريبي",
        "status": "draft",
        "monthly_salary": 1200,
    }, user=owner))
    employee_id = created["employee_id"]
    assert len(db[employee_routes.EMPLOYEES].rows) == 1
    assert db[employee_routes.SALARY_CONTRACTS].rows[0]["payroll_enabled"] is False

    update = _route_endpoint(
        router,
        "/employees-v2/management/pilot/{employee_id}",
        "PUT",
    )
    asyncio.run(update(employee_id=employee_id, payload={
        "expected_version": 1,
        "name": "موظف تجريبي معدل",
        "monthly_salary": 1300,
    }, user=owner))
    assert db[employee_routes.EMPLOYEES].rows[0]["version"] == 2
    assert db[employee_routes.SALARY_CONTRACTS].rows[0]["monthly_amount"] == 1300

    awaitable_user = {
        "id": "pilot-user",
        "created_by": "owner-1",
        "name": "حساب تجربة",
        "email": "pilot@example.com",
        "role": "viewer",
    }
    db.users.rows.append(awaitable_user)
    link = _route_endpoint(
        router,
        "/employees-v2/management/pilot/{employee_id}/account",
        "PUT",
    )
    asyncio.run(link(employee_id=employee_id, payload={
        "account_user_id": "pilot-user",
        "confirmation": PILOT_ACCOUNT_LINK_CONFIRMATION,
    }, user=owner))
    assert db[employee_routes.EMPLOYEES].rows[0]["account_user_id"] == "pilot-user"
    assert "linked_employee_id" not in db.users.rows[0]

    assign = _route_endpoint(
        router,
        "/employees-v2/management/pilot/{employee_id}/role",
        "PUT",
    )
    asyncio.run(assign(employee_id=employee_id, payload={
        "confirmation": PILOT_ROLE_ASSIGNMENT_CONFIRMATION,
        "role_key": "warehouse_operator",
        "enabled": True,
        "extra_permissions": [],
        "denied_permissions": [],
        "warehouse_ids": [],
        "fulfillment_responsibilities": [],
    }, user=owner))
    assignment = db[employee_routes.ROLE_ASSIGNMENTS].rows[0]
    assert assignment["employee_v2_id"] == employee_id
    assert assignment["assignment_scope"] == "employee_pilot"
    assert "inventory.receipts.read" in assignment["effective_permissions"]

    unlink = _route_endpoint(
        router,
        "/employees-v2/management/pilot/{employee_id}/account",
        "DELETE",
    )
    asyncio.run(unlink(employee_id=employee_id, payload={
        "confirmation": employee_routes.PILOT_ACCOUNT_UNLINK_CONFIRMATION,
    }, user=owner))
    assert db[employee_routes.EMPLOYEES].rows[0]["account_user_id"] is None
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["enabled"] is False
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["effective_permissions"] == []

    event_types = [row["event_type"] for row in db[employee_routes.EMPLOYEE_EVENTS].rows]
    assert event_types == [
        "employee_pilot_created",
        "employee_pilot_updated",
        "employee_pilot_account_linked",
        "employee_pilot_role_assigned",
        "employee_pilot_account_unlinked",
    ]
    assert "operating_salaries" not in db.collections
    assert "general_ledger" not in db.collections
    assert "liabilities" not in db.collections


def test_management_code_never_writes_legacy_payroll_or_ledger():
    source = (ROOT / "backend/employees_v2_routes.py").read_text(encoding="utf-8")

    assert "db.operating_salaries.update" not in source
    assert "db.operating_salaries.insert" not in source
    assert "db.general_ledger.update" not in source
    assert "db.general_ledger.insert" not in source
    assert '"legacy_user_reverse_link_written": False' in source
