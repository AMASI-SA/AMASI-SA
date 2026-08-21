from __future__ import annotations

import asyncio

import pytest

from ai_store_access_contract import PERMISSIONS, effective_permissions
from mobile_app_permissions import (
    MOBILE_APP_PERMISSIONS,
    effective_mobile_app_permissions,
    mobile_app_access_for_user,
    validate_mobile_app_permissions,
)
import supplier_receiving_routes


class _Collection:
    def __init__(self, row=None):
        self.row = row

    async def find_one(self, query, _projection=None):
        if not self.row:
            return None
        if all(self.row.get(key) == value for key, value in query.items()):
            return dict(self.row)
        return None


class _Db:
    def __init__(self, row=None):
        self.collection = _Collection(row)

    def __getitem__(self, _name):
        return self.collection


def test_mobile_permissions_are_disjoint_from_mezan_operational_permissions():
    assert MOBILE_APP_PERMISSIONS
    assert MOBILE_APP_PERMISSIONS.isdisjoint(PERMISSIONS)
    assert effective_permissions({
        "role_key": "preparation_operator",
        "enabled": True,
        "extra_permissions": [],
        "denied_permissions": [],
    }) == [
        "preparation.assigned.read",
        "preparation.assigned.stop",
        "preparation.assigned.work",
    ]


def test_new_or_unknown_mobile_permission_fails_closed():
    assert validate_mobile_app_permissions([]) == []
    with pytest.raises(ValueError, match="unknown_mobile_app_permission"):
        validate_mobile_app_permissions(["app.page.future_page"])
    with pytest.raises(ValueError, match="mobile_app_permission_parent_required"):
        validate_mobile_app_permissions([
            "app.action.my_products.service.add",
        ])


def test_employee_may_have_mobile_pages_with_zero_mezan_permissions():
    access = {
        "owner_user_id": "owner-1",
        "user_id": "employee-1",
        "enabled": True,
        "permissions": ["app.page.orders"],
    }
    result = asyncio.run(mobile_app_access_for_user(
        _Db(access),
        {
            "id": "employee-1",
            "created_by": "owner-1",
            "role": "viewer",
        },
    ))

    assert result == {
        "configured": True,
        "enabled": True,
        "owner_override": False,
        "permissions": ["app.page.orders"],
    }
    assert effective_mobile_app_permissions(access) == ["app.page.orders"]


def test_owner_always_receives_every_mobile_app_permission():
    result = asyncio.run(mobile_app_access_for_user(
        _Db(),
        {"id": "owner-1", "role": "owner"},
    ))
    assert result["owner_override"] is True
    assert set(result["permissions"]) == MOBILE_APP_PERMISSIONS


def test_supplier_receiving_translates_actions_only_for_signed_mobile_session(monkeypatch):
    async def base_context(_db, _user):
        return {
            "merchant_id": "owner-1",
            "actor_id": "employee-1",
            "is_owner": False,
            "permissions": [],
        }

    async def app_access(_db, _user):
        return {
            "permissions": [
                "app.page.my_products",
                "app.action.my_products.service.add",
            ],
        }

    monkeypatch.setattr(supplier_receiving_routes, "_base_actor_context", base_context)
    monkeypatch.setattr(supplier_receiving_routes, "mobile_app_access_for_user", app_access)
    mobile = asyncio.run(supplier_receiving_routes._actor_context(
        object(),
        {"id": "employee-1", "_session_client": "amasi_mobile"},
    ))
    web = asyncio.run(supplier_receiving_routes._actor_context(
        object(),
        {"id": "employee-1", "_session_client": None},
    ))

    assert "inventory.preparation.receive" in mobile["permissions"]
    assert "supplier_receiving.service.add" in mobile["permissions"]
    assert "supplier_receiving.product_price.edit" not in mobile["permissions"]
    assert web["permissions"] == []
