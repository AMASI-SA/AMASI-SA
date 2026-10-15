from __future__ import annotations

import asyncio

from mobile_app_permissions import mobile_app_access_for_user


class _Collection:
    def __init__(self, rows):
        self.rows = list(rows)

    async def find_one(self, query, _projection=None):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return dict(row)
        return None


class _Db:
    def __init__(self, *, access_rows, employee_rows):
        self.collections = {
            "mezan_mobile_app_access_v1": _Collection(access_rows),
            "mezan_employees_v2": _Collection(employee_rows),
        }

    def __getitem__(self, name):
        return self.collections[name]


def test_linked_legacy_employee_receives_saved_pages_without_created_by():
    db = _Db(
        access_rows=[{
            "owner_user_id": "owner-1",
            "user_id": "employee-account-1",
            "enabled": True,
            "permissions": [
                "app.page.orders",
                "app.page.pending_review",
            ],
        }],
        employee_rows=[{
            "id": "employee-v2-1",
            "user_id": "owner-1",
            "account_user_id": "employee-account-1",
        }],
    )

    result = asyncio.run(mobile_app_access_for_user(db, {
        "id": "employee-account-1",
        "role": "viewer",
    }))

    assert result["configured"] is True
    assert result["enabled"] is True
    assert result["permissions"] == [
        "app.page.orders",
        "app.page.pending_review",
    ]


def test_unlinked_account_cannot_claim_another_employees_saved_pages():
    db = _Db(
        access_rows=[{
            "owner_user_id": "owner-1",
            "user_id": "employee-account-1",
            "enabled": True,
            "permissions": ["app.page.orders"],
        }],
        employee_rows=[{
            "id": "employee-v2-1",
            "user_id": "owner-1",
            "account_user_id": "different-account",
        }],
    )

    result = asyncio.run(mobile_app_access_for_user(db, {
        "id": "employee-account-1",
        "role": "viewer",
    }))

    assert result["configured"] is False
    assert result["enabled"] is False
    assert result["permissions"] == []
