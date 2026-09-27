"""Opt-in real-Mongo storage acceptance; never use the application's MONGO_URI."""
import asyncio
import os
from uuid import uuid4

import pytest

from mezan_special_orders.contracts import CreateOrder
from mezan_special_orders.domain import DomainError
from mezan_special_orders.repository import MongoStore
from mezan_special_orders.tests.test_core import Harness, OWNER, request_data

URI = os.environ.get("MEZAN_SPECIAL_TEST_MONGO_URI")
pytestmark = pytest.mark.skipif(not URI, reason="Dedicated isolated Mongo test URI not supplied")


async def with_mongo(scenario):
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=5000)
    name = "test_mezan_special_" + uuid4().hex
    assert name.startswith("test_mezan_special_")
    try:
        await client.admin.command("ping")
        store = MongoStore(client[name])
        await store.ensure_indexes()
        h = Harness()
        h.store = h.service.store = store
        await scenario(h)
    finally:
        await client.drop_database(name)
        client.close()


def test_real_mongo_concurrent_create_single_document_and_event():
    async def scenario(h):
        result = await asyncio.gather(*(h.create() for _ in range(12)))
        assert len({r["order_id"] for r in result}) == 1
        assert await h.store.collection.count_documents({"tenant_id": OWNER.tenant_id}) == 1
        assert len((await h.doc(result[0]))["outbox"]) == 1
    asyncio.run(with_mongo(scenario))


def test_real_mongo_compare_and_swap_has_one_winner():
    async def scenario(h):
        o = await h.create()
        results = await asyncio.gather(h.command(o, "freeze_source", key="mongo-command-one"),
            h.command(o, "freeze_source", key="mongo-command-two"), return_exceptions=True)
        assert sum(isinstance(r, DomainError) for r in results) == 1
        doc = await h.doc(o)
        assert doc["revision"] == 2 and len(doc["command_log"]) == 1
    asyncio.run(with_mongo(scenario))


def test_real_mongo_carrier_tracking_unique_across_orders():
    async def scenario(h):
        data = request_data()
        data["delivery"] = {"method": "carrier", "carrier_key": "test-carrier", "tracking_number": "TEST-TRACKING",
            "label": {"object_id": "new-test-label", "kind": "carrier_label", "sha256": "b" * 64}}
        await h.create(data=data)
        with pytest.raises(DomainError, match="unique_resource_already_used"):
            await h.create(data=data, key="other-mongo-order")
    asyncio.run(with_mongo(scenario))


def test_real_mongo_financial_reference_unique_across_orders():
    async def scenario(h):
        first = await h.bank(await h.receipt(await h.create(partial=True)))
        second = await h.receipt(await h.create(partial=True, key="second-mongo-order"), object_id="second-receipt")
        with pytest.raises(DomainError, match="unique_resource_already_used"):
            await h.bank(second)  # Test double deliberately reuses the first movement ID.
        assert not (await h.doc(second))["payments"]
        assert len((await h.doc(first))["payments"]) == 1
    asyncio.run(with_mongo(scenario))


def ordinary_order(number, date, tenant="test-store"):
    raw = {"id": number, "reference_id": number, "date": {"date": date},
           "status": {"slug": "under_review", "name": "بإنتظار المراجعة"},
           "customer": {"full_name": "عميل اختبار سلة"},
           "amounts": {"total": {"amount": 100, "currency": "SAR"}},
           "items": [{"id": "item-" + number, "quantity": 1,
                      "product": {"id": "p-demo", "name": "منتج اختبار سلة", "sku": "SALLA-TEST"}}]}
    return {"user_id": tenant, "order_number": number, "order_date": date,
            "order_status": "بإنتظار المراجعة", "raw_by_source": {"salla_direct": raw}}


