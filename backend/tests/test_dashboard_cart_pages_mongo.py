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
        assert len(probe.docs) == 1 + 10
        assert len(probe.docs[0]["page"]) == 11
        assert sum("items" in d for d in probe.docs) == 10
        assert any("$sort" in step for c in probe.commands for stage in c["pipeline"] if "$facet" in stage for step in stage["$facet"]["page"])
    finally:
        client.close()


@pytest.mark.asyncio
async def test_historical_timestamp_shapes_match_ingestion_parser(carts):
    from datetime import datetime, timezone
    from dashboard_abandoned_page import _converted
    from salla_integration.abandoned_carts import parse_salla_datetime
    timestamp = datetime(2026, 8, 14, 21, tzinfo=timezone.utc).timestamp()
    shapes = [timestamp, int(timestamp * 1000), str(timestamp), str(int(timestamp * 1000)),
              " 2026-08-14T21:00:00Z ", "2026-08-15T00:00:00+03:00",
              {"date": "2026-08-15 00:00:00", "timezone": "Asia/Riyadh"},
              {"datetime": "2026-08-14T21:00:00Z", "timezone": "Asia/Riyadh"},
              {"value": str(timestamp)}, {"timestamp": timestamp},
              {"date": "", "value": "2026-08-14T21:00:00"},
              {"date": "2026-08-14T21:00:00", "timezone": "Invalid/Zone"},
              "Fri Aug 14 2026 21:00:00 GMT+0000 (Coordinated Universal Time)",
              "Sat Aug 15 2026 00:00:00 GMT+0300 (Arabian Standard Time)",
              None, "not a date", False, {}, {"date": "broken", "value": str(timestamp)},
              "NaN", "Infinity", 1e30, "August 15 2026"]
    await carts.insert_many([row(i, cart_created_at=value) for i, value in enumerate(shapes)])
    converted = await carts.aggregate([{"$sort": {"_id": 1}}, {"$project": {"date": _converted("cart_created_at")}}]).to_list(100)
    for original, result in zip(shapes, converted):
        expected = parse_salla_datetime(original)
        actual = result["date"]
        if actual is not None:
            actual = actual.replace(tzinfo=timezone.utc)
        assert actual == expected, repr(original)
    rows, count, recovered, pagination = await page(carts, limit=100)
    expected_count = sum(parse_salla_datetime(value) == datetime(2026, 8, 14, 21, tzinfo=timezone.utc) for value in shapes)
    assert len(rows) == count == pagination["total_active"] == expected_count
    assert recovered == 0


@pytest.mark.asyncio
async def test_legacy_dates_keep_riyadh_boundaries_activity_order_and_recovery(carts):
    from datetime import datetime, timezone
    from dashboard_v2_routes import select_abandoned_carts_for_period
    boundary = datetime(2026, 8, 14, 21, tzinfo=timezone.utc).timestamp()
    fixtures = [
        row(1, cart_created_at=boundary, cart_updated_at=boundary + 1),
        row(2, cart_created_at=str(boundary * 1000), cart_updated_at={"timestamp": boundary + 2}),
        row(3, cart_created_at={"date": "2026-08-15 00:00:00", "timezone": "Asia/Riyadh"},
            cart_updated_at="Sat Aug 15 2026 00:00:03 GMT+0300 (AST)"),
        row(4, cart_created_at=boundary - 1),
        row(5, cart_created_at=boundary + 86400),
        row(6, purchased=True, cart_updated_at=str(boundary + 5)),
        row(7, purchased=True, cart_updated_at={"date": "2026-08-15T00:00:06", "timezone": "Asia/Riyadh"}),
    ]
    await carts.insert_many(fixtures)
    expected, abandoned, recovered = select_abandoned_carts_for_period(fixtures, start="2026-08-15", end="2026-08-15")
    first, a, r, pagination = await page(carts, limit=2)
    second, a2, r2, _ = await page(carts, limit=2, cursor=pagination["next_cursor"])
    assert [x["cart_id"] for x in first + second] == [x["cart_id"] for x in expected]
    assert (a, r) == (a2, r2) == (abandoned, recovered)


async def assert_canonical_pages(carts, fixtures, *, start, end, limit=2):
    from dashboard_v2_routes import select_abandoned_carts_for_period
    expected, abandoned, recovered = select_abandoned_carts_for_period(fixtures, start=start, end=end)
    await carts.insert_many(fixtures)
    collected, cursor = [], None
    for _ in range(len(fixtures) + 1):
        rows, a, r, pagination = await read_cart_page(
            carts, "owner", start=start, end=end, limit=limit, cursor=cursor)
        assert (a, r) == (abandoned, recovered)
        assert pagination["total_active"] == len(expected)
        collected.extend(rows)
        if not pagination["has_more"]:
            break
        assert pagination["next_cursor"] != cursor
        cursor = pagination["next_cursor"]
    else:
        pytest.fail("cursor did not terminate")
    assert [item["cart_id"] for item in collected] == [item["cart_id"] for item in expected]
    assert len({item["cart_id"] for item in collected}) == len(collected)


