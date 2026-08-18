"""Lightweight scheduler for Campaign AI child-process execution.

The FastAPI web process owns only a tiny timer and subprocess lifecycle. All
provider reads, Salla profitability work and OpenAI analysis happen in the
short-lived worker process. A Mongo global cadence gate inside the worker is
the authority for whether heavy analysis is actually due.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any


logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parent
WORKER_PATH = ROOT_DIR / "campaign_ai_worker_runner.py"
DEFAULT_INITIAL_DELAY_SECONDS = 12.0
DEFAULT_INTERVAL_SECONDS = 5 * 60 * 60
DEFAULT_RETRY_DELAY_SECONDS = 15 * 60
DEFAULT_CADENCE_RECHECK_SECONDS = 5 * 60
DEFAULT_WORKER_TIMEOUT_SECONDS = 10 * 60
CADENCE_SKIP_EXIT_CODE = 3

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


def _float_env(name: str, default: float, *, minimum: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r", name, raw)
        return default


def scheduler_enabled() -> bool:
    explicit = (os.environ.get("MEZAN_CAMPAIGN_AI_SUBPROCESS_SCHEDULER_ENABLED") or "").strip().lower()
    if explicit in _FALSE_VALUES:
        return False
    if explicit in _TRUE_VALUES:
        return True
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("MEZAN_TESTING"):
        return False
    return True


def _bounded_stdout(value: bytes | None, *, limit: int = 4000) -> str:
    if not value:
        return ""
    rendered = value.decode("utf-8", errors="replace").strip()
    return rendered[-limit:]


def next_scheduler_delay(
    code: int,
    *,
    elapsed: float,
    interval: float,
    retry_delay: float,
    cadence_recheck: float,
) -> float:
    """Choose the next lightweight check without changing global AI cadence."""
    if code == 0:
        return max(60.0, interval - max(0.0, elapsed))
    if code == CADENCE_SKIP_EXIT_CODE:
        # Global Mongo state says another replica owns the cycle or next_run_at
        # has not arrived. Re-check soon, but do not run provider/OpenAI work.
        return max(60.0, cadence_recheck)
    # Actual worker/OpenAI/provider failure gets the explicit retry window.
    return max(60.0, retry_delay)


async def run_worker_once(*, timeout_seconds: float | None = None) -> int:
    """Launch one isolated Campaign AI process and return its exit code."""
    timeout = timeout_seconds or _float_env(
        "MEZAN_CAMPAIGN_AI_CHILD_TIMEOUT_SECONDS",
        DEFAULT_WORKER_TIMEOUT_SECONDS,
        minimum=60.0,
    )
    env = os.environ.copy()
    env["MEZAN_CAMPAIGN_AI_CHILD_PROCESS"] = "1"

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(WORKER_PATH),
        cwd=str(ROOT_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.kill()
            await process.wait()
        logger.error("Campaign AI child exceeded %.0fs timeout", timeout)
        return 124

    stdout_text = _bounded_stdout(stdout)
    if stdout_text:
        logger.info("Campaign AI child: %s", stdout_text)
    if process.returncode:
        logger.error(
            "Campaign AI child exited %s (stderr_bytes=%s)",
            process.returncode,
            len(stderr or b""),
        )
    elif stderr:
        logger.warning("Campaign AI child emitted suppressed stderr (%s bytes)", len(stderr))
    return int(process.returncode or 0)


async def scheduler_loop() -> None:
    initial_delay = _float_env(
        "MEZAN_CAMPAIGN_AI_INITIAL_DELAY_SECONDS",
        DEFAULT_INITIAL_DELAY_SECONDS,
        minimum=0.0,
    )
    interval = _float_env(
        "MEZAN_CAMPAIGN_AI_INTERVAL_SECONDS",
        DEFAULT_INTERVAL_SECONDS,
        minimum=5 * 60.0,
    )
    retry_delay = _float_env(
        "MEZAN_CAMPAIGN_AI_RETRY_DELAY_SECONDS",
        DEFAULT_RETRY_DELAY_SECONDS,
        minimum=60.0,
    )
    cadence_recheck = _float_env(
        "MEZAN_CAMPAIGN_AI_CADENCE_RECHECK_SECONDS",
        DEFAULT_CADENCE_RECHECK_SECONDS,
        minimum=60.0,
    )

    await asyncio.sleep(initial_delay)
    while True:
        started = time.monotonic()
        try:
            code = await run_worker_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Campaign AI child launcher failed")
            code = 1

        elapsed = time.monotonic() - started
        delay = next_scheduler_delay(
            code,
            elapsed=elapsed,
            interval=interval,
            retry_delay=retry_delay,
            cadence_recheck=cadence_recheck,
        )
        await asyncio.sleep(delay)


def attach_campaign_ai_subprocess_scheduler(router: Any) -> None:
    """Register startup/shutdown handlers on an APIRouter exactly once."""
    if getattr(router, "_campaign_ai_subprocess_scheduler_attached", False):
        return
    setattr(router, "_campaign_ai_subprocess_scheduler_attached", True)

    state: dict[str, asyncio.Task | None] = {"task": None}

    @router.on_event("startup")
    async def _start_campaign_ai_subprocess_scheduler() -> None:
        if not scheduler_enabled():
            logger.info("Campaign AI subprocess scheduler disabled")
            return
        task = state.get("task")
        if task is not None and not task.done():
            return
        state["task"] = asyncio.create_task(
            scheduler_loop(),
            name="campaign-ai-subprocess-scheduler",
        )
        logger.info(
            "Campaign AI subprocess scheduler started; heavy analysis remains outside web process"
        )

    @router.on_event("shutdown")
    async def _stop_campaign_ai_subprocess_scheduler() -> None:
        task = state.get("task")
        state["task"] = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


__all__ = [
    "CADENCE_SKIP_EXIT_CODE",
    "attach_campaign_ai_subprocess_scheduler",
    "next_scheduler_delay",
    "run_worker_once",
    "scheduler_enabled",
    "scheduler_loop",
]
