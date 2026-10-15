from __future__ import annotations

import asyncio
import sys
import types

import pytest
from fastapi import HTTPException

# The request-context module installs two operational guards on import. They are
# irrelevant to this permission-map regression and pull PDF/runtime dependencies,
# so keep this focused test dependency-free.
route_history = types.ModuleType("preparation_route_history")
route_history.install_supplier_dispatch_route_guard = lambda: None
custody = types.ModuleType("supplier_receipt_employee_custody")
custody.install_supplier_receipt_employee_custody = lambda: None
sys.modules.setdefault("preparation_route_history", route_history)
sys.modules.setdefault("supplier_receipt_employee_custody", custody)

import mobile_app_request_context as module


class Collection:
    def __init__(self, rows):
        self.rows = list(rows)

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return dict(row)
        return None


class DB:
    def __init__(self):
        self.users = Collection([
            {"id": "owner-1", "email": "owner@example.com", "role": "owner"},
        ])
        self.cols = {
            "mezan_employees_v2": Collection([
                {"account_user_id": "staff-1", "user_id": "owner-1"},
            ]),
        }

    def __getitem__(self, name):
        return self.cols[name]


def staff():
    return {
        "id": "staff-1",
        "email": "staff@example.com",
        "role": "viewer",
        "_session_client": "amasi_mobile",
    }


def test_build37_preparation_file_safety_accepts_reviewed_or_my_products():
    required = module.required_mobile_permissions(
        "/api/preparation-file-safety-v1/drafts"
    )
    assert required == frozenset({
        "app.page.reviewed_preparation",
        "app.page.my_products",
    })


@pytest.mark.parametrize(
    "permission",
    ["app.page.reviewed_preparation", "app.page.my_products"],
)
def test_build37_each_supported_page_permission_can_create_safe_draft(
    monkeypatch,
    permission,
):
    async def access(_db, _user):
        return {
            "enabled": True,
            "permissions": [permission],
        }

    monkeypatch.setattr(module, "mobile_app_access_for_user", access)
    result = asyncio.run(
        module.mobile_app_request_user(
            DB(),
            staff(),
            path="/api/preparation-file-safety-v1/drafts",
            method="POST",
        )
    )
    assert result["id"] == "owner-1"
    assert result["_mobile_actor_id"] == "staff-1"
    assert permission in result["_mobile_app_permissions"]


def test_build37_unrelated_page_permission_still_fails_closed(monkeypatch):
    async def access(_db, _user):
        return {
            "enabled": True,
            "permissions": ["app.page.orders"],
        }

    monkeypatch.setattr(module, "mobile_app_access_for_user", access)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            module.mobile_app_request_user(
                DB(),
                staff(),
                path="/api/preparation-file-safety-v1/drafts",
                method="POST",
            )
        )
    assert caught.value.status_code == 403
    assert caught.value.detail["code"] == "mobile_app_page_permission_required"
