"""Small raw-BSON read-ahead for one authorized refresh's immutable audit.

The complete decoded history remains required. Only the next four raw pages
may overlap; every page is decoded and validated in manifest order before a
financial operation can see the state. Other readers retain their serial path.
"""
import asyncio
import time

from bson import BSON
from bson.errors import InvalidBSON
from bson.raw_bson import RawBSONDocument
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
import pymongo
from pymongo.common import MAX_BSON_SIZE

from operational_demand_refresh import calculation_checkpoint, checkpoint, demand_context
from operational_financial_activity_runtime import join_task, prepare_in_thread


PREFETCH_PAGES = 4
MAX_PREFETCH_BYTES = PREFETCH_PAGES * MAX_BSON_SIZE
PAGE_READ_SECONDS = 15


def _collection(db, owner):
    from operational_balance_archive import AUDIT_PAGES
    from operational_read_runtime import _snapshot_session
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    context = demand_context()
    ordinary = (type(db) is AsyncIOMotorDatabase or type(db) is AcceptanceConfigDatabase
                and type(db._db) is AsyncIOMotorDatabase)
    if (not ordinary or context is None or context.db is not db or context.owner != owner
            or not getattr(context, "leased", False) or _snapshot_session(db)):
        return None
    collection = db[AUDIT_PAGES]
    # Do not add concurrency or codec capabilities to strict review/session
    # adapters. Their declared sequential readers are intentionally unchanged.
    if type(collection) is not AsyncIOMotorCollection or collection.codec_options.document_class is not dict:
        return None
    codec = collection.codec_options
    return collection.with_options(codec_options=codec.with_options(document_class=RawBSONDocument)), codec


async def _read_raw(collection, owner, ref):
    from operational_balance_archive import ArchiveError
    from operational_read_runtime import _DEADLINE
    calculation_checkpoint()
    remaining = PAGE_READ_SECONDS
    context = demand_context()
    for deadline in (_DEADLINE.get(), getattr(context, "read_deadline", None)):
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError("reconciliation audit read deadline expired")
    # CSOT is installed before Motor dispatch and also preserves an earlier
    # inherited PyMongo deadline. An asyncio timeout alone cannot bound the
    # executor/socket that the cancellation path must join.
    with pymongo.timeout(remaining):
        future = asyncio.ensure_future(collection.find_one({"_id": ref["id"], "owner_id": owner}))
        try:
            row = await asyncio.shield(future)
        except asyncio.CancelledError:
            # Finish the active executor read before releasing its owner lease.
            try:
                await join_task(future)
            except (asyncio.CancelledError, Exception):
                pass
            raise
    calculation_checkpoint()
    if row is None:
        return None
    if not isinstance(row, RawBSONDocument) or len(row.raw) > MAX_BSON_SIZE:
        raise ArchiveError("invalid reconciliation audit page encoding")
    return row.raw


def _validated_raw(data, codec, owner, start, ref):
    from operational_balance_archive import ArchiveError, validated_events
    calculation_checkpoint()
    try:
        page = BSON(data).decode(codec_options=codec) if data is not None else None
    except InvalidBSON as exc:
        raise ArchiveError("unreadable reconciliation audit page") from exc
    return validated_events(page, owner, start, ref)


async def read_audit_pages(db, owner, refs):
    """Return the complete ordered pages, or None for the unchanged serial path."""
    from operational_balance_archive import ArchiveError, VISIBLE_AUDIT_GATE_PAGES
    capability = _collection(db, owner)
    if capability is None:
        return None
    collection, codec = capability
    events = []
    for offset in range(0, len(refs), PREFETCH_PAGES):
        if offset % VISIBLE_AUDIT_GATE_PAGES == 0:
            await checkpoint()
        group = refs[offset:offset + PREFETCH_PAGES]
        for ref in group:
            calculation_checkpoint()
            if (not isinstance(ref, dict) or set(ref) != {"id", "count"}
                    or not isinstance(ref["id"], str) or type(ref["count"]) is not int or ref["count"] <= 0):
                raise ArchiveError("invalid reconciliation audit reference")
        tasks = [asyncio.create_task(_read_raw(collection, owner, ref)) for ref in group]
        buffered_bytes = 0
        try:
            for ref in group:
                task = tasks[0]
                data = await asyncio.shield(task)
                # The completed I/O no longer needs a cleanup owner. Drop its
                # task/result now, so validation is the last holder of this raw
                # page instead of retaining all four buffers until batch end.
                tasks.pop(0)
                task = None
                buffered_bytes += len(data) if data is not None else 0
                if buffered_bytes > MAX_PREFETCH_BYTES:
                    raise ArchiveError("reconciliation audit prefetch capacity")
                events.extend(await prepare_in_thread(_validated_raw, data, codec, owner, len(events), ref))
                data = None
        finally:
            data = task = None
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Even corruption, a revoked owner, a timeout or repeated caller
            # cancellation must join all already admitted reads in this group.
            await join_task(asyncio.gather(*tasks, return_exceptions=True))
            tasks.clear()
            task = data = None
        completed = offset + len(group)
        if completed % VISIBLE_AUDIT_GATE_PAGES == 0 or completed == len(refs):
            await checkpoint()
    return events
