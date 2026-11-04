"""Exercise actual current role starters, claims and shutdown in isolated Mongo."""
import asyncio
import os
import sys

sys.path.insert(0, "/opt/mezan/backend")
import independent_runtime as runtime


async def exercise(server, role):
    from boot_runtime import process_local_readiness_event as ready
    before = set(asyncio.all_tasks())
    ready.clear()
    stop = asyncio.Event()
    controller = asyncio.create_task(runtime.worker(server, stop, role))
    try:
        for _ in range(300):
            if controller.done():
                await controller
                raise AssertionError("role exited before readiness")
            if ready.is_set():
                break
            await asyncio.sleep(0.05)
        assert ready.is_set(), "role readiness timeout"
        created = set(asyncio.all_tasks()) - before
        task_names = {task.get_name() for task in created}
        if role == "worker-sync":
            assert "salla-token-maintenance" in task_names
            assert "review-completion-auto-resume" in task_names
            assert "assembly-completion-delivery" in task_names
            assert "qoyod-pipeline-worker" not in task_names
            assert "qoyod-plan-b-auto-send-worker" not in task_names
        else:
            assert "qoyod-pipeline-worker" in task_names
            assert "qoyod-plan-b-auto-send-worker" in task_names
            assert "operational-balances" in task_names
            assert "salla-token-maintenance" not in task_names
            assert "review-completion-auto-resume" not in task_names
        try:
            await runtime.worker(server, asyncio.Event(), role)
        except RuntimeError as error:
            assert "another worker" in str(error)
        else:
            raise AssertionError("duplicate role started")
    finally:
        stop.set()
        await asyncio.wait_for(controller, 12)
    assert not ready.is_set()
    key = "backend-heavy-initialization:independent-worker:" + role
    assert await server.db.backend_startup_leases_v1.find_one({"_id": key}) is None
    leaked = {task for task in asyncio.all_tasks() if task not in before and not task.done()}
    assert not leaked, "role leaked a task after successful shutdown"
    print("PASS actual role ownership, duplicate denial and bounded shutdown:", role, flush=True)


async def main():
    runtime.validate_before_import("worker-sync")
    server = runtime.load_server("worker-sync")
    try:
        for role in ("worker-sync", "worker-accounting"):
            await exercise(server, role)
    finally:
        server.client.close()


if __name__ == "__main__":
    assert os.environ.get("MEZAN_WORKER_ENABLED") == "1"
    asyncio.run(main())
