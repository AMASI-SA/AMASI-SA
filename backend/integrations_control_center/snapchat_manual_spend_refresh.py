"""Owner-requested spend refresh through the existing canonical Snapchat writer.

No startup polling or alternate fact writer: a click starts one supervised,
bounded task. The canonical Mongo/provider lease still excludes other replicas.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, model_validator

from .snapchat_native_data_common import BUSINESS_TIMEZONE, _timezone

START_TIMEOUT_SECONDS = 10
JOB_TIMEOUT_SECONDS = 25 * 60
MAX_OWNED_JOBS = 8
ROUTE = "/ads-auto-sync/snapchat/refresh"


class ManualSpendPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def valid_period(self):
        today = datetime.now(timezone.utc).astimezone(_timezone(BUSINESS_TIMEZONE)).date()
        if self.date_to < self.date_from or (self.date_to - self.date_from).days >= 93 or self.date_to > today:
            raise ValueError("invalid_snapchat_manual_spend_period")
        return self


def _safe_job(scheduler: Any, row: dict[str, Any]) -> dict[str, Any]:
    result = scheduler._safe_provider_run_status(row)
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    for field in ("date_from", "date_to"):
        try:
            result[field] = date.fromisoformat(str(summary.get(field))).isoformat()
        except ValueError:
            result[field] = None
    return result


class ManualSpendJobs:
    def __init__(self, db: Any):
        self.db = db
        self.owned: dict[str, dict[str, Any]] = {}

    async def _active(self, user_id: str, period: ManualSpendPeriod) -> dict[str, Any] | None:
        from . import ads_auto_sync_scheduler as scheduler
        active = await scheduler._active_run(self.db, user_id=user_id,
            provider=scheduler.SNAPCHAT_PROVIDER_ID, now=scheduler._utcnow())
        if not active:
            return None
        row = await self.db[scheduler.RUNS_COLLECTION].find_one({
            "user_id": user_id, "run_id": active["run_id"],
            "provider": scheduler.SNAPCHAT_PROVIDER_ID, "run_type": scheduler.SNAP_RUN_TYPE,
        })
        if row:
            job = _safe_job(scheduler, row)
            if job.get("date_from") and job.get("date_to") and job["date_from"] <= period.date_from.isoformat() and job["date_to"] >= period.date_to.isoformat():
                return job
        raise HTTPException(409, detail={"code": "snapchat_sync_in_progress",
            "message": "يوجد تحديث سناب جارٍ. انتظر اكتماله ثم أعد المحاولة."})

    async def request(self, user_id: str, period: ManualSpendPeriod) -> dict[str, Any]:
        current = self.owned.get(user_id)
        if current is None:
            active = await self._active(user_id, period)
            if active:
                return active
            # A second request may have completed that lookup in the same loop.
            current = self.owned.get(user_id)
        if current is None:
            if len(self.owned) >= MAX_OWNED_JOBS:
                raise HTTPException(503, detail={"code": "snapchat_manual_refresh_busy"})
            started = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(self._execute(user_id, period, started),
                                       name="manual-snapchat-spend-refresh")
            current = {"task": task, "started": started, "period": period}
            self.owned[user_id] = current

            def completed(done):
                if self.owned.get(user_id) is current:
                    self.owned.pop(user_id, None)
                if not done.cancelled():
                    done.exception()
                if started.done() and not started.cancelled():
                    started.exception()

            task.add_done_callback(completed)
        selected = current["period"]
        if selected.date_from > period.date_from or selected.date_to < period.date_to:
            raise HTTPException(409, detail={"code": "snapchat_sync_in_progress"})
        try:
            return await asyncio.wait_for(asyncio.shield(current["started"]), START_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            # The coordinator retains ownership. A retry joins this same task;
            # it never creates a second provider call after an ambiguous reply.
            raise HTTPException(503, detail={"code": "snapchat_refresh_start_unconfirmed",
                "message": "تعذر تأكيد بدء التحديث الآن. أعد المحاولة بعد قليل."}) from exc

    async def _execute(self, user_id: str, period: ManualSpendPeriod, started) -> None:
        from . import ads_auto_sync_scheduler as scheduler

        def registered(run_id: str):
            if not started.done():
                started.set_result({"run_id": run_id, "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "run_type": scheduler.SNAP_RUN_TYPE, "status": "running",
                    "date_from": period.date_from.isoformat(), "date_to": period.date_to.isoformat()})

        async def run():
            async with scheduler.governor.heavy("snapchat", task_name="manual_snapchat_spend"):
                return await scheduler._refresh_snapchat(self.db, user_id=user_id,
                    start_date=period.date_from, end_date=period.date_to, now=scheduler._utcnow(),
                    manual=True, on_started=registered)

        try:
            await asyncio.wait_for(run(), JOB_TIMEOUT_SECONDS)
            if not started.done():
                # Another replica can acquire the provider lease after our
                # initial read. Join only a canonical run covering this range.
                active = await self._active(user_id, period)
                if active:
                    started.set_result(active)
                else:
                    started.set_exception(HTTPException(503, detail={
                        "code": "snapchat_manual_refresh_unavailable",
                        "message": "تعذر بدء جلب صرف سناب الآن.",
                    }))
        except asyncio.CancelledError:
            if not started.done():
                started.cancel()
            raise
        except Exception as exc:
            if not started.done():
                started.set_exception(exc if isinstance(exc, HTTPException) else HTTPException(503,
                    detail={"code": "snapchat_manual_refresh_unavailable"}))

    async def stop(self) -> None:
        tasks = [entry["task"] for entry in self.owned.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def attach_manual_spend_routes(router: APIRouter, db: Any,
                               current_user: Callable, require_owner: Callable) -> None:
    if getattr(router, "_snapchat_manual_spend_attached", False):
        return
    setattr(router, "_snapchat_manual_spend_attached", True)
    jobs = ManualSpendJobs(db)

    @router.post(ROUTE, status_code=202, name="start_manual_snapchat_spend_refresh")
    async def start_manual_spend(period: ManualSpendPeriod, user: dict = Depends(current_user)):
        owner = require_owner(user)
        return await jobs.request(str(owner["id"]), period)

    @router.get(f"{ROUTE}/{{run_id}}", name="read_manual_snapchat_spend_refresh")
    async def read_manual_spend(run_id: str, user: dict = Depends(current_user)):
        from . import ads_auto_sync_scheduler as scheduler
        owner = require_owner(user)
        row = await db[scheduler.RUNS_COLLECTION].find_one({"user_id": str(owner["id"]),
            "run_id": run_id, "provider": scheduler.SNAPCHAT_PROVIDER_ID,
            "run_type": scheduler.SNAP_RUN_TYPE})
        if not row:
            raise HTTPException(404, detail="snapchat_refresh_not_found")
        return _safe_job(scheduler, row)

    router.on_shutdown.append(jobs.stop)
