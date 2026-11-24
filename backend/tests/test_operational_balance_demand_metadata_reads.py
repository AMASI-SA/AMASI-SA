"""Large saved reports never cross a status/claim boundary during a rebuild."""
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from bson import BSON
import pytest

import demand_read_jobs as jobs
import demand_read_store as store
from demand_read_mongo_fixture import read_db, read_mongo_uri as demand_read_mongo_uri


@pytest.fixture(scope="session")
def read_mongo_uri(tmp_path_factory, request):
    # The existing operational gates own one explicit local replica. Never
    # inherit MONGO_URL, a dotenv file, credentials, or a non-loopback service.
    if not (os.environ.get("DEMAND_READ_TEST_MONGO_URI") or os.environ.get("DEMAND_READ_TEST_MONGOD")):
        supplied = {os.environ[name] for name in ("OPERATIONAL_TEST_MONGO_URI", "MZ2_TEST_MONGO_URI")
                    if os.environ.get(name)}
        if supplied:
            assert len(supplied) == 1, "Conflicting isolated Mongo test capabilities"
            uri = supplied.pop()
            parsed = urlsplit(uri)
            assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port
            assert parsed.username is None and parsed.password is None and parsed.path in ("", "/")
            yield uri
            return
    yield from demand_read_mongo_uri.__wrapped__(tmp_path_factory, request)


def spec(**changes):
    value = {"family": "operational_ledger", "owner_id": "synthetic-owner", "actor_id": "synthetic-actor",
        "client_kind": "web", "permission_fingerprint": "grants-synthetic-v1",
        "request": {"path": "/api/operational-balances/workspace/ledger/bank/synthetic", "method": "GET", "query": {}},
        "source_revision": {"revision": 41}, "resource": ["bank", "synthetic"], "currency": "SAR",
        "period": ["2030-04-01", "2030-04-30"], "filters": {}, "projection_version": "ledger-v1"}
    return {**value, **changes}


class ObservedCollection:
    """Measure actual Mongo result documents, not the caller's projection dict."""
    def __init__(self, collection, key, samples):
        self.collection, self.key, self.samples = collection, key, samples

    def __getattr__(self, name):
        return getattr(self.collection, name)

    def record(self, method, row):
        if isinstance(row, dict) and row.get("_id") == self.key:
            self.samples.append({"method": method, "bytes": len(BSON.encode(row)),
                                 "has_header": "header" in row})
        return row

    async def find_one(self, *args, **kwargs):
        return self.record("find_one", await self.collection.find_one(*args, **kwargs))

    async def find_one_and_update(self, *args, **kwargs):
        return self.record("find_one_and_update", await self.collection.find_one_and_update(*args, **kwargs))


class ObservedDatabase:
    def __init__(self, db, key):
        self.db, self.key, self.samples = db, key, []

    def __getattr__(self, name):
        return getattr(self.db, name)

    def __getitem__(self, name):
        collection = self.db[name]
        return ObservedCollection(collection, self.key, self.samples) if name == store.JOBS else collection


def metadata_only(samples):
    assert samples, "The real Mongo boundary was not exercised"
    assert all(not row["has_header"] for row in samples)
    assert max(row["bytes"] for row in samples) < 16 * 1024


async def saved_large_report(db, value):
    key = (await store.demand(db, value, "initial_consumer"))["key"]
    header = {"currency": "SAR", "closing_balance": "123.45", "label": "رصيد تجريبي",
              "saved_details": "x" * (4 * 1024 * 1024)}
    assert 4 * 1024 * 1024 < len(BSON.encode(header)) < store.MAX_HEADER_BYTES
    stamp = datetime.now(timezone.utc)
    await db[store.JOBS].update_one({"_id": key}, {"$set": {
        "status": "ready", "active_slot": False, "header": header, "generation": "previous-generation",
        "paged": False, "total": 0, "generated_at": stamp, "ready_until": stamp + timedelta(minutes=1)}})
    # Ready delivery must continue returning the complete saved header.
    ready = await store.read_ready(db, value)
    assert ready["header"] == header and ready["items"] is None
    await db[store.JOBS].update_one({"_id": key}, {"$set": {"ready_until": stamp - timedelta(seconds=1)}})
    return key, header


