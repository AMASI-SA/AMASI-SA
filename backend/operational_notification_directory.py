"""Recipient-scoped notification metadata; never hydrate orders or task bodies."""
import base64
import json
from datetime import datetime, timezone

from fastapi import HTTPException

from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from operational_instruction_notifications import NOTIFICATIONS

SYSTEM = "__system__"
CREATOR_EVENTS = ["created", "assigned", "edit_sent", "approved", "rejected", "completed", "closed"]


def pipeline(merchant_id, actor_id, *, directory=False):
    stages = [{"$match": {"merchant_id": merchant_id, "recipient_id": actor_id}}]
    if directory:
        stages.append({"$project": {"id": 1, "instruction_id": 1, "event_type": 1,
            "sender_id": 1, "sender_name": 1, "read_at": 1, "created_at": 1, "metadata.actor_id": 1}})
    stages += [
        {"$lookup": {"from": ORDER_TRACKING_INSTRUCTIONS, "let": {"instruction": "$instruction_id"},
            "pipeline": [{"$match": {"user_id": merchant_id, "$expr": {"$eq": ["$id", "$$instruction"]}}},
                         {"$project": {"_id": 0, "created_by": 1, "created_by_name": 1}}], "as": "_source"}},
        {"$set": {"_sender": {"$ifNull": ["$sender_id", {"$cond": [
            {"$in": ["$event_type", CREATOR_EVENTS]},
            {"$ifNull": [{"$arrayElemAt": ["$_source.created_by", 0]}, SYSTEM]},
            {"$ifNull": ["$metadata.actor_id", SYSTEM]},
        ]}]}}},
        {"$lookup": {"from": "users", "let": {"sender": "$_sender"},
            "pipeline": [{"$match": {"$expr": {"$eq": ["$id", "$$sender"]},
                "$or": [{"id": merchant_id}, {"created_by": merchant_id}]}},
                {"$project": {"_id": 0, "id": 1}}], "as": "_sender_account"}},
        {"$set": {"_sender": {"$cond": [{"$gt": [{"$size": "$_sender_account"}, 0]}, "$_sender", SYSTEM]}}},
        {"$set": {"_sender": {"$cond": [{"$eq": ["$_sender", ""]}, SYSTEM, "$_sender"]},
            "_sort_at": {"$convert": {"input": "$created_at", "to": "date",
                "onError": datetime(1970, 1, 1, tzinfo=timezone.utc),
                "onNull": datetime(1970, 1, 1, tzinfo=timezone.utc)}}}},
    ]
    return stages


async def sender_names(db, merchant_id, ids):
    rows = await db.users.find({"id": {"$in": ids},
        "$or": [{"id": merchant_id}, {"created_by": merchant_id}]},
        {"_id": 0, "id": 1, "name": 1, "display_name": 1}).to_list(None)
    return {row["id"]: str(row.get("name") or row.get("display_name") or "الموظف") for row in rows}


async def sender_directory(db, *, merchant_id, actor_id):
    rows = await db[NOTIFICATIONS].aggregate(pipeline(merchant_id, actor_id, directory=True) + [
        {"$group": {"_id": "$_sender", "total": {"$sum": 1},
            "unread_count": {"$sum": {"$cond": [{"$eq": [{"$ifNull": ["$read_at", None]}, None]}, 1, 0]}}}},
    ]).to_list(None)
    names = await sender_names(db, merchant_id, [row["_id"] for row in rows if row["_id"] != SYSTEM])
    items = []
    for row in rows:
        sender = row["_id"]
        # Never disclose a name from another merchant or invent an employee for
        # legacy events which did not retain their true author.
        key = sender if sender in names else SYSTEM
        existing = next((item for item in items if item["id"] == key), None)
        if existing:
            existing["total"] += row["total"]; existing["unread_count"] += row["unread_count"]
        else:
            items.append({"id": key, "name": names.get(key, "تحديثات النظام"),
                          "total": row["total"], "unread_count": row["unread_count"]})
    items.sort(key=lambda item: (item["name"], item["id"]))
    return {"directory_only": True, "items": items, "unread_count": sum(row["unread_count"] for row in items)}


def _decode_cursor(cursor):
    if not cursor:
        return None
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, str) for item in value):
            raise ValueError()
        at = datetime.fromisoformat(value[0])
        if not at.tzinfo or not value[1] or len(value[1]) > 128:
            raise ValueError()
        return at, value[1]
    except Exception:
        raise HTTPException(422, detail={"code": "notification_cursor_invalid"}) from None


async def sender_page(db, *, merchant_id, actor_id, sender_id, read_state="all", cursor=None):
    names = await sender_names(db, merchant_id, [sender_id])
    if sender_id != SYSTEM and sender_id not in names:
        raise HTTPException(404, detail={"code": "notification_sender_not_found"})
    stages = pipeline(merchant_id, actor_id)
    stages.append({"$match": {"_sender": sender_id}})
    if read_state == "unread":
        stages.append({"$match": {"read_at": None}})
    elif read_state == "read":
        stages.append({"$match": {"read_at": {"$ne": None}}})
    elif read_state != "all":
        raise HTTPException(422, detail={"code": "notification_read_state_invalid"})
    boundary = _decode_cursor(cursor)
    if boundary:
        at, identity = boundary
        stages.append({"$match": {"$or": [{"_sort_at": {"$lt": at}}, {"_sort_at": at, "id": {"$lt": identity}}]}})
    rows = await db[NOTIFICATIONS].aggregate(stages + [
        {"$sort": {"_sort_at": -1, "id": -1}}, {"$limit": 11},
        {"$project": {"_id": 0, "id": 1, "instruction_id": 1, "order_number": 1,
            "title": 1, "message": 1, "created_at": 1, "read_at": 1, "priority": 1,
            "event_type": 1, "metadata.product_name": 1, "metadata.task_id": 1, "_sort_at": 1}},
    ]).to_list(11)
    page = rows[:10]
    next_cursor = None
    if len(rows) > 10:
        at = page[-1]["_sort_at"]
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)  # BSON dates are UTC, even with a naive Motor decoder.
        next_cursor = base64.urlsafe_b64encode(json.dumps([at.isoformat(), page[-1]["id"]]).encode()).decode().rstrip("=")
    for row in page:
        row.pop("_sort_at", None)
        if isinstance(row.get("created_at"), datetime):
            at = row["created_at"]
            row["created_at"] = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()
        row["read"] = bool(row.pop("read_at", None))
        row["sender_id"] = sender_id
        row["sender_name"] = names.get(sender_id, "تحديثات النظام")
    return {"sender_id": sender_id, "items": page, "next_cursor": next_cursor}