@pytest.mark.asyncio
async def test_microsecond_ordering_and_mixed_millisecond_cursor_are_exact(carts):
    fixtures = [
        row(90, cart_updated_at="2026-08-15T10:00:00.000001Z"),
        row(1, cart_updated_at="2026-08-15T10:00:00.000999Z"),
        row(2, cart_updated_at="2026-08-15T10:00:00.000100Z"),
        row(99, cart_updated_at="2026-08-15T10:00:00.000Z"),
        row(3, cart_updated_at="2026-08-15T10:00:00.001Z"),
    ]
    await assert_canonical_pages(carts, fixtures, start="2026-08-15", end="2026-08-15", limit=1)


@pytest.mark.asyncio
async def test_iso_week_basic_dates_and_second_offsets_match_canonical(carts):
    fixtures = [
        row(1, cart_created_at="2026-W33-6T00:00:00+03:00", cart_updated_at="2026-W33-6T10:00:00Z"),
        row(2, cart_created_at="20260815T000000+0300", cart_updated_at="20260815T110000Z"),
        row(3, cart_created_at="2026-08-15T00:00:30+03:00:30", cart_updated_at="2026-08-15T12:00:30+00:00:30"),
        row(4, cart_created_at="2026-08-15T00:00:30.000001+03:00:30", cart_updated_at="2026-08-15T13:00:00Z"),
        row(5, cart_created_at="2026-08-15T00:00:29.999999+03:00:30"),
        row(6, cart_created_at="2026-08-16T00:00:30+03:00:30"),
    ]
    await assert_canonical_pages(carts, fixtures, start="2026-08-15", end="2026-08-15")


@pytest.mark.asyncio
@pytest.mark.parametrize("day,local_time,earlier,later", [
    ("2026-11-01", "2026-11-01T01:30:00", "2026-11-01T05:00:00Z", "2026-11-01T06:00:00Z"),
    ("2026-03-08", "2026-03-08T02:30:00", "2026-03-08T07:00:00Z", "2026-03-08T08:00:00Z"),
])
async def test_named_timezone_fold_and_gap_follow_ingestion_parser(carts, day, local_time, earlier, later):
    fixtures = [
        row(1, cart_created_at=day + "T00:00:00Z", cart_updated_at={"date": local_time, "timezone": "America/New_York"}),
        row(2, cart_created_at=day + "T00:00:00Z", cart_updated_at=earlier),
        row(3, cart_created_at=day + "T00:00:00Z", cart_updated_at=later),
    ]
    await assert_canonical_pages(carts, fixtures, start=day, end=day, limit=1)


@pytest.mark.asyncio
async def test_old_or_missing_provider_creation_never_uses_updated_or_local_creation(carts):
    fixtures = [
        row(1, cart_created_at="2026-W32-6", cart_updated_at="2026-08-15T20:00:00.000001Z"),
        row(2, cart_created_at=None, created_at="2026-08-15T10:00:00Z", first_seen_at="2026-08-15T10:00:00Z"),
        row(3, cart_created_at="broken", cart_updated_at={"date": "2026-08-15T12:00:00", "timezone": "Asia/Riyadh"}),
        row(4, cart_created_at={"date": [], "value": "2026-08-15T00:00:00+03:00"}, cart_updated_at="2026-08-15T11:00:00Z"),
        row(5, cart_created_at={"date": {}, "timestamp": "2026-08-15T00:00:00+03:00"}, cart_updated_at="2026-08-15T12:00:00Z"),
        row(6, purchased=True, cart_created_at="2026-W32-6", cart_updated_at="2026-W33-6T14:00:00Z"),
    ]
    await assert_canonical_pages(carts, fixtures, start="2026-08-15", end="2026-08-15")


@pytest.mark.asyncio
async def test_legacy_metadata_batches_exclude_item_payloads(carts):
    from pymongo.monitoring import CommandListener
    class Probe(CommandListener):
        def __init__(self):
            self.max_batch = self.metadata_rows = self.detail_rows = 0
        def started(self, event):
            pass
        def failed(self, event):
            pass
        def succeeded(self, event):
            cursor = event.reply.get("cursor", {})
            batch = cursor.get("firstBatch", cursor.get("nextBatch", []))
            self.max_batch = max(self.max_batch, len(batch))
            self.metadata_rows += sum("_key" in item and "cart_created_at" in item for item in batch)
            self.detail_rows += sum("items" in item for item in batch)
    await carts.insert_many([row(i, cart_created_at="2026-W33-6", items=[{"name": "x" * 8192}]) for i in range(301)])
    probe = Probe()
    client = AsyncIOMotorClient(os.environ["DASHBOARD_TEST_MONGO_URI"], event_listeners=[probe])
    try:
        result = await page(client[carts.database.name].carts, limit=7)
        assert result[3]["total_active"] == 301
        assert probe.max_batch <= 128
        assert probe.metadata_rows == 301
        assert probe.detail_rows == 7
    finally:
        client.close()
