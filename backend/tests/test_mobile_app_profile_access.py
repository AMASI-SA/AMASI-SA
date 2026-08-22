from __future__ import annotations

import asyncio

import ai_store_access_contract
import mobile_app_permissions
import server


def test_auth_profile_returns_own_mobile_access_without_merging_mezan_permissions(monkeypatch):
    async def merged_session_permissions(_db, _user, _permissions):
        return []

    async def app_access(_db, user):
        assert user["id"] == "employee-account-1"
        return {
            "configured": True,
            "enabled": True,
            "owner_override": False,
            "owner_baseline": False,
            "manager": False,
            "permissions": [
                "app.page.orders",
                "app.page.my_products",
            ],
        }

    monkeypatch.setattr(
        ai_store_access_contract,
        "merged_session_permissions",
        merged_session_permissions,
    )
    monkeypatch.setattr(
        mobile_app_permissions,
        "mobile_app_access_for_user",
        app_access,
    )
    monkeypatch.setattr(server, "_effective_perms", lambda _user: [])

    profile = asyncio.run(server.me({
        "id": "employee-account-1",
        "email": "employee@example.com",
        "name": "Employee",
        "role": "viewer",
        "created_by": "owner-1",
        # Deliberately no _session_client marker: profile refresh must remain
        # authoritative even if middleware metadata is lost.
    }))

    assert profile["permissions"] == []
    assert profile["mobile_app_access"]["permissions"] == [
        "app.page.orders",
        "app.page.my_products",
    ]
