"""Complete, indexed monitor metadata; failures must remain visibly unknown."""
import ast
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockCollection
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ExecutionTimeout, NetworkTimeout, OperationFailure
import pytest
import pytest_asyncio

from salla_integration import webhook_monitor_routes as monitor
from salla_integration import webhook_inbox as inbox


AT = datetime(2026, 1, 8, tzinfo=timezone.utc)
PRIVATE = "synthetic-private-do-not-return@example.invalid"


def app_for(db):
    async def current_user():
        return {"id": "owner-a", "role": "owner"}

    server = types.ModuleType("server")
    server.current_user = current_user
    router = APIRouter()
    with patch.dict(sys.modules, {"server": server}):
        monitor.attach_salla_webhook_monitor_routes(router, db)
    app = FastAPI()
    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


async def get_monitor(db):
    async with AsyncClient(transport=ASGITransport(app=app_for(db)),
                           base_url="http://isolated.test") as client:
        return await client.get("/salla/webhook-monitor?merchant_id=other-store&user_id=other-owner")


async def bound_db():
    db = AsyncMongoMockClient(tz_aware=True).db
    await db.salla_integrations.insert_one({"user_id": "owner-a", "store_id": "store-a",
                                            "status": "connected"})
    return db


def capture(identity, event="order.created", **overrides):
    return {"_id": identity, "merchant_id": "store-a", "event": event,
        "first_received_at": AT - timedelta(days=3), "last_received_at": AT,
        "delivery_count": 1, "payload": {"data": {"reference_id": "synthetic-" + identity,
            "customer": {"email": PRIVATE}, "private": PRIVATE}},
        "order_sync": {"attempted": True, "synced": True, "error": PRIVATE,
                       "nested": {"customer": PRIVATE}}, **overrides}


@pytest.mark.asyncio
@pytest.mark.parametrize("merchant", [None, "", " ", {}, True])
async def test_unbound_event_scope_never_reads_a_collection(merchant):
    class NoRead:
        def __getitem__(self, name):
            pytest.fail("unbound scope reached capture storage")

    with pytest.raises(monitor.HTTPException) as caught:
        await monitor._event_snapshot(NoRead(), merchant)
    assert caught.value.status_code == 409
    assert caught.value.detail["status"] == "unknown"


@pytest.mark.asyncio
async def test_full_history_matches_previous_selection_without_payload_grouping(monkeypatch):
    db = await bound_db()
    documents = [
        capture("new", delivery_count=7, first_received_at=AT-timedelta(days=20)),
        capture("old", delivery_count=3, last_received_at=AT-timedelta(days=1)),
        capture("missing-count", event="order.updated", last_received_at=AT-timedelta(days=2)),
        capture("null-count", event="order.updated", delivery_count=None),
        capture("no-last", event="order.cancelled", last_received_at=None),
        capture("other-store", merchant_id="other-store", delivery_count=10000),
        capture("unapproved", event="product.updated", delivery_count=10000),
    ]
    documents[2].pop("delivery_count")
    documents[4].pop("last_received_at")
    await db[monitor.CAPTURES].insert_many(documents)
    # Original route's independent query, including its exact first/last rules.
    original = await db[monitor.CAPTURES].aggregate([
        {"$match": {"merchant_id": "store-a"}},
        {"$sort": {"last_received_at": -1}},
        {"$group": {"_id": "$event", "last_received_at": {"$first": "$last_received_at"},
            "first_received_at": {"$last": "$first_received_at"},
            "delivery_count": {"$sum": {"$ifNull": ["$delivery_count", 1]}},
            "last_payload": {"$first": "$payload"}}},
    ]).to_list(length=200)
    originals = {row["_id"]: row for row in original}
    aggregates, finds = [], []
    aggregate = AsyncMongoMockCollection.aggregate
    find = AsyncMongoMockCollection.find

    def seen_aggregate(collection, pipeline, *args, **kwargs):
        if collection.name == monitor.CAPTURES:
            aggregates.append((pipeline, kwargs))
        return aggregate(collection, pipeline, *args, **kwargs)

    def seen_find(collection, query, projection=None, *args, **kwargs):
        if collection.name == monitor.CAPTURES:
            finds.append((query, projection))
        return find(collection, query, projection, *args, **kwargs)

    monkeypatch.setattr(AsyncMongoMockCollection, "aggregate", seen_aggregate)
    monkeypatch.setattr(AsyncMongoMockCollection, "find", seen_find)
    result = await monitor._event_snapshot(db, "store-a")
    assert set(result) == {"order.created", "order.updated", "order.cancelled"}
    for event, row in result.items():
        for key in ("delivery_count", "first_received_at", "last_received_at"):
            assert row.get(key) == originals[event].get(key)
        assert monitor._extract_order_number(row["last_payload"]) == monitor._extract_order_number(
            originals[event]["last_payload"])
    assert result["order.created"]["delivery_count"] == 10
    assert result["order.updated"]["delivery_count"] == 2
    assert result["order.cancelled"]["last_received_at"] is None
    assert len(aggregates) == 1 and len(finds) == 3
    pipeline, options = aggregates[0]
    assert "$limit" not in repr(pipeline) and "payload" not in repr(pipeline)
    assert options == {"hint": monitor.MONITOR_INDEX, "maxTimeMS": monitor.QUERY_MS,
                       "allowDiskUse": False}
    assert all(query["merchant_id"] == "store-a" for query, _ in finds)
    assert all("payload" not in projection and "order_sync" not in projection
               for _, projection in finds)
    assert PRIVATE not in repr(result)
    assert result["order.created"]["last_order_sync"] == {"attempted": True, "synced": True}


