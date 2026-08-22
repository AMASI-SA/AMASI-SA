import pytest

from mobile_app_permissions import (
    MOBILE_APP_MANAGER,
    MOBILE_APP_PERMISSIONS,
    OWNER_BASELINE_PERMISSIONS,
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
async def test_owner_gets_supervisory_monitoring_baseline_without_manager_role():
    result = await mobile_app_access_for_user(
        _Db(None),
        {"id": "owner-1", "role": "owner", "is_owner": True},
    )
    assert result["owner_override"] is False
    assert result["owner_baseline"] is True
    assert result["enabled"] is True
    assert set(result["permissions"]) == set(OWNER_BASELINE_PERMISSIONS)
    assert MOBILE_APP_MANAGER not in result["permissions"]


@pytest.mark.asyncio
async def test_explicit_owner_manager_access_still_expands_to_all_permissions():
    result = await mobile_app_access_for_user(
        _Db({"enabled": True, "permissions": [MOBILE_APP_MANAGER]}),
        {"id": "owner-1", "role": "owner", "is_owner": True},
    )
    assert result["owner_override"] is False
    assert result["owner_baseline"] is True
    assert result["manager"] is True
    assert set(result["permissions"]) == set(MOBILE_APP_PERMISSIONS)
