"""Queue performance budgets exercised through the real read routes and mapper.

Only database transport latency is simulated; no production/provider connection
is used. Counters cover the real tenant-scoped MongoOrderRepository reads.
"""
import asyncio
from collections import Counter

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

from fulfillment_v2_routes import make_fulfillment_v2_router, WORKFLOWS
from preparation_piece_operations import _assembly_order_board, PIECES, make_preparation_piece_operations_router


class ReadCounter:
    def __init__(self, db, delay=0, delay_collections=("unified_orders",)):
        self.db = db
        self.delay = delay
        self.delay_collections = set(delay_collections)
        self.reads = Counter()
        self.index_calls = Counter()

    def __getitem__(self, name):
        return self._collection(name)

    def __getattr__(self, name):
        return self._collection(name)

    def _collection(self, name):
        target = self.db[name]
        owner = self

        class Collection:
            def __getattr__(self, attr):
                return getattr(target, attr)

            def find(self, *args, **kwargs):
                owner.reads[(name, "find")] += 1
                cursor = target.find(*args, **kwargs)
                if not owner.delay or name not in owner.delay_collections:
                    return cursor

                class DelayedCursor:
                    delayed = False

                    async def wait(self):
                        if not self.delayed:
                            self.delayed = True
                            await asyncio.sleep(owner.delay)

                    def sort(self, *args, **kwargs):
                        cursor.sort(*args, **kwargs)
                        return self

                    def limit(self, *args, **kwargs):
                        cursor.limit(*args, **kwargs)
                        return self

                    async def to_list(self, *args, **kwargs):
                        await self.wait()
                        return await cursor.to_list(*args, **kwargs)

                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        await self.wait()
                        return await cursor.__anext__()

                return DelayedCursor()

            async def find_one(self, *args, **kwargs):
                owner.reads[(name, "find_one")] += 1
                if owner.delay and name in owner.delay_collections:
                    await asyncio.sleep(owner.delay)
                return await target.find_one(*args, **kwargs)

            async def create_index(self, *args, **kwargs):
                owner.index_calls[name] += 1
                await asyncio.sleep(0)
                return await target.create_index(*args, **kwargs)

        return Collection()


async def seed_queue(db, stage, count=40, owner="owner", **workflow_fields):
    numbers = [f"{index + 1000}" for index in range(count)]
    for number in numbers:
        await db.unified_orders.insert_one({
            "user_id": owner, "order_number": number,
            "order_date": "2026-11-01T08:00:00Z", "order_status": stage,
            "raw_by_source": {"salla_direct": {
                "id": number, "reference_id": number,
                "date": "2026-11-01T08:00:00Z", "status": stage,
                "customer": {"full_name": "Synthetic customer"},
                "items": [{"id": number, "name": "Synthetic item", "quantity": 1}],
            }},
        })
        await db[WORKFLOWS].insert_one({
            "user_id": owner, "order_number": number, "stage": stage,
            "assembly_status": "completed", "ready_to_ship_source": "preparation_receipt",
            "carrier_handoff_state": "with_handoff_employee",
            "preparation_piece_count": 1, "updated_at": number,
            **workflow_fields,
        })
        await db[PIECES].insert_one({
            "user_id": owner, "order_number": number, "piece_id": f"piece-{number}",
            "assembly_status": "ready", "status": "ready_for_assembly",
        })
    return numbers


async def queue_response(db, path, *, params=None, user=None):
    app = FastAPI()
    actor = user or {"id": "owner", "role": "owner"}
    async def current_user():
        return actor
    app.include_router(make_fulfillment_v2_router(db, current_user))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("path,stage,params", [
    ("/fulfillment-v2/ready-to-ship", "ready_to_ship", {}),
    ("/fulfillment-v2/completed", "completed", {}),
    ("/fulfillment-v2/carrier-handoff", "completed", {}),
    ("/fulfillment-v2/delivery-tracking", "delivering", {"stage": "delivering"}),
    ("/fulfillment-v2/delivery-tracking", "delivered", {"stage": "delivered"}),
])
async def test_queue_reads_orders_in_bounded_batches_and_preserves_tenant(path, stage, params):
    db = AsyncMongoMockClient().read_budget
    numbers = await seed_queue(db, stage)
    await seed_queue(db, stage, 3, owner="other-owner")
    # The completed labeling list excludes handoff custody.
    if path.endswith("/completed"):
        await db[WORKFLOWS].update_many({}, {"$unset": {"carrier_handoff_employee_id": ""}})
    counted = ReadCounter(db)
    before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    result = await queue_response(counted, path, params=params)
    assert [row["order_number"] for row in result["items"]] == numbers
    assert result["total"] == len(numbers)
    assert all(row["customer_name"] == "Synthetic customer" for row in result["items"])
    assert all(row["items"][0]["quantity"] == 1 for row in result["items"])
    assert counted.reads[("unified_orders", "find_one")] == 0
    assert counted.reads[("unified_orders", "find")] == 1
    after = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    assert before == after


@pytest.mark.asyncio
async def test_missing_orders_refill_page_without_reordering_or_cross_tenant_leak():
    db = AsyncMongoMockClient().refill
    numbers = await seed_queue(db, "ready_to_ship", 120)
    await db.unified_orders.delete_many({"order_number": {"$in": numbers[:10]}})
    await seed_queue(db, "ready_to_ship", 10, owner="other-owner")
    # Keep the original look-ahead semantics; limit=40 examines up to 120 workflows.
    counted = ReadCounter(db)
    result = await queue_response(counted, "/fulfillment-v2/ready-to-ship", params={"limit": 40})
    assert [row["order_number"] for row in result["items"]] == numbers[10:50]
    assert counted.reads[("unified_orders", "find_one")] == 0
    assert counted.reads[("unified_orders", "find")] <= 2


