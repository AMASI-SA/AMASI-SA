"""Server-side five-minute shadow scheduler for Snapchat Reporting V2.

Every backend replica may start this loop.  The per-account distributed lease
inside ``SnapchatV2SyncPipeline`` is the authority that prevents overlapping
provider work, so a replica restart cannot strand a browser BackgroundTask.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .accounts import (
    LEGACY_INTEGRATION_ACCOUNTS_COLLECTION,
    SNAPCHAT_ACCOUNTS_COLLECTION,
)
from .models import SNAPCHAT_PROVIDER
from .sync_pipeline import SnapchatV2SyncPipeline

SNAPCHAT_SHADOW_SCHEDULER_COLLECTION = "mezan_snapchat_shadow_scheduler_v2"
ENABLED_ENV = "SNAPCHAT_REPORTING_V2_SHADOW_SCHEDULER_ENABLED"
INTERVAL_ENV = "SNAPCHAT_REPORTING_V2_SHADOW_INTERVAL_SECONDS"
STARTUP_DELAY_ENV = "SNAPCHAT_REPORTING_V2_SHADOW_STARTUP_DELAY_SECONDS"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 300
MAX_INTERVAL_SECONDS = 3600
DEFAULT_STARTUP_DELAY_SECONDS = 45
MAX_SCHEDULER_ACCOUNTS = 50
MAX_PARALLEL_ACCOUNTS = 1


def max_parallel_accounts() -> int:
    """Snapchat is fixed at one account until staged streaming lands in PR 3."""
    return MAX_PARALLEL_ACCOUNTS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def shadow_scheduler_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def shadow_interval_seconds() -> int:
    try:
        value = int(os.environ.get(INTERVAL_ENV, str(DEFAULT_INTERVAL_SECONDS)))
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_INTERVAL_SECONDS
    return min(max(value, MIN_INTERVAL_SECONDS), MAX_INTERVAL_SECONDS)


def shadow_startup_delay_seconds() -> int:
    try:
        value = int(os.environ.get(STARTUP_DELAY_ENV, str(DEFAULT_STARTUP_DELAY_SECONDS)))
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_STARTUP_DELAY_SECONDS
    return min(max(value, 0), 300)


async def _to_list(cursor: Any, limit: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "sort"):
        cursor = cursor.sort([("user_id", 1), ("ad_account_id", 1)])
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit + 1)
    if hasattr(cursor, "to_list"):
        try:
            rows = list(await cursor.to_list(length=limit + 1))
        except TypeError:
            rows = list(await cursor.to_list(limit + 1))
    else:
        rows = []
        async for row in cursor:
            rows.append(row)
            if len(rows) > limit:
                break
    if len(rows) > limit:
        raise RuntimeError("Snapchat V2 shadow scheduler account list was truncated")
    return rows


async def _selected_accounts(db: Any) -> list[dict[str, str]]:
    v2_rows = await _to_list(
        db[SNAPCHAT_ACCOUNTS_COLLECTION].find(
            {
                "provider": SNAPCHAT_PROVIDER,
                "selected": True,
                "active": True,
                "connection_status": "connected",
            },
            {"_id": 0, "user_id": 1, "ad_account_id": 1},
        ),
        MAX_SCHEDULER_ACCOUNTS,
    )
    source_rows = v2_rows
    if not source_rows:
        source_rows = await _to_list(
            db[LEGACY_INTEGRATION_ACCOUNTS_COLLECTION].find(
                {
                    "provider": SNAPCHAT_PROVIDER,
                    "mezan_selected": True,
                    "connection_status": "connected",
                },
                {
                    "_id": 0,
                    "user_id": 1,
                    "ad_account_id": 1,
                    "external_account_id": 1,
                },
            ),
            MAX_SCHEDULER_ACCOUNTS,
        )
    identities: dict[tuple[str, str], dict[str, str]] = {}
    for row in source_rows:
        user_id = str(row.get("user_id") or "").strip()
        account_id = str(
            row.get("ad_account_id") or row.get("external_account_id") or ""
        ).strip()
        if not user_id or not account_id:
            continue
        identities[(user_id, account_id)] = {
            "user_id": user_id,
            "ad_account_id": account_id,
        }
    return list(identities.values())


async def _run_one(
    db: Any,
    identity: dict[str, str],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        result = await SnapchatV2SyncPipeline(db).run(
            identity["user_id"],
            identity["ad_account_id"],
            run_type="rolling_refresh",
        )
        return {
            "user_id": identity["user_id"],
            "ad_account_id": identity["ad_account_id"],
            "status": result.get("status"),
            "sync_run_id": result.get("sync_run_id"),
            "reason": result.get("reason"),
            "error": result.get("error"),
        }


async def run_shadow_cycle(db: Any, *, now: Callable[[], datetime] = _utcnow) -> dict[str, Any]:
    started_at = now().astimezone(timezone.utc)
    accounts = await _selected_accounts(db)
    semaphore = asyncio.Semaphore(max_parallel_accounts())
    # Do not allocate one coroutine/task per account.  The account query is
    # bounded, and each result is released before the next account starts.
    results: list[Any] = []
    for identity in accounts:
        try:
            results.append(await _run_one(db, identity, semaphore))
        except Exception as exc:  # preserve existing per-account semantics
            results.append(exc)
    safe_results: list[dict[str, Any]] = []
    for identity, result in zip(accounts, results):
        if isinstance(result, BaseException):
            safe_results.append(
                {
                    "user_id": identity["user_id"],
                    "ad_account_id": identity["ad_account_id"],
                    "status": "failed",
                    "error": {
                        "code": type(result).__name__[:96],
                        "retryable": True,
                    },
                }
            )
        else:
            safe_results.append(result)
    finished_at = now().astimezone(timezone.utc)
    status = (
        "complete"
        if safe_results and all(row.get("status") in {"complete", "partial"} for row in safe_results)
        else "idle"
        if not safe_results
        else "partial"
    )
    summary = {
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "next_due_at": finished_at + timedelta(seconds=shadow_interval_seconds()),
        "accounts_found": len(accounts),
        "completed": sum(row.get("status") in {"complete", "partial"} for row in safe_results),
        "skipped": sum(row.get("status") == "skipped" for row in safe_results),
        "failed": sum(row.get("status") == "failed" for row in safe_results),
        "results": safe_results,
        "shadow_mode": True,
        "ui_enabled": False,
    }
    await db[SNAPCHAT_SHADOW_SCHEDULER_COLLECTION].update_one(
        {"_id": "snapchat-reporting-v2-shadow"},
        {
            "$set": {
                **summary,
                "updated_at": finished_at,
            },
            "$setOnInsert": {"created_at": started_at},
        },
        upsert=True,
    )
    return summary


async def shadow_scheduler_status(db: Any) -> dict[str, Any]:
    row = await db[SNAPCHAT_SHADOW_SCHEDULER_COLLECTION].find_one(
        {"_id": "snapchat-reporting-v2-shadow"},
        {"_id": 0},
    ) or {}
    return {
        "enabled": shadow_scheduler_enabled(),
        "interval_seconds": shadow_interval_seconds(),
        "runs_without_browser": True,
        **row,
    }


async def shadow_scheduler_loop(db: Any) -> None:
    if not shadow_scheduler_enabled():
        return
    await asyncio.sleep(shadow_startup_delay_seconds())
    while True:
        try:
            await run_shadow_cycle(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            now = _utcnow()
            await db[SNAPCHAT_SHADOW_SCHEDULER_COLLECTION].update_one(
                {"_id": "snapchat-reporting-v2-shadow"},
                {
                    "$set": {
                        "status": "failed",
                        "last_error": {
                            "code": type(exc).__name__[:96],
                            "retryable": True,
                        },
                        "last_finished_at": now,
                        "next_due_at": now
                        + timedelta(seconds=shadow_interval_seconds()),
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
        await asyncio.sleep(shadow_interval_seconds())


def attach_shadow_scheduler(router: Any, db: Any) -> None:
    if getattr(router, "_snapchat_v2_shadow_scheduler_attached", False):
        return
    setattr(router, "_snapchat_v2_shadow_scheduler_attached", True)
    task: asyncio.Task | None = None

    async def start() -> None:
        nonlocal task
        if shadow_scheduler_enabled() and (task is None or task.done()):
            task = asyncio.create_task(
                shadow_scheduler_loop(db),
                name="snapchat-reporting-v2-shadow-5m",
            )

    async def stop() -> None:
        nonlocal task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        task = None

    router.on_startup.append(start)
    router.on_shutdown.append(stop)


__all__ = [
    "ENABLED_ENV",
    "INTERVAL_ENV",
    "SNAPCHAT_SHADOW_SCHEDULER_COLLECTION",
    "attach_shadow_scheduler",
    "run_shadow_cycle",
    "shadow_interval_seconds",
    "shadow_scheduler_enabled",
    "shadow_scheduler_status",
]
