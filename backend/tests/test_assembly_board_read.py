"""Current employee queues: exact membership/counts without historical hydration."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

import preparation_piece_operations as operations
import assembly_board_read as reader
from order_engine.repository import MongoOrderRepository
from order_engine.service import _map_row
from order_engine.mapper import _map_item
from product_fulfillment_rules import payment_is_eligible
from review_local_policy import LOCAL_COMPLETION_MODE, OPERATIONS, WORKFLOWS


WHEN = "2026-11-01T08:00:00Z"
OWNER = "board-owner"


async def seed_order(db, number, *, routes=("direct_assembly",), quantities=None,
                     status="in_progress", owner=OWNER, local=False, proof=True,
                     workflow_stage="in_progress", received=True, payment=None):
    quantities = quantities or [1] * len(routes)
    items = [{"order_item_id": f"salla:{number}:{number}-line-{index}", "quantity": quantity,
              "preparation_route": route}
             for index, (route, quantity) in enumerate(zip(routes, quantities))]
    raw = {"id": f"provider-{number}", "reference_id": number, "date": WHEN,
           "status": status, "payment_method": "cod",
           "items": [{"id": f"{number}-line-{index}", "product_id": f"product-{index}",
                      "name": "Synthetic product", "quantity": item["quantity"]}
                     for index, item in enumerate(items)]}
    if payment:
        raw.update(payment)
    source = {"user_id": owner, "order_number": number, "order_date": WHEN,
              "order_status": status, "raw_by_source": {"salla_direct": raw}}
    workflow = {"user_id": owner, "order_number": number, "stage": workflow_stage,
                "items": items, "preparation_piece_count": sum(quantities),
                "updated_at": number}
    if local:
        workflow.update(completion_mode=LOCAL_COMPLETION_MODE,
                        review_completion_operation_id=f"review-{owner}-{number}")
        if proof:
            await db[OPERATIONS].insert_one({
                "_id": workflow["review_completion_operation_id"], "user_id": owner,
                "order_number": number, "completion_mode": LOCAL_COMPLETION_MODE,
                "state": "completed",
            })
    await db.unified_orders.insert_one(source)
    await db[WORKFLOWS].insert_one(workflow)
    for item in items:
        if item["preparation_route"] == "supplier_file":
            for index in range(item["quantity"]):
                await db[operations.PIECES].insert_one({
                    "user_id": owner, "order_number": number,
                    "piece_id": f"{owner}-{item['order_item_id']}-{index}",
                    "order_item_id": item["order_item_id"], "unit_index": index + 1,
                    "batch_id": f"batch-{number}", "status": "received" if received else "assigned",
                    "supplier_dispatch_status": "received" if received else "assigned",
                    **({"received_at": datetime(2026, 11, 2, tzinfo=timezone.utc)} if received else {}),
                    "assembly_status": "pending",
                })
    return source, workflow


async def board(db, state="supplier_received", **kwargs):
    return await operations._assembly_order_board(
        db, user_id=OWNER, state=state, limit=kwargs.pop("limit", 10),
        offset=kwargs.pop("offset", 0), **kwargs,
    )


@pytest.mark.asyncio
async def test_historical_limit_cannot_hide_a_current_order():
    db = AsyncMongoMockClient().assembly_history
    # Old workflows exceed the former cap and precede the current order.
    await db[WORKFLOWS].insert_many([
        {"user_id": OWNER, "order_number": f"old-{index:05}", "stage": "completed",
         "updated_at": f"0-{index:05}", "preparation_piece_count": 1}
        for index in range(5001)
    ])
    await db.unified_orders.insert_many([
        {"user_id": OWNER, "order_number": f"old-{index:05}", "order_status": "completed",
         "order_date": WHEN, "raw_by_source": {"salla_direct": {
             "id": str(index), "reference_id": f"old-{index:05}", "date": WHEN,
             "status": "completed", "items": []}}}
        for index in range(5001)
    ])
    await seed_order(db, "new-current", routes=("supplier_file",))
    result = await board(db)
    assert [row["order_number"] for row in result["items"]] == ["new-current"]
    assert result["queue_counts"] == {"in_progress": 1, "direct_preparation": 0, "supplier_received": 1}


@pytest.mark.asyncio
async def test_current_status_is_required_for_both_groups_with_local_review_retained():
    db = AsyncMongoMockClient().assembly_status
    for route in ("direct_assembly", "supplier_file"):
        for status in ("in_progress", "under_review", "completed", "cancelled"):
            await seed_order(db, f"{route}-{status}", routes=(route,), status=status, local=True)
        await seed_order(db, f"{route}-conflict", routes=(route,))
        await db.unified_orders.update_one({"order_number": f"{route}-conflict"},
                                          {"$set": {"order_status": "completed"}})
    for state, route in (("direct_preparation", "direct_assembly"), ("supplier_received", "supplier_file")):
        result = await board(db, state)
        assert [row["order_number"] for row in result["items"]] == [f"{route}-in_progress"]
    broad = await board(db, "in_progress", limit=50)
    assert {f"{route}-under_review" for route in ("direct_assembly", "supplier_file")} <= {
        row["order_number"] for row in broad["items"]}


@pytest.mark.asyncio
async def test_supplier_membership_is_complete_receipt_without_employee_handoff_or_ready():
    db = AsyncMongoMockClient().assembly_receipt
    await seed_order(db, "direct")
    await seed_order(db, "mixed", routes=("direct_assembly", "supplier_file"), quantities=(1, 2))
    await seed_order(db, "supplier", routes=("supplier_file",), quantities=(2,))
    await seed_order(db, "partial", routes=("supplier_file",), quantities=(2,))
    await db[operations.PIECES].update_one({"order_number": "partial"},
                                         {"$set": {"supplier_dispatch_status": "partial_received"}})
    await seed_order(db, "unreceived", routes=("supplier_file",), received=False)
    before = await db[operations.PIECES].find({}).to_list(None)
    result = await board(db)
    assert [row["order_number"] for row in result["items"]] == ["mixed", "supplier"]
    assert all(row["ready_count"] == 0 for row in result["items"])
    assert result["group_counts"] == {"direct_preparation": 1, "supplier_received": 2}
    assert await db[operations.PIECES].find({}).to_list(None) == before
    assert not any(row.get("preparation_received_at") for row in before)


@pytest.mark.asyncio
async def test_pagination_and_search_leave_global_counts_exact():
    db = AsyncMongoMockClient().assembly_pages
    for index in range(21):
        await seed_order(db, f"current-{index:02}")
    await seed_order(db, "supplier", routes=("supplier_file",))
    await seed_order(db, "partial", routes=("supplier_file",), received=False)
    all_numbers = []
    for offset, size in ((0, 10), (10, 10), (20, 1)):
        result = await board(db, "direct_preparation", offset=offset)
        assert len(result["items"]) == size
        assert result["total"] == 21
        assert result["queue_counts"] == {"in_progress": 23, "direct_preparation": 21, "supplier_received": 1}
        all_numbers.extend(row["order_number"] for row in result["items"])
    assert all_numbers == [f"current-{index:02}" for index in range(21)]
    searched = await board(db, "direct_preparation", query="#current-20")
    assert searched["total"] == 1
    assert searched["queue_counts"] == result["queue_counts"]
    assert searched["group_counts"] == result["group_counts"]
    empty = await board(db, "supplier_received", query="no-match")
    assert empty["items"] == [] and empty["total"] == 0
    assert empty["queue_counts"] == result["queue_counts"]


@pytest.mark.asyncio
async def test_local_payment_and_review_proof_are_not_bypassed():
    db = AsyncMongoMockClient().assembly_policy
    await seed_order(db, "approved", local=True, workflow_stage="reviewed")
    await seed_order(db, "missing-proof", local=True, proof=False)
    await seed_order(db, "unpaid", local=True, payment={
        "payment_method": "bank", "payment_collection_status": "unpaid",
        "paid_amount": 0, "remaining_amount": 100, "has_remaining_amount": True,
    })
    # Existing received-supplier contract permits local reviewed visibility
    # independently of employee custody and the original local operation proof.
    await seed_order(db, "receipt-exception", local=True, proof=False,
                     workflow_stage="reviewed", routes=("supplier_file",))
    direct = await board(db, "direct_preparation")
    supplier = await board(db)
    assert [row["order_number"] for row in direct["items"]] == ["approved"]
    assert [row["order_number"] for row in supplier["items"]] == ["receipt-exception"]


@pytest.mark.asyncio
async def test_current_provider_additions_and_quantity_changes_are_not_complete():
    db = AsyncMongoMockClient().assembly_current_lines
    for number, route in (("direct-added", "direct_assembly"), ("supplier-quantity", "supplier_file")):
        await seed_order(db, number, routes=(route,))
    await db.unified_orders.update_one({"order_number": "direct-added"}, {"$push": {
        "raw_by_source.salla_direct.items": {"id": "new-line", "quantity": 1, "name": "New product"}}})
    await db.unified_orders.update_one({"order_number": "supplier-quantity"}, {"$set": {
        "raw_by_source.salla_direct.items.0.quantity": 2}})
    assert (await board(db, "direct_preparation"))["total"] == 0
    assert (await board(db))["total"] == 0


@pytest.mark.asyncio
async def test_tenant_isolation_and_default_ten_row_route_are_read_only():
    db = AsyncMongoMockClient().assembly_route
    for index in range(12):
        await seed_order(db, f"route-{index:02}")
    await seed_order(db, "route-00", owner="other-owner", routes=("supplier_file",))
    before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
    app = FastAPI()
    async def actor():
        return {"id": OWNER, "role": "owner"}
    app.include_router(operations.make_preparation_piece_operations_router(db, actor))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        response = await client.get("/preparation-work-v1/assembly/orders", params={"state": "direct_preparation"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["limit"] == 10 and len(result["items"]) == 10
    assert result["queue_counts"] == {"in_progress": 12, "direct_preparation": 12, "supplier_received": 0}
    after = {name: await db[name].find({}).to_list(None) for name in before}
    assert after == before


class ProjectedReads:
    def __init__(self, db):
        self.db = db
        self.finds = []
        self.returned = []

    def __getitem__(self, name):
        target = self.db[name]
        owner = self
        class Collection:
            def __getattr__(self, attr):
                return getattr(target, attr)

            def find(self, query, projection=None, **kwargs):
                owner.finds.append((name, deepcopy(query), deepcopy(projection)))
                cursor = target.find(query, projection, **kwargs)
                class Cursor:
                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        row = await cursor.__anext__()
                        owner.returned.append((name, deepcopy(row)))
                        return row

                    async def to_list(self, *args, **kwargs):
                        rows = await cursor.to_list(*args, **kwargs)
                        owner.returned.extend((name, deepcopy(row)) for row in rows)
                        return rows
                return Cursor()
        return Collection()

    def __getattr__(self, name):
        return self[name]


@pytest.mark.asyncio
async def test_projection_budget_and_counts_endpoint_do_not_hydrate_full_orders(monkeypatch):
    raw = AsyncMongoMockClient().assembly_light
    await seed_order(raw, "current")
    await seed_order(raw, "historical", status="completed")
    huge = "payload-not-needed-for-a-queue" * 4000
    await raw.unified_orders.update_many({}, {"$set": {
        "raw_by_source.salla_direct.customer": {"full_name": huge, "avatar": huge},
        "raw_by_source.salla_direct.items.0.images": [huge],
        "raw_by_source.salla_direct.timeline": [{"message": huge}],
    }})
    counted = ProjectedReads(raw)
    def forbid_full(*args, **kwargs):
        raise AssertionError("active board must not fetch full order DTOs")
    monkeypatch.setattr(operations, "_workflow_orders", forbid_full)
    monkeypatch.setattr(MongoOrderRepository, "get_salla_orders", forbid_full)
    original = reader.map_salla_order
    def check_mapper_payload(payload, **kwargs):
        assert not any(key in payload for key in ("items", "customer", "timeline"))
        return original(payload, **kwargs)
    monkeypatch.setattr(reader, "map_salla_order", check_mapper_payload)
    app = FastAPI()
    async def actor():
        return {"id": OWNER, "role": "owner"}
    app.include_router(operations.make_preparation_piece_operations_router(counted, actor))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        counts = await client.get("/preparation-work-v1/assembly/order-counts")
    assert counts.status_code == 200, counts.text
    assert set(counts.json()) == {"queue_counts", "group_counts", "poll_seconds", "source"}
    assert counts.json()["queue_counts"] == {"in_progress": 1, "direct_preparation": 1, "supplier_received": 0}
    source_rows = [row for name, row in counted.returned if name == "unified_orders"]
    assert [row["order_number"] for row in source_rows] == ["current"]
    for name, _, projection in counted.finds:
        assert projection and projection.get("_id") == 0
        assert "raw_by_source.salla_direct" not in projection
        assert not any("customer" in field or "image" in field or "timeline" in field for field in projection)
    returned = source_rows[0]["raw_by_source"]["salla_direct"]
    assert set(returned["items"][0]) == {"id", "quantity"}


@pytest.mark.asyncio
@pytest.mark.parametrize("payment,root", [
    ({"payment_method": "cod", "payment": {"status": "unpaid"}}, {}),
    ({"payment_method": {"code": "mada", "name": "مدى"}, "paid_amount": 120,
      "remaining_amount": 0, "payment_collection_status": "paid", "total": {"amount": 120, "currency": "SAR"}}, {}),
    ({"payment_method": "bank", "paid_amount": 30, "remaining_amount": 90,
      "has_remaining_amount": True}, {}),
    ({"payment_method": "bank", "payment_actions": {"remaining_action": {
        "paid_amount": {"amount": 120}, "remaining_amount": 80, "has_remaining_amount": False}},
      "paid_amount": 20, "remaining_amount": 100, "total": {"amount": 120, "currency": "SAR"}}, {}),
    ({"payment_method": None, "paid_amount": None, "remaining_amount": None},
     {"payment_method": "bank", "paid_amount": 120, "remaining_amount": 0, "payment_collection_status": "paid"}),
    ({"payment_method": "mada", "paid_amount": 120, "remaining_amount": 0,
      "total": {"amount": 120, "currency": "SAR"}, "updated_at": "2026-11-02T08:00:00Z"},
     {"g47_salla_snapshot": {"source_updated_at": "2026-11-03T08:00:00Z"}}),
    ({"payment_method": "mada", "paid_amount": 120, "remaining_amount": 0,
      "total": {"amount": 120, "currency": "SAR"}}, {"g47_salla_snapshot": {"requires_authoritative_refresh": True}}),
])
async def test_projected_payment_and_status_match_canonical_mapper(payment, root):
    db = AsyncMongoMockClient().assembly_payment_parity
    await seed_order(db, "payment", local=True, payment=payment)
    await db.unified_orders.update_one({"order_number": "payment"}, {"$set": root or {"parity_fixture": True}})
    full_source = await db.unified_orders.find_one({"order_number": "payment"})
    full_row = MongoOrderRepository._to_discovery_row(full_source)
    full = _map_row(full_row.salla_raw, current_status=full_row.current_status,
                    native_payment_source=full_row.native_payment_source)
    thin_source = await db.unified_orders.find_one({"order_number": "payment"}, reader.SOURCE_PROJECTION)
    thin = reader.projected_order(thin_source)
    eligibility_fields = {"method", "method_native", "status", "paid_amount", "remaining_amount",
                          "has_remaining_amount", "collection_status", "source_balance"}
    assert thin.payment.model_dump(include=eligibility_fields) == full.payment.model_dump(include=eligibility_fields)
    assert payment_is_eligible(thin.payment) == payment_is_eligible(full.payment)
    assert (thin.status, thin.status_native, thin.created_at) == (full.status, full.status_native, full.created_at)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["in_progress", "IN_PROGRESS", "in progress", "قيد التنفيذ"])
async def test_coherent_current_status_aliases_are_not_lost_by_source_prefilter(status):
    db = AsyncMongoMockClient().assembly_aliases
    await seed_order(db, "alias", status=status)
    assert (await board(db, "direct_preparation"))["total"] == 1


@pytest.mark.asyncio
async def test_anonymous_current_identity_uses_bounded_canonical_fallback():
    db = AsyncMongoMockClient().assembly_anonymous
    await seed_order(db, "anonymous")
    item = {"quantity": 2, "product": {"id": "bundle-parent", "name": "Parent product", "sku": "PARENT"},
            "variant": {"id": "variant", "name": "Variant", "sku": "V", "options": [{"name": "size", "value": "M"}]},
            "custom_fields": [{"name": "initials", "value": "AB"}], "images": ["https://example.test/not-needed.jpg"]}
    identity = _map_item(order_number="anonymous", item=item, index=0).order_item_id
    await db.unified_orders.update_one({"order_number": "anonymous"}, {"$set": {"raw_by_source.salla_direct.items": [item]}})
    await db[WORKFLOWS].update_one({"order_number": "anonymous"}, {"$set": {
        "items": [{"order_item_id": identity, "quantity": 2, "preparation_route": "direct_assembly"}],
        "operational_items": [{"operational_item_id": "extra", "blocks_order_completion": True}],
    }})
    counted = ProjectedReads(db)
    result = await board(counted, "direct_preparation")
    assert result["total"] == 1 and result["items"][0]["total_count"] == 3
    source_reads = [(query, projection) for name, query, projection in counted.finds if name == "unified_orders"]
    assert len(source_reads) == 2
    assert source_reads[1][0]["order_number"] == {"$in": ["anonymous"]}
    assert source_reads[1][1] == reader.IDENTITY_PROJECTION
    assert all("image" not in field for field in source_reads[1][1])
    await db.unified_orders.update_one({"order_number": "anonymous"}, {"$set": {"raw_by_source.salla_direct.items.0.variant.name": "Changed"}})
    assert (await board(db, "direct_preparation"))["total"] == 0


@pytest.mark.asyncio
async def test_counts_endpoint_preserves_denied_employee_authorization():
    db = AsyncMongoMockClient().assembly_counts_denied
    await seed_order(db, "private")
    counted = ProjectedReads(db)
    app = FastAPI()
    async def actor():
        return {"id": "worker", "role": "employee", "created_by": OWNER}
    app.include_router(operations.make_preparation_piece_operations_router(counted, actor))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        response = await client.get("/preparation-work-v1/assembly/order-counts")
    assert response.status_code == 403
    assert not any(name == "unified_orders" for name, _, _ in counted.finds)


@pytest.mark.asyncio
async def test_duplicate_supplier_unit_in_another_batch_is_not_complete_receipt():
    db = AsyncMongoMockClient().assembly_duplicate_unit
    await seed_order(db, "two-units", routes=("supplier_file",), quantities=(2,))
    await db[operations.PIECES].update_one({"unit_index": 2}, {"$set": {"unit_index": 1, "batch_id": "another-batch"}})
    assert (await board(db))["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [None, 0, -1, 1.5, "nan", "Infinity"])
async def test_unknown_or_non_whole_current_quantity_does_not_prove_completion(quantity):
    db = AsyncMongoMockClient().assembly_invalid_quantity
    await seed_order(db, "invalid")
    await seed_order(db, "valid")
    await db.unified_orders.update_one({"order_number": "invalid"}, {"$set": {"raw_by_source.salla_direct.items.0.quantity": quantity}})
    assert [row["order_number"] for row in (await board(db, "direct_preparation"))["items"]] == ["valid"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/assembly/orders?state=supplier_received", "/assembly/order-counts"])
@pytest.mark.parametrize("stall_during", ["read_active_assembly_board", "ensure_piece_operation_indexes"])
async def test_read_deadline_cancels_work_and_returns_retryable_error_not_empty(monkeypatch, path, stall_during):
    db = AsyncMongoMockClient().assembly_deadline
    cancelled = asyncio.Event()
    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(operations, stall_during, stalled)
    monkeypatch.setattr(operations, "ASSEMBLY_BOARD_READ_TIMEOUT_SECONDS", 0.01)
    app = FastAPI()
    async def actor():
        return {"id": OWNER, "role": "owner"}
    app.include_router(operations.make_preparation_piece_operations_router(db, actor))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        response = await client.get("/preparation-work-v1" + path)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "assembly_board_read_timeout"
    assert "queue_counts" not in response.json() and "items" not in response.json()
    assert cancelled.is_set()
