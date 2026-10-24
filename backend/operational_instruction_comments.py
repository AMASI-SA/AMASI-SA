"""Private per-piece conversation between the request author and its assignee."""
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from operational_instruction_notifications import TASKS, ensure_instruction_tasks, emit_instruction_notification
from operational_push import notification_actor

COMMENTS = "operational_instruction_comments_v1"


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)
    client_request_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


async def conversation_context(db, actor, instruction_id, task_id):
    from review_session_fence import merchant_for_actor
    merchant = await merchant_for_actor(db, actor)
    instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one({"user_id": merchant, "id": instruction_id}, {"_id": 0})
    if not instruction:
        raise HTTPException(404, detail={"code": "instruction_not_found"})
    tasks = await ensure_instruction_tasks(db, merchant, instruction)
    task = next((row for row in tasks if row["id"] == task_id), None)
    if not task or actor["id"] not in {instruction.get("created_by"), task.get("assignee_id")}:
        raise HTTPException(404, detail={"code": "instruction_task_not_found"})
    return merchant, instruction, task


def public_comment(row):
    return {key: row.get(key) for key in ("id", "body", "author_id", "author_name", "created_by_name", "created_at")}


async def notify_comment(db, row):
    instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one({"user_id": row["merchant_id"], "id": row["instruction_id"]}, {"_id": 0})
    task = await db[TASKS].find_one({"merchant_id": row["merchant_id"], "instruction_id": row["instruction_id"], "id": row["task_id"]})
    if not instruction or not task:
        return
    targets = {instruction.get("created_by"), task.get("assignee_id")} - {None, "", row["author_id"]}
    await emit_instruction_notification(db, merchant_id=row["merchant_id"],
        instruction={**instruction, "notification_revision": row["id"]}, event_type="comment", recipient_ids=targets)
    await db[COMMENTS].update_one({"_id": row["id"]}, {"$set": {"notification_pending": False}})


def make_instruction_comments_router(db, current_user):
    router = APIRouter(prefix="/order-tracking-notes", tags=["order-tracking-notes"])

    @router.get("/instructions/{instruction_id}/tasks/{task_id}/comments")
    async def list_comments(instruction_id: str, task_id: str, before: str = "", user=Depends(current_user)):
        user = await notification_actor(db, user)
        merchant, _, _ = await conversation_context(db, user, instruction_id, task_id)
        query = {"merchant_id": merchant, "instruction_id": instruction_id, "task_id": task_id}
        if before:
            anchor = await db[COMMENTS].find_one({**query, "id": before})
            if not anchor:
                raise HTTPException(422, detail={"code": "invalid_comment_cursor"})
            query["$or"] = [{"created_at": {"$lt": anchor["created_at"]}}, {"created_at": anchor["created_at"], "id": {"$lt": before}}]
        rows = await db[COMMENTS].find(query).sort([("created_at", -1), ("id", -1)]).to_list(101)
        page = rows[:100]
        return {"items": [public_comment(row) for row in reversed(page)], "next_cursor": page[-1]["id"] if len(rows) > 100 else None}

    @router.post("/instructions/{instruction_id}/tasks/{task_id}/comments")
    async def create_comment(instruction_id: str, task_id: str, payload: CommentCreate, user=Depends(current_user)):
        user = await notification_actor(db, user)
        merchant, instruction, task = await conversation_context(db, user, instruction_id, task_id)
        body = payload.body.strip()
        if not body:
            raise HTTPException(422, detail={"code": "comment_body_required"})
        identity = hashlib.sha256(f"{merchant}:{instruction_id}:{task_id}:{user['id']}:{payload.client_request_id}".encode()).hexdigest()
        row = {"_id": identity, "id": identity, "merchant_id": merchant,
            "instruction_id": instruction_id, "task_id": task_id, "body": body,
            "author_id": user["id"], "author_name": str(user.get("name") or "موظف"),
            "created_by_name": str(user.get("name") or "موظف"), "created_at": datetime.now(timezone.utc), "notification_pending": True}
        await db[COMMENTS].update_one({"_id": identity}, {"$setOnInsert": row}, upsert=True)
        saved = await db[COMMENTS].find_one({"_id": identity})
        if saved["body"] != body:
            raise HTTPException(409, detail={"code": "comment_request_reused"})
        # No task, approval, hold, inventory or workflow state is changed here.
        try:
            await notify_comment(db, saved)
        except Exception:
            pass  # durable pending marker is retried by worker
        return {"ok": True, "comment": public_comment(saved)}

    return router


async def retry_comment_notifications(db):
    rows = await db[COMMENTS].find({"notification_pending": True}).sort("created_at", 1).to_list(50)
    for row in rows:
        await notify_comment(db, row)
