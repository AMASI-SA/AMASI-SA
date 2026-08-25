from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from preparation_file_failure_safety import (
    SafePreparationFileDraftRequest,
    _batch_order_numbers,
    make_preparation_file_failure_safety_router,
    reconcile_released_preparation_stages,
    release_orphan_ready_batches,
    release_incomplete_preparation_request,
)
from preparation_file_registry import REGISTRY
from preparation_piece_operations import PIECES
from reviewed_preparation_batches import BATCHES
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS
from order_review_routes import EVENTS, WORKFLOWS


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length):
        return self.rows[:length]


class FakeCollection:
    def __init__(self, *, find_one_rows=None, find_rows=None):
        self.find_one_rows = list(find_one_rows or [])
        self.find_rows = list(find_rows or [])
        self.deleted_many = []
        self.deleted_one = []
        self.inserted = []
        self.updated = []

    async def find_one(self, *_args, **_kwargs):
        return self.find_one_rows.pop(0) if self.find_one_rows else None

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.find_rows)

    async def delete_many(self, query):
        self.deleted_many.append(query)
        return SimpleNamespace(deleted_count=len(self.find_rows))

    async def delete_one(self, query):
        self.deleted_one.append(query)
        return SimpleNamespace(deleted_count=1)

    async def insert_one(self, row):
        self.inserted.append(row)
        return SimpleNamespace(inserted_id=row.get("id"))

    async def update_one(self, query, update, **_kwargs):
        self.updated.append((query, update))
        return SimpleNamespace(modified_count=1)


class FakeDb:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, key):
        return self.collections[key]


