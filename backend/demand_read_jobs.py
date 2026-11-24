"""Demand-only read work in one fenced CPU subprocess across replicas.

The shared background loop does only queue/lease I/O. Fixed family builders run
in a child, authorize their own fresh scope, and publish disposable read data.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import importlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

import demand_read_store as store


RUNNER_ID = "read-projection-runner-v1"
LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 5
MONITOR_SECONDS = 1
POLL_SECONDS = 2
MAX_JOB_SECONDS = 180
MAX_ATTEMPTS = 3


class ClaimLost(RuntimeError):
    pass


class DemandExpired(ClaimLost):
    pass


def _live_runner(token):
    return {"_id": RUNNER_ID, "lease_token": token,
            "$expr": {"$gt": ["$lease_until", "$$NOW"]}}


def _live_claim(row, *, demand=True):
    conditions = [{"$gt": ["$lease_until", "$$NOW"]}]
    if demand:
        conditions.append(store.has_demand())
    return {"_id": row["_id"], "owner_id": row["owner_id"], "actor_id": row["actor_id"],
            "status": "running", "lease_token": row["lease_token"], "generation": row["generation"],
            "code_version": store.CODE_VERSION, "$expr": {"$and": conditions}}


async def _claim_runner(db, token):
    try:
        row = await db[store.JOBS].find_one_and_update({"_id": RUNNER_ID}, [
            {"$replaceWith": {"$cond": [
                {"$lte": [{"$ifNull": ["$lease_until", "$$NOW"]}, "$$NOW"]},
                {"$mergeObjects": ["$$ROOT", {"kind": "runner", "lease_token": {"$literal": token},
                    "lease_until": {"$add": ["$$NOW", LEASE_SECONDS * 1000]}}]}, "$$ROOT",
            ]}},
        ], upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        return False
    return bool(row and row.get("lease_token") == token)


async def _release_runner(db, token):
    await db[store.JOBS].update_one({"_id": RUNNER_ID, "lease_token": token},
                                  [{"$set": {"lease_until": "$$NOW"}}])


async def claim_next(db):
    await store.ensure_indexes(db)
    # A repeatedly crashed child cannot remain in an endless preparing state.
    await db[store.JOBS].update_one({"code_version": store.CODE_VERSION, "status": "running",
        "attempts": {"$gte": MAX_ATTEMPTS}, "$expr": {"$lte": ["$lease_until", "$$NOW"]}},
        [{"$set": {"status": "failed", "active_slot": False,
                   "error_code": "read_worker_recovery_exhausted", "finished_at": "$$NOW"}}])
    pending = {"code_version": store.CODE_VERSION, "attempts": {"$lt": MAX_ATTEMPTS},
        "$expr": {"$and": [store.has_demand(),
            {"$lte": [{"$ifNull": ["$retry_at", "$$NOW"]}, "$$NOW"]}]},
        "$or": [{"status": "queued"}, {"status": "running", "$expr": {"$lte": ["$lease_until", "$$NOW"]}}]}
    if await db[store.JOBS].find_one(pending, {"_id": 1}) is None:
        return None
    token = uuid.uuid4().hex
    if not await _claim_runner(db, token):
        return None
    try:
        # Preserve all claim metadata without transferring an old result.
        row = await db[store.JOBS].find_one_and_update(pending, [{"$set": {
            "status": "running", "active_slot": True, "lease_token": {"$literal": token},
            "generation": {"$literal": uuid.uuid4().hex},
            "lease_until": {"$add": ["$$NOW", LEASE_SECONDS * 1000]},
            "started_at": "$$NOW", "attempts": {"$add": ["$attempts", 1]}, "error_code": None,
        }}], sort=[("requested_at", 1)], return_document=ReturnDocument.AFTER,
            projection={"header": 0})
        if row is None:
            await _release_runner(db, token)
        return row
    except BaseException:
        with suppress(Exception):
            await _release_runner(db, token)
        raise


async def check_claim(db, row):
    if await db[store.JOBS].find_one(_live_runner(row["lease_token"]), {"_id": 1}) is None:
        raise ClaimLost("read_runner_claim_lost")
    if await db[store.JOBS].find_one(_live_claim(row), {"_id": 1}) is None:
        if await db[store.JOBS].find_one(_live_claim(row, demand=False), {"_id": 1}) is not None:
            raise DemandExpired("read_demand_expired")
        raise ClaimLost("read_job_claim_lost")


async def renew_claim(db, row):
    await check_claim(db, row)
    for query in (_live_runner(row["lease_token"]), _live_claim(row)):
        changed = await db[store.JOBS].update_one(query,
            [{"$set": {"lease_until": {"$add": ["$$NOW", LEASE_SECONDS * 1000]}}}])
        if changed.matched_count != 1:
            raise ClaimLost("read_job_claim_lost")


async def _pause(db, row):
    await db[store.JOBS].update_one(_live_claim(row, demand=False),
        [{"$set": {"status": "paused", "active_slot": False, "finished_at": "$$NOW"}}])


async def _fail(db, row, code, *, fatal=False):
    terminal = fatal or row["attempts"] >= MAX_ATTEMPTS
    await db[store.JOBS].update_one(_live_claim(row, demand=False), [{"$set": {
        "status": "failed" if terminal else "queued", "active_slot": not terminal,
        "error_code": code, "finished_at": "$$NOW",
        "retry_at": {"$add": ["$$NOW", min(30, 2 ** row["attempts"]) * 1000]},
    }}])


def _builder_module(family):
    if family.startswith("operational_") and family in store.FAMILIES:
        return importlib.import_module("operational_workspace_read_builders")
    modules = {"dashboard_card": "dashboard_card_read_models", "assembly_board": "assembly_board_read_models"}
    name = modules.get(family)
    if name is None:
        raise store.InvalidReadSpec("read_family_invalid")
    return importlib.import_module(name)


async def execute_claim(db, key, token, generation, *, builder=None, authorize=None, startup_started=None, runner_import_ms=None):
    """Child entry; test injections stay process-local, never in stored jobs."""
    # The child rebuilds from the scoped source; it never consumes the stale header.
    row = await db[store.JOBS].find_one({"_id": key, "status": "running", "lease_token": token,
        "generation": generation, "code_version": store.CODE_VERSION,
        "$expr": {"$gt": ["$lease_until", "$$NOW"]}}, {"header": 0})
    if row is None:
        return False
    started, cpu_started = time.monotonic(), time.process_time()
    metrics = {"family": row["family"], "outcome": "failed", "pid": os.getpid(),
               "build_ms": 0.0, "materialize_ms": 0.0, "authorize_ms": 0.0, "check_ms": 0.0, "check_calls": 0}
    from demand_read_diagnostics import _number, capture, save_owner_trace
    if _number(startup_started) and 0 <= started - startup_started <= MAX_JOB_SECONDS + LEASE_SECONDS:
        metrics["startup_ms"] = (started - startup_started) * 1000
    if _number(runner_import_ms) and runner_import_ms <= (MAX_JOB_SECONDS + LEASE_SECONDS) * 1000:
        metrics["runner_import_ms"] = runner_import_ms
    async def checkpoint():
        stamp = time.monotonic()
        try:
            await check_claim(db, row)
        finally:
            metrics["check_ms"] += (time.monotonic() - stamp) * 1000
            metrics["check_calls"] += 1
    async def authorize_current(spec):
        stamp = time.monotonic()
        try:
            return await authorize(db, spec)
        finally:
            metrics["authorize_ms"] += (time.monotonic() - stamp) * 1000
    try:
        await checkpoint()
        spec = store.normalize_spec(row["spec"])
        if store.projection_key(spec) != row["_id"] or (spec["owner_id"], spec["actor_id"]) != (row["owner_id"], row["actor_id"]):
            raise store.InvalidReadSpec("read_scope_invalid")
        if builder is None or authorize is None:
            stamp = time.monotonic()
            try:
                module = _builder_module(spec["family"])
            finally:
                metrics["builder_import_ms"] = (time.monotonic() - stamp) * 1000
            builder = builder or module.build_projection
            authorize = authorize or module.authorize_spec
        if await authorize_current(spec) is not True:
            await _fail(db, row, "read_authorization_changed", fatal=True)
            return False
        await checkpoint()
        stamp = time.monotonic()
        try:
            with capture(metrics, enabled=spec["owner_id"] == spec["actor_id"]):
                result = await builder(db, spec, checkpoint)
        finally:
            metrics["build_ms"] = (time.monotonic() - stamp) * 1000
        await checkpoint()
        stamp = time.monotonic()
        try:
            artifact = await store.materialize(db, row, result, checkpoint)
        finally:
            metrics["materialize_ms"] = (time.monotonic() - stamp) * 1000
        if await authorize_current(spec) is not True:
            await _fail(db, row, "read_authorization_changed", fatal=True)
            return False
        await checkpoint()

        async def publish(session):
            runner = await db[store.JOBS].update_one(_live_runner(token),
                [{"$set": {"publication_checked_at": "$$NOW"}}], session=session)
            saved = await db[store.JOBS].update_one(_live_claim(row), [{"$set": {
                "status": "ready", "active_slot": False, "header": {"$literal": artifact["header"]},
                "paged": artifact["paged"], "total": artifact["total"], "generated_at": "$$NOW",
                "ready_until": {"$add": ["$$NOW", spec.get("cache_seconds", 120) * 1000]},
                "finished_at": "$$NOW", "error_code": None, "consumers": [],
            }}], session=session)
            if runner.matched_count != 1 or saved.matched_count != 1:
                raise ClaimLost("read_publication_claim_lost")
        async with await db.client.start_session() as session:
            await session.with_transaction(publish, read_concern=ReadConcern("snapshot"),
                                           write_concern=WriteConcern("majority", j=True))
        metrics["outcome"] = "ready"
        return True
    except DemandExpired:
        metrics["outcome"] = "paused"
        await _pause(db, row)
        return False
    except ClaimLost:
        metrics["outcome"] = "claim_lost"
        return False
    except asyncio.CancelledError:
        metrics["outcome"] = "cancelled"
        raise
    except (store.InvalidReadSpec, store.ProjectionUnavailable):
        await _fail(db, row, "read_projection_unavailable", fatal=True)
        return False
    except Exception as error:
        # HTTP authority failures are terminal. Never save exception text or
        # request/customer payloads in operational metadata or logs.
        forbidden = getattr(error, "status_code", None) in {401, 403, 404, 409}
        await _fail(db, row, "read_authorization_changed" if forbidden else "read_preparation_failed", fatal=forbidden)
        return False
    finally:
        metrics.update(wall_ms=(time.monotonic() - started) * 1000,
                       process_cpu_ms=(time.process_time() - cpu_started) * 1000)
        with suppress(Exception):
            import resource
            metrics["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with suppress(Exception):
            metrics["queue_ms"] = max(0.0, (row["started_at"] - row["requested_at"]).total_seconds() * 1000)
        with suppress(Exception):
            metrics.update(_mongo_driver_metrics())
        with suppress(Exception):
            await save_owner_trace(db, row, metrics)
        _emit_metrics(metrics)


def _mongo_driver_metrics():
    """Bounded driver/network-inclusive timing samples, not executionStats.

    Each production child owns one Mongo client and one job. Read the existing
    listener's bounded snapshot without issuing queries or exporting shapes.
    """
    from mongo_observability import mongo_metrics
    snapshot = mongo_metrics.snapshot()
    command = snapshot.get("operation_duration_ms") or {}
    checkout = snapshot.get("checkout_wait_ms") or {}
    selected = {
        "mongo_driver_command_p50_ms": command.get("p50"),
        "mongo_driver_command_p95_ms": command.get("p95"),
        "mongo_driver_command_p99_ms": command.get("p99"),
        "mongo_driver_checkout_p95_ms": checkout.get("p95"),
        "mongo_driver_command_timeouts": snapshot.get("operation_timeouts"),
        "mongo_driver_checkout_timeouts": snapshot.get("checkout_timeouts"),
    }
    return {key: value for key, value in selected.items()
            if type(value) in (int, float) and 0 <= value <= 10 ** 12}


def _emit_metrics(metrics):
    with suppress(Exception):
        safe = store.safe_metrics(metrics)
        if safe is not None:
            print(store.METRIC_PREFIX + json.dumps(safe, sort_keys=True, allow_nan=False), flush=True)


async def _stop_process(process):
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), 1)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()


async def execute_once(db, *, builder, authorize):
    """Isolated integration seam; production dispatch always uses run_claim."""
    row = await claim_next(db)
    if row is None:
        return False
    try:
        return await execute_claim(db, row["_id"], row["lease_token"], row["generation"],
                                   builder=builder, authorize=authorize)
    finally:
        await _release_runner(db, row["lease_token"])


async def run_claim(db, row, *, launcher=None):
    launcher = launcher or asyncio.create_subprocess_exec
    process = monitor = waiter = collector = None
    child_metrics = []
    started = time.monotonic()
    outcome = "failed"
    async def completed():
        return await db[store.JOBS].find_one({"_id": row["_id"], "generation": row["generation"],
                                              "status": "ready"}, {"_id": 1}) is not None
    try:
        await check_claim(db, row)
        process = await launcher(sys.executable, "-B", str(Path(__file__).with_name("demand_read_runner.py")),
            row["_id"], row["lease_token"], row["generation"], format(started, ".9f"),
            cwd=str(Path(__file__).resolve().parent),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        def accept(record):
            if record["family"] == row["family"] and not child_metrics:
                child_metrics.append(record)
        collector = asyncio.create_task(store.read_metric_stream(process.stdout, accept))
        async def watch():
            renewed = time.monotonic()
            while True:
                await asyncio.sleep(MONITOR_SECONDS)
                try:
                    if time.monotonic() - renewed >= HEARTBEAT_SECONDS:
                        await renew_claim(db, row)
                        renewed = time.monotonic()
                    else:
                        await check_claim(db, row)
                except ClaimLost:
                    # Publication clears active demand. It is a successful
                    # completion, not a lost lease while the child closes DB.
                    if await completed():
                        return True
                    raise
        monitor = asyncio.create_task(watch())
        waiter = asyncio.create_task(process.wait())
        done, _ = await asyncio.wait_for(asyncio.wait([monitor, waiter], return_when=asyncio.FIRST_COMPLETED),
                                         timeout=MAX_JOB_SECONDS)
        if monitor in done:
            if await monitor:
                outcome = "ready"
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(waiter), 3)
        elif waiter.result() != 0:
            await _fail(db, row, "read_preparation_failed")
        elif not await completed():
            await _fail(db, row, "read_worker_incomplete")
        else:
            outcome = "ready"
    except DemandExpired:
        outcome = "paused"
        await _pause(db, row)
    except ClaimLost:
        outcome = "claim_lost"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except asyncio.TimeoutError:
        await _fail(db, row, "read_preparation_timeout")
    except Exception:
        await _fail(db, row, "read_worker_unavailable")
    finally:
        async def cleanup():
            if process is not None:
                await _stop_process(process)
            for task in (monitor, waiter):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (monitor, waiter, collector) if task is not None), return_exceptions=True)
            with suppress(Exception):
                await _release_runner(db, row["lease_token"])
            metrics = child_metrics[0] if child_metrics else {"family": row["family"],
                "pid": process.pid if process is not None else os.getpid(),
                "wall_ms": (time.monotonic() - started) * 1000}
            _emit_metrics({**metrics, "outcome": outcome})
        cleaning = asyncio.create_task(cleanup())
        cancelled = False
        while not cleaning.done():
            try:
                await asyncio.shield(cleaning)
            except asyncio.CancelledError:
                cancelled = True
        cleaning.result()
        if cancelled:
            raise asyncio.CancelledError


async def run(db, *, wake_event=None):
    from background_worker_process import is_worker_process
    if not is_worker_process():
        raise RuntimeError("demand_reads_require_background_worker")
    await store.ensure_indexes(db)
    while True:
        try:
            row = await claim_next(db)
            if row is not None:
                await run_claim(db, row)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Bounded queue retry must not terminate unrelated workers.
        if wake_event is None:
            await asyncio.sleep(POLL_SECONDS)
        else:
            try:
                await asyncio.wait_for(wake_event.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            wake_event.clear()

