import asyncio

import pytest
from fastapi import HTTPException

from mobile_app_request_context import mobile_app_request_user


class Collection:
    def __init__(self, rows): self.rows = list(rows)
    async def find_one(self, query, projection=None):
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()): return dict(row)
        return None


class DB:
    def __init__(self):
        self.users = Collection([{"id": "owner-1", "email": "owner@x", "role": "owner"}])
        self.cols = {
            "mezan_employees_v2": Collection([{"account_user_id": "staff-1", "user_id": "owner-1"}]),
            "mezan_mobile_app_access_v1": Collection([{
                "owner_user_id": "owner-1", "user_id": "staff-1", "enabled": True,
                "permissions": ["app.page.orders"],
            }]),
        }
    def __getitem__(self, name): return self.cols[name]


def staff():
    return {"id": "staff-1", "email": "staff@x", "role": "viewer", "_session_client": "amasi_mobile"}


def test_linked_employee_reads_owner_orders_with_app_permission():
    result = asyncio.run(mobile_app_request_user(DB(), staff(), path="/api/orders-v2", method="GET"))
    assert result["id"] == "owner-1"
    assert result["role"] == "owner"
    assert result["_mobile_actor_id"] == "staff-1"


def test_page_without_permission_fails_closed():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(mobile_app_request_user(DB(), staff(), path="/api/products-v2", method="GET"))
    assert caught.value.status_code == 403
    assert caught.value.detail["code"] == "mobile_app_page_permission_required"


def test_unknown_native_route_fails_closed():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(mobile_app_request_user(DB(), staff(), path="/api/admin/secrets", method="GET"))
    assert caught.value.detail["code"] == "mobile_app_route_not_allowed"


def test_auth_profile_keeps_employee_identity():
    result = asyncio.run(mobile_app_request_user(DB(), staff(), path="/api/auth/me", method="GET"))
    assert result["id"] == "staff-1"
