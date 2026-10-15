from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest
from fastapi import HTTPException

from auth import account_is_disabled, create_access_token, get_current_user_from_db


ROOT = Path(__file__).resolve().parents[2]


class _Users:
    def __init__(self, row):
        self.row = row

    async def find_one(self, query):
        if query.get("id") == self.row.get("id"):
            return copy.deepcopy(self.row)
        return None


class _DB:
    def __init__(self, row):
        self.users = _Users(row)


class _Request:
    def __init__(self, token):
        self.cookies = {"access_token": token}
        self.headers = {}


@pytest.mark.parametrize("fields", [
    {"disabled": True},
    {"is_active": False},
    {"deleted_at": "2026-08-10T00:00:00+00:00"},
])
def test_existing_sessions_are_rejected_immediately_for_disabled_employee_accounts(monkeypatch, fields):
    monkeypatch.setenv("JWT_SECRET", "employee-access-test-secret-long-enough")
    user = {
        "id": "employee-user",
        "email": "employee@example.com",
        "role": "viewer",
        **fields,
    }
    token = create_access_token(user["id"], user["email"])

    assert account_is_disabled(user) is True
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user_from_db(_Request(token), _DB(user)))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Account disabled"


def test_login_gate_uses_the_same_disabled_account_policy():
    source = (ROOT / "backend/server.py").read_text(encoding="utf-8")

    assert "or account_is_disabled(user)" in source
