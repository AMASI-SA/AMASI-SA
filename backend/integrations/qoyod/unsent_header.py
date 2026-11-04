"""Display-only snapshot of the canonical unsent-orders report."""
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from integrations.qoyod.eligible_orders import QOYOD_SYNC_START_DATE


def _key(tenant: str, owner: str) -> dict:
    return {"tenant": tenant, "orders_owner": owner, "from_date": QOYOD_SYNC_START_DATE}


async def read_header_count(db, tenant: str, owner: str) -> dict:
    snapshot = await db.qoyod_unsent_header_snapshots.find_one(
        {"_id": _key(tenant, owner)}, {"_id": 0, "count": 1, "as_of": 1},
    )
    return snapshot or {"count": None, "as_of": None}


async def refresh_header_count(db, tenant: str, owner: str) -> dict:
    # Resolve after the canonical queue/report installer has run.
    from integrations.qoyod.unsent_orders import UNSENT, list_unsent_orders

    report = await list_unsent_orders(
        db, user_id=tenant, orders_user_id=owner,
        from_date=QOYOD_SYNC_START_DATE, status=UNSENT, limit=1,
    )
    if report["scan_truncated"] is not False:
        return {**await read_header_count(db, tenant, owner), "partial": True}

    # The report start time survives cache hits; delayed reads cannot replace a
    # newer result. Use fixed precision for chronological string comparison.
    captured = datetime.fromisoformat(report["captured_at"])
    count = report["counts"][UNSENT]
    if report.get("ok") is not True or captured.tzinfo is None or type(count) is not int or count < 0:
        raise ValueError("invalid unsent-orders report")
    as_of = captured.astimezone(timezone.utc).isoformat(timespec="microseconds")
    collection = db.qoyod_unsent_header_snapshots
    query = {"_id": _key(tenant, owner), "as_of": {"$lt": as_of}}
    update = {"$set": {"count": count, "as_of": as_of}}
    try:
        await collection.update_one(query, update, upsert=True)
    except DuplicateKeyError:
        # A newer snapshot already exists or another refresh inserted first.
        await collection.update_one(query, update)
    return {**await read_header_count(db, tenant, owner), "partial": False}
