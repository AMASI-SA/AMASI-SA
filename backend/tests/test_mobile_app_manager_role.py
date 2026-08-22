import pytest

from mobile_app_permissions import (
    MOBILE_APP_MANAGER,
    MOBILE_APP_PERMISSIONS,
    effective_mobile_app_permissions,
    mobile_app_access_for_user,
    validate_mobile_app_permissions,
)


def test_manager_role_expands_to_all_app_permissions():
    access = {"enabled": True, "permissions": [MOBILE_APP_MANAGER]}
    assert set(effective_mobile_app_permissions(access)) == set(MOBILE_APP_PERMISSIONS)


def test_manager_role_does_not_require_parent_page():
    assert validate_mobile_app_permissions([MOBILE_APP_MANAGER]) == [MOBILE_APP_MANAGER]


class _Collection:
    def __init__(self, row):
        self.row = row

    async def find_one(self, query, projection):
        return self.row


class _Db:
    def __init__(self, row):
        self.row = row

    def __getitem__(self, name):
        return _Collection(self.row)


@pytest.mark.asyncio
async def test_owner_gets_all_native_app_permissions_without_stored_access():
    result = await mobile_app_access_for_user(
        _Db(None),
        {"id": "owner-1", "role": "owner", "is_owner": True},
    )
    assert result["owner_override"] is True
    assert result["owner_baseline"] is True
    assert result["enabled"] is True
    assert result["manager"] is True
    assert set(result["permissions"]) == set(MOBILE_APP_PERMISSIONS)


@pytest.mark.asyncio
async def test_owner_full_access_cannot_be_reduced_by_partial_stored_permissions():
    result = await mobile_app_access_for_user(
        _Db({"enabled": True, "permissions": ["app.page.operations_monitoring"]}),
        {"id": "owner-1", "role": "owner", "is_owner": True},
    )
    assert result["owner_override"] is True
    assert result["manager"] is True
    assert set(result["permissions"]) == set(MOBILE_APP_PERMISSIONS)


@pytest.mark.asyncio
async def test_disabled_owner_fails_closed():
    result = await mobile_app_access_for_user(
        _Db(None),
        {
            "id": "owner-1",
            "role": "owner",
            "is_owner": True,
            "disabled": True,
        },
    )
    assert result["owner_override"] is False
    assert result["enabled"] is False
    assert result["manager"] is False
    assert result["permissions"] == []