def test_required_schedule_is_validated_before_allocating_units():
    with pytest.raises(ValidationError):
        SafePreparationFileDraftRequest(
            client_request_id="request-123",
            file_title="دفعة",
            responsible_employee_id="employee-1",
            expected_quantity=2,
            selected_product_count=1,
            schedule_mode="required",
        )

    automatic = SafePreparationFileDraftRequest(
        client_request_id="request-123",
        file_title="دفعة",
        responsible_employee_id="employee-1",
        expected_quantity=2,
        selected_product_count=1,
        schedule_mode="automatic",
        required_due_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    assert automatic.required_due_at is None


def test_batch_order_numbers_merge_batch_lines_and_allocation_rows():
    assert _batch_order_numbers(
        {
            "order_numbers": ["3001"],
            "lines": [{"order_number": "3002"}],
        },
        [{"order_number": "3003"}, {"order_number": "3001"}],
    ) == ["3001", "3002", "3003"]


@pytest.mark.asyncio
async def test_incomplete_request_releases_committed_units(monkeypatch):
    registry = FakeCollection(find_one_rows=[{
        "status": "draft",
        "client_request_id": "request-123",
    }])
    batches = FakeCollection(find_one_rows=[{
        "id": "batch-1",
        "status": "ready",
        "client_request_id": "request-123",
        "order_numbers": ["3001"],
    }])
    pieces = FakeCollection(find_one_rows=[])
    allocations = FakeCollection(find_rows=[{
        "order_number": "3001",
        "status": "committed",
    }])
    events = FakeCollection()
    db = FakeDb({
        REGISTRY: registry,
        BATCHES: batches,
        PIECES: pieces,
        PREPARATION_UNIT_ALLOCATIONS: allocations,
        EVENTS: events,
    })

    async def reconcile(*_args, **_kwargs):
        return False, 1

    import reviewed_preparation_batches as batch_module

    monkeypatch.setattr(batch_module, "_reconcile_order_stage", reconcile)

    result = await release_incomplete_preparation_request(
        db,
        user_id="owner-1",
        client_request_id="request-123",
        actor={"id": "owner-1", "role": "owner"},
        reason="test_failure",
    )

    assert result["released"] is True
    assert result["released_unit_count"] == 1
    assert allocations.deleted_many[0]["batch_id"] == "batch-1"
    assert batches.deleted_one[0]["id"] == "batch-1"
    assert registry.deleted_one[0]["status"] == {"$ne": "ready"}
    assert events.inserted[0]["event_type"] == (
        "failed_preparation_request_released"
    )
    assert events.inserted[0]["salla_updated"] is False
    assert events.inserted[0]["qoyod_updated"] is False


@pytest.mark.asyncio
async def test_ready_registry_is_never_released():
    registry = FakeCollection(find_one_rows=[{
        "status": "ready",
        "file_number": "PF-20260803-0011",
    }])
    db = FakeDb({REGISTRY: registry})

    result = await release_incomplete_preparation_request(
        db,
        user_id="owner-1",
        client_request_id="request-123",
        actor={"id": "owner-1", "role": "owner"},
        reason="test_failure",
    )

    assert result == {
        "ok": True,
        "released": False,
        "status": "already_finalized",
        "file_number": "PF-20260803-0011",
    }


@pytest.mark.asyncio
async def test_released_event_replays_stage_reconciliation(monkeypatch):
    events = FakeCollection(find_rows=[{
        "event_type": "failed_preparation_request_released",
        "order_numbers": ["3001", "3001"],
    }])
    workflows = FakeCollection(find_one_rows=[{
        "stage": "in_progress",
    }])
    db = FakeDb({EVENTS: events, WORKFLOWS: workflows})

    async def reconcile(*_args, **kwargs):
        assert kwargs["order_number"] == "3001"
        assert kwargs["batch_id"] == ""
        return False, 11

    import reviewed_preparation_batches as batch_module

    monkeypatch.setattr(batch_module, "_reconcile_order_stage", reconcile)
    result = await reconcile_released_preparation_stages(
        db,
        user_id="owner-1",
        actor={"id": "owner-1", "role": "owner"},
    )

    assert result["restored_order_count"] == 1
    assert result["restored_order_numbers"] == ["3001"]


@pytest.mark.asyncio
async def test_in_progress_workflow_without_release_event_is_reconciled(monkeypatch):
    events = FakeCollection(find_rows=[])
    workflows = FakeCollection(
        find_rows=[{"order_number": "lost-11"}],
        find_one_rows=[{"stage": "in_progress"}],
    )
    db = FakeDb({EVENTS: events, WORKFLOWS: workflows})

    async def reconcile(*_args, **kwargs):
        assert kwargs["order_number"] == "lost-11"
        return False, 11

    import reviewed_preparation_batches as batch_module

    monkeypatch.setattr(batch_module, "_reconcile_order_stage", reconcile)
    result = await reconcile_released_preparation_stages(
        db,
        user_id="owner-1",
        actor={"id": "owner-1", "role": "owner"},
    )

    assert result["restored_order_count"] == 1
    assert result["restored_order_numbers"] == ["lost-11"]


@pytest.mark.asyncio
async def test_orphan_ready_batch_releases_unstarted_committed_units():
    batch = FakeCollection(find_rows=[{
        "id": "batch-lost",
        "status": "ready",
        "client_request_id": "request-lost",
        "order_numbers": ["4001"],
    }])
    registry = FakeCollection(find_one_rows=[])
    pieces = FakeCollection(find_one_rows=[])
    allocations = FakeCollection(find_rows=[{
        "batch_id": "batch-lost",
        "order_number": "4001",
        "status": "committed",
    }] * 11)
    events = FakeCollection()
    db = FakeDb({
        BATCHES: batch,
        REGISTRY: registry,
        PIECES: pieces,
        PREPARATION_UNIT_ALLOCATIONS: allocations,
        EVENTS: events,
    })

    result = await release_orphan_ready_batches(
        db,
        user_id="owner-1",
        actor={"id": "owner-1", "role": "owner"},
        threshold=datetime.now(timezone.utc),
    )

    assert result["released_count"] == 1
    assert result["released_unit_count"] == 11
    assert result["released"][0]["order_numbers"] == ["4001"]
    assert allocations.deleted_many
    assert batch.deleted_one
    assert events.inserted[0]["event_type"] == "orphan_ready_batch_released"


@pytest.mark.asyncio
async def test_committed_allocations_without_batch_are_released():
    allocations = FakeCollection(find_rows=[{
        "batch_id": "deleted-batch",
        "order_number": "5001",
        "status": "committed",
    }] * 11)
    events = FakeCollection()
    db = FakeDb({
        BATCHES: FakeCollection(),
        REGISTRY: FakeCollection(),
        PIECES: FakeCollection(),
        PREPARATION_UNIT_ALLOCATIONS: allocations,
        EVENTS: events,
    })

    result = await release_orphan_ready_batches(
        db,
        user_id="owner-1",
        actor={"id": "owner-1", "role": "owner"},
        threshold=datetime.now(timezone.utc),
    )

    assert result["released_count"] == 1
    assert result["released_unit_count"] == 11
    assert result["released"][0]["order_numbers"] == ["5001"]
    assert events.inserted[0]["event_type"] == "orphan_committed_allocation_released"


def test_router_registers_atomic_draft_release_and_stale_recovery():
    router = make_preparation_file_failure_safety_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method)
        for route in router.routes
        for method in route.methods
    }
    assert ("/preparation-file-safety-v1/drafts", "POST") in routes
    assert (
        "/preparation-file-safety-v1/requests/{client_request_id}/release",
        "POST",
    ) in routes
    assert ("/preparation-file-safety-v1/recover-stale", "POST") in routes
