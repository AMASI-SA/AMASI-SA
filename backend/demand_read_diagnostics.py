"""Bounded owner diagnostics for disposable reads, never financial authority."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import math
import re
from threading import Lock
import time


PHASES = frozenset({"state_read_ms", "state_decode_ms", "state_decode_cpu_ms", "report_build_ms",
    "report_build_cpu_ms", "report_copy_cpu_ms", "forecast_sources_ms", "order_labels_ms",
    "response_encode_ms", "response_encode_cpu_ms", "response_prepare_ms"})
VIEWS = frozenset({"all", "summary", "providers", "couriers", "drivers", "employees", "accounts",
                  "suppliers", "advertising", "other", "expected", "forecast"})
_CURRENT = ContextVar("owned_demand_read_diagnostics", default=None)
_KEY = re.compile(r"[a-f0-9]{64}\Z")
_GENERATION = re.compile(r"[a-f0-9]{32}\Z")
IO_SECONDS = 0.2
MAX_TRACE_BYTES = 3072


def _number(value):
    return type(value) in (int, float) and 0 <= value <= 10 ** 12 and math.isfinite(value)


class _Record:
    def __init__(self):
        self.values, self.lock, self.closed = {}, Lock(), False

    def add(self, name, value):
        if name not in PHASES or not _number(value):
            return
        with self.lock:
            if not self.closed:
                self.values[name] = min(10 ** 12, self.values.get(name, 0) + value)


@contextmanager
def capture(target, *, enabled=True):
    record = _Record() if enabled and type(target) is dict else None
    token = _CURRENT.set(record)
    try:
        yield
    finally:
        _CURRENT.reset(token)
        if record is not None:
            with record.lock:
                record.closed = True
                target.update({key: round(value, 3) for key, value in record.values.items()})


@contextmanager
def phase(name, *, cpu=False):
    record = _CURRENT.get()
    clock = time.thread_time if cpu else time.monotonic
    try:
        started = clock() if record is not None and name in PHASES else None
    except Exception:
        started = None
    try:
        yield
    finally:
        if started is not None:
            try:
                record.add(name, max(0, clock() - started) * 1000)
            except Exception:
                pass


def thread_call(name, function):
    if _CURRENT.get() is None:
        return function
    @wraps(function)
    def measured(*args, **kwargs):
        with phase(name, cpu=True):
            return function(*args, **kwargs)
    return measured


def _collection(db):
    from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    from operational_read_runtime import _snapshot_session
    from demand_read_store import JOBS
    ordinary = (type(db) is AsyncIOMotorDatabase or type(db) is AcceptanceConfigDatabase
                and type(db._db) is AsyncIOMotorDatabase)
    if not ordinary or _snapshot_session(db):
        return None
    collection = db[JOBS]
    return collection if type(collection) is AsyncIOMotorCollection else None


async def _io(operation):
    # Set the driver deadline before Motor dispatch. Cancellation joins the
    # actual finite read/write, not merely an abandoned asyncio waiter.
    import pymongo
    from operational_financial_activity_runtime import join_task
    with pymongo.timeout(IO_SECONDS):
        future = asyncio.ensure_future(operation())
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            try:
                await join_task(future)
            except (asyncio.CancelledError, Exception):
                pass
            raise


def _owner_spec(value, key, generation):
    from demand_read_store import normalize_spec, projection_key
    spec = normalize_spec(value)
    if (spec["actor_id"] != spec["owner_id"] or not spec["family"].startswith("operational_")
            or not isinstance(key, str) or not _KEY.fullmatch(key)
            or not isinstance(generation, str) or not _GENERATION.fullmatch(generation)
            or projection_key(spec) != key):
        return None
    return spec


async def save_owner_trace(db, claim, metrics):
    """One small field in the exact claimed job; all failures are observational."""
    try:
        from demand_read_store import CODE_VERSION, safe_metrics
        spec = _owner_spec(claim.get("spec"), claim.get("_id"), claim.get("generation"))
        safe = safe_metrics(metrics)
        collection = _collection(db)
        if (spec is None or safe is None or collection is None or claim.get("code_version") != CODE_VERSION
                or (claim.get("owner_id"), claim.get("actor_id"), claim.get("family"))
                != (spec["owner_id"], spec["actor_id"], spec["family"])
                or safe["family"] != spec["family"]):
            return False
        view = spec.get("filters", {}).get("view", "all") if spec["family"] == "operational_report" else None
        revision = (spec.get("source_revision") or {}).get("revision")
        if (view is not None and view not in VIEWS
                or type(revision) is not int or not 0 <= revision <= 2 ** 53 - 1):
            return False
        trace = {"schema_version": 1, "generation": claim["generation"], "family": spec["family"],
                 "view": view, "source_revision": revision, "metrics": safe}
        if len(json.dumps(trace, allow_nan=False).encode()) > MAX_TRACE_BYTES:
            return False
        changed = await _io(lambda: collection.update_one({"_id": claim["_id"],
            "owner_id": spec["owner_id"], "actor_id": spec["actor_id"], "code_version": CODE_VERSION,
            "generation": claim["generation"], "lease_token": claim["lease_token"]},
            [{"$set": {"read_diagnostics": {"$literal": trace}}},
             {"$set": {"read_diagnostics.captured_at": "$$NOW"}}]))
        return changed.matched_count == 1
    except Exception:
        return False


async def save_delivery_trace(db, value, key, generation, metrics):
    try:
        from demand_read_store import CODE_VERSION
        spec = _owner_spec(value, key, generation)
        collection = _collection(db)
        allowed = {"response_encode_ms", "response_encode_cpu_ms", "response_prepare_ms"}
        if (spec is None or collection is None or not metrics or not set(metrics) <= allowed
                or any(not _number(number) for number in metrics.values())):
            return False
        fields = {"read_diagnostics.metrics." + key: round(value, 3) for key, value in metrics.items()}
        fields["read_diagnostics.response_captured_at"] = "$$NOW"
        changed = await _io(lambda: collection.update_one({"_id": key,
            "owner_id": spec["owner_id"], "actor_id": spec["actor_id"], "code_version": CODE_VERSION,
            "status": "ready", "generation": generation, "read_diagnostics.generation": generation,
            "$expr": {"$gt": ["$ready_until", "$$NOW"]}}, [{"$set": fields}]))
        return changed.matched_count == 1
    except Exception:
        return False


def _stamp(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()


async def read_owner_trace(db, owner, key, generation):
    unavailable = {"available": False}
    try:
        from demand_read_store import CODE_VERSION, FAMILIES, safe_metrics
        if (not isinstance(owner, str) or not owner or not isinstance(key, str) or not _KEY.fullmatch(key)
                or not isinstance(generation, str) or not _GENERATION.fullmatch(generation)):
            return unavailable
        collection = _collection(db)
        if collection is None:
            return unavailable
        row = await _io(lambda: collection.find_one({"_id": key, "owner_id": owner, "actor_id": owner,
            "code_version": CODE_VERSION, "generation": generation, "read_diagnostics.generation": generation,
            "$expr": {"$gt": ["$retire_at", "$$NOW"]}}, {"_id": 0, "family": 1, "read_diagnostics": 1}))
        trace = row.get("read_diagnostics") if row else None
        if not isinstance(trace, dict):
            return unavailable
        metrics = safe_metrics(trace.get("metrics"))
        stamp = _stamp(trace.get("captured_at"))
        if (type(trace.get("schema_version")) is not int or trace["schema_version"] != 1
                or trace.get("generation") != generation or not stamp
                or metrics is None or not row.get("family") == trace.get("family") == metrics["family"]
                or trace.get("family") not in FAMILIES or not trace["family"].startswith("operational_")
                or trace.get("view") not in VIEWS | {None}
                or type(trace.get("source_revision")) is not int or not 0 <= trace["source_revision"] <= 2 ** 53 - 1):
            return unavailable
        return {"available": True, "schema_version": 1, "family": trace["family"], "view": trace["view"],
                "source_revision": trace["source_revision"], "captured_at": stamp, "metrics": metrics,
                "response_captured_at": _stamp(trace.get("response_captured_at"))}
    except Exception:
        return unavailable
