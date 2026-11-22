"""Read-only, bounded human notification previews for the My Products page.

This consumes existing private event projections. It never backfills legacy
notifications, hydrates orders/products, reconciles work or acknowledges reads.
"""
from datetime import datetime, timezone
import re

from fastapi import HTTPException
from pymongo.errors import ExecutionTimeout

from operational_instruction_conversations import CONVERSATIONS, EVENTS
from operational_instruction_notifications import NOTIFICATIONS


PREVIEW_LIMIT = 2
PREVIEW_MAX_CHARS = 240
IDENTIFIER = r"^[^\s\x00-\x1f\x7f](?:[^\x00-\x1f\x7f]{0,198}[^\s\x00-\x1f\x7f])?$"


def preview_pipeline(merchant_id, actor_id):
    """Filter every authorization/read condition before selecting two events.

    The first indexed match excludes read history. The cursor remains ordered
    across the small authorization lookups, so an ineligible recent event does
    not displace an older eligible one. No full message enters this projection.
    """
    return [
        {"$match": {"merchant_id": merchant_id, "recipient_id": actor_id,
            "read_at": None, "silent": False,
            "author_id": {"$type": "string", "$regex": IDENTIFIER, "$nin": ["", "__system__", actor_id]},
            "kind": {"$in": ["instruction", "message", "progress", "completed"]},
            "created_at": {"$type": "date"},
            **{field: {"$type": "string", "$regex": IDENTIFIER}
               for field in ("event_id", "instruction_id", "conversation_id")}}},
        {"$sort": {"created_at": -1, "event_id": -1}},
        {"$project": {"_id": 0, "event_id": 1, "instruction_id": 1, "conversation_id": 1,
            "source_notice_id": 1, "author_id": 1, "kind": 1, "created_at": 1, "preview": 1}},
        {"$lookup": {"from": CONVERSATIONS, "localField": "conversation_id", "foreignField": "_id",
            "let": {"instruction": "$instruction_id", "author": "$author_id"}, "pipeline": [
                {"$match": {"merchant_id": merchant_id, "$expr": {"$and": [
                    {"$eq": ["$instruction_id", "$$instruction"]},
                    {"$or": [
                        {"$and": [{"$eq": ["$creator_id", actor_id]}, {"$eq": ["$assignee_id", "$$author"]}]},
                        {"$and": [{"$eq": ["$assignee_id", actor_id]}, {"$eq": ["$assignee_active", True]},
                            {"$eq": ["$creator_id", "$$author"]}]},
                    ]},
                ]}}},
                {"$project": {"_id": 0, "order_number": 1, "sender_name": {"$cond": [
                    {"$eq": ["$creator_id", actor_id]}, "$assignee_name", "$creator_name"]}}},
            ], "as": "_conversation"}},
        {"$unwind": "$_conversation"},
        # Legacy clients mark the source notice read without updating EVENTS.
        # A foreign recipient/merchant/other instruction with a reused ID must
        # never suppress this actor's independent unread event.
        {"$lookup": {"from": NOTIFICATIONS,
            "let": {"notice": {"$ifNull": ["$source_notice_id", ""]}, "instruction": "$instruction_id"},
            "pipeline": [
                {"$match": {"merchant_id": merchant_id, "recipient_id": actor_id, "read_at": {"$ne": None},
                    "$expr": {"$and": [{"$ne": ["$$notice", ""]}, {"$eq": ["$id", "$$notice"]},
                        {"$eq": ["$instruction_id", "$$instruction"]}]}}},
                {"$project": {"_id": 1}}, {"$limit": 1},
            ], "as": "_read_source"}},
        {"$match": {"_read_source.0": {"$exists": False}}},
        {"$set": {
            "preview": {"$trim": {"input": {"$cond": [{"$eq": [{"$type": "$preview"}, "string"]}, "$preview", ""]}}},
            "_sender_name": {"$trim": {"input": {"$cond": [
                {"$eq": [{"$type": "$_conversation.sender_name"}, "string"]}, "$_conversation.sender_name", ""]}}},
            "_order_number": {"$convert": {"input": "$_conversation.order_number", "to": "string", "onError": "", "onNull": ""}},
        }},
        {"$match": {"preview": {"$ne": ""}, "_order_number": {"$regex": r"^[^\x00-\x1f\x7f]{1,80}$"}}},
        {"$limit": PREVIEW_LIMIT},
        {"$project": {"_id": 0, "event_id": 1, "instruction_id": 1, "conversation_id": 1,
            "source_notice_id": 1, "kind": 1, "created_at": 1,
            "preview": {"$substrCP": ["$preview", 0, PREVIEW_MAX_CHARS]},
            "order_number": "$_order_number",
            "sender": {"id": "$author_id", "name": {"$substrCP": [
                {"$cond": [{"$ne": ["$_sender_name", ""]}, "$_sender_name", "الموظف"]}, 0, 160]}}}},
    ]


async def unread_preview(db, *, merchant_id, actor_id):
    try:
        rows = await db[EVENTS].aggregate(preview_pipeline(merchant_id, actor_id),
            maxTimeMS=1500, allowDiskUse=False, batchSize=PREVIEW_LIMIT).to_list(PREVIEW_LIMIT)
    except ExecutionTimeout:
        # A temporarily unavailable preview is not an authoritative empty inbox.
        raise HTTPException(503, detail={"code": "notification_preview_temporarily_unavailable"}) from None
    for row in rows:
        notice = row.pop("source_notice_id", None)
        if isinstance(notice, str) and re.fullmatch(IDENTIFIER, notice):
            row["notice_id"] = notice
        at = row["created_at"]
        if isinstance(at, datetime):
            row["created_at"] = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat()
    return {"unread_preview": True, "schema_version": 1, "items": rows}


async def ensure_indexes(db):
    # Registered with the existing startup index task, never on a preview GET.
    await db[EVENTS].create_index([("merchant_id", 1), ("recipient_id", 1), ("read_at", 1),
        ("silent", 1), ("created_at", -1), ("event_id", -1)], name="recipient_unread_preview")
    await db[NOTIFICATIONS].create_index([("merchant_id", 1), ("recipient_id", 1), ("id", 1)],
        name="recipient_notice_reference")
