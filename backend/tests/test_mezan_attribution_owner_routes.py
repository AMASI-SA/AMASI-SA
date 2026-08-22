import pytest
from fastapi import HTTPException

from mezan_attribution_owner_routes import build_backfill_preview, require_owner


class CountCollection:
    def __init__(self, value):
        self.value = value
        self.queries = []

    async def count_documents(self, query):
        self.queries.append(query)
        return self.value


class FakeDB:
    def __init__(self, unified_count=12, ledger_count=7):
        self.unified_orders = CountCollection(unified_count)
        self.collections = {"mezan_attribution_order_ledger_v1": CountCollection(ledger_count)}

    def __getitem__(self, name):
        return self.collections[name]


def test_owner_guard_accepts_owner_role():
    user = {"role": "owner", "id": "u1"}
    assert require_owner(user) is user


def test_owner_guard_accepts_is_owner_flag():
    user = {"role": "admin", "is_owner": True, "id": "u1"}
    assert require_owner(user) is user


def test_owner_guard_rejects_employee():
    with pytest.raises(HTTPException) as exc:
        require_owner({"role": "employee", "id": "e1"})
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "owner_required"


@pytest.mark.asyncio
async def test_backfill_preview_is_read_only_and_tenant_scoped():
    db = FakeDB(unified_count=12, ledger_count=7)
    result = await build_backfill_preview(db, user_id="owner-1")

    assert result == {
        "unified_orders": 12,
        "ledger_rows": 7,
        "estimated_missing_rows": 5,
        "external_writes": False,
        "read_only": True,
    }
    assert db.unified_orders.queries == [{"user_id": "owner-1"}]
    assert db["mezan_attribution_order_ledger_v1"].queries == [{"user_id": "owner-1"}]


@pytest.mark.asyncio
async def test_backfill_preview_never_reports_negative_missing_rows():
    db = FakeDB(unified_count=4, ledger_count=9)
    result = await build_backfill_preview(db, user_id="owner-1")
    assert result["estimated_missing_rows"] == 0
