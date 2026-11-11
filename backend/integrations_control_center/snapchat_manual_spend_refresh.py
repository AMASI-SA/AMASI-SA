"""Explicit, bounded account-spend jobs; independent of full reporting sync."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
import re
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, model_validator
from pymongo.errors import DuplicateKeyError

from snapchat_v2.accounts import list_enabled_accounts
from snapchat_v2 import manual_spend as spend

ROUTE = "/ads-auto-sync/snapchat/refresh"
MAX_OWNED_JOBS = 8
JOB_TIMEOUT_SECONDS = spend.JOB_TIMEOUT_SECONDS
ACCOUNT_TIMEOUT_SECONDS = spend.ACCOUNT_TIMEOUT_SECONDS


class ManualSpendPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def valid_period(self):
        spend.period_window(self.date_from, self.date_to, spend.utcnow())
        return self


def _safe_job(row: dict[str, Any]) -> dict[str, Any]:
    results = [{key: result.get(key) for key in
        ("account_id", "status", "observed_at", "error_code", "snapshot_job_id")}
        for result in row.get("account_results", [])]
    refreshed = sum(result["status"] == "complete" for result in results)
    failed = sum(result["status"] == "failed" for result in results)
    return {"run_id": row["run_id"], "provider": "snapchat_ads", "scope": spend.SCOPE,
        "source_mode": spend.SOURCE_MODE, "status": row["status"],
        "date_from": row["date_from"], "date_to": row["date_to"],
        "requested_account_ids": [account["ad_account_id"] for account in row["accounts"]],
        "selected_accounts": len(results), "accounts_attempted": refreshed + failed,
        "accounts_complete": refreshed, "refreshed_count": refreshed, "failed_count": failed,
        "account_results": results, "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"), "error_code": row.get("error_code"),
        "source_only": True, "provider_write_reached": False, "campaign_write_reached": False,
        "accounting_write_reached": False, "qoyod_write_reached": False}


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "manual_spend_account_timeout"
    code = getattr(exc, "code", None)
    if not code and isinstance(exc, ValueError):
        code = str(exc)
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code) else "manual_spend_account_failed"


def _terminal(results: list[dict[str, Any]], *, error_code: str) -> tuple[str, list[dict[str, Any]]]:
    final = [dict(result) if result.get("status") in {"complete", "failed"} else
        {**result, "status": "failed", "error_code": error_code,
         "observed_at": None, "snapshot_job_id": None} for result in results]
    complete = sum(result["status"] == "complete" for result in final)
    return ("complete" if final and complete == len(final) else "partial" if complete else "failed"), final


class ManualSpendJobs:
    def __init__(self, db: Any, *, now: Callable = spend.utcnow, client_factory: Callable | None = None):
        self.db, self.now, self.client_factory = db, now, client_factory
        self.owned: dict[str, asyncio.Task] = {}

    async def _indexes(self) -> None:
        # The default _id uniqueness protects immutable jobs/snapshots. This
        # partial index makes admission atomic across processes and replicas.
        await self.db[spend.JOBS_COLLECTION].create_index("user_id", unique=True,
            partialFilterExpression={"active": True}, name="manual_spend_one_active_owner")
        await self.db[spend.JOBS_COLLECTION].create_index("expires_at", expireAfterSeconds=0,
            name="manual_spend_jobs_retention")
        await self.db[spend.JOBS_COLLECTION].create_index(
            [("user_id", 1), ("date_from", 1), ("date_to", 1), ("finished_at", -1)],
            name="manual_spend_owner_period_jobs")
        await self.db[spend.COLLECTION].create_index("expires_at", expireAfterSeconds=0,
            name="manual_spend_snapshots_retention")
        await self.db[spend.COLLECTION].create_index(
            [("user_id", 1), ("ad_account_id", 1), ("date_from", 1), ("date_to", 1), ("observed_at", -1)],
            name="manual_spend_period_observations")

    async def _recover(self, user_id: str) -> None:
        current = spend.stored_time(self.now())
        collection = self.db[spend.JOBS_COLLECTION]
        for _ in range(2):
            row = await collection.find_one(
                {"user_id": user_id, "active": True, "deadline_at": {"$lte": current}})
            if not row:
                return
            status, results = _terminal(row["account_results"], error_code="manual_spend_worker_abandoned")
            changed = await collection.update_one(
                {"_id": row["_id"], "user_id": user_id, "active": True,
                    "revision": row["revision"], "deadline_at": {"$lte": current}},
                {"$set": {"active": False, "status": status, "account_results": results,
                    "finished_at": current, "error_code": "manual_spend_worker_abandoned"},
                 "$inc": {"revision": 1}})
            if changed.matched_count == 1:
                return

    def _schedule(self, row: dict[str, Any]) -> None:
        run_id = row["run_id"]
        if row["status"] != "queued" or run_id in self.owned or len(self.owned) >= MAX_OWNED_JOBS:
            return
        task = asyncio.create_task(self._execute(row["user_id"], run_id), name="manual-snapchat-spend-only")
        self.owned[run_id] = task

        def done(completed):
            if self.owned.get(run_id) is completed:
                self.owned.pop(run_id, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(done)

    async def request(self, user_id: str, period: ManualSpendPeriod) -> dict[str, Any]:
        user_id = str(user_id)
        current = spend.stored_time(self.now())
        spend.period_window(period.date_from, period.date_to, current)
        try:
            accounts = sorted([spend.account_identity(row) for row in await list_enabled_accounts(self.db, user_id)],
                              key=lambda row: row["ad_account_id"])
        except (ValueError, KeyError):
            raise HTTPException(409, detail={"code": "manual_spend_account_identity_invalid"}) from None
        if not accounts:
            raise HTTPException(409, detail={"code": "manual_spend_no_enabled_accounts"})
        if len(accounts) > spend.MAX_ACCOUNTS or len({row["ad_account_id"] for row in accounts}) != len(accounts):
            raise HTTPException(422, detail={"code": "manual_spend_account_limit"})
        await self._indexes()
        await self._recover(user_id)
        collection = self.db[spend.JOBS_COLLECTION]
        for _ in range(2):
            active = await collection.find_one({"user_id": user_id, "active": True})
            if active:
                if (active.get("scope") != spend.SCOPE or active.get("date_from") != period.date_from.isoformat()
                        or active.get("date_to") != period.date_to.isoformat() or active.get("accounts") != accounts):
                    raise HTTPException(409, detail={"code": "snapchat_sync_in_progress",
                        "message": "يوجد تحديث صرف سناب جارٍ لفترة أو حسابات أخرى."})
                self._schedule(active)
                return _safe_job(active)
            if len(self.owned) >= MAX_OWNED_JOBS:
                raise HTTPException(503, detail={"code": "snapchat_manual_refresh_busy"})
            run_id = str(uuid4())
            row = {"_id": run_id, "run_id": run_id, "user_id": user_id, "provider": "snapchat_ads",
                "scope": spend.SCOPE, "source_mode": spend.SOURCE_MODE, "active": True, "status": "queued", "revision": 0,
                "date_from": period.date_from.isoformat(), "date_to": period.date_to.isoformat(),
                "accounts": accounts, "created_at": current, "started_at": None, "finished_at": None,
                "expires_at": current + spend.RECORD_RETENTION,
                "deadline_at": current + timedelta(seconds=JOB_TIMEOUT_SECONDS),
                "account_results": [{"account_id": account["ad_account_id"], "status": "queued",
                    "observed_at": None, "error_code": None, "snapshot_job_id": None} for account in accounts]}
            try:
                await collection.insert_one(row)
            except DuplicateKeyError:
                continue
            self._schedule(row)
            return _safe_job(row)
        raise HTTPException(503, detail={"code": "snapchat_manual_refresh_busy"})

    async def _authorized(self, user_id: str, frozen: dict[str, Any]) -> None:
        rows = await list_enabled_accounts(self.db, user_id)
        if not any(spend.account_identity(row) == frozen for row in rows):
            raise ValueError("manual_spend_account_changed")

    async def _finish(self, user_id: str, run_id: str, token: str, error_code: str) -> None:
        query = {"_id": run_id, "user_id": user_id, "active": True, "worker_token": token}
        row = await self.db[spend.JOBS_COLLECTION].find_one(query)
        if row:
            status, results = _terminal(row["account_results"], error_code=error_code)
            await self.db[spend.JOBS_COLLECTION].update_one({**query, "revision": row["revision"]}, {"$set": {
                "active": False, "status": status, "account_results": results,
                "finished_at": spend.stored_time(self.now()),
                "error_code": error_code if status != "complete" else None}, "$inc": {"revision": 1}})

    async def _execute(self, user_id: str, run_id: str) -> None:
        collection = self.db[spend.JOBS_COLLECTION]
        current, token = spend.stored_time(self.now()), str(uuid4())
        claim = await collection.update_one({"_id": run_id, "user_id": user_id,
            "active": True, "status": "queued", "deadline_at": {"$gt": current}},
            {"$set": {"status": "running", "started_at": current, "worker_token": token}, "$inc": {"revision": 1}})
        if claim.matched_count != 1:
            return
        query = {"_id": run_id, "user_id": user_id, "active": True, "worker_token": token}
        row = await collection.find_one(query)
        if row is None:
            return
        budget = {"used": 0}

        async def work():
            for index, account in enumerate(row["accounts"]):
                guarded = {**query, "deadline_at": {"$gt": spend.stored_time(self.now())}}
                if not await collection.find_one(guarded, {"_id": 1}):
                    return
                result = {"account_id": account["ad_account_id"], "status": "failed",
                          "observed_at": None, "snapshot_job_id": None, "error_code": None}
                try:
                    async def fetch():
                        await self._authorized(user_id, account)
                        snapshot = await spend.fetch_manual_spend(self.db, user_id=user_id, account=account,
                            date_from=date.fromisoformat(row["date_from"]), date_to=date.fromisoformat(row["date_to"]),
                            job_id=run_id, requested_at=spend.stored_time(row["created_at"]), now=self.now,
                            client_factory=self.client_factory, job_budget=budget)
                        await self._authorized(user_id, account)
                        return snapshot
                    snapshot = await asyncio.wait_for(fetch(), ACCOUNT_TIMEOUT_SECONDS)
                    # Immutable candidates become visible only after the parent
                    # terminal CAS and matching successful account proof.
                    await self.db[spend.COLLECTION].insert_one(snapshot)
                    result.update(status="complete", observed_at=snapshot["observed_at"], snapshot_job_id=run_id)
                except Exception as exc:
                    result["error_code"] = _error_code(exc)
                changed = await collection.update_one(
                    {**query, "deadline_at": {"$gt": spend.stored_time(self.now())}},
                    {"$set": {f"account_results.{index}": result}, "$inc": {"revision": 1}})
                if changed.matched_count != 1:
                    return

        try:
            remaining = max(0.01, (spend.stored_time(row["deadline_at"]) - spend.stored_time(self.now())).total_seconds())
            await asyncio.wait_for(work(), min(JOB_TIMEOUT_SECONDS, remaining))
            await self._finish(user_id, run_id, token, "manual_spend_incomplete")
        except (TimeoutError, asyncio.TimeoutError):
            await self._finish(user_id, run_id, token, "manual_spend_job_timeout")
        except asyncio.CancelledError:
            await asyncio.shield(self._finish(user_id, run_id, token, "manual_spend_worker_stopped"))
            raise
        except Exception:
            await self._finish(user_id, run_id, token, "manual_spend_worker_failed")

    async def read(self, user_id: str, run_id: str) -> dict[str, Any]:
        await self._recover(str(user_id))
        row = await self.db[spend.JOBS_COLLECTION].find_one({"_id": run_id,
            "user_id": str(user_id), "scope": spend.SCOPE})
        if not row:
            raise HTTPException(404, detail="snapchat_refresh_not_found")
        return _safe_job(row)

    async def stop(self) -> None:
        tasks = list(self.owned.values())
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
        owner = require_owner(user)
        return await jobs.read(str(owner["id"]), run_id)

    router.on_shutdown.append(jobs.stop)
