"""Real Mongo tests: run with DASHBOARD_TEST_MONGO_URI pointing to isolated localhost."""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from dashboard_abandoned_page import read_cart_page


@pytest_asyncio.fixture
async def carts():
    uri = os.environ["DASHBOARD_TEST_MONGO_URI"]
    assert uri.startswith("mongodb://127.0.0.1:")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    name = "dashboard_memory_test_" + uuid.uuid4().hex
    db = client[name]
    await client.admin.command("ping")
    try:
        yield db.carts
    finally:
        await client.drop_database(name)
        client.close()


def row(index, **overrides):
    return {"_id": str(index).zfill(8), "user_id": "owner", "cart_id": str(index),
            "purchased": False, "cart_created_at": "2026-08-15T08:00:00Z",
            "cart_updated_at": "2026-08-15T10:00:00Z", "total": index,
            "items": [{"product_id": "p", "quantity": 1}], **overrides}


async def page(carts, **kwargs):
    return await read_cart_page(carts, "owner", start="2026-08-15", end="2026-08-15", **kwargs)


@pytest.mark.asyncio
async def test_creation_boundary_counts_and_isolation(carts):
    await carts.insert_many([
        row(1), row(2, cart_created_at="2026-08-14T21:00:00Z"),
        row(3, cart_created_at="2026-08-14T20:59:59Z"),
        row(4, cart_created_at=None, first_seen_at="2026-08-15T10:00:00Z"),
        row(5, purchased=True), row(6, user_id="other"),
        row(7, cart_created_at="2026-08-10T10:00:00Z", purchased=True),
    ])
    rows, abandoned, recovered, pagination = await page(carts)
    assert {r["cart_id"] for r in rows} == {"1", "2"}
    assert (abandoned, recovered) == (3, 2)
    assert pagination["total_active"] == 2
    assert pagination["next_cursor"] is None


@pytest.mark.asyncio
async def test_stable_pages_no_duplicates_and_full_coverage(carts):
    await carts.insert_many([row(i) for i in range(251)])
    ids, cursor = [], None
    while True:
        rows, count, _, p = await page(carts, limit=17, cursor=cursor)
        assert count == 251
        assert len(rows) <= 17
        ids.extend(r["cart_id"] for r in rows)
        cursor = p["next_cursor"]
        if not p["has_more"]:
            break
    assert ids == list(map(str, reversed(range(251))))
    assert len(set(ids)) == 251


@pytest.mark.asyncio
async def test_empty_invalid_cursor_and_concurrent_readers(carts):
    assert (await page(carts))[0] == []
    with pytest.raises(ValueError, match="invalid_cart_cursor"):
        await page(carts, cursor="invalid")
    await carts.insert_many([row(i) for i in range(150)])
    results = await asyncio.gather(*(page(carts, limit=30) for _ in range(4)))
    assert all(r == results[0] for r in results)
    assert await carts.count_documents({}) == 150


@pytest.mark.asyncio
async def test_large_items_only_one_page_and_tenant_safe(carts):
    for offset in range(0, 3000, 100):
        await carts.insert_many([row(i, items=[{"product_id": "p", "name": "x" * 8192}])
                                 for i in range(offset, offset + 100)])
    rows, count, _, p = await page(carts, limit=10)
    assert len(rows) == 10
    assert count == p["total_active"] == 3000
    assert all(len(r["items"][0]["name"]) == 8192 for r in rows)
    assert p["has_more"] is True


@pytest.mark.asyncio
async def test_mongo_sorting_matches_creation_scoped_business_rows(carts):
    from dashboard_v2_routes import select_abandoned_carts_for_period
    fixtures = [row(i, cart_updated_at=f"2026-08-15T{8+i:02}:00:00Z") for i in range(6)]
    fixtures += [row(9, cart_created_at="2026-08-01T09:00:00Z", cart_updated_at="2026-08-15T20:00:00Z"),
                 row(10, purchased=True), row(11, cart_created_at=None)]
    await carts.insert_many(fixtures)
    expected, abandoned, recovered = select_abandoned_carts_for_period(fixtures, start="2026-08-15", end="2026-08-15")
    actual, a, r, p = await page(carts, limit=100)
    assert [x["cart_id"] for x in actual] == [x["cart_id"] for x in expected]
    assert (a, r) == (abandoned, recovered)
    assert sum(x["total"] for x in actual) == sum(x["total"] for x in expected)
    with pytest.raises(ValueError):
        await read_cart_page(carts, "owner", start="bad", end="bad")


@pytest.mark.asyncio
async def test_only_page_metadata_and_details_cross_mongo_boundary(carts):
    from pymongo.monitoring import CommandListener
    class Probe(CommandListener):
        def __init__(self): self.docs = []; self.commands = []
        def started(self, event):
            if event.command_name == "aggregate": self.commands.append(event.command)
        def failed(self, event): pass
        def succeeded(self, event):
            cursor = event.reply.get("cursor", {})
            self.docs.extend(cursor.get("firstBatch", cursor.get("nextBatch", [])))
    await carts.insert_many([row(i) for i in range(1000)])
    probe = Probe()
    client = AsyncIOMotorClient(os.environ["DASHBOARD_TEST_MONGO_URI"], event_listeners=[probe])
    try:
        result = await page(client[carts.database.name].carts, limit=10)
        assert result[3]["total_active"] == 1000
        assert len(probe.docs) == 1 + 11 + 10
        assert sum("items" in d for d in probe.docs) == 10
        assert any("$sort" in step for c in probe.commands for step in c["pipeline"])
    finally:
        client.close()
