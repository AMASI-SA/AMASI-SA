"""Derived read results are scoped, immutable and read through bounded pages."""
from copy import deepcopy
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import demand_read_store as store
import demand_read_jobs as jobs
from demand_read_mongo_fixture import read_db, read_mongo_uri


def spec(**changes):
    value = {
        "family": "operational_ledger", "owner_id": "owner", "actor_id": "actor",
        "client_kind": "web", "permission_fingerprint": "grants-v7",
        "request": {"path": "/api/operational-balances/workspace/ledger/bank/a", "method": "GET", "query": {}},
        "source_revision": {"revision": 8}, "resource": ["bank", "a"],
        "currency": "SAR", "period": ["2026-11-01", "2026-11-22"],
        "filters": {"q": "synthetic"}, "projection_version": "ledger-v1",
    }
    return {**value, **changes}


def test_key_is_order_independent_but_binds_every_authority_and_view_input():
    original = spec()
    expected = store.projection_key(original)
    assert store.projection_key(dict(reversed(list(original.items())))) == expected
    for field, replacement in {
        "owner_id": "other-owner", "actor_id": "other-actor", "client_kind": "mobile",
        "permission_fingerprint": "grants-v8", "source_revision": {"revision": 9},
        "resource": ["bank", "b"], "currency": "USD", "period": ["2026-11-02", "2026-11-22"],
        "filters": {"q": "another"}, "projection_version": "ledger-v2",
        "request": {"path": "/api/another", "method": "GET", "query": {}},
    }.items():
        assert store.projection_key({**original, field: replacement}) != expected, field
    assert original == spec()


@pytest.mark.parametrize("change", [
    {"owner_id": ""}, {"actor_id": None}, {"family": "arbitrary_python_module"},
    {"filters": {"number": float("nan")}}, {"filters": {"huge": "x" * 20000}},
])
def test_invalid_or_unbounded_spec_is_rejected_before_database_access(change):
    with pytest.raises(store.InvalidReadSpec):
        store.projection_key(spec(**change))


def test_caller_spec_is_not_retained_by_reference():
    original = spec()
    normalized = store.normalize_spec(original)
    original["filters"]["q"] = "changed"
    assert normalized["filters"]["q"] == "synthetic"


@pytest.mark.asyncio
async def test_concurrent_requests_coalesce_and_owner_queue_capacity_is_atomic(read_db):
    db = read_db
    await store.ensure_indexes(db)
    replies = await asyncio.gather(*[store.demand(db, spec(), f"consumer_{index:03}") for index in range(24)])
    assert len({reply["key"] for reply in replies}) == 1
    assert await db[store.JOBS].count_documents({"family": "operational_ledger"}) == 1
    row = await db[store.JOBS].find_one({"_id": replies[0]["key"]})
    assert len(row["consumers"]) == store.MAX_CONSUMERS and row["attempts"] == 0
    replies = await asyncio.gather(*[store.demand(db, spec(filters={"q": str(index)}), "another_consumer")
                                    for index in range(20)])
    assert sum(reply["status"] == "preparing" for reply in replies) == store.MAX_OWNER_PENDING - 1
    assert await db[store.JOBS].count_documents({"active_slot": True}) == store.MAX_OWNER_PENDING


@pytest.mark.asyncio
async def test_release_is_actor_and_tenant_bound_and_last_consumer_stops_claims(read_db):
    db = read_db
    value = spec()
    key = (await store.demand(db, value, "consumer_one"))["key"]
    await store.demand(db, value, "consumer_two")
    await store.release_by_key(db, key, "other-actor", "consumer_one", owner_id="owner")
    await store.release_by_key(db, key, "actor", "consumer_one", owner_id="other-owner")
    assert len((await db[store.JOBS].find_one({"_id": key}))["consumers"]) == 2
    await store.release(db, value, "consumer_one")
    assert len((await db[store.JOBS].find_one({"_id": key}))["consumers"]) == 1
    await store.release(db, value, "consumer_two")
    assert await jobs.claim_next(db) is None
    row = await db[store.JOBS].find_one({"_id": key})
    assert row["status"] == "paused" and row["active_slot"] is False