@pytest.mark.asyncio
async def test_employee_queue_preserves_warehouse_and_claim_restrictions():
    db = AsyncMongoMockClient().employee_queue
    numbers = await seed_queue(db, "ready_to_ship", 5)
    await db.mezan_role_assignments_v2.insert_one({
        "owner_user_id": "owner", "user_id": "worker", "role_key": "shipping_operator",
        "enabled": True, "warehouse_ids": ["warehouse-one"],
        "fulfillment_responsibilities": ["instant_ready"],
    })
    await db[WORKFLOWS].update_many({"order_number": {"$in": numbers[:4]}}, {"$set": {"fulfillment_decision.warehouse_ids": ["warehouse-one"]}})
    await db[WORKFLOWS].update_one({"order_number": numbers[1]}, {"$set": {"claimed_by": "other-worker"}})
    await db[WORKFLOWS].update_one({"order_number": numbers[2]}, {"$set": {"fulfillment_decision.warehouse_ids": ["warehouse-two"]}})
    await db[WORKFLOWS].update_one({"order_number": numbers[3]}, {"$set": {"claimed_by": "worker"}})
    result = await queue_response(ReadCounter(db), "/fulfillment-v2/ready-to-ship",
                                  user={"id": "worker", "role": "employee", "created_by": "owner"})
    assert [row["order_number"] for row in result["items"]] == [numbers[0], numbers[3], numbers[4]]


@pytest.mark.asyncio
async def test_denied_employee_does_not_hydrate_queue_orders():
    db = AsyncMongoMockClient().denied_queue
    await seed_queue(db, "ready_to_ship", 3)
    counted = ReadCounter(db)
    with pytest.raises(httpx.HTTPStatusError) as error:
        await queue_response(counted, "/fulfillment-v2/ready-to-ship",
                             user={"id": "worker", "role": "employee", "created_by": "owner"})
    assert error.value.response.status_code == 403
    assert counted.reads[("unified_orders", "find")] == counted.reads[("unified_orders", "find_one")] == 0


@pytest.mark.asyncio
async def test_assembly_board_batches_orders_and_keeps_total_sort_and_pagination():
    db = AsyncMongoMockClient().assembly_budget
    numbers = await seed_queue(db, "in_progress", 205)
    await seed_queue(db, "in_progress", 3, owner="other-owner")
    counted = ReadCounter(db)
    result = await _assembly_order_board(counted, user_id="owner", state="in_progress", limit=20, offset=30)
    assert result["total"] == len(numbers)
    assert [row["order_number"] for row in result["items"]] == numbers[30:50]
    assert all(row["ready_count"] == row["total_count"] == 1 for row in result["items"])
    assert counted.reads[("unified_orders", "find_one")] == 0
    assert counted.reads[("unified_orders", "find")] <= 3


@pytest.mark.asyncio
async def test_search_does_not_hydrate_unmatched_assembly_orders():
    db = AsyncMongoMockClient().assembly_search_budget
    await seed_queue(db, "in_progress", 205)
    counted = ReadCounter(db)
    result = await _assembly_order_board(counted, user_id="owner", state="in_progress", limit=20, offset=0, query="1005")
    assert result["total"] == 1
    assert result["items"][0]["order_number"] == "1005"
    assert counted.reads[("unified_orders", "find_one")] == 0
    assert counted.reads[("unified_orders", "find")] == 1


@pytest.mark.asyncio
async def test_simultaneous_board_polls_initialize_indexes_once_per_router():
    raw = AsyncMongoMockClient().index_budget
    await seed_queue(raw, "in_progress", 3)
    db = ReadCounter(raw)
    app = FastAPI()
    async def actor():
        return {"id": "owner", "role": "owner"}
    app.include_router(make_preparation_piece_operations_router(db, actor))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        responses = await asyncio.gather(*[
            client.get("/preparation-work-v1/assembly/orders", params={"state": "in_progress"})
            for _ in range(4)
        ])
    assert all(response.status_code == 200 and response.json()["total"] == 3 for response in responses)
    assert sum(db.index_calls.values()) == 5


@pytest.mark.asyncio
async def test_index_failure_can_retry_without_marking_the_router_ready(monkeypatch):
    import preparation_piece_operations as operations
    raw = AsyncMongoMockClient().index_retry
    db = ReadCounter(raw)
    original = operations.ensure_piece_operation_indexes
    attempts = 0
    async def ensure_once_failure(db):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic index transport failure")
        await original(db)
    monkeypatch.setattr(operations, "ensure_piece_operation_indexes", ensure_once_failure)
    app = FastAPI()
    async def actor():
        return {"id": "owner", "role": "owner"}
    app.include_router(make_preparation_piece_operations_router(db, actor))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://local.test") as client:
        responses = [await client.get("/preparation-work-v1/assembly/orders", params={"state": "in_progress"}) for _ in range(3)]
    assert [response.status_code for response in responses] == [500, 200, 200]
    assert attempts == 2
    assert sum(db.index_calls.values()) == 5
