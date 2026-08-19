"""Lightweight scheduler for the isolated advertising product watch.

Every web replica may wake the tiny child launcher, but the Mongo cadence gate in
``advertising_product_watch_v3`` guarantees that only one replica performs the
15-minute operational scan.  No provider/OpenAI work runs in the web event loop.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys
from typing import Any


logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parent
RUNNER = ROOT_DIR / "advertising_product_watch_runner_v3.py"
CHECK_INTERVAL_SECONDS = 5 * 60
INITIAL_DELAY_SECONDS = 45
CHILD_TIMEOUT_SECONDS = 5 * 60
_FALSE = {"0", "false", "no", "off", "disabled"}


def enabled() -> bool:
    explicit = (os.environ.get("MEZAN_ADVERTISING_PRODUCT_WATCH_ENABLED") or "").strip().lower()
    if explicit in _FALSE:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("MEZAN_TESTING"):
        return False
    return True


async def run_once() -> int:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(RUNNER),
        cwd=str(ROOT_DIR),
        env={**os.environ, "MEZAN_ADVERTISING_PRODUCT_WATCH_CHILD": "1"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=CHILD_TIMEOUT_SECONDS
        )
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.kill()
            await process.wait()
        logger.error("Advertising product watch child timed out")
        return 124
    if process.returncode:
        logger.error(
            "Advertising product watch child exited %s (stderr_bytes=%s)",
            process.returncode,
            len(stderr or b""),
        )
    elif stdout:
        rendered = stdout.decode("utf-8", errors="replace").strip()
        logger.info("Advertising product watch: %s", rendered[-2000:])
    return int(process.returncode or 0)


async def loop() -> None:
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Advertising product watch launcher failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def attach_advertising_product_watch_scheduler(router: Any) -> None:
    if getattr(router, "_advertising_product_watch_v3_attached", False):
        return
    setattr(router, "_advertising_product_watch_v3_attached", True)
    state: dict[str, asyncio.Task | None] = {"task": None}

    @router.on_event("startup")
    async def _start() -> None:
        if not enabled():
            return
        task = state.get("task")
        if task is not None and not task.done():
            return
        state["task"] = asyncio.create_task(loop(), name="advertising-product-watch-v3")
        logger.info("Advertising product watch scheduler started")

    @router.on_event("shutdown")
    async def _stop() -> None:
        task = state.get("task")
        state["task"] = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


__all__ = ["attach_advertising_product_watch_scheduler", "enabled", "run_once"]
