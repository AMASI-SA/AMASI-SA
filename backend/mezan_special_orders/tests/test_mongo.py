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