async def authorized(_db, _spec):
    return True


@pytest.mark.asyncio
async def test_expired_large_header_is_not_transferred_by_polls_claim_or_execution(read_db):
    value = spec()
    key, old_header = await saved_large_report(read_db, value)
    sentinel = {"_id": "financial-source", "amount": "123.45", "business_date": "2030-04-04"}
    await read_db.canonical_sentinel.insert_one(sentinel)
    observed = ObservedDatabase(read_db, key)
    expected_status = {"status": "preparing", "key": key, "retry_after_ms": 2000}
    for _ in range(3):
        observed.samples.clear()
        assert await store.demand(observed, value, "visible_consumer") == expected_status
        metadata_only(observed.samples)
    assert (await read_db[store.JOBS].find_one({"_id": key}, {"header": 1}))["header"] == old_header

    observed.samples.clear()
    claim = await jobs.claim_next(observed)
    assert claim["_id"] == key and claim["status"] == "running" and "header" not in claim
    metadata_only(observed.samples)
    observed.samples.clear()
    assert await store.demand(observed, value, "visible_consumer") == expected_status
    metadata_only(observed.samples)

    fresh = {"header": {"currency": "SAR", "closing_balance": "127.45", "label": "رصيد تجريبي"},
             "rows": [{"id": "synthetic-entry", "amount": "4.00", "business_date": "2030-04-05"}]}
    async def build(_db, received, checkpoint):
        assert received == store.normalize_spec(value)
        await checkpoint()
        return fresh

    observed.samples.clear()
    assert await jobs.execute_claim(observed, key, claim["lease_token"], claim["generation"],
                                    builder=build, authorize=authorized)
    metadata_only(observed.samples)
    ready = await store.read_ready(read_db, value)
    encoded = lambda obj: json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    assert encoded({"header": ready["header"], "rows": ready["items"]}) == encoded(fresh)
    assert ready["snapshot"] == claim["generation"] and ready["total"] == 1
    assert await read_db.canonical_sentinel.find_one({"_id": sentinel["_id"]}) == sentinel
    assert await read_db[store.ROWS].count_documents({"projection_id": key}) == 1


@pytest.mark.asyncio
async def test_large_failed_header_keeps_error_and_explicit_retry_semantics(read_db):
    value = spec()
    key, header = await saved_large_report(read_db, value)
    await read_db[store.JOBS].update_one({"_id": key}, {"$set": {
        "status": "failed", "error_code": "read_worker_recovery_exhausted", "active_slot": False}})
    observed = ObservedDatabase(read_db, key)
    assert await store.demand(observed, value, "visible_consumer") == {
        "status": "failed", "key": key, "retry_after_ms": None, "error_code": "read_worker_recovery_exhausted"}
    metadata_only(observed.samples)
    observed.samples.clear()
    assert await store.demand(observed, value, "visible_consumer", retry=True) == {
        "status": "preparing", "key": key, "retry_after_ms": 2000}
    metadata_only(observed.samples)
    row = await read_db[store.JOBS].find_one({"_id": key})
    assert row["header"] == header and row["error_code"] is None and row["attempts"] == 0


@pytest.mark.asyncio
async def test_cancelled_rebuild_never_publishes_or_returns_stale_large_header(read_db):
    value = spec()
    key, old_header = await saved_large_report(read_db, value)
    await store.demand(read_db, value, "visible_consumer")
    claim = await jobs.claim_next(read_db)
    # Release both admitted consumers. A failed build must not recreate demand.
    await store.release(read_db, value, "initial_consumer")
    await store.release(read_db, value, "visible_consumer")
    observed = ObservedDatabase(read_db, key)
    async def never_build(*_args):
        raise AssertionError("An abandoned read must not enter the builder")
    assert not await jobs.execute_claim(observed, key, claim["lease_token"], claim["generation"],
                                        builder=never_build, authorize=authorized)
    metadata_only(observed.samples)
    assert await store.read_ready(read_db, value) is None
    row = await read_db[store.JOBS].find_one({"_id": key})
    assert row["status"] == "paused" and row["header"] == old_header
    assert await read_db[store.ROWS].count_documents({"projection_id": key}) == 0