def test_real_mongo_canonical_read_and_batch_are_source_aware_and_opt_in():
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import get_order, get_orders, OrderNotFoundError
    async def scenario(h):
        o = await h.create()
        db = h.store.collection.database
        await db.unified_orders.insert_one(ordinary_order("1001", "2026-09-20T10:00:00+00:00"))
        default = MongoOrderRepository(db)
        with pytest.raises(OrderNotFoundError):
            await get_order(default, user_id="test-store", order_number=o["order_number"])
        aware = MongoOrderRepository(db, include_mezan=True)
        one = await get_order(aware, user_id="test-store", order_number=o["order_number"])
        assert one.source.provider == "mezan" and one.order_purpose == "replacement"
        both = await get_orders(aware, user_id="test-store", order_numbers=[o["order_number"], "1001", "1001"])
        assert len(both) == 2 and both["1001"].source.provider == "salla"
        assert both["1001"].totals.total == 100 and both["1001"].order_purpose == "sale"
        assert await db.unified_orders.count_documents({}) == 1
        assert await h.store.collection.count_documents({}) == 1
        with pytest.raises(OrderNotFoundError):
            await get_order(aware, user_id="another-merchant", order_number=o["order_number"])
    asyncio.run(with_mongo(scenario))


def test_real_mongo_mixed_source_cursor_has_no_skips_duplicates_or_foreign_tenant():
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import list_orders
    async def scenario(h):
        db = h.store.collection.database
        for i in range(5):
            o = await h.create(key=f"mixed-source-create-{i}")
            # Synthetic test fixture timestamps; the source snapshot excludes creation time.
            await h.store.collection.update_one({"order_id": o["order_id"]}, {"$set": {"created_at": f"2026-09-20T10:00:0{i}+00:00"}})
            await db.unified_orders.insert_one(ordinary_order(str(1000+i), f"2026-09-20T10:00:0{i}+00:00"))
        await db.unified_orders.insert_one(ordinary_order("9000", "2026-09-27T10:00:00+00:00", tenant="other-store"))
        repo = MongoOrderRepository(db, include_mezan=True)
        cursor, seen = None, []
        for _ in range(8):
            page = await list_orders(repo, user_id="test-store", limit=3, cursor=cursor)
            seen.extend((i.order_number, i.source.provider) for i in page.items)
            cursor = page.next_cursor
            if not cursor:
                break
        assert len(seen) == len(set(seen)) == 10
        assert sum(p == "mezan" for _, p in seen) == 5
        assert sum(p == "salla" for _, p in seen) == 5
        assert all(n != "9000" for n, _ in seen)
        old = await list_orders(MongoOrderRepository(db), user_id="test-store", limit=20)
        assert len(old.items) == 5 and all(i.source.provider == "salla" for i in old.items)
    asyncio.run(with_mongo(scenario))


def test_real_mongo_numbered_review_queue_and_status_filters_include_local_orders_once():
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import list_orders
    async def scenario(h):
        db = h.store.collection.database
        a = await h.create(key="pending-source-a")
        b = await h.create(key="pending-source-b")
        await db.unified_orders.insert_one(ordinary_order("1001", "2026-09-20T10:00:00+00:00"))
        # The shared-workflow exclusion is applied to both providers before count/page.
        b = await h.command(b, "freeze_source")
        await db.order_review_workflows.insert_one({"user_id": "test-store", "order_id": b["order_id"], "order_number": b["order_number"], "stage": "reviewed"})
        repo = MongoOrderRepository(db, include_mezan=True)
        numbers, total = await repo.numbered_pending_review_order_numbers(user_id="test-store", page=1, limit=20,
            workflow_collection="order_review_workflows", completed_stages={"reviewed", "completed", "delivered"})
        assert total == 2 and set(numbers) == {a["order_number"], "1001"}
        old_numbers, old_total = await MongoOrderRepository(db).numbered_pending_review_order_numbers(user_id="test-store", page=1, limit=20,
            workflow_collection="order_review_workflows", completed_stages={"reviewed", "completed", "delivered"})
        assert old_total == 1 and old_numbers == ["1001"]
        reviewed = await list_orders(repo, user_id="test-store", status_group="reviewed")
        assert [i.order_number for i in reviewed.items] == [b["order_number"]]
        # Advancing the real shared workflow cannot make a local order disappear
        # or return to waiting review while an aggregate observation is delayed.
        await db.order_review_workflows.update_one({"order_number": b["order_number"]}, {"$set": {"stage": "in_progress"}})
        processing = await list_orders(repo, user_id="test-store", status_group="processing")
        assert [i.order_number for i in processing.items] == [b["order_number"]]
        numbers, total = await repo.numbered_pending_review_order_numbers(user_id="test-store", page=1, limit=20,
            workflow_collection="order_review_workflows", completed_stages={"reviewed", "completed", "delivered"})
        assert total == 2 and b["order_number"] not in numbers
    asyncio.run(with_mongo(scenario))
