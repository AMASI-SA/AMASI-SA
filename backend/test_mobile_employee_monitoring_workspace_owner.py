import pytest

from mobile_employee_monitoring_workspace_routes import _resolve_monitored_employee


class _Collection:
    def __init__(self, row=None):
        self.row = row

    async def find_one(self, query, projection=None):
        if self.row is None:
            return None
        if self.row.get("id") != query.get("id"):
            return None
        if "user_id" in query and self.row.get("user_id") != query.get("user_id"):
            return None
        return dict(self.row)


class _DB:
    def __init__(self, *, employee=None, owner=None):
        self._rows = {
            "mezan_employees_v2": _Collection(employee),
            "users": _Collection(owner),
        }
        self.users = self._rows["users"]

    def __getitem__(self, name):
        return self._rows[name]


@pytest.mark.asyncio
async def test_owner_resolves_without_duplicate_employee_record():
    db = _DB(
        owner={
            "id": "owner-1",
            "name": "Owner Name",
            "email": "owner@example.test",
        }
    )

    result = await _resolve_monitored_employee(
        db,
        owner_id="owner-1",
        employee_id="owner-1",
    )

    assert result is not None
    assert result["id"] == "owner-1"
    assert result["display_name"] == "Owner Name"
    assert result["status"] == "active"
    assert result["synthetic_owner"] is True


@pytest.mark.asyncio
async def test_unknown_non_owner_still_fails_closed():
    db = _DB(owner={"id": "owner-1", "name": "Owner"})

    result = await _resolve_monitored_employee(
        db,
        owner_id="owner-1",
        employee_id="unknown-employee",
    )

    assert result is None


@pytest.mark.asyncio
async def test_real_employee_remains_authoritative():
    db = _DB(
        employee={
            "id": "employee-1",
            "user_id": "owner-1",
            "display_name": "Employee One",
            "status": "active",
        },
        owner={"id": "owner-1", "name": "Owner"},
    )

    result = await _resolve_monitored_employee(
        db,
        owner_id="owner-1",
        employee_id="employee-1",
    )

    assert result is not None
    assert result["display_name"] == "Employee One"
    assert result.get("synthetic_owner") is not True
