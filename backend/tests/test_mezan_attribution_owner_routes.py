import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from mezan_attribution_owner_routes import (
    AttributionBackfillRequest,
    BACKFILL_BATCH_DEFAULT,
    BACKFILL_BATCH_MAX,
    BACKFILL_STATE_COLLECTION,
    build_backfill_preview,
    require_owner,
)


class CountCollection:
    def __init__(self, value):
        self.value = value
        self.queries = []

    async def count_documents(self, query):
        self.queries.append(query)
        return self.value


class StateCollection:
    def __init__(self, row=None):
        self.row = row
        self.find_queries = []

    async def find_one(self, query, projection=None):
        self.find_queries.append((query, projection))
        if self.row is None:
            return None
        result = dict(self.row)
        if projection:
            for key, included in projection.items():
                if included == 0:
                    result.pop(key, None)
        return result


class FakeDB:
    def __init__(self, unified_count=12, ledger_count=7, state=None):
        self.unified_orders = CountCollection(unified_count)
        self.collections = {
            "mezan_attribution_order_ledger_v1": CountCollection(ledger_count),
            BACKFILL_STATE_COLLECTION: StateCollection(state),
        }

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


def test_backfill_request_defaults_to_small_batch():
    payload = AttributionBackfillRequest(confirmation="تشغيل")
    assert payload.limit == BACKFILL_BATCH_DEFAULT == 25


def test_backfill_request_rejects_large_batch():
    assert BACKFILL_BATCH_MAX == 50
    with pytest.raises(ValidationError):
        AttributionBackfillRequest(confirmation="تشغيل", limit=500)


@pytest.mark.asyncio
async def test_backfill_preview_is_read_only_and_tenant_scoped():
    db = FakeDB(unified_count=12, ledger_count=7)
    result = await build_backfill_preview(db, user_id="owner-1")

    assert result == {
        "unified_orders": 12,
        "ledger_rows": 7,
        "estimated_missing_rows": 5,
        "backfill_state": None,
        "batch_default": 25,
        "batch_max": 50,
        "external_writes": False,
        "read_only": True,
    }
    assert db.unified_orders.queries == [{"user_id": "owner-1"}]
    assert db["mezan_attribution_order_ledger_v1"].queries == [{"user_id": "owner-1"}]
    assert db[BACKFILL_STATE_COLLECTION].find_queries == [
        (
            {"_id": "attribution-backfill:owner-1", "user_id": "owner-1"},
            {"_id": 0, "cursor_id": 0},
        )
    ]


@pytest.mark.asyncio
async def test_backfill_preview_exposes_public_checkpoint_without_cursor():
    db = FakeDB(
        state={
            "_id": "attribution-backfill:owner-1",
            "user_id": "owner-1",
            "cursor_id": "opaque-db-cursor",
            "running": False,
            "scanned": 25,
            "synced": 24,
            "failed": 1,
            "completed": False,
        }
    )
    result = await build_backfill_preview(db, user_id="owner-1")
    assert result["backfill_state"] == {
        "user_id": "owner-1",
        "running": False,
        "scanned": 25,
        "synced": 24,
        "failed": 1,
        "completed": False,
    }
    assert "cursor_id" not in result["backfill_state"]


@pytest.mark.asyncio
async def test_backfill_preview_never_reports_negative_missing_rows():
    db = FakeDB(unified_count=4, ledger_count=9)
    result = await build_backfill_preview(db, user_id="owner-1")
    assert result["estimated_missing_rows"] == 0