@pytest.mark.asyncio
async def test_http_metadata_has_no_customer_data_and_does_not_write(monkeypatch):
    db = await bound_db()
    row = capture("safe")
    row["payload"]["order_number"] = {"email": PRIVATE}
    row["shipment_sync"] = {"synced": PRIVATE, "attempted": False, "result": PRIVATE}
    await db[monitor.CAPTURES].insert_one(row)

    async def no_write(*args, **kwargs):
        pytest.fail("read-only monitor attempted schema or data mutation")

    for method in ("create_index", "insert_one", "insert_many", "update_one", "update_many",
                   "delete_one", "delete_many", "replace_one"):
        monkeypatch.setattr(AsyncMongoMockCollection, method, no_write)
    response = await get_monitor(db)
    assert response.status_code == 200
    data = response.json()
    assert data["merchant_id"] == "store-a" and data["received_events"] == 1
    event = next(row for row in data["events"] if row["event"] == "order.created")
    assert event["delivery_count"] == 1 and event["last_order_number"] == "synthetic-safe"
    assert event["order_sync"] == {"attempted": True, "synced": True}
    assert event["shipment_sync"] == {"attempted": False}
    assert PRIVATE not in response.text and "payload" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [{"user_id": "owner-a"}],
    [{"user_id": "owner-a", "store_id": " "}],
    [{"user_id": "owner-a", "store_id": {"id": "store-a"}}],
    [{"user_id": "owner-a", "store_id": True}],
    [{"user_id": "owner-a", "store_id": "store-a"},
     {"user_id": "owner-a", "store_id": "store-b"}],
    [{"user_id": "owner-a", "store_id": "store-a"},
     {"user_id": "owner-b", "store_id": "store-a"}],
    [{"user_id": "owner-a", "store_id": "321"},
     {"user_id": "owner-b", "store_id": 321}],
])
async def test_missing_or_conflicting_binding_is_unknown_without_capture_read(rows, monkeypatch):
    db = AsyncMongoMockClient(tz_aware=True).db
    if rows:
        await db.salla_integrations.insert_many(rows)
    snapshot = AsyncMock(side_effect=AssertionError("unbound capture read"))
    monkeypatch.setattr(monitor, "_event_snapshot", snapshot)
    response = await get_monitor(db)
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "unknown"
    snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnected_but_unique_binding_keeps_authorized_history_visible():
    db = await bound_db()
    await db.salla_integrations.update_one({}, {"$set": {"status": "disconnected"}})
    await db[monitor.CAPTURES].insert_one(capture("historic"))
    response = await get_monitor(db)
    assert response.status_code == 200
    assert response.json()["integration_status"] == "disconnected"
    assert response.json()["received_events"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [NetworkTimeout(PRIVATE), ExecutionTimeout(PRIVATE, code=50),
    OperationFailure(PRIVATE, code=2)])
@pytest.mark.parametrize("collection_name", [monitor.CAPTURES, inbox.COLLECTION])
async def test_storage_failure_returns_unknown_not_zero_or_raw_error(
        failure, collection_name, monkeypatch, caplog):
    db = await bound_db()
    original = AsyncMongoMockCollection.aggregate

    def fail(collection, *args, **kwargs):
        if collection.name == collection_name:
            raise failure
        return original(collection, *args, **kwargs)

    monkeypatch.setattr(AsyncMongoMockCollection, "aggregate", fail)
    with caplog.at_level(logging.WARNING, logger="salla.webhook_monitor"):
        response = await get_monitor(db)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["detail"]["status"] == "unknown"
    assert "events" not in response.json() and "queue" not in response.json()
    assert PRIVATE not in response.text and PRIVATE not in caplog.text
    assert type(failure).__name__ in caplog.text


