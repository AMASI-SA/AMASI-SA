"""Opt-in, bounded operational control; never touches business collections.

One document per release contains the permit and bounded worker acknowledgments.
No threads, provider calls, tokens in documents, or automatic session renewal.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import threading
import time
import uuid

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

COLLECTION = "latency_evidence_control_v1"
POLL_SECONDS = 5
FRESH_SECONDS = 10
IO_TIMEOUT = 1.0
MAX_WORKERS = 16


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_id: str = Field(max_length=100)
    session_id: uuid.UUID
    expected_revision: int = Field(ge=0)
    duration_seconds: int = Field(default=900, ge=1, le=900)


class Controller:
    def __init__(self, collection, release_id, expected_workers, *, clock=time.time,
                 monotonic=time.monotonic):
        self.collection = collection
        self.release_id = release_id
        self.expected_workers = expected_workers
        self.clock, self.monotonic = clock, monotonic
        self.worker_id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.session_id = None
        self.deadline = 0.0
        self.fresh_until = 0.0
        self.reason = "not_confirmed"
        self._task = None

    def allowed(self):
        with self.lock:
            return bool(self.session_id and self.monotonic() < min(self.deadline, self.fresh_until))

    def off(self, reason):
        with self.lock:
            self.session_id = None
            self.deadline = self.fresh_until = 0.0
            self.reason = reason

    async def _io(self, awaitable):
        return await asyncio.wait_for(awaitable, IO_TIMEOUT)

    async def document(self):
        return await self._io(self.collection.find_one({"_id": self.release_id}))

    async def poll_once(self):
        try:
            doc = await self.document()
            if doc is None:
                await self._io(self.collection.update_one(
                    {"_id": self.release_id}, {"$setOnInsert": {
                        "revision": 0, "enabled": False, "session_id": None,
                        "expires_at": 0, "targets": [], "workers": {},
                    }}, upsert=True))
                doc = await self.document()
            now = self.clock()
            # A new process is never added to an already running session.
            active = bool(doc["enabled"] and now < doc["expires_at"])
            with self.lock:
                if active and self.worker_id in doc["targets"]:
                    remaining = max(0, min(900, doc["expires_at"] - now))
                    deadline = self.monotonic() + remaining
                    if self.session_id == doc["session_id"]:
                        deadline = min(self.deadline, deadline)
                    self.session_id = doc["session_id"]
                    self.deadline = deadline
                    self.fresh_until = self.monotonic() + FRESH_SECONDS
                    self.reason = "active"
                else:
                    self.off("not_targeted" if active else "disabled_or_expired")
            # Reclaim only stale non-target workers, with a timestamp CAS.
            for worker, ack in doc["workers"].items():
                if worker not in doc["targets"] and now - ack["seen_at"] > 60:
                    await self._io(self.collection.update_one(
                        {"_id": self.release_id, "revision": doc["revision"],
                         f"workers.{worker}.seen_at": ack["seen_at"]},
                        {"$unset": {f"workers.{worker}": ""}}))
            ack = {"seen_at": now, "enabled": self.allowed(),
                   "revision": doc["revision"], "session_id": doc["session_id"],
                   "reason": self.reason}
            result = await self._io(self.collection.update_one(
                {"_id": self.release_id, "revision": doc["revision"], "$or": [
                    {f"workers.{self.worker_id}": {"$exists": True}},
                    {"$expr": {"$lt": [{"$size": {"$objectToArray": "$workers"}}, MAX_WORKERS]}},
                ]}, {"$set": {f"workers.{self.worker_id}": ack}}))
            if not result.matched_count:
                self.off("revision_changed_or_registry_full")
        except asyncio.CancelledError:
            self.off("shutdown")
            raise
        except Exception:
            # Error content can contain a connection string; never log it.
            self.off("control_unavailable")

    async def run(self):
        try:
            while True:
                await self.poll_once()
                await asyncio.sleep(POLL_SECONDS)
        finally:
            self.off("shutdown")

    async def start(self):
        # Startup runs in each serving process, not during module import.
        self.worker_id = uuid.uuid4().hex
        self.off("startup")
        self._task = asyncio.create_task(self.run(), name="latency-control")

    async def stop(self):
        self.off("shutdown")
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def status(self):
        doc = await self.document()
        if doc is None:
            return {"release_id": self.release_id, "state": "unconfirmed", "revision": 0, "workers": {}}
        now = self.clock()
        enabled = bool(doc["enabled"] and now < doc["expires_at"])
        targets = doc["targets"]
        rows = {}
        for worker, ack in doc["workers"].items():
            rows[worker] = {**ack, "fresh": 0 <= now - ack["seen_at"] < FRESH_SECONDS}
        confirmed = len(targets) == self.expected_workers and all(
            worker in rows and rows[worker]["fresh"]
            and rows[worker]["revision"] == doc["revision"]
            and rows[worker]["session_id"] == doc["session_id"]
            and rows[worker]["enabled"] == enabled for worker in targets)
        return {"release_id": self.release_id, "revision": doc["revision"],
                "session_id": doc["session_id"], "expires_at": doc["expires_at"],
                "expected_workers": self.expected_workers, "targets": targets,
                "desired_enabled": enabled, "state": (
                    "enabled_confirmed" if enabled else "disabled_confirmed") if confirmed else "unconfirmed",
                "workers": rows}

    async def change(self, body, enabled):
        if body.release_id != self.release_id:
            raise HTTPException(409, "release_identity_mismatch")
        doc = await self.document()
        if doc is None:
            raise HTTPException(409, "workers_not_registered")
        session = str(body.session_id)
        # Replay cannot extend the expiry, even after the session has expired.
        if enabled and session == doc["session_id"]:
            return await self.status()
        if not enabled and session != doc["session_id"]:
            raise HTTPException(409, "session_mismatch")
        if doc["revision"] != body.expected_revision:
            raise HTTPException(409, "revision_mismatch")
        values = {"enabled": False}
        if enabled:
            now = self.clock()
            workers = [worker for worker, ack in doc["workers"].items()
                       if 0 <= now - ack["seen_at"] < FRESH_SECONDS]
            if (doc["enabled"] and now < doc["expires_at"] or
                    len(workers) != self.expected_workers or any(
                        doc["workers"][w]["enabled"] or
                        doc["workers"][w]["revision"] != doc["revision"] for w in workers)):
                raise HTTPException(409, "workers_not_confirmed_off")
            values = {"enabled": True, "session_id": session, "targets": workers,
                      "expires_at": now + body.duration_seconds}
        else:
            self.off("owner_stop")
        result = await self._io(self.collection.update_one(
            {"_id": self.release_id, "revision": body.expected_revision},
            {"$set": values, "$inc": {"revision": 1}}))
        if not result.matched_count:
            raise HTTPException(409, "revision_mismatch")
        # No claim that another worker has already observed the command.
        return await self.status()


def attach_latency_control(app, db, release_id):
    """No reads, writes or background task unless capability is explicit."""
    if (os.environ.get("LATENCY_EVIDENCE_CONTROL_ENABLED") != "1" or
            os.environ.get("LATENCY_EVIDENCE_ENABLED") != "1"):
        return
    token = os.environ.get("LATENCY_EVIDENCE_CONTROL_TOKEN", "")
    try:
        expected = int(os.environ.get("LATENCY_EVIDENCE_EXPECTED_WORKERS", "0"))
    except ValueError:
        return
    # A diagnostics reader must never acquire write authority implicitly.
    if (len(token) < 32 or token == os.environ.get("INTERNAL_DIAGNOSTICS_TOKEN")
            or not 1 <= expected <= 8 or not release_id or
            os.environ.get("LATENCY_EVIDENCE_PERMIT_FILE")):
        return
    collection = db[COLLECTION].with_options(
        read_preference=ReadPreference.PRIMARY,
        read_concern=ReadConcern("majority"), write_concern=WriteConcern("majority", wtimeout=900))
    control = Controller(collection, release_id, expected)
    import latency_evidence
    latency_evidence.bind_control(control)
    app.router.add_event_handler("startup", control.start)
    app.router.add_event_handler("shutdown", control.stop)

    def authorize(request, response):
        supplied = request.headers.get("X-Mezan-Latency-Control-Token", "")
        if not supplied or not hmac.compare_digest(token.encode(), supplied.encode()):
            raise HTTPException(403, "latency_control_forbidden")
        response.headers["Cache-Control"] = "no-store"

    async def status(request: Request, response: Response):
        authorize(request, response)
        try:
            return await control.status()
        except Exception:
            raise HTTPException(503, "latency_control_unavailable") from None

    async def change(body: SessionRequest, request: Request, response: Response):
        authorize(request, response)
        try:
            return await control.change(body, request.url.path.endswith("/start"))
        except HTTPException:
            raise
        except Exception:
            control.off("control_unavailable")
            raise HTTPException(503, "latency_control_unavailable") from None

    prefix = "/api/internal/latency-evidence"
    app.add_api_route(prefix, status, methods=["GET"], include_in_schema=False)
    for action in ("start", "stop"):
        app.add_api_route(prefix + "/" + action, change, methods=["POST"], include_in_schema=False)
