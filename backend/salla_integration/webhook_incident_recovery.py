"""Explicit, once-only retry grants for the 2026-11-15 capture-contract incident.

This maintenance surface never decrypts or dispatches an event. A read-only
plan binds the authenticated merchant owner, validated running release and
complete persisted rows. Applying it only returns that exact bounded batch to
the existing inbox worker. Ordinary at-least-once business idempotency still
applies; a retry grant is not a claim of exactly-once business execution.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
from typing import Any

from bson import json_util
from fastapi import APIRouter, Depends, HTTPException, Response
from pymongo import ReadPreference
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from . import webhook_inbox as inbox


INCIDENT_ID = "salla-capture-contract-20261115"
INCIDENT_START = "2026-11-15T13:57:00Z"
MERCHANT_ID = "1673234356"
MAX_BATCH = 50
MAX_DURATION_SECONDS = 24 * 60 * 60
MARKER = "capture_contract_recovery_20261115"
BINDING_FENCE = "capture_contract_recovery_fence_20261115"
FLAG = "SALLA_WEBHOOK_INCIDENT_RECOVERY_ENABLED"
MAX_PLAN_BYTES = 128 * 1024
MAX_BATCH_CIPHERTEXT_BYTES = 16 * 1024 * 1024
TRANSACTION_SECONDS = 20
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_EVENT = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_HISTORY_FIELDS = (
    "state", "attempts", "last_error_code", "created_at", "first_received_at",
    "last_received_at", "delivery_count", "started_at", "last_attempt_at",
    "quarantined_at", "next_attempt_at", "lease_until", "heartbeat_at",
)


class RecoveryError(RuntimeError):
    """Fixed public codes only; no provider, payload or database error text."""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def enabled() -> bool:
    return os.environ.get(FLAG, "") in {"true", "1"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    # Motor may return naive UTC depending on the existing application's codec.
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(
        timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime:
    if type(value) is not str or len(value) > 32:
        raise RecoveryError("invalid_request", 422)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RecoveryError("invalid_request", 422) from None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise RecoveryError("invalid_request", 422)
    return result.astimezone(timezone.utc)


def _window(end: Any, limit: Any, now: datetime) -> datetime:
    start, finish = _parse_time(INCIDENT_START), _parse_time(end)
    if (type(limit) is not int or not 1 <= limit <= MAX_BATCH
            or not start < finish <= now
            or (finish - start).total_seconds() > MAX_DURATION_SECONDS):
        raise RecoveryError("invalid_request", 422)
    # Mongo stores milliseconds. Bind the same exclusive boundary in the
    # persisted-row query and the returned/replayed plan.
    return finish.replace(microsecond=(finish.microsecond // 1000) * 1000)


def _json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(value, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False).encode("ascii")
    except (ValueError, TypeError, RecursionError):
        raise RecoveryError("invalid_plan") from None
    if len(rendered) > MAX_PLAN_BYTES:
        raise RecoveryError("invalid_plan")
    return rendered


def _plan_hash(plan: dict) -> str:
    return hashlib.sha256(_json_bytes(plan)).hexdigest()


def _row_hash(row: dict) -> str:
    # Canonical BSON preserves types, missing/null distinctions and ciphertext
    # changes. Only the digest leaves the service, never this serialized row.
    rendered = json_util.dumps(row, json_options=json_util.CANONICAL_JSON_OPTIONS,
                               sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _runtime_identity() -> dict:
    from release_identity import BOOT_RELEASE_IDENTITY

    identity = BOOT_RELEASE_IDENTITY
    checks = ("verified_identity_available", "backend_runtime_source_verified",
              "release_control_source_bound", "critical_file_hashes_match",
              "frontend_build_verified")
    release, source = identity.get("release_id"), identity.get("source_git_sha")
    if (type(identity.get("protocol_version")) is not int or identity["protocol_version"] != 5
            or any(identity.get(key) is not True for key in checks)
            or type(release) is not str or not release.startswith("rg5-")
            or not _SHA256.fullmatch(release[4:])
            or type(source) is not str or not _SHA1.fullmatch(source)):
        raise RecoveryError("runtime_unverified")
    return {"release_id": release, "source_git_sha": source}


def _owner(user: Any) -> str:
    if (type(user) is not dict or type(user.get("id")) is not str
            or not user["id"].strip()
            or (user.get("role") != "owner" and user.get("is_owner") is not True)):
        raise RecoveryError("owner_only", 403)
    return user["id"]


async def _binding(db: Any, owner: str, *, session=None) -> dict:
    candidates = await db.salla_integrations.find(
        {"store_id": {"$in": [MERCHANT_ID, int(MERCHANT_ID)]}}, {"user_id": 1},
        session=session,
    ).sort("updated_at", -1).limit(2).to_list(length=2)
    if len(candidates) > 1:
        # The normal resolver may select the newest of several bindings. This
        # exceptional maintenance grant refuses that ambiguity without changing
        # canonical ownership resolution or any OAuth/binding writer.
        raise RecoveryError("merchant_binding_ambiguous")
    current = candidates[0] if candidates else None
    if not current or current.get("user_id") != owner:
        raise RecoveryError("merchant_owner_mismatch", 403)
    return current


def _query(owner: str, finish: datetime, now: datetime) -> dict:
    return {
        "merchant_id": MERCHANT_ID, "user_id": owner,
        "created_at": {"$gte": _parse_time(INCIDENT_START), "$lt": finish},
        "state": "quarantined", "attempts": 8,
        "$or": [{"attempts": {"$type": "int"}}, {"attempts": {"$type": "long"}}],
        "last_error_code": "attempt_limit",
        "verified_before_enqueue": {"$eq": True, "$type": "bool"},
        "payload_ciphertext": {"$type": "binData", "$ne": b""},
        "lease_until": {"$lte": now, "$type": "date"},
        "claim_token": {"$exists": False}, "worker_token": {"$exists": False},
        "completed_at": {"$exists": False}, MARKER: {"$exists": False},
    }


def _row_metadata(row: dict) -> dict:
    if (type(row.get("_id")) is not str or not _SHA256.fullmatch(row["_id"])
            or row.get("event_hash") != row["_id"]
            or type(row.get("event_name")) is not str
            or not _EVENT.fullmatch(row["event_name"])
            or row["event_name"] in inbox.LIFECYCLE_EVENTS):
        raise RecoveryError("plan_stale")
    return {"job_id": row["_id"], "row_sha256": _row_hash(row),
            "event_name": row["event_name"], "created_at": _iso(row["created_at"]),
            "state": "quarantined", "attempts": 8, "last_error_code": "attempt_limit"}


class IncidentRecovery:
    def __init__(self, db: Any, *, clock=None, runtime_reader=None):
        self.db = db
        self.clock = clock or _now
        self.runtime_reader = runtime_reader or _runtime_identity

    async def config(self, user: dict) -> dict:
        owner = _owner(user)
        await _binding(self.db, owner)
        try:
            runtime = self.runtime_reader()
        except RecoveryError:
            runtime = None
        return {"enabled": enabled(), "incident_start": INCIDENT_START,
                "merchant_id": MERCHANT_ID, "max_batch": MAX_BATCH,
                "max_duration_seconds": MAX_DURATION_SECONDS,
                "runtime_validated": runtime is not None, "runtime": runtime,
                "queue": await self._queue_metadata(owner)}

    async def _queue_metadata(self, owner: str) -> dict:
        states = ("pending", "retry", "processing", "quarantined", "done")
        scope = {"merchant_id": MERCHANT_ID, "user_id": owner, "state": {"$in": list(states)}}
        collection = self.db[inbox.COLLECTION]
        totals = await collection.aggregate([
            {"$match": scope}, {"$group": {"_id": "$state", "count": {"$sum": 1},
                                            "latest_completed_at": {"$max": "$completed_at"}}},
        ]).to_list(length=len(states))
        granted = await collection.aggregate([
            {"$match": {**scope, f"{MARKER}.incident_id": INCIDENT_ID}},
            {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        ]).to_list(length=len(states))
        counts, grant_counts = dict.fromkeys(states, 0), dict.fromkeys(states, 0)
        latest = None
        for row in totals:
            counts[row["_id"]] = row["count"]
            if row["_id"] == "done" and isinstance(row.get("latest_completed_at"), datetime):
                latest = _iso(row["latest_completed_at"])
        for row in granted:
            grant_counts[row["_id"]] = row["count"]
        return {"counts": counts, "latest_completed_at": latest,
                "grant_counts": grant_counts, "total_granted": sum(grant_counts.values())}

    def _context(self, user: dict) -> tuple[str, dict]:
        owner = _owner(user)
        if not enabled():
            raise RecoveryError("recovery_disabled")
        return owner, self.runtime_reader()

    async def _make_plan(self, owner: str, runtime: dict, finish: datetime,
                         limit: int, *, session=None) -> tuple[dict, list[dict]]:
        await _binding(self.db, owner, session=session)
        query = _query(owner, finish, self.clock())
        collection = self.db[inbox.COLLECTION]
        total = await collection.count_documents(query, session=session)
        rows, ciphertext_bytes = [], 0
        cursor = collection.find(query, session=session).sort(
            [("created_at", 1), ("_id", 1)]).limit(limit).batch_size(1)
        async for row in cursor:
            ciphertext_bytes += len(row["payload_ciphertext"])
            if ciphertext_bytes > MAX_BATCH_CIPHERTEXT_BYTES:
                raise RecoveryError("batch_payload_limit", 422)
            rows.append(row)
        if total < len(rows):
            raise RecoveryError("plan_stale")
        plan = {"version": 1, "incident_id": INCIDENT_ID,
                "owner_id": owner, "merchant_id": MERCHANT_ID, "runtime": runtime,
                "incident_start": INCIDENT_START, "incident_end": _iso(finish),
                "batch_limit": limit, "eligible_total": total,
                "rows": [_row_metadata(row) for row in rows]}
        return plan, rows

    async def dry_run(self, user: dict, request: dict) -> dict:
        owner, runtime = self._context(user)
        if type(request) is not dict or set(request) != {"incident_end", "batch_limit"}:
            raise RecoveryError("invalid_request", 422)
        finish = _window(request["incident_end"], request["batch_limit"], self.clock())
        plan, rows = await self._make_plan(owner, runtime, finish, request["batch_limit"])
        # No index creation, lease acquisition, grants or other writes here.
        return {"plan": plan, "plan_sha256": _plan_hash(plan), "summary": {
            "incident_start": INCIDENT_START, "incident_end": plan["incident_end"],
            "eligible_total": plan["eligible_total"], "selected_count": len(rows),
            "remaining": max(0, plan["eligible_total"] - len(rows)),
            "state": "quarantined", "attempts": 8,
        }}

    def _validate_plan(self, plan: Any, digest: Any, owner: str, runtime: dict) -> datetime:
        keys = {"version", "incident_id", "owner_id", "merchant_id", "runtime",
                "incident_start", "incident_end", "batch_limit", "eligible_total", "rows"}
        if (type(plan) is not dict or set(plan) != keys or type(digest) is not str
                or not _SHA256.fullmatch(digest)):
            raise RecoveryError("invalid_plan")
        if not hmac.compare_digest(_plan_hash(plan), digest):
            raise RecoveryError("plan_hash_mismatch")
        if (type(plan["version"]) is not int or plan["version"] != 1
                or plan["incident_id"] != INCIDENT_ID or plan["owner_id"] != owner
                or plan["merchant_id"] != MERCHANT_ID or plan["incident_start"] != INCIDENT_START
                or type(plan["eligible_total"]) is not int
                or type(plan["rows"]) is not list or not 1 <= len(plan["rows"]) <= MAX_BATCH):
            raise RecoveryError("invalid_plan")
        if _json_bytes(plan["runtime"]) != _json_bytes(runtime):
            raise RecoveryError("runtime_changed")
        finish = _window(plan["incident_end"], plan["batch_limit"], self.clock())
        if len(plan["rows"]) > plan["batch_limit"] or plan["eligible_total"] < len(plan["rows"]):
            raise RecoveryError("invalid_plan")
        ids = []
        for row in plan["rows"]:
            if (type(row) is not dict or type(row.get("job_id")) is not str
                    or not _SHA256.fullmatch(row["job_id"])):
                raise RecoveryError("invalid_plan")
            ids.append(row["job_id"])
        if len(set(ids)) != len(ids):
            raise RecoveryError("invalid_plan")
        return finish

    async def _fence(self, lease: dict, session) -> datetime:
        at = self.clock()
        result = await self.db[inbox.LEASES_COLLECTION].update_one(
            inbox._worker_filter(lease, at),
            {"$set": {"heartbeat_at": at,
                      "lease_until": at + timedelta(seconds=inbox.LEASE_SECONDS)}},
            session=session,
        )
        if result.matched_count != 1:
            raise RecoveryError("worker_lease_lost")
        return at

    async def _fence_binding(self, owner: str, binding_id, session) -> None:
        current = await _binding(self.db, owner, session=session)
        if current["_id"] != binding_id:
            raise RecoveryError("merchant_owner_mismatch", 403)
        changed = await self.db.salla_integrations.update_one(
            {"_id": binding_id, "user_id": owner,
             "store_id": {"$in": [MERCHANT_ID, int(MERCHANT_ID)]}},
            {"$inc": {BINDING_FENCE: 1}}, session=session,
        )
        if changed.matched_count != 1:
            raise RecoveryError("merchant_owner_mismatch", 403)
        # This dedicated field must not alter updated_at, whose ordering decides
        # the current owner. A same-document transfer now conflicts/serializes.

    async def apply(self, user: dict, request: dict) -> dict:
        owner, runtime = self._context(user)
        if type(request) is not dict or set(request) != {"plan", "plan_sha256"}:
            raise RecoveryError("invalid_request", 422)
        plan, digest = request["plan"], request["plan_sha256"]
        finish = self._validate_plan(plan, digest, owner, runtime)
        binding = await _binding(self.db, owner)
        lease = await inbox._acquire_worker(self.db, self.clock(), inbox.LEASE_SECONDS)
        if lease is None:
            raise RecoveryError("worker_lease_busy")
        try:
            # No automatic transaction/application replay. A changed row, lost
            # fence or ambiguous commit requires a new explicit dry-run.
            async with asyncio.timeout(TRANSACTION_SECONDS):
                async with await self.db.client.start_session() as session:
                    async with session.start_transaction(
                        read_concern=ReadConcern("snapshot"),
                        write_concern=WriteConcern("majority", j=True, wtimeout=5000),
                        read_preference=ReadPreference.PRIMARY,
                        max_commit_time_ms=5000,
                    ):
                        at = await self._fence(lease, session)
                        await self._fence_binding(owner, binding["_id"], session)
                        if await self.db[inbox.COLLECTION].find_one({
                            "_id": {"$in": [row["job_id"] for row in plan["rows"]]},
                            "merchant_id": MERCHANT_ID, "user_id": owner,
                            MARKER: {"$exists": True},
                        }, {"_id": 1}, session=session):
                            raise RecoveryError("already_granted")
                        actual, rows = await self._make_plan(
                            owner, runtime, finish, plan["batch_limit"], session=session)
                        if _json_bytes(actual) != _json_bytes(plan):
                            raise RecoveryError("plan_stale")
                        for row in rows:
                            grant = {"incident_id": INCIDENT_ID, "plan_sha256": digest,
                                     "granted_at": at, "granted_by": owner, "runtime": runtime,
                                     "incident_start": INCIDENT_START,
                                     "incident_end": plan["incident_end"],
                                     "row_sha256_before": _row_hash(row),
                                     "before": {key: row[key] for key in _HISTORY_FIELDS if key in row}}
                            changed = await self.db[inbox.COLLECTION].update_one(
                                {"_id": row["_id"], MARKER: {"$exists": False},
                                 "$expr": {"$eq": ["$$ROOT", {"$literal": row}]}},
                                {"$set": {MARKER: grant, "state": "retry", "attempts": 0,
                                          "next_attempt_at": at, "lease_until": at,
                                          "last_error_code": "incident_recovery_granted"}},
                                session=session,
                            )
                            if changed.matched_count != 1:
                                raise RecoveryError("plan_stale")
                        if not enabled():
                            raise RecoveryError("recovery_disabled")
                        if self.runtime_reader() != runtime:
                            raise RecoveryError("runtime_changed")
                        # Also observe a different newly winning binding outside
                        # the transaction snapshot. Canonical binding writers do
                        # not share a merchant mutex, so new-document phantoms
                        # after this check cannot be excluded here. The existing
                        # worker still revalidates ownership before dispatch.
                        fresh_binding = await _binding(self.db, owner)
                        if fresh_binding["_id"] != binding["_id"]:
                            raise RecoveryError("merchant_owner_mismatch", 403)
                        # This is a conditional WRITE, not a snapshot-only lease
                        # read: a successor epoch conflicts with the row updates.
                        await self._fence(lease, session)
            return {"applied_count": len(plan["rows"]), "plan_sha256": digest,
                    "state": "retry", "processing": "queued_for_existing_worker"}
        except RecoveryError:
            raise
        except (PyMongoError, TimeoutError):
            raise RecoveryError("recovery_conflict") from None
        finally:
            # Token+epoch prevents releasing a successor; cleanup failure leaves
            # only the ordinary 90-second lease to expire.
            with suppress(Exception):
                at = self.clock()
                await inbox._collection(self.db, inbox.LEASES_COLLECTION).update_one(
                    inbox._worker_filter(lease), {"$set": {"lease_until": at, "released_at": at}})


def make_incident_recovery_router(db, current_user) -> APIRouter:
    """Use dependencies already supplied by the canonical route factory."""
    router = APIRouter(prefix="/webhook-incident-recovery", tags=["salla"])

    async def run(action: str, response: Response, user: dict, body=None):
        response.headers["Cache-Control"] = "no-store"
        try:
            service = IncidentRecovery(db)
            method = getattr(service, action)
            return await method(user) if body is None else await method(user, body)
        except RecoveryError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code},
                                headers={"Cache-Control": "no-store"}) from None
        except Exception:
            raise HTTPException(503, detail={"code": "recovery_unavailable"},
                                headers={"Cache-Control": "no-store"}) from None

    @router.get("/config")
    async def config(response: Response, user=Depends(current_user)):
        return await run("config", response, user)

    @router.post("/dry-run")
    async def dry_run(body: dict, response: Response, user=Depends(current_user)):
        return await run("dry_run", response, user, body)

    @router.post("/apply")
    async def apply(body: dict, response: Response, user=Depends(current_user)):
        return await run("apply", response, user, body)

    return router
