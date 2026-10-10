"""Fail closed on missing or contradictory canonical Salla status evidence.

These observations are local evidence, not a remote compare-and-set guarantee.
"""

ALIASES = {"قيد التنفيذ": "in_progress", "تم التنفيذ": "completed"}


def status_values(value):
    if isinstance(value, dict):
        values = set()
        for key in ("slug", "name", "customized", "original", "parent"):
            values.update(status_values(value.get(key)))
        return values
    text = str(value or "").strip().lower().replace(" ", "_")
    return {ALIASES.get(str(value).strip(), text)} if text else set()


def canonical_status(source):
    source = source or {}
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    provider = status_values(raw.get("status"))
    values = provider | status_values(source.get("order_status")) | status_values(source.get("order_status_slug"))
    if not provider or len(values) != 1:
        return None
    snapshot = source.get("g47_salla_snapshot") or {}
    if snapshot.get("requires_authoritative_refresh") or snapshot.get("component_pending"):
        return None
    return next(iter(values))


async def source_status(db, owner, number):
    source = await db["unified_orders"].find_one({"user_id": owner, "order_number": number})
    return canonical_status(source)


async def invalidate_shipping_if_blocked(db, owner, number):
    if await source_status(db, owner, number) in {"in_progress", "completed"}:
        return
    await db["order_review_workflows"].update_one(
        {"user_id": owner, "order_number": number},
        {"$set": {"carrier_label_ready": False, "salla_order_status": "unknown",
                  "salla_order_status_verified_at": None,
                  "carrier_label_url": None, "carrier_label_print_data": None}},
    )
    # Do not create an outbox for an order which has never completed assembly.
    await db["order_review_workflows"].update_one(
        {"user_id": owner, "order_number": number, "assembly_delivery": {"$exists": True}},
        {"$set": {"assembly_delivery.order_confirmed": False,
                  "assembly_delivery.state": "requires_attention",
                  "assembly_delivery.error_code": "assembly_canonical_status_blocked"}},
    )
