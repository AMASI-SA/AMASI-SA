"""Exercise actual endpoint code without importing the production app startup."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import uuid
from typing import Optional

import pytest
from fastapi import FastAPI, APIRouter, Depends, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, EmailStr, Field, validator

from auth import hash_password, verify_password, validate_bcrypt_secret
from employee_password_policy import EMPLOYEE_PASSWORD_MIN_LENGTH, validate_account_password
import employees_v2_routes as routes
from tests.test_employee_salary_contract_management import OWNER, db


def application(database, actor=None):
    # Compile the unchanged account writer and the new employee adapter from source.
    source = Path(__file__).resolve().parents[1] / "server.py"
    names = {"TeamUserCreateIn", "EmployeeTeamUserCreateIn", "create_team_user", "create_employee_team_user", "_is_owner", "_require_owner",
             "TeamUserUpdateIn", "ChangePasswordIn", "update_team_user", "change_my_password"}
    nodes = [n for n in ast.parse(source.read_text(encoding="utf-8")).body if getattr(n, "name", None) in names]
    namespace = dict(globals(), api=APIRouter(), current_user=lambda: actor or OWNER, db=database,
                     MIN_PASSWORD_LENGTH=12,
                     _ROLE_HIERARCHY={"owner": 3, "viewer": 0},
                     PERMISSIONS_CATALOGUE={"dashboard.view": {}},
                     _public_user_view=lambda row: {k: v for k, v in row.items() if k not in {"password_hash", "_id"}})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
    app = FastAPI()
    app.include_router(namespace["api"], prefix="/api")
    app.include_router(routes.make_employees_v2_router(database, lambda: OWNER), prefix="/api")
    return app


@pytest.mark.parametrize("length", [0, 5, 6, 7, 10, 11, 12])
async def test_employee_create_password_boundaries(db, length):
    await db.mezan_employees_v2.insert_one({"id": "employee", "user_id": "owner"})
    async with AsyncClient(transport=ASGITransport(app=application(db)), base_url="http://test") as client:
        response = await client.post("/api/team/users/employee", json={
            "employee_id": "employee", "name": "Employee", "email": "employee@example.com",
            "password": "a" * length, "role": "viewer", "denied_permissions": ["dashboard.view"],
        })
    assert response.status_code == (200 if length >= 6 else 422), response.text
    account = await db.users.find_one({})
    if length >= 6:
        assert verify_password("a" * length, account["password_hash"])
        assert account["email"] == "employee@example.com"
        assert account["role"] == "viewer" and account["denied_permissions"] == ["dashboard.view"]
        assert "password" not in response.text
    else:
        assert account is None


@pytest.mark.parametrize("length", [0, 5, 6, 7, 10, 11, 12])
async def test_employee_reset_from_twelve_characters(db, length):
    await db.mezan_employees_v2.insert_one({"id": "employee", "user_id": "owner", "account_user_id": "login"})
    original = {"id": "login", "created_by": "owner", "role": "viewer", "email": "same@example.com",
                "password_hash": hash_password("Original1234"), "denied_permissions": ["dashboard.view"]}
    await db.users.insert_one(dict(original))
    async with AsyncClient(transport=ASGITransport(app=application(db)), base_url="http://test") as client:
        response = await client.put("/api/employees-v2/management/employees/employee/account/password", json={
            "new_password": "b" * length, "confirmation": routes.EMPLOYEE_PASSWORD_CONFIRMATION,
        })
    assert response.status_code == (200 if length >= 6 else 422), response.text
    account = await db.users.find_one({}, {"_id": 0})
    if length >= 6:
        assert verify_password("b" * length, account["password_hash"])
        assert not verify_password("Original1234", account["password_hash"])
        for key in ("email", "role", "denied_permissions"):
            assert account[key] == original[key]
    else:
        assert account == original


async def test_general_account_minimum_and_employee_scope_unchanged(db):
    async with AsyncClient(transport=ASGITransport(app=application(db)), base_url="http://test") as client:
        payload = {"name": "Employee", "email": "employee@example.com", "password": "abc123"}
        assert (await client.post("/api/team/users", json=payload)).status_code == 422
        assert (await client.post("/api/team/users/employee", json={**payload, "employee_id": "missing"})).status_code == 404
        await db.mezan_employees_v2.insert_one({"id": "foreign", "user_id": "other"})
        assert (await client.post("/api/team/users/employee", json={**payload, "employee_id": "foreign"})).status_code == 404
        await db.mezan_employees_v2.insert_one({"id": "linked", "user_id": "owner", "account_user_id": "existing"})
        assert (await client.post("/api/team/users/employee", json={**payload, "employee_id": "linked"})).status_code == 409
    assert await db.users.count_documents({}) == 0


def test_frontend_and_backend_minimum_match():
    source = (Path(__file__).resolve().parents[2] / "frontend/src/pages/EmployeesV2Management.jsx").read_text(encoding="utf-8")
    assert EMPLOYEE_PASSWORD_MIN_LENGTH == 6
    assert source.count("minLength={6}") == 2
    assert "password.length < 6" in source


@pytest.mark.parametrize("path", ["team", "profile"])
@pytest.mark.parametrize("linked", [False, True])
@pytest.mark.parametrize("length", [0, 5, 6, 11, 12])
async def test_shared_password_routes_use_persisted_mz2_link(db, path, linked, length):
    original = {"id": "login", "created_by": "owner", "role": "viewer",
                "email": "same@example.com", "denied_permissions": ["dashboard.view"],
                "password_hash": hash_password("Original1234")}
    await db.users.insert_one(dict(original))
    if linked:
        await db.mezan_employees_v2.insert_one({"id": "employee", "user_id": "owner", "account_user_id": "login"})
    actor = OWNER if path == "team" else {"id": "login", "role": "viewer"}
    url = "/api/team/users/login" if path == "team" else "/api/auth/profile/password"
    async with AsyncClient(transport=ASGITransport(app=application(db, actor)), base_url="http://test") as client:
        response = await client.put(url, json={"new_password": "x" * length, "current_password": "Original1234"})
    accepted = length >= (6 if linked else 12)
    assert response.status_code == (200 if accepted else 422), response.text
    stored = await db.users.find_one({"id": "login"}, {"_id": 0})
    if accepted:
        assert verify_password("x" * length, stored["password_hash"])
        assert not verify_password("Original1234", stored["password_hash"])
        for field in ("email", "role", "denied_permissions", "created_by"):
            assert stored[field] == original[field]
    else:
        assert stored == original


@pytest.mark.parametrize("path", ["team", "profile"])
async def test_foreign_owner_link_does_not_relax_minimum(db, path):
    await db.users.insert_one({"id": "login", "created_by": "owner", "role": "viewer", "password_hash": hash_password("Original1234")})
    await db.mezan_employees_v2.insert_one({"id": "employee", "user_id": "different-owner", "account_user_id": "login"})
    actor = OWNER if path == "team" else {"id": "login", "role": "viewer"}
    url = "/api/team/users/login" if path == "team" else "/api/auth/profile/password"
    async with AsyncClient(transport=ASGITransport(app=application(db, actor)), base_url="http://test") as client:
        response = await client.put(url, json={"new_password": "abc123", "current_password": "Original1234"})
    assert response.status_code == 422


async def test_existing_current_password_and_owner_guards_remain(db):
    await db.users.insert_one({"id": "login", "created_by": "owner", "role": "viewer", "password_hash": hash_password("Original1234")})
    await db.mezan_employees_v2.insert_one({"id": "employee", "user_id": "owner", "account_user_id": "login"})
    async with AsyncClient(transport=ASGITransport(app=application(db, {"id": "login", "role": "viewer"})), base_url="http://test") as client:
        assert (await client.put("/api/team/users/login", json={"new_password": "abc123"})).status_code == 403
        assert (await client.put("/api/auth/profile/password", json={"current_password": "wrong", "new_password": "abc123"})).status_code == 400
        assert (await client.put("/api/auth/profile/password", json={"current_password": "Original1234", "new_password": "Original1234"})).status_code == 400
    assert verify_password("Original1234", (await db.users.find_one({"id": "login"}))["password_hash"])
