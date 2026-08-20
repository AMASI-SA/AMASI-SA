from types import SimpleNamespace

import pytest

import preparation_piece_operations as module


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, _length):
        return list(self.rows)


class Collection:
    def __init__(self, *, find_one=None, find_rows=None):
        self.find_one_value = find_one
        self.find_rows = find_rows or []
        self.updated = []
        self.inserted = []

    async def find_one(self, *_args, **_kwargs):
        return self.find_one_value

    def find(self, *_args, **_kwargs):
        return Cursor(self.find_rows)

    async def update_one(self, query, update):
        self.updated.append((query, update))
        return SimpleNamespace(modified_count=1)

    async def insert_one(self, row):
        self.inserted.append(row)
        return SimpleNamespace(inserted_id="event-1")


class FakeDB:
    def __init__(self, workflow=None):
        self.collections = {
            module.WORKFLOWS: Collection(find_one=workflow or {
                "stage": "reviewed",
                "revision": 2,
                "items": [{
                    "order_item_id": "item-1",
                    "supplier_export": True,
                }],
            }),
            module.PREPARATION_UNIT_ALLOCATIONS: Collection(find_rows=[{
                "order_item_id": "item-1",
                "unit_index": 1,
                "status": "committed",
            }]),
            module.EVENTS: Collection(),
        }

    def __getitem__(self, name):
        return self.collections[name]


def _order():
    return SimpleNamespace(
        order_number="3001",
        order_id="local-3001",
        source=SimpleNamespace(source_order_id="13261517"),
        items=[SimpleNamespace(order_item_id="item-1", quantity=1)],
    )


@pytest.mark.asyncio
async def test_fully_allocated_order_moves_to_in_progress_after_salla_confirmation(
    monkeypatch,
):
    order = _order()

    async def context(*_args, **_kwargs):
        return {"pairs": [(order, {"order_number": "3001"})]}

    sync_calls = []

    async def sync(*_args, **kwargs):
        sync_calls.append(kwargs["order"])
        return "sent", None

    monkeypatch.setattr(module, "load_reviewed_product_context", context)
    monkeypatch.setattr(module, "_sync_salla_in_progress", sync)
    db = FakeDB()

    transitioned, remaining = await module._assigned_reconcile_order_stage(
        db,
        user_id="owner-1",
        order_number="3001",
        batch_id="batch-1",
        actor={"id": "manager-1", "name": "مدير التجهيز"},
    )

    assert transitioned is True
    assert remaining == 0
    assert sync_calls == [order]
    _, update = db[module.WORKFLOWS].updated[0]
    assert update["$set"]["stage"] == "in_progress"
    assert update["$set"]["preparation_assignment_status"] == "assigned"
    assert update["$set"]["salla_status_name"] == "قيد التنفيذ"
    event = db[module.EVENTS].inserted[0]
    assert event["event_type"] == "order_moved_to_in_progress"
    assert event["salla_updated"] is True
    assert event["qoyod_updated"] is False


@pytest.mark.asyncio
async def test_experiment_without_status_write_permission_stays_reviewed(
    monkeypatch,
):
    order = _order()

    async def context(*_args, **_kwargs):
        return {"pairs": [(order, {"order_number": "3001"})]}

    async def forbidden_sync(*_args, **_kwargs):
        raise AssertionError("Salla must not be called")

    monkeypatch.setattr(module, "load_reviewed_product_context", context)
    monkeypatch.setattr(module, "_sync_salla_in_progress", forbidden_sync)
    db = FakeDB({
        "stage": "reviewed",
        "revision": 2,
        "experiment_mode": True,
        "salla_status_writes_allowed": False,
        "items": [{
            "order_item_id": "item-1",
            "supplier_export": True,
        }],
    })

    transitioned, remaining = await module._assigned_reconcile_order_stage(
        db,
        user_id="owner-1",
        order_number="3001",
        batch_id="batch-1",
        actor={"id": "manager-1"},
    )

    assert transitioned is False
    assert remaining == 0
    _, update = db[module.WORKFLOWS].updated[0]
    assert "stage" not in update["$set"]
    assert db[module.EVENTS].inserted[0]["event_type"] == (
        "order_preparation_fully_assigned"
    )


@pytest.mark.asyncio
async def test_sync_uses_exact_custom_status_and_verifies_salla(monkeypatch):
    calls = []
    order_reads = 0

    async def call_salla(_db, _user_id, method, path, **kwargs):
        nonlocal order_reads
        calls.append((method, path, kwargs))
        if path == "/orders/statuses":
            return {"data": [{
                "id": 2020523226,
                "name": "قيد التنفيذ",
                "slug": "in_progress",
            }]}
        if method == "POST":
            return {"success": True}
        order_reads += 1
        return {
            "data": {
                "status": (
                    {"name": "بإنتظار المراجعة", "slug": "under_review"}
                    if order_reads == 1
                    else {"name": "قيد التنفيذ", "slug": "in_progress"}
                ),
            },
        }

    monkeypatch.setattr(module, "call_salla", call_salla)

    status, error = await module._sync_salla_in_progress(
        SimpleNamespace(),
        user_id="owner-1",
        order=_order(),
    )

    assert (status, error) == ("sent", None)
    post = next(row for row in calls if row[0] == "POST")
    assert post[1] == "/orders/13261517/status"
    assert post[2]["json"] == {"status_id": 2020523226}
    assert order_reads == 2
