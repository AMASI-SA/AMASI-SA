"""Bounded instruction-account lookup using the existing merchant authority."""
from order_tracking_notes import text
from review_session_fence import merchant_for_actor


async def accounts_for_merchant(db, merchant_id, account_ids, *, active_only=True):
    """Resolve only requested accounts; task membership never establishes tenancy.

    The shared resolver preserves owner identity, then trusted ``created_by``,
    then the authoritative Employees V2 link. Historical participant names may
    include inactive accounts; that is not permission to send them new alerts.
    """
    ids = sorted({text(value) for value in account_ids} - {""})
    if not ids:
        return []
    query = {"id": {"$in": ids}}
    if active_only:
        query.update({"disabled": {"$ne": True}, "is_active": {"$ne": False},
            "deleted_at": {"$in": [None, ""]}})
    rows = await db["users"].find(query, {
        "_id": 0, "id": 1, "name": 1, "role": 1, "is_owner": 1, "created_by": 1,
        "extra_permissions": 1, "denied_permissions": 1,
    }).to_list(5000)
    scoped = []
    for row in rows:
        if await merchant_for_actor(db, row) == merchant_id:
            scoped.append(row)
    return scoped
