"""Real leases/publication races and subprocess lifetime for demand-only reads."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import logging
import sys
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

import demand_read_jobs as jobs
import demand_read_store as store
from demand_read_mongo_fixture import read_db, read_mongo_uri
from test_demand_read_store import spec


async def authorized(_db, _spec):
    return True


async def build(_db, _spec, checkpoint):
    await checkpoint()
    return {"header": {"opening_balance": "19.00", "closing_balance": "23.00"},
            "rows": [{"id": "one", "amount": "4.00"}]}


async def expire(db, row):
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db[store.JOBS].update_many({"_id": {"$in": [row["_id"], jobs.RUNNER_ID]}},
                                   {"$set": {"lease_until": past}})


@pytest.mark.asyncio
async def test_replicas_admit_only_one_cpu_child_and_keep_other_requests_queued(read_db):
    db = read_db
    await store.demand(db, spec(), "consumer_one")
    await store.demand(db, spec(owner_id="second-owner", actor_id="second-actor"), "consumer_two")
    claimed = [row for row in await asyncio.gather(*[jobs.claim_next(db) for _ in range(16)]) if row]
    assert len(claimed) == 1
    assert await db[store.JOBS].count_documents({"status": "running"}) == 1
    assert await db[store.JOBS].count_documents({"status": "queued"}) == 1
    assert await jobs.claim_next(db) is None


@pytest.mark.asyncio
async def test_no_visible_demand_never_claims_or_rebuilds(read_db):
    db = read_db
    value = spec()
    assert await jobs.claim_next(db) is None
    assert await db[store.JOBS].count_documents({}) == 0
    key = (await store.demand(db, value, "consumer_one"))["key"]
    await db[store.JOBS].update_one({"_id": key}, {"$set": {"consumers": []}})
    assert await jobs.claim_next(db) is None
    assert await db[store.JOBS].find_one({"_id": jobs.RUNNER_ID}) is None


@pytest.mark.asyncio
async def test_crashed_claim_is_recovered_and_old_generation_cannot_publish(read_db):
    db = read_db
    value = spec()
    await store.demand(db, value, "consumer_one")
    first = await jobs.claim_next(db)
    async def checkpoint():
        await jobs.check_claim(db, first)
    await store.materialize(db, first, {"header": {"opening_balance": "wrong"}, "rows": [{"id": "stale"}]}, checkpoint)
    await expire(db, first)
    second = await jobs.claim_next(db)
    assert second["attempts"] == 2 and second["generation"] != first["generation"]
    assert not await jobs.execute_claim(db, first["_id"], first["lease_token"], first["generation"],
                                         builder=build, authorize=authorized)
    assert await jobs.execute_claim(db, second["_id"], second["lease_token"], second["generation"],
                                    builder=build, authorize=authorized)
    ready = await store.read_ready(db, value, limit=8)
    assert ready["snapshot"] == second["generation"]
    assert ready["items"] == [{"id": "one", "amount": "4.00"}]
    assert ready["header"]["opening_balance"] == "19.00"


@pytest.mark.asyncio
async def test_fresh_authorization_after_build_fences_publication_and_sanitizes_failure(read_db):
    db = read_db
    value = spec()
    await db.canonical_sentinel.insert_one({"_id": "financial-authority", "balance": "143.21"})
    await store.demand(db, value, "consumer_one")
    checks = 0
    async def revoke_after_build(_db, _spec):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise HTTPException(409, detail="private customer and grant changed")
        return True
    assert not await jobs.execute_once(db, builder=build, authorize=revoke_after_build)
    assert await store.read_ready(db, value) is None
    row = await db[store.JOBS].find_one({"_id": store.projection_key(value)})
    assert row["status"] == "failed" and row["error_code"] == "read_authorization_changed"
    assert "private customer" not in repr(row)
    assert await db.canonical_sentinel.find_one({"_id": "financial-authority"}) == {
        "_id": "financial-authority", "balance": "143.21"}


@pytest.mark.asyncio
async def test_failures_retry_only_three_times_and_require_explicit_retry(read_db):
    db = read_db
    value = spec()
    key = (await store.demand(db, value, "consumer_one"))["key"]
    async def broken(_db, _spec, checkpoint):
        raise RuntimeError("private query material")
    for attempt in range(1, 4):
        assert not await jobs.execute_once(db, builder=broken, authorize=authorized)
        row = await db[store.JOBS].find_one({"_id": key})
        assert row["attempts"] == attempt
        assert "private query" not in repr(row)
        await db[store.JOBS].update_one({"_id": key}, {"$set": {"retry_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert row["status"] == "failed" and row["active_slot"] is False
    assert (await store.demand(db, value, "consumer_one"))["status"] == "failed"
    assert await jobs.claim_next(db) is None
    await db[store.JOBS].update_one({"_id": key}, {"$set": {"retry_at": datetime.now(timezone.utc) + timedelta(hours=1)}})
    assert (await store.demand(db, value, "consumer_one", retry=True))["status"] == "preparing"
    assert await jobs.execute_once(db, builder=build, authorize=authorized)


@pytest.mark.asyncio
async def test_departure_during_build_never_publishes_and_frees_owner_slot(read_db):
    db = read_db
    value = spec()
    await store.demand(db, value, "consumer_one")
    async def departed(_db, _spec, checkpoint):
        await store.release(db, value, "consumer_one")
        return await build(_db, _spec, checkpoint)
    assert not await jobs.execute_once(db, builder=departed, authorize=authorized)
    row = await db[store.JOBS].find_one({"_id": store.projection_key(value)})
    assert row["status"] == "paused" and row["active_slot"] is False
    assert await store.read_ready(db, value) is None


def busy_child(tmp_path):
    path = tmp_path / "synthetic_busy_read.py"
    marker = tmp_path / "child_started"
    path.write_text("from pathlib import Path\nimport sys\n"
        f"sys.path.insert(0, {str(Path(jobs.__file__).resolve().parent)!r})\n"
        "from demand_read_runner import watch_parent_pipe\n"
        "watch_parent_pipe()\n"
        f"Path({str(marker)!r}).write_text('started')\n"
        "while True:\n    sum(range(50000))\n", encoding="utf-8")
    return path, marker


@pytest.mark.asyncio
async def test_last_consumer_departure_stops_cpu_child_but_another_consumer_keeps_it(read_db, monkeypatch, tmp_path):
    db = read_db
    value = spec()
    await store.demand(db, value, "consumer_one")
    await store.demand(db, value, "consumer_two")
    claim = await jobs.claim_next(db)
    path, marker = busy_child(tmp_path)
    processes = []
    async def launcher(*_args, **kwargs):
        process = await asyncio.create_subprocess_exec(sys.executable, "-B", str(path), **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(jobs, "MONITOR_SECONDS", .02)
    task = asyncio.create_task(jobs.run_claim(db, claim, launcher=launcher))
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(.01)
        await store.release(db, value, "consumer_one")
        await asyncio.sleep(.08)
        assert processes[0].returncode is None and not task.done()
        await store.release(db, value, "consumer_two")
        await asyncio.wait_for(task, 3)
        assert processes[0].returncode is not None
        row = await db[store.JOBS].find_one({"_id": claim["_id"]})
        assert row["status"] == "paused" and row["active_slot"] is False
        runner = await db[store.JOBS].find_one({"_id": jobs.RUNNER_ID})
        assert runner["lease_until"] <= datetime.now(timezone.utc)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("double_cancel", [False, True])
async def test_supervisor_cancellation_joins_actual_busy_child_before_releasing_runner(read_db, monkeypatch, tmp_path, double_cancel):
    db = read_db
    await store.demand(db, spec(), "consumer_one")
    claim = await jobs.claim_next(db)
    path, marker = busy_child(tmp_path)
    processes = []
    async def launcher(*_args, **kwargs):
        process = await asyncio.create_subprocess_exec(sys.executable, "-B", str(path), **kwargs)
        processes.append(process)
        return process
    original_release = jobs._release_runner
    async def checked_release(*args):
        assert processes[0].returncode is not None
        await original_release(*args)
    monkeypatch.setattr(jobs, "_release_runner", checked_release)
    stopping, allow_stop = asyncio.Event(), asyncio.Event()
    if not double_cancel:
        allow_stop.set()
    original_stop = jobs._stop_process
    async def gated_stop(process):
        stopping.set()
        await allow_stop.wait()
        await original_stop(process)
    monkeypatch.setattr(jobs, "_stop_process", gated_stop)
    task = asyncio.create_task(jobs.run_claim(db, claim, launcher=launcher))
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(.01)
        heartbeats = 0
        for _ in range(20):
            await asyncio.sleep(.002)
            heartbeats += 1
        assert heartbeats == 20 and processes[0].pid != __import__("os").getpid()
        task.cancel()
        await stopping.wait()
        if double_cancel:
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and processes[0].returncode is None
            allow_stop.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert processes[0].returncode is not None
    finally:
        allow_stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_actual_assembly_child_reads_canonical_fixture_and_publishes_ready(read_db, read_mongo_uri, monkeypatch):
    import assembly_board_read_models as assembly
    from test_fulfillment_read_performance import seed_queue
    db = read_db
    user = {"id": "owner", "role": "owner", "is_active": True, "name": "Private synthetic owner"}
    await db.users.insert_one(user)
    numbers = await seed_queue(db, "in_progress", 3)
    value = await assembly.make_spec(db, user=user, state="in_progress")
    await store.demand(db, value, "assembly_consumer")
    claim = await jobs.claim_next(db)
    monkeypatch.setenv("MONGO_URL", read_mongo_uri)
    monkeypatch.setenv("DB_NAME", db.name)
    monkeypatch.setenv("MEZAN_BACKGROUND_WORKER_PROCESS", "1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    collected = []
    monkeypatch.setattr(jobs, "_emit_metrics", lambda value: collected.append(value))
    await asyncio.wait_for(jobs.run_claim(db, claim), 20)
    ready = await store.read_ready(db, value, limit=2)
    row = await db[store.JOBS].find_one({"_id": claim["_id"]})
    assert ready is not None, (row["status"], row.get("error_code"), collected)
    assert ready["total"] == 3 and [item["order_number"] for item in ready["items"]] == numbers[:2]
    assert len(collected) == 1 and collected[0]["outcome"] == "ready"
    assert collected[0]["pid"] != __import__("os").getpid()
    assert all(collected[0][key] >= 0 for key in ("build_ms", "materialize_ms", "check_ms", "process_cpu_ms", "peak_rss_kib"))
    assert "Private synthetic" not in json.dumps(collected)


@pytest.mark.asyncio
async def test_supervisor_forwards_only_fixed_numeric_sample_and_caps_each_family(monkeypatch, caplog):
    import background_worker_process as supervisor
    monkeypatch.setattr(supervisor, "_read_metric_last", {})
    caplog.set_level(logging.INFO, logger=supervisor.log.name)
    reader = asyncio.StreamReader()
    record = {"family": "assembly_board", "outcome": "ready", "pid": 12, "wall_ms": 53.6}
    payloads = ["private provider exception", "x" * 20000,
        store.METRIC_PREFIX + json.dumps({**record, "customer": "private customer"}),
        store.METRIC_PREFIX + json.dumps({**record, "wall_ms": "private financial amount"}),
        store.METRIC_PREFIX + json.dumps(record), store.METRIC_PREFIX + json.dumps(record)]
    reader.feed_data(("\n".join(payloads) + "\n").encode())
    reader.feed_eof()
    await supervisor._discard(reader, trusted_read_metrics=True)
    observed = [message.getMessage() for message in caplog.records if message.name == supervisor.log.name]
    assert len(observed) == 1 and "demand_read_sample" in observed[0]
    assert "private" not in observed[0] and "assembly_board" in observed[0]


@pytest.mark.asyncio
async def test_log_sink_failure_does_not_stop_the_private_output_drain(monkeypatch):
    import background_worker_process as supervisor
    monkeypatch.setattr(supervisor, "_read_metric_last", {})
    def broken(*_args):
        raise OSError("log sink unavailable")
    monkeypatch.setattr(supervisor.log, "info", broken)
    reader = asyncio.StreamReader()
    reader.feed_data((store.METRIC_PREFIX + json.dumps({"family": "assembly_board", "outcome": "failed"}) + "\nprivate\n").encode())
    reader.feed_eof()
    await supervisor._discard(reader, trusted_read_metrics=True)
    assert reader.at_eof()
