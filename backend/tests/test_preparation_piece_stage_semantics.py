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
    def __init__(self):
        self.collections = {
            module.WORKFLOWS: Collection(find_one={
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


@pytest.mark.asyncio
async def test_fully_allocated_order_stays_reviewed_until_employee_starts(monkeypatch):
    order = SimpleNamespace(
        order_number="3001",
        items=[SimpleNamespace(order_item_id="item-1", quantity=1)],
    )

    async def context(*_args, **_kwargs):
        return {"pairs": [(order, {"order_number": "3001"})]}

    monkeypatch.setattr(module, "load_reviewed_product_context", context)
    db = FakeDB()

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
    assert update["$set"]["preparation_assignment_status"] == "assigned"
    assert db[module.EVENTS].inserted[0]["event_type"] == (
        "order_preparation_fully_assigned"
    )
