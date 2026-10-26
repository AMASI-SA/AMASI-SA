"""Tiny, default-off launcher for the isolated Google automation worker.

The web process owns only readiness, a timer and one child process handle. It
never imports Google, OpenAI, Mongo or the heavy automation graph. Existing
Campaign AI and other providers keep their independent scheduler and cadence.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from pathlib import Path
import sys
from typing import Any

logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parent
WORKER_PATH = ROOT_DIR / "google_ads_automation_runner.py"
INITIAL_DELAY_SECONDS = 12.0
POLL_INTERVAL_SECONDS = 300
# Reserve the final ten seconds of the eight-minute lifecycle for owned-child
# terminate/kill acknowledgement; the child's own cycle deadline is 450s.
CHILD_TIMEOUT_SECONDS = 470.0
STOP_GRACE_SECONDS = 5.0
_TRUE = {"1", "true", "yes", "on", "enabled"}
_active_child = None
_launch_busy = False
_pending_spawn = None


def scheduler_enabled() -> bool:
    return (os.getenv("GOOGLE_ADS_AUTOMATION_CHILD_PROCESS") != "1"
        and os.getenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "false").strip().lower() in _TRUE)


async def _stop_owned_child(process) -> bool:
    """Use only the child handle we created; never an arbitrary PID lookup."""
    if process.returncode is not None:
        return True
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    except Exception:
        logger.error("google_automation_child_stop_unconfirmed")
        return False
    try:
        await asyncio.wait_for(process.wait(), timeout=STOP_GRACE_SECONDS)
        return True
    except asyncio.TimeoutError:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except Exception:
                logger.error("google_automation_child_stop_unconfirmed")
                return False
        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_GRACE_SECONDS)
            return True
        except Exception:
            logger.error("google_automation_child_stop_unconfirmed")
            return False
    except Exception:
        logger.error("google_automation_child_stop_unconfirmed")
        return False


def _clear_spawn(record) -> None:
    global _active_child, _pending_spawn
    record["resolved"] = True
    if _pending_spawn is record:
        _pending_spawn = None
    if _active_child is record.get("process"):
        _active_child = None


async def _cleanup_spawn(record) -> bool:
    process = record.get("process")
    if process is not None and await _stop_owned_child(process):
        _clear_spawn(record)
        return True
    return False


def _ensure_spawn_cleanup(record):
    if record.get("process") is None:
        return None
    if record.get("cleanup_task") is None:
        record["cleanup_task"] = asyncio.create_task(_cleanup_spawn(record))
    return record["cleanup_task"]


def _consume_spawn_exception(task) -> None:
    # A late unacknowledged spawn remains reserved even after its caller timed
    # out. Retrieve its task exception without logging any platform details.
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def _spawn_and_track(record, env):
    global _active_child
    if record["abandoned"]:
        # The caller stopped us before the subprocess API was entered.
        _clear_spawn(record)
        return None
    record["entered"] = True
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, "-B", str(WORKER_PATH),
            cwd=str(ROOT_DIR), env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    except BaseException:
        # No handle acknowledgement means no exit proof. Keep the reservation;
        # do not invent a PID, forget ownership or admit a replacement child.
        record["unconfirmed"] = True
        raise
    record["process"] = process
    _active_child = process
    if record["abandoned"]:
        _ensure_spawn_cleanup(record)
    return process


async def _abandon_spawn(record) -> bool:
    """Bound cleanup while retaining late-spawn ownership until exit proof."""
    record["abandoned"] = True
    deadline = asyncio.get_running_loop().time() + 2 * STOP_GRACE_SECONDS
    if record.get("process") is None and not record["resolved"]:
        try:
            await asyncio.wait_for(asyncio.shield(record["spawn_task"]), timeout=2 * STOP_GRACE_SECONDS)
        except Exception:
            # The shielded spawn may still return a handle later. Its own
            # adoption path then immediately schedules owned-handle cleanup.
            logger.warning("google_automation_spawn_ownership_unconfirmed")
            return False
    if record["resolved"]:
        return True
    cleanup = _ensure_spawn_cleanup(record)
    if cleanup is None:
        return False
    try:
        remaining = max(0.001, deadline - asyncio.get_running_loop().time())
        return await asyncio.wait_for(asyncio.shield(cleanup), timeout=remaining)
    except Exception:
        logger.warning("google_automation_child_stop_unconfirmed")
        return False


async def run_worker_once(*, timeout_seconds: float | None = None) -> int:
    """Launch fixed, finite argv; suppress all child output at the boundary."""
    global _active_child, _launch_busy, _pending_spawn
    if not scheduler_enabled():
        return 0
    # A launch or uncertain termination cannot admit another process. The
    # retained process handle is cleared only once exit is proved.
    if _pending_spawn is not None:
        previous = _pending_spawn.get("process")
        if previous is not None and previous.returncode is not None:
            _clear_spawn(_pending_spawn)
        else:
            return 75
    if _launch_busy or (_active_child is not None and _active_child.returncode is None):
        return 75
    _active_child = None
    try:
        timeout = float(timeout_seconds) if timeout_seconds is not None else CHILD_TIMEOUT_SECONDS
        timeout = min(CHILD_TIMEOUT_SECONDS, max(0.01, timeout)) if math.isfinite(timeout) else CHILD_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        timeout = CHILD_TIMEOUT_SECONDS
    env = os.environ.copy()
    env["GOOGLE_ADS_AUTOMATION_CHILD_PROCESS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    process = None
    completed = False
    _launch_busy = True
    deadline = asyncio.get_running_loop().time() + timeout
    record = {"process": None, "spawn_task": None, "cleanup_task": None,
        "abandoned": False, "resolved": False, "entered": False, "unconfirmed": False}
    _pending_spawn = record
    record["spawn_task"] = asyncio.create_task(_spawn_and_track(record, env))
    record["spawn_task"].add_done_callback(_consume_spawn_exception)
    try:
        # Shield preserves the ownership record even if OS spawn happened but
        # its handle acknowledgement is delayed past cancellation or timeout.
        process = await asyncio.wait_for(asyncio.shield(record["spawn_task"]), timeout=timeout)
        if process is None:
            return 0
        remaining = max(0.001, deadline - asyncio.get_running_loop().time())
        code = await asyncio.wait_for(process.wait(), timeout=remaining)
        completed = True
    except asyncio.CancelledError:
        await _abandon_spawn(record)
        raise
    except asyncio.TimeoutError:
        await _abandon_spawn(record)
        logger.warning("google_automation_child_timeout")
        return 124
    except Exception:
        await _abandon_spawn(record)
        logger.error("google_automation_child_launcher_failed")
        return 1
    finally:
        _launch_busy = False
        if completed or (process is not None and process.returncode is not None):
            _clear_spawn(record)
    logger.log(logging.INFO if code == 0 else logging.WARNING,
        "google_automation_child_finished exit=%d", int(code))
    return int(code)


async def scheduler_loop() -> None:
    try:
        await asyncio.sleep(INITIAL_DELAY_SECONDS)
        while scheduler_enabled():
            try:
                # Sequential awaiting means this process owns at most one child.
                # Mongo account-cycle claims inside it arbitrate other replicas.
                await run_worker_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("google_automation_child_launcher_failed")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        if _pending_spawn is not None:
            await _abandon_spawn(_pending_spawn)


def attach_google_ads_automation_scheduler(router: Any) -> None:
    """Attach once beside the existing ads-manager schedulers; default off."""
    if getattr(router, "_google_ads_automation_scheduler_attached", False):
        return
    setattr(router, "_google_ads_automation_scheduler_attached", True)
    state = {"task": None}

    async def ready_loop():
        from boot_runtime import wait_for_local_readiness
        await wait_for_local_readiness()
        if scheduler_enabled():
            await scheduler_loop()

    @router.on_event("startup")
    async def start_google_ads_automation_scheduler():
        if not scheduler_enabled():
            return
        if state["task"] is None or state["task"].done():
            state["task"] = asyncio.create_task(ready_loop(), name="google-ads-automation-scheduler")

    @router.on_event("shutdown")
    async def stop_google_ads_automation_scheduler():
        task, state["task"] = state["task"], None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


__all__ = ["attach_google_ads_automation_scheduler", "scheduler_enabled", "scheduler_loop", "run_worker_once"]
