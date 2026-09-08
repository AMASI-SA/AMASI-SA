"""Exercise the real worker controller and guards, not only its drain helper.

Linux: actual server, Mongo, direct tasks and registered hooks; fault injection
wraps one stop hook preserving its identity. Local: memory Mongo and inert
provider collaborators, with the SAME worker/controller/claim/classifier code.
No operational code or allowlist is replaced to make a scenario pass.
"""
import asyncio
from functools import wraps
import os
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
# In the image this file is /opt/acceptance/worker_shutdown.py.
if Path("/opt/mezan/backend").is_dir():
    sys.path.insert(0, "/opt/mezan/backend")
import independent_runtime as runtime

DIRECT = {"qoyod-pipeline-worker", "qoyod-plan-b-auto-send-worker", "salla-token-maintenance"}
CLAIM = "backend-heavy-initialization:independent-worker:singleton"


async def scenario(server, mode):
    from boot_runtime import process_local_readiness_event as ready
    ready.clear()
    original_stops = list(server.app.router.on_shutdown)
    target = next(cb for cb in original_stops if cb is not server.on_shutdown)
    entered, release = asyncio.Event(), asyncio.Event()
    @wraps(target)
    async def injected_stop():
        await target()
        entered.set()
        if mode == "error":
            raise RuntimeError("synthetic stop hook failure")
        await release.wait()
    server.app.router.on_shutdown = [injected_stop if cb is target else cb for cb in original_stops]
    stop = asyncio.Event()
    controller = asyncio.create_task(runtime.worker(server, stop), name="shutdown-probe-controller")
    leases = server.db.backend_startup_leases_v1
    retained = None
    try:
        for _ in range(300):
            if controller.done():
                await controller
                raise AssertionError("worker exited before ready")
            if ready.is_set():
                break
            await asyncio.sleep(0.02)
        assert ready.is_set(), "worker never initialized"
        direct = [t for t in asyncio.all_tasks() if t.get_name() in DIRECT]
        assert {t.get_name() for t in direct} == DIRECT
        assert all(not t.done() for t in direct)
        retained = await leases.find_one({"_id": CLAIM})
        assert retained and retained["status"] == "running"
        stop.set()
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(100):
            if all(t.done() for t in direct):
                break
            await asyncio.sleep(0.01)
        assert all(t.done() and t.cancelling() > 0 for t in direct), "direct work survived while stop hook drained"
        assert not ready.is_set()
        if mode in {"slow", "successor"}:
            assert not controller.done(), "slow stop must still be draining"
            held = await leases.find_one({"_id": CLAIM})
            assert held["owner_id"] == retained["owner_id"] and held["fence"] == retained["fence"]
            if mode == "successor":
                await leases.update_one({"_id": CLAIM, "fence": retained["fence"]},
                    {"$set": {"fence": "synthetic-successor", "owner_id": "synthetic-successor"}})
            release.set()
            await asyncio.wait_for(controller, 3)
            result = await leases.find_one({"_id": CLAIM})
            assert result is None if mode == "slow" else result["owner_id"] == "synthetic-successor"
        else:
            try:
                await asyncio.wait_for(controller, 8)
            except RuntimeError as exc:
                expected = "deadline exceeded" if mode == "timeout" else "shutdown failed"
                assert expected in str(exc), str(exc)
            else:
                raise AssertionError("failed drain reported success")
            result = await leases.find_one({"_id": CLAIM})
            assert result and result["owner_id"] == retained["owner_id"] and result["fence"] == retained["fence"]
        print("PASS real worker shutdown scenario:", mode, "direct cancellation first; fenced claim outcome verified", flush=True)
    finally:
        release.set()
        stop.set()
        if not controller.done():
            controller.cancel()
        await asyncio.gather(controller, return_exceptions=True)
        server.app.router.on_shutdown = original_stops
        # Only the known disposable test claim is cleaned between scenarios.
        if retained:
            owner = "synthetic-successor" if mode == "successor" else retained["owner_id"]
            fence = "synthetic-successor" if mode == "successor" else retained["fence"]
            await leases.delete_one({"_id": CLAIM, "owner_id": owner, "fence": fence})


async def exercise(server):
    for mode in ("slow", "error", "timeout", "successor"):
        await scenario(server, mode)


async def local():
    from mongomock_motor import AsyncMongoMockClient
    database = AsyncMongoMockClient()["mezan_exit2c_local_unit"]
    await database.backend_startup_leases_v1.insert_one({"_id": "backend-heavy-initialization:test:exit2c-candidate", "status": "completed"})
    async def no_event():
        pass
    async def start():
        pass
    async def finish():
        pass
    for cb, name in ((start, "start"), (finish, "stop")):
        cb.__module__ = "snapchat_v2.scheduler"
        cb.__qualname__ = "attach_shadow_scheduler.<locals>." + name
    server = SimpleNamespace(db=database, release_health_payload=lambda: {}, on_startup=no_event, on_shutdown=no_event,
        app=SimpleNamespace(router=SimpleNamespace(on_startup=[no_event, start], on_shutdown=[no_event, finish])))
    async def inert(*args):
        await asyncio.Event().wait()
    modules = {}
    for name, task_name in (("integrations.qoyod.worker", "qoyod-pipeline-worker"), ("integrations.qoyod_manual.auto_send", "qoyod-plan-b-auto-send-worker")):
        mod = ModuleType(name)
        mod.start_worker = lambda db, label=task_name: asyncio.create_task(inert(), name=label)
        modules[name] = mod
    service = ModuleType("salla_integration.service")
    service.salla_token_maintenance_loop = inert
    modules[service.__name__] = service
    async def no_configuration(server):
        pass
    # Local collaborator boundaries only. Real release key, migration marker,
    # startup claim/heartbeat, classifier and worker are NOT patched.
    with patch.dict(sys.modules, modules), patch.object(runtime, "load_process_configuration", no_configuration):
        await exercise(server)


async def linux():
    runtime.validate_before_import("worker")
    server = runtime.load_server("worker")
    try:
        await exercise(server)
    finally:
        server.client.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--local"]:
        with patch.dict(os.environ, {"APP_ENV": "test", "TEST_RELEASE_STARTUP_KEY": "test:exit2c-candidate", "MEZAN_WORKER_ENABLED": "1"}, clear=True):
            asyncio.run(local())
    else:
        assert sys.argv[1:] == ["--linux"]
        assert os.environ.get("MEZAN_WORKER_ENABLED") == "1"
        asyncio.run(linux())
