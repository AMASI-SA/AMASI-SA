"""Observe preparation progress for the author of an urgent/nonblocking note.

This observer never updates pieces, workflow, inventory or instruction status.
Snapshots and a pending event marker survive worker restarts/provider outages.
"""
import hashlib
import uuid
from datetime import datetime, timezone

from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS, instruction_targets
from operational_instruction_notifications import emit_instruction_notification

PROGRESS = "operational_instruction_progress_v1"


async def refresh_instruction_progress(db, instruction):
    if instruction.get("action_type") not in {"general", "urgent_preparation", "delivery_instruction"}:
        return
    owner = instruction["user_id"]
    creator = instruction.get("created_by")
    if not creator:
        return
    workflow = await db["order_review_workflows"].find_one({"user_id": owner, "order_number": instruction["order_number"]}) or {}
    pieces = await db["mezan_preparation_pieces_v1"].find({"user_id": owner, "order_number": instruction["order_number"],
        "experiment_archived_at": {"$in": [None, ""]}}).to_list(10000)
    from order_tracking_notes_routes import _piece_stage
    for piece in pieces:
        pid = str(piece.get("piece_id") or "")
        if not pid or not instruction_targets(instruction, piece_id=pid, order_item_id=str(piece.get("order_item_id") or "")):
            continue
        stage = _piece_stage(piece, workflow)
        key = hashlib.sha256(f"{owner}:{instruction['id']}:{pid}".encode()).hexdigest()
        state = {"stage": stage, "status": piece.get("status"), "execution_status": piece.get("execution_status"),
            "assembly_status": piece.get("assembly_status"), "assignee_id": piece.get("responsible_employee_id")}
        saved = await db[PROGRESS].find_one({"_id": key})
        if stage not in instruction.get("target_stages", []) and not saved:
            continue  # a future-stage note must not announce work beforehand
        if not saved:
            await db[PROGRESS].update_one({"_id": key}, {"$setOnInsert": {"state": state, "instruction_id": instruction["id"], "merchant_id": owner}}, upsert=True)
            continue
        if not saved.get("pending") and saved.get("state") != state:
            pending = {"id": uuid.uuid4().hex, "state": state, "at": datetime.now(timezone.utc)}
            await db[PROGRESS].update_one({"_id": key, "state": saved["state"], "pending": {"$exists": False}}, {"$set": {"pending": pending}})
            saved = await db[PROGRESS].find_one({"_id": key})
        pending = saved.get("pending")
        if not pending:
            continue
        employee = await db["users"].find_one({"id": pending["state"].get("assignee_id"), "created_by": owner}, {"name": 1}) or {}
        await emit_instruction_notification(db, merchant_id=owner, instruction={**instruction,
            "notification_revision": pending["id"], "notification_metadata": {
                "piece_id": pid, "order_item_id": piece.get("order_item_id"),
                "product_name": piece.get("product_name") or piece.get("item_name") or "منتج",
                "employee_name": employee.get("name") or "", "previous": saved["state"],
                "current": pending["state"], "observed_at": pending["at"],
            }}, event_type="progress", recipient_ids={creator})
        await db[PROGRESS].update_one({"_id": key, "pending.id": pending["id"]}, {"$set": {"state": pending["state"]}, "$unset": {"pending": ""}})


async def progress_sweep(db, after=""):
    rows = await db[ORDER_TRACKING_INSTRUCTIONS].find({"status": "active", "thread_protocol": 1,
        "action_type": {"$in": ["general", "urgent_preparation", "delivery_instruction"]}, "id": {"$gt": after}}).sort("id", 1).to_list(25)
    for row in rows:
        await refresh_instruction_progress(db, row)
    return rows[-1]["id"] if rows else ""