@pytest.mark.asyncio
async def test_total_deadline_cancels_unfinished_read_and_keeps_health_responsive(monkeypatch):
    db = await bound_db()
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr(monitor, "_event_snapshot", slow)
    monkeypatch.setattr(monitor, "REQUEST_SECONDS", .05)
    async with AsyncClient(transport=ASGITransport(app=app_for(db)),
                           base_url="http://isolated.test") as client:
        task = asyncio.create_task(client.get("/salla/webhook-monitor"))
        await entered.wait()
        health = await client.get("/health")
        response = await asyncio.wait_for(task, 1)
    assert health.status_code == 200 and response.status_code == 503
    assert stopped.is_set() and response.json()["detail"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_binding_change_during_read_does_not_return_old_merchant_data(monkeypatch):
    db = await bound_db()

    async def changed(*args, **kwargs):
        await db.salla_integrations.update_one({}, {"$set": {"store_id": "different-store"}})
        return {"backlog": 0}

    monkeypatch.setattr(monitor, "_queue_snapshot", changed)
    response = await get_monitor(db)
    assert response.status_code == 409 and "events" not in response.json()


@pytest.mark.asyncio
async def test_index_initialization_failure_is_bounded_owned_and_does_not_stop_health(monkeypatch, caplog):
    db = await bound_db()
    entered, stopped = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow_index(collection, keys, **options):
        calls.append((keys, options))
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr(AsyncMongoMockCollection, "create_index", slow_index)
    monkeypatch.setattr(monitor, "INDEX_BUILD_SECONDS", .05)
    app = app_for(db)
    app.state.readiness = "ready"
    with caplog.at_level(logging.INFO, logger="salla.webhook_monitor"):
        app.state.salla_monitor_index_task = monitor.start_monitor_index_initialization(db)
        await entered.wait()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated.test") as client:
            assert (await client.get("/health")).status_code == 200
        assert await asyncio.wait_for(app.state.salla_monitor_index_task, 1) is False
    assert stopped.is_set() and app.state.readiness == "ready"
    assert len(calls) == 1 and calls[0][1]["maxTimeMS"] == monitor.INDEX_BUILD_MS
    assert "index_unavailable" in caplog.text and "index_ready" not in caplog.text
    assert not any("payload" in field for field, _ in calls[0][0])


@pytest.mark.asyncio
async def test_index_task_cancellation_is_joined_without_retry(monkeypatch):
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def slow(_db):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            stopped.set()

    monkeypatch.setattr(monitor, "ensure_monitor_indexes", slow)
    task = monitor.start_monitor_index_initialization(object())
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and stopped.is_set()


def test_initializer_is_owned_by_web_lifecycle_not_worker_first_completed_jobs():
    backend = Path(__file__).parents[1]
    functions = {node.name: node for node in ast.parse((backend / "server.py").read_text()).body
                 if isinstance(node, ast.AsyncFunctionDef)}
    startup = ast.unparse(functions["_local_startup"])
    shutdown = ast.unparse(functions["on_shutdown"])
    assert "app.state.salla_monitor_index_task = start_monitor_index_initialization(db)" in startup
    assert "await start_monitor_index_initialization" not in startup
    assert shutdown.index("await _asyncio.gather(monitor_index_task") < shutdown.index("client.close()")
    assert "start_monitor_index_initialization" not in (backend / "background_worker_runtime.py").read_text()


@pytest_asyncio.fixture
async def monitor_db():
    # Same explicit disposable-loopback contract as the Salla durable inbox CI.
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI required; no production fallback")
    parsed = urlsplit(uri)
    assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port
    assert parsed.username is None and parsed.password is None and parsed.path in ("", "/")
    client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    name = "test_salla_monitor_" + uuid4().hex
    try:
        assert (await client.admin.command("hello")).get("setName")
        db = client[name]
        await db.salla_integrations.create_index("user_id", unique=True)
        await inbox.ensure_indexes(db)
        await db.salla_integrations.insert_one({"user_id": "owner-a", "store_id": "store-a",
                                                "status": "connected"})
        yield db
    finally:
        assert name.startswith("test_salla_monitor_")
        await client.drop_database(name)
        client.close()


def execution_stats(explained):
    if "executionStats" in explained:
        return explained["executionStats"]
    return next(stage["$cursor"]["executionStats"] for stage in explained["stages"] if "$cursor" in stage)


@pytest.mark.asyncio
async def test_real_mongo_complete_counts_are_covered_and_latest_reads_fetch_one(monitor_db):
    db = monitor_db
    await monitor.ensure_monitor_indexes(db)
    count = 6000
    rows = [capture(str(i), event="order.created" if i % 2 else "order.updated",
        merchant_id="store-a" if i < count else "other-store", delivery_count=i % 3 + 1,
        first_received_at=AT+timedelta(seconds=i-1), last_received_at=AT+timedelta(seconds=i),
        payload={"data": {"reference_id": "synthetic-"+str(i), "private": PRIVATE*80}})
        for i in range(count+2000)]
    await db[monitor.CAPTURES].insert_many(rows)
    explained = await db.command("explain", {"aggregate": monitor.CAPTURES,
        "pipeline": monitor._event_pipeline("store-a"), "hint": monitor.MONITOR_INDEX,
        "cursor": {}, "maxTimeMS": monitor.QUERY_MS}, verbosity="executionStats")
    stats = execution_stats(explained)
    assert stats["totalDocsExamined"] == 0
    assert count <= stats["totalKeysExamined"] <= count + len(monitor.APPROVED_EVENTS)
    assert "PROJECTION_COVERED" in repr(explained) and "COLLSCAN" not in repr(explained)
    response = await get_monitor(db)
    assert response.status_code == 200
    observed = {row["event"]: row for row in response.json()["events"] if row["observed"]}
    assert set(observed) == {"order.created", "order.updated"}
    for event, row in observed.items():
        expected = [document for document in rows[:count] if document["event"] == event]
        assert row["delivery_count"] == sum(document["delivery_count"] for document in expected)
        latest = expected[-1]
        assert row["last_order_number"] == latest["payload"]["data"]["reference_id"]
        plan = await db.command("explain", {"find": monitor.CAPTURES,
            "filter": {"merchant_id": "store-a", "event": event,
                       "last_received_at": latest["last_received_at"]},
            "projection": monitor._LATEST_PROJECTION, "hint": monitor.MONITOR_INDEX,
            "limit": 1, "maxTimeMS": monitor.QUERY_MS}, verbosity="executionStats")
        assert execution_stats(plan)["totalDocsExamined"] == 1
    assert PRIVATE not in response.text


@pytest.mark.asyncio
async def test_real_mongo_missing_and_unusable_index_fail_closed_without_get_ddl(monitor_db):
    db = monitor_db
    await db[monitor.CAPTURES].insert_one(capture("index-required"))
    before = await db[monitor.CAPTURES].index_information()
    response = await get_monitor(db)
    assert response.status_code == 503 and response.json()["detail"]["status"] == "unknown"
    assert await db[monitor.CAPTURES].index_information() == before
    task = monitor.start_monitor_index_initialization(db)
    assert await asyncio.wait_for(task, 20) is True
    assert (await get_monitor(db)).status_code == 200
    # A known but unusable index must not cause an unindexed fallback either.
    await db.command("collMod", monitor.CAPTURES,
                     index={"name": monitor.MONITOR_INDEX, "hidden": True})
    assert (await get_monitor(db)).status_code == 503


@pytest.mark.asyncio
async def test_real_mongo_pending_index_keeps_only_monitor_unknown(monitor_db, monkeypatch):
    db = monitor_db
    await db[monitor.CAPTURES].insert_one(capture("pending-index"))
    entered, release = asyncio.Event(), asyncio.Event()
    original = monitor.ensure_monitor_indexes

    async def pending(_db):
        entered.set()
        await release.wait()
        await original(_db)

    monkeypatch.setattr(monitor, "ensure_monitor_indexes", pending)
    task = monitor.start_monitor_index_initialization(db)
    try:
        await entered.wait()
        async with AsyncClient(transport=ASGITransport(app=app_for(db)),
                               base_url="http://isolated.test") as client:
            assert (await client.get("/health")).status_code == 200
            response = await client.get("/salla/webhook-monitor")
            assert response.status_code == 503
            assert response.json()["detail"]["status"] == "unknown"
        release.set()
        assert await asyncio.wait_for(task, 20) is True
        assert (await get_monitor(db)).status_code == 200
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_mongo_original_first_last_rules_include_missing_timestamps(monitor_db):
    db = monitor_db
    await monitor.ensure_monitor_indexes(db)
    rows = [capture("last-new", first_received_at=AT-timedelta(days=20)),
            capture("last-old", last_received_at=AT-timedelta(days=1),
                    first_received_at=AT-timedelta(days=2)),
            capture("last-absent", event="order.updated")]
    rows[-1].pop("last_received_at")
    await db[monitor.CAPTURES].insert_many(rows)
    result = await monitor._event_snapshot(db, "store-a")
    assert result["order.created"]["first_received_at"] == AT-timedelta(days=2)
    assert result["order.created"]["last_received_at"] == AT
    assert result["order.updated"]["last_received_at"] is None
    assert result["order.updated"]["first_received_at"] == AT-timedelta(days=3)
    assert monitor._extract_order_number(result["order.updated"]["last_payload"]) == "synthetic-last-absent"