async def publish(db, value, header, rows):
    await store.demand(db, value, "consumer_test")
    claim = await jobs.claim_next(db)
    async def authorized(_db, _spec):
        return True
    async def build(_db, _spec, checkpoint):
        await checkpoint()
        return {"header": header, "rows": rows}
    assert await jobs.execute_claim(db, claim["_id"], claim["lease_token"], claim["generation"],
                                    builder=build, authorize=authorized)
    await jobs._release_runner(db, claim["lease_token"])
    return claim


@pytest.mark.asyncio
async def test_immutable_ready_generation_survives_new_client_and_reads_one_indexed_page(read_db, read_mongo_uri):
    from motor.motor_asyncio import AsyncIOMotorClient
    db = read_db
    value = spec()
    rows = [{"id": f"row-{index}", "amount": f"{index}.00"} for index in range(1001)]
    header = {"opening_balance": "17.00", "closing_balance": "99.00", "balance_status": "available"}
    claim = await publish(db, value, header, rows)
    client = AsyncIOMotorClient(read_mongo_uri, tz_aware=True)
    try:
        actual = await store.read_ready(client[db.name], value, offset=400, limit=8)
        assert actual["header"] == header and actual["items"] == rows[400:408]
        assert actual["total"] == 1001 and actual["snapshot"] == claim["generation"]
    finally:
        client.close()
    assert await store.find_row_ordinal(db, value, "row-735", snapshot=claim["generation"]) == 735
    with pytest.raises(store.SnapshotChanged):
        await store.read_ready(db, value, snapshot="previous-generation")
    assert await store.read_ready(db, spec(actor_id="other-actor")) is None
    query = {"projection_id": claim["_id"], "generation": claim["generation"],
             "owner_id": "owner", "actor_id": "actor", "ordinal": {"$gte": 400, "$lt": 408}}
    plan = await db.command("explain", {"find": store.ROWS, "filter": query, "sort": {"ordinal": 1}, "limit": 8},
                            verbosity="executionStats")
    assert plan["executionStats"]["nReturned"] == plan["executionStats"]["totalDocsExamined"] == 8
    again = await store.demand(db, value, "consumer_new")
    assert again["status"] == "ready" and await jobs.claim_next(db) is None


@pytest.mark.asyncio
async def test_ttl_starts_at_publication_and_expired_ready_is_not_served(read_db):
    db = read_db
    value = spec(cache_seconds=5)
    claim = await publish(db, value, {"summary": "complete"}, None)
    row = await db[store.JOBS].find_one({"_id": claim["_id"]})
    assert (row["ready_until"] - row["generated_at"]).total_seconds() == 5
    assert (await store.read_ready(db, value))["items"] is None
    await db[store.JOBS].update_one({"_id": claim["_id"]}, {"$set": {
        "ready_until": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert await store.read_ready(db, value) is None
    assert (await store.demand(db, value, "consumer_again"))["status"] == "preparing"
    replacement = await jobs.claim_next(db)
    assert replacement["generation"] != claim["generation"] and replacement["attempts"] == 1


@pytest.mark.asyncio
async def test_partial_materialization_never_exposes_a_ready_page(read_db):
    db = read_db
    value = spec()
    await store.demand(db, value, "consumer_test")
    claim = await jobs.claim_next(db)
    calls = 0
    async def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise jobs.ClaimLost("synthetic partial write")
    rows = [{"id": str(index)} for index in range(300)]
    with pytest.raises(jobs.ClaimLost):
        await store.materialize(db, claim, {"header": {"opening_balance": "1.00"}, "rows": rows}, checkpoint)
    assert await db[store.ROWS].count_documents({"projection_id": claim["_id"]}) == 128
    assert await store.read_ready(db, value) is None


@pytest.mark.asyncio
async def test_oversized_report_header_is_rejected_without_publishing_or_truncating(read_db):
    db = read_db
    value = spec(family="operational_report")
    await store.demand(db, value, "report_consumer")
    claim = await jobs.claim_next(db)
    async def checkpoint():
        await jobs.check_claim(db, claim)
    with pytest.raises(store.ProjectionUnavailable, match="read_projection_too_large"):
        await store.materialize(db, claim, {"header": {"report": "x" * store.MAX_HEADER_BYTES}, "rows": None}, checkpoint)
    assert await store.read_ready(db, value) is None
    assert await db[store.ROWS].count_documents({"projection_id": claim["_id"]}) == 0
