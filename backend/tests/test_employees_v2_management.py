from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest

import employees_v2_routes as employee_routes
from employees_v2_routes import (
    EMPLOYEE_ACCOUNT_LINK_CONFIRMATION,
    EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION,
    EMPLOYEE_CREATE_CONFIRMATION,
    EMPLOYEE_PASSWORD_CONFIRMATION,
    EMPLOYEE_MOBILE_APP_PERMISSIONS_CONFIRMATION,
    EMPLOYEE_PAYROLL_STATUS_CONFIRMATION,
    EMPLOYEE_ROLE_ASSIGNMENT_CONFIRMATION,
    NATIVE_SOURCE_SYSTEM,
    build_employee_management_snapshot,
    make_employees_v2_router,
    normalize_employee_payload,
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


def test_employee_payload_accepts_operational_statuses_without_salary_fields():
    result = normalize_employee_payload({
        "name": "  تركي صادق  ",
        "phone": "0500000000",
        "contact_email": "Turki@Example.com",
        "job_title": "موظف تجهيز",
        "department": "التجهيز",
        "hire_date": "2026-08-10",
        "status": "active",
        "notes": "هوية تشغيلية",
        "monthly_salary": 5000,
    })

    assert result == {
        "display_name": "تركي صادق",
        "phone": "0500000000",
        "contact_email": "turki@example.com",
        "job_title": "موظف تجهيز",
        "department": "التجهيز",
        "notes": "هوية تشغيلية",
        "hire_date": "2026-08-10",
        "status": "active",
    }

    with pytest.raises(ValueError, match="employee_status_invalid"):
        normalize_employee_payload({"name": "موظف", "status": "stopped"})
    assert normalize_employee_payload({
        "name": "موظف بإجازة",
        "status": "unpaid_leave",
    })["status"] == "unpaid_leave"


def test_full_management_snapshot_contains_all_employees_and_protects_owner_account():
    snapshot = build_employee_management_snapshot(
        owner_id="owner",
        employees=[
            {
                "id": "migrated-1",
                "legacy_employee_id": "legacy-1",
                "display_name": "موظف مرحّل",
                "status": "active",
                "account_user_id": "used",
                "source": {"system": "mezan_legacy"},
            },
            {
                "id": "native-1",
                "legacy_employee_id": "native:native-1",
                "display_name": "موظف جديد",
                "status": "inactive",
                "source": {"system": NATIVE_SOURCE_SYSTEM},
            },
        ],
        salary_contracts=[{
            "employee_id": "migrated-1",
            "monthly_amount": 3000,
            "source_authority": "operating_salaries_until_cutover",
        }],
        team_users=[
            {"id": "owner", "name": "عرفات", "email": "owner@example.com", "role": "owner"},
            {"id": "used", "name": "موظف مرحّل", "email": "used@example.com", "role": "viewer"},
            {"id": "free", "name": "حساب متاح", "email": "free@example.com", "role": "viewer", "disabled": True},
        ],
        role_assignments=[{
            "user_id": "used",
            "role_key": "preparation_operator",
            "enabled": True,
            "extra_permissions": [],
            "denied_permissions": [],
            "warehouse_ids": ["warehouse-1"],
            "fulfillment_responsibilities": ["preparation"],
        }],
        preview_employees=[{
            "employee_id": "migrated-1",
            "legacy_employee_id": "legacy-1",
            "financial_snapshot": {"salary_payable": 100, "advance": 20, "custody": 0},
            "account": {
                "status": "linked",
                "account_user_id": "used",
                "suggested_account": {"id": "owner", "name": "عرفات"},
            },
        }],
        latest_events=[],
    )

    assert snapshot["rollout_mode"] == "full_management"
    assert snapshot["managed_count"] == 2
    assert snapshot["active_count"] == 1
    assert snapshot["inactive_count"] == 1
    assert snapshot["linked_account_count"] == 1
    assert snapshot["migrated_employee_writes_enabled"] is True
    assert snapshot["legacy_payroll_writes_enabled"] is False
    assert snapshot["general_ledger_writes_enabled"] is False
    assert snapshot["financial_writes"] == 0
    assert snapshot["login_account_candidates"] == [{
        "id": "free",
        "name": "حساب متاح",
        "email": "free@example.com",
        "account_role": "viewer",
        "disabled": True,
        "has_existing_role": False,
        "role_key": None,
    }]
    migrated = next(row for row in snapshot["employees"] if row["id"] == "migrated-1")
    assert migrated["migrated"] is True
    assert migrated["salary_contract"]["monthly_amount"] == 3000
    assert migrated["financial_snapshot"]["salary_payable"] == 100
    assert migrated["operational_role"]["role_key"] == "preparation_operator"
    assert migrated["operational_role"]["warehouse_ids"] == ["warehouse-1"]
    assert migrated["operational_role"]["fulfillment_responsibilities"] == ["preparation"]


def test_management_routes_use_general_employee_contracts():
    router = make_employees_v2_router(object(), lambda: {"id": "owner", "role": "owner"})
    paths = {route.path for route in router.routes}
    source = (ROOT / "backend/employees_v2_routes.py").read_text(encoding="utf-8")

    assert "/employees-v2/management/employees" in paths
    assert "/employees-v2/mobile-directory" in paths
    assert "/employees-v2/management/employees/{employee_id}" in paths
    assert "/employees-v2/management/employees/{employee_id}/account" in paths
    assert "/employees-v2/management/employees/{employee_id}/account/password" in paths
    assert "/employees-v2/management/employees/{employee_id}/role" in paths
    assert "/employees-v2/management/employees/{employee_id}/mobile-app-permissions" in paths
    assert "/employees-v2/management/employees/{employee_id}/events" in paths
    assert not any("/management/pilot" in path for path in paths)
    assert '"mode": "pilot_management"' not in source
    assert '"mode": "full_management"' in source
    assert EMPLOYEE_CREATE_CONFIRMATION == "CREATE_EMPLOYEE_V2"
    assert EMPLOYEE_ACCOUNT_LINK_CONFIRMATION == "LINK_EMPLOYEE_V2_ACCOUNT"
    assert EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION == "UNLINK_EMPLOYEE_V2_ACCOUNT"
    assert EMPLOYEE_ROLE_ASSIGNMENT_CONFIRMATION == "ASSIGN_EMPLOYEE_V2_ROLE"
    assert EMPLOYEE_MOBILE_APP_PERMISSIONS_CONFIRMATION == "ASSIGN_EMPLOYEE_V2_MOBILE_APP_PERMISSIONS"
    assert EMPLOYEE_PASSWORD_CONFIRMATION == "RESET_EMPLOYEE_V2_ACCOUNT_PASSWORD"
    assert EMPLOYEE_PAYROLL_STATUS_CONFIRMATION == "CHANGE_EMPLOYEE_V2_PAYROLL_STATUS"


def test_full_employee_api_flow_revokes_and_restores_access_without_financial_writes(monkeypatch):
    db = _Database()
    owner = {"id": "owner-1", "role": "owner", "name": "المالك"}

    async def response(_db, *, owner_id):
        return {"mode": "full_management", "owner_id": owner_id}

    monkeypatch.setattr(employee_routes, "_employee_management_response", response)
    router = make_employees_v2_router(db, lambda: owner)

    create = _route_endpoint(router, "/employees-v2/management/employees", "POST")
    created = asyncio.run(create(payload={
        "confirmation": EMPLOYEE_CREATE_CONFIRMATION,
        "name": "تركي صادق",
        "status": "active",
    }, user=owner))
    employee_id = created["employee_id"]
    employee = db[employee_routes.EMPLOYEES].rows[0]
    assert employee["source"]["system"] == NATIVE_SOURCE_SYSTEM
    assert employee["version"] == 1

    db.users.rows.append({
        "id": "turki-user",
        "created_by": "owner-1",
        "name": "تركي صادق",
        "email": "turki@example.com",
        "role": "viewer",
    })
    link = _route_endpoint(
        router,
        "/employees-v2/management/employees/{employee_id}/account",
        "PUT",
    )
    asyncio.run(link(employee_id=employee_id, payload={
        "account_user_id": "turki-user",
        "confirmation": EMPLOYEE_ACCOUNT_LINK_CONFIRMATION,
    }, user=owner))
    assert db[employee_routes.EMPLOYEES].rows[0]["account_user_id"] == "turki-user"
    assert db.users.rows[0]["disabled"] is False

    assign = _route_endpoint(
        router,
        "/employees-v2/management/employees/{employee_id}/role",
        "PUT",
    )
    asyncio.run(assign(employee_id=employee_id, payload={
        "confirmation": EMPLOYEE_ROLE_ASSIGNMENT_CONFIRMATION,
        "role_key": "preparation_operator",
        "enabled": True,
        "extra_permissions": [],
        "denied_permissions": [],
        "warehouse_ids": [],
        "fulfillment_responsibilities": [],
    }, user=owner))
    assignment = db[employee_routes.ROLE_ASSIGNMENTS].rows[0]
    assert assignment["employee_v2_id"] == employee_id
    assert assignment["assignment_scope"] == "employee_v2"
    assert assignment["effective_permissions"] == [
        "preparation.assigned.read",
        "preparation.assigned.stop",
        "preparation.assigned.work",
    ]

    update = _route_endpoint(
        router,
        "/employees-v2/management/employees/{employee_id}",
        "PUT",
    )
    asyncio.run(update(employee_id=employee_id, payload={
        "expected_version": 2,
        "status": "inactive",
        "status_effective_date": "2026-08-13",
        "confirmation": EMPLOYEE_PAYROLL_STATUS_CONFIRMATION,
    }, user=owner))
    assert db.users.rows[0]["disabled"] is True
    assert db.users.rows[0]["is_active"] is False
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["enabled"] is False
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["effective_permissions"] == []

    asyncio.run(update(employee_id=employee_id, payload={
        "expected_version": 3,
        "status": "active",
        "status_effective_date": "2026-08-13",
        "confirmation": EMPLOYEE_PAYROLL_STATUS_CONFIRMATION,
    }, user=owner))
    assert db.users.rows[0]["disabled"] is False
    assert db.users.rows[0]["is_active"] is True
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["enabled"] is True
    assert "preparation.assigned.work" in db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["effective_permissions"]

    password = _route_endpoint(
        router,
        "/employees-v2/management/employees/{employee_id}/account/password",
        "PUT",
    )
    asyncio.run(password(employee_id=employee_id, payload={
        "confirmation": EMPLOYEE_PASSWORD_CONFIRMATION,
        "new_password": "Temporary123!",
    }, user=owner))
    assert db.users.rows[0]["password_hash"] != "Temporary123!"

    unlink = _route_endpoint(
        router,
        "/employees-v2/management/employees/{employee_id}/account",
        "DELETE",
    )
    asyncio.run(unlink(employee_id=employee_id, payload={
        "confirmation": EMPLOYEE_ACCOUNT_UNLINK_CONFIRMATION,
    }, user=owner))
    assert db[employee_routes.EMPLOYEES].rows[0]["account_user_id"] is None
    assert db.users.rows[0]["disabled"] is True
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["enabled"] is False
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["employee_v2_id"] is None

    event_types = [row["event_type"] for row in db[employee_routes.EMPLOYEE_EVENTS].rows]
    assert event_types == [
        "employee_created",
        "employee_account_linked",
        "employee_role_assigned",
        "employee_payroll_status_changed",
        "employee_payroll_status_changed",
        "employee_account_password_reset",
        "employee_account_unlinked",
    ]
    assert db[employee_routes.SALARY_CONTRACTS].rows == []
    assert "operating_salaries" not in db.collections
    assert "general_ledger" not in db.collections
    assert "liabilities" not in db.collections
    assert "Temporary123!" not in repr(db[employee_routes.EMPLOYEE_EVENTS].rows)


def test_mobile_app_permissions_are_saved_without_changing_mezan_role(monkeypatch):
    db = _Database()
    owner = {"id": "owner-1", "role": "owner", "name": "المالك"}
    db[employee_routes.EMPLOYEES].rows.append({
        "id": "employee-1",
        "user_id": "owner-1",
        "display_name": "موظف التطبيق",
        "status": "active",
        "account_user_id": "mobile-user",
        "version": 1,
    })
    db.users.rows.append({
        "id": "mobile-user",
        "created_by": "owner-1",
        "name": "موظف التطبيق",
        "email": "mobile@example.com",
        "role": "viewer",
    })
    db[employee_routes.ROLE_ASSIGNMENTS].rows.append({
        "owner_user_id": "owner-1",
        "user_id": "mobile-user",
        "role_key": "preparation_operator",
        "enabled": True,
        "extra_permissions": [],
        "denied_permissions": [
            "preparation.assigned.read",
            "preparation.assigned.stop",
            "preparation.assigned.work",
        ],
        "effective_permissions": [],
    })

    async def response(_db, *, owner_id):
        return {"mode": "full_management", "owner_id": owner_id}

    monkeypatch.setattr(employee_routes, "_employee_management_response", response)
    endpoint = _route_endpoint(
        make_employees_v2_router(db, lambda: owner),
        "/employees-v2/management/employees/{employee_id}/mobile-app-permissions",
        "PUT",
    )
    result = asyncio.run(endpoint(
        employee_id="employee-1",
        payload={
            "confirmation": EMPLOYEE_MOBILE_APP_PERMISSIONS_CONFIRMATION,
            "enabled": True,
            "permissions": [
                "app.page.my_products",
                "app.action.my_products.service.add",
            ],
        },
        user=owner,
    ))

    assert result["ok"] is True
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["effective_permissions"] == []
    saved = db[employee_routes.MOBILE_APP_ACCESS].rows[0]
    assert saved["scope"] == "amasi_mobile_only"
    assert saved["permissions"] == [
        "app.action.my_products.service.add",
        "app.page.my_products",
    ]
    event = db[employee_routes.EMPLOYEE_EVENTS].rows[-1]
    assert event["event_type"] == "employee_mobile_app_permissions_assigned"
    assert event["metadata"]["mezan_permission_changes"] == 0


def test_status_change_writes_v2_contract_leave_history_only(monkeypatch):
    db = _Database()
    owner = {"id": "owner-1", "role": "owner", "name": "المالك"}
    db[employee_routes.EMPLOYEES].rows.append({
        "id": "employee-1",
        "user_id": "owner-1",
        "legacy_employee_id": "legacy-1",
        "display_name": "موظف",
        "status": "active",
        "version": 1,
    })
    db[employee_routes.SALARY_CONTRACTS].rows.append({
        "id": "contract-1",
        "user_id": "owner-1",
        "employee_id": "employee-1",
        "legacy_salary_id": "legacy-1",
        "monthly_amount": 3100,
        "effective_from": "2026-01-01",
        "status": "active",
        "version": 1,
    })

    async def response(_db, *, owner_id):
        return {"mode": "full_management", "owner_id": owner_id}

    monkeypatch.setattr(employee_routes, "_employee_management_response", response)
    update = _route_endpoint(
        make_employees_v2_router(db, lambda: owner),
        "/employees-v2/management/employees/{employee_id}",
        "PUT",
    )
    asyncio.run(update(employee_id="employee-1", payload={
        "expected_version": 1,
        "status": "unpaid_leave",
        "status_effective_date": "2026-08-10",
        "confirmation": EMPLOYEE_PAYROLL_STATUS_CONFIRMATION,
    }, user=owner))

    contract = db[employee_routes.SALARY_CONTRACTS].rows[0]
    assert contract["payroll_state"] == "unpaid_leave"
    assert contract["status"] == "paused"
    assert contract["source_authority"] == "mezan_employee_salary_contracts_v2"
    assert contract["suspension_periods"][0]["started_on"] == "2026-08-10"
    assert contract["suspension_periods"][0]["returned_on"] is None

    asyncio.run(update(employee_id="employee-1", payload={
        "expected_version": 2,
        "status": "active",
        "status_effective_date": "2026-08-13",
        "confirmation": EMPLOYEE_PAYROLL_STATUS_CONFIRMATION,
    }, user=owner))
    contract = db[employee_routes.SALARY_CONTRACTS].rows[0]
    assert contract["payroll_state"] == "active"
    assert contract["suspension_periods"][0]["returned_on"] == "2026-08-13"
    assert "operating_salaries" not in db.collections


def test_management_code_never_writes_financial_sources():
    source = (ROOT / "backend/employees_v2_routes.py").read_text(encoding="utf-8")

    assert "db.operating_salaries.update" not in source
    assert "db.operating_salaries.insert" not in source
    assert "db.general_ledger.update" not in source
    assert "db.general_ledger.insert" not in source
    assert '"legacy_user_reverse_link_written": False' in source


def test_employee_access_policy_never_mutates_owner_account_or_role():
    db = _Database()
    db.users.rows.append({
        "id": "owner-1",
        "created_by": "owner-1",
        "role": "owner",
        "disabled": False,
        "is_active": True,
    })
    db[employee_routes.ROLE_ASSIGNMENTS].rows.append({
        "user_id": "owner-1",
        "role_key": "owner",
        "enabled": True,
        "effective_permissions": ["audit.read"],
    })

    asyncio.run(employee_routes._set_employee_account_access(
        db,
        account_id="owner-1",
        active=False,
        owner_id="owner-1",
        reason="test_owner_protection",
    ))

    assert db.users.rows[0]["disabled"] is False
    assert db.users.rows[0]["is_active"] is True
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["enabled"] is True
    assert db[employee_routes.ROLE_ASSIGNMENTS].rows[0]["effective_permissions"] == ["audit.read"]
