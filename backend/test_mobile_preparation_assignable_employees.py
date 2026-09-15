import asyncio

from mobile_preparation_assignable_employees import native_preparation_assignable_employees
from mobile_app_permissions import MOBILE_APP_ACCESS


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, _limit):
        return list(self.rows)


class Collection:
    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, query, _projection=None):
        def matches(row):
            for key, expected in query.items():
                value = row.get(key)
                if isinstance(expected, dict) and "$in" in expected:
                    if value not in expected["$in"]:
                        return False
                elif isinstance(expected, dict) and "$nin" in expected:
                    if value in expected["$nin"]:
                        return False
                elif value != expected:
                    return False
            return True
        return Cursor([row for row in self.rows if matches(row)])


class DB:
    def __init__(self):
        self.users = Collection([
            {"id": "owner-1", "name": "المالك", "email": "owner@example.com", "role": "owner"},
            {"id": "employee-allowed", "created_by": "owner-1", "name": "موظف مسموح", "email": "a@example.com", "role": "viewer"},
            {"id": "employee-no-page", "created_by": "owner-1", "name": "موظف بدون الصفحة", "email": "b@example.com", "role": "viewer"},
            {"id": "legacy-linked", "name": "", "email": "", "role": "viewer"},
            {"id": "disabled", "created_by": "owner-1", "name": "موقوف", "email": "d@example.com", "disabled": True},
        ])
        self.collections = {
            "mezan_employees_v2": Collection([
                {"user_id": "owner-1", "account_user_id": "legacy-linked", "name": "موظف قديم", "email": "legacy@example.com"},
            ]),
            MOBILE_APP_ACCESS: Collection([
                {"owner_user_id": "owner-1", "user_id": "employee-allowed", "enabled": True, "permissions": ["app.page.my_products"]},
                {"owner_user_id": "owner-1", "user_id": "employee-no-page", "enabled": True, "permissions": ["app.page.orders"]},
                {"owner_user_id": "owner-1", "user_id": "legacy-linked", "enabled": True, "permissions": ["app.role.manager"]},
                {"owner_user_id": "owner-1", "user_id": "disabled", "enabled": True, "permissions": ["app.page.my_products"]},
            ]),
        }

    def __getitem__(self, key):
        return self.collections[key]


def test_native_assignable_employees_use_app_permissions_not_web_role():
    db = DB()
    reviewer = {"id": "owner-1", "name": "المالك", "email": "owner@example.com", "role": "owner"}
    rows = asyncio.run(native_preparation_assignable_employees(
        db,
        user_id="owner-1",
        reviewer=reviewer,
    ))

    ids = [row["id"] for row in rows]
    assert "owner-1" in ids
    assert "employee-allowed" in ids
    assert "legacy-linked" in ids
    assert "employee-no-page" not in ids
    assert "disabled" not in ids

    legacy = next(row for row in rows if row["id"] == "legacy-linked")
    assert legacy["name"] == "موظف قديم"
    assert legacy["email"] == "legacy@example.com"
