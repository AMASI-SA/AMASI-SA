"""Private instruction conversations, independent of business acknowledgments.

The original notification API and its documents remain compatible. This opt-in
projection keeps one thread per instruction/assignee, and observes committed
operational facts without writing pieces, workflows, invoices or task status.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from order_tracking_notes import BLOCKING_ACTION_TYPES, ORDER_TRACKING_INSTRUCTIONS, instruction_targets, text
from operational_instruction_notifications import ACTIVE, NOTIFICATIONS, TASKS

CONVERSATIONS = "operational_instruction_conversations_v1"
EVENTS = "operational_instruction_conversation_events_v1"
BOOKMARKS = "operational_instruction_conversation_backfill_v1"
COMMENTS = "operational_instruction_comments_v1"
PIECES = "mezan_preparation_pieces_v1"
SYSTEM = "__system__"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
TERMINAL_LABELS = {"completed": "اكتمل المطلوب", "closed": "أُغلقت التعليمات", "cancelled": "أُلغيت التعليمات",
    "rejected": "رُفضت التعليمات", "archived": "أُرشفت التعليمات"}
PIECE_SNAPSHOT_FIELDS = (
    "piece_id", "order_item_id", "product_name", "item_name", "selected_image_url", "resolved_image_url",
    "image_url", "product_options_snapshot", "specifications_snapshot", "assigned_at", "file_number",
    "file_piece_number", "responsible_employee_id", "status", "execution_status", "supplier_dispatch_status",
    "received_at", "preparation_receipt_status", "preparation_received_at", "preparation_received_from_employee_id",
    "assembly_status", "assembly_ready_at", "remaining_service_count", "service_plan_status",
)
TASK_SNAPSHOT_FIELDS = ("id", "piece_id", "order_item_id", "product_name", "assignee_id", "stage", "status", "current",
    "result_note", "stop_acknowledged_at", "stop_acknowledged_by", "stop_note", "submitted_at", "submitted_by",
    "submitted_revision", "rejection_note", "acknowledged_at", "acknowledged_by", "confirmation")


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return EPOCH
    if not isinstance(value, datetime):
        return EPOCH
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def identity(*values):
    return hashlib.sha256(":".join(map(str, values)).encode()).hexdigest()


def conversation_id(merchant_id, instruction_id, assignee_id):
    return identity(merchant_id, instruction_id, assignee_id)


class ConversationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=128)
    last_event_id: str = Field(default="", max_length=128)
    event_ids: list[str] | None = Field(default=None, max_length=200)
    notice_id: str | None = Field(default=None, max_length=128)


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=2000)
    client_request_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def completion_for(piece, stage):
    """Use durable operation fields, never a draft scan or stage movement alone."""
    if text(piece.get("status")) in {"blocked", "cancelled"}:
        return None
    if stage in {"preparation", "supplier_dispatch", "supplier_receiving"}:
        from preparation_piece_operations import _preparation_incomplete_services
        if (piece.get("supplier_dispatch_status") == "received"
                and piece.get("execution_status") == "received_from_supplier"
                and stamp(piece.get("received_at")) > EPOCH
                and piece.get("remaining_service_count", 0) == 0
                and not _preparation_incomplete_services(piece)):
            return "تم الاستلام من المورد"
        # A subsequent committed handoff also proves preparation finished.
        if (piece.get("preparation_receipt_status") == "received"
                and stamp(piece.get("preparation_received_at")) > EPOCH):
            return "اكتمل تجهيز المنتج"
        if piece.get("assembly_status") == "ready" and stamp(piece.get("assembly_ready_at")) > EPOCH:
            return "المنتج جاهز"  # Direct preparation can finish without a supplier route.
    if stage == "preparation_receiving" and (piece.get("preparation_receipt_status") == "received"
            and stamp(piece.get("preparation_received_at")) > EPOCH):
        return "تم الاستلام من موظف التجهيز"
    if stage == "assembly_labeling" and (piece.get("assembly_status") == "ready"
            and stamp(piece.get("assembly_ready_at")) > EPOCH):
        return "المنتج جاهز"
    return None


def receipt_transfer_completed(piece, assignee, stage):
    """Approval may transfer custody; ordinary reassignment is not completion."""
    return bool(stage in {"preparation", "supplier_dispatch", "supplier_receiving"}
        and piece.get("reassignment_reason") == "supplier_receipt"
        and piece.get("previous_responsible_employee_id") == assignee
        and text(piece.get("reassignment_invoice_id")) and text(piece.get("reassignment_session_id"))
        and stamp(piece.get("reassigned_at")) == stamp(piece.get("received_at")) > EPOCH
        and completion_for(piece, stage))


def _snapshot_instruction(instruction, tasks):
    piece_ids = {task["piece_id"] for task in tasks}
    item_ids = {text(task.get("order_item_id")) for task in tasks if text(task.get("order_item_id"))}
    fields = ("id", "order_number", "scope", "action_type", "action_label", "priority", "note", "target_stages",
        "target_stage_labels", "enforcement", "required_action", "approval_required", "status", "created_by",
        "created_by_name", "created_at", "task_protocol", "thread_protocol", "edit_flow", "edit_phase", "edit_revision")
    result = {key: instruction[key] for key in fields if key in instruction}
    target_ids = sorted(item_ids if instruction.get("scope") == "item" else piece_ids)
    result.update(tasks=tasks, target_piece_ids=sorted(piece_ids), target_ids=target_ids,
        target_id=next(iter(target_ids), None))
    result["proposed_changes"] = [change for change in instruction.get("proposed_changes", [])
        if not change.get("order_item_id") or change["order_item_id"] in item_ids]
    for field in ("edit_baseline", "edit_requested"):
        result[field] = {key: value for key, value in (instruction.get(field) or {}).items() if key in item_ids}
    return result


async def reconcile_instruction(db, instruction, *, pieces=None, workflow=None, tasks=None, historical_recipients=()):
    """Called by existing event/progress hooks, or once on explicit thread open."""
    merchant, iid, creator = instruction["user_id"], instruction["id"], text(instruction.get("created_by"))
    if not creator:
        return []
    if pieces is None:
        pieces = await db[PIECES].find({"user_id": merchant, "order_number": instruction["order_number"]},
            {"_id": 0, "image_b64": 0}).to_list(5000)
    if tasks is None:
        tasks = await db[TASKS].find({"merchant_id": merchant, "instruction_id": iid}, {"_id": 0}).to_list(5000)
    previous = await db[CONVERSATIONS].find({"merchant_id": merchant, "instruction_id": iid}).to_list(5000)
    previous_by_actor = {row["assignee_id"]: row for row in previous}
    by_piece = {text(piece.get("piece_id")): piece for piece in pieces}
    tasks = list(tasks)
    historical_recipients = set(historical_recipients) - {creator, ""}
    historical_recipients.update(row["assignee_id"] for row in previous if row.get("terminal_history_authorized"))
    if not previous:
        old_delivery = await db[NOTIFICATIONS].find({"merchant_id": merchant, "instruction_id": iid,
            "event_type": {"$in": ["created", "assigned", "edit_sent"]}}, {"recipient_id": 1}).limit(5000).to_list(5000)
        historical_recipients.update(text(source.get("recipient_id")) for source in old_delivery)
        historical_recipients -= {creator, ""}
    # Pre-upgrade tasks may already belong to the employee who approved the
    # receipt. Rebuild only the earlier recipient's proven receipt references;
    # these are projection records, never restored business task assignments.
    for assignee in historical_recipients:
        for piece in pieces:
            pid = text(piece.get("piece_id"))
            if not pid or not instruction_targets(instruction, piece_id=pid, order_item_id=text(piece.get("order_item_id"))):
                continue
            stage = next((stage for stage in instruction.get("target_stages", [])
                if receipt_transfer_completed(piece, assignee, stage)), None)
            if stage and not any(task.get("assignee_id") == assignee and task.get("piece_id") == pid for task in tasks):
                tasks.append({"id": identity(merchant, iid, pid, stage), "piece_id": pid,
                    "order_item_id": text(piece.get("order_item_id")), "product_name": text(piece.get("product_name")),
                    "assignee_id": assignee, "stage": stage, "status": "pending", "current": False})
    # The pending business task may already have transferred to the receiver.
    # Its former participant is retained only with the committed custody proof.
    for prior in previous:
        for old in prior.get("tasks", []):
            if receipt_transfer_completed(by_piece.get(old["piece_id"], {}), prior["assignee_id"], old.get("stage")):
                if not any(task["id"] == old["id"] and task.get("assignee_id") == prior["assignee_id"] for task in tasks):
                    tasks.append(old)
    grouped = {}
    for task in tasks:
        assignee = text(task.get("assignee_id"))
        piece = by_piece.get(task.get("piece_id"))
        if not assignee or assignee == creator:
            continue
        terminal_history = instruction.get("status") in TERMINAL_LABELS and assignee in historical_recipients
        # A pending reassignment revokes the former employee's access. Moving
        # to a later stage after their own completed work preserves history.
        if piece and task.get("stage") in {"preparation", "supplier_dispatch", "supplier_receiving", "preparation_receiving"}:
            if (text(piece.get("responsible_employee_id")) != assignee
                    and text(piece.get("preparation_received_from_employee_id")) != assignee
                    and not receipt_transfer_completed(piece, assignee, task.get("stage"))):
                if not terminal_history:
                    continue
                task = {**task, "_history_only": True}
        if terminal_history and not piece:
            task = {**task, "_history_only": True}
        if not task.get("current", True) and not piece and previous_by_actor.get(assignee, {}).get("state") != "completed" and not terminal_history:
            continue
        established = any(old.get("id") == task["id"] for old in previous_by_actor.get(assignee, {}).get("tasks", []))
        if not (task.get("current", True) or established or terminal_history or task.get("status") == "submitted"
                or task.get("acknowledged_at") or completion_for(piece or {}, task.get("stage"))):
            continue  # A future-stage task is not permission to read its note early.
        grouped.setdefault(assignee, []).append(task)
    # Keep a proven terminal snapshot if archived products no longer exist.
    for assignee, prior in previous_by_actor.items():
        if prior.get("state") == "completed" and assignee not in grouped and (
                prior.get("receipt_transfer_authorized") or prior.get("terminal_history_authorized")
                or all(task["piece_id"] not in by_piece for task in prior.get("tasks", []))):
            grouped[assignee] = prior.get("tasks", [])
    account_ids = set(grouped) | {creator}
    accounts = await db.users.find({"id": {"$in": sorted(account_ids)},
        "$or": [{"id": merchant}, {"created_by": merchant}]}, {"_id": 0, "id": 1, "name": 1}).to_list(5000)
    names = {row["id"]: text(row.get("name")) or "الموظف" for row in accounts}
    result = []
    for assignee, own in grouped.items():
        if assignee not in names or creator not in names:
            continue
        prior = previous_by_actor.get(assignee) or {}
        cid = conversation_id(merchant, iid, assignee)
        # One physical piece appears once even when its nonblocking task has
        # moved between stages. Its first applicable task defines the goal.
        selected = {task["piece_id"]: task for task in prior.get("tasks", []) if task["piece_id"] in {t["piece_id"] for t in own}}
        for task in own:
            existing = selected.get(task["piece_id"])
            if existing and existing["id"] == task["id"]:
                selected[task["piece_id"]] = task
            else:
                selected.setdefault(task["piece_id"], task)
        new_pieces = bool(set(selected) - {task["piece_id"] for task in prior.get("tasks", [])}) if prior else False
        own = [{key: task.get(key) for key in TASK_SNAPSHOT_FIELDS} for task in selected.values()]
        own.sort(key=lambda task: (text(task.get("piece_id")), text(task.get("id"))))
        snapshots = {piece["piece_id"]: piece for piece in prior.get("pieces", [])}
        for pid in selected:
            if selected[pid].get("_history_only") and pid not in snapshots:
                snapshots[pid] = {key: selected[pid].get(key) for key in ("piece_id", "order_item_id", "product_name")}
            elif pid in by_piece and not selected[pid].get("_history_only") and (prior.get("state") != "completed" or pid not in snapshots):
                old_assignment_at = snapshots.get(pid, {}).get("assigned_at")
                snapshots[pid] = {key: by_piece[pid][key] for key in PIECE_SNAPSHOT_FIELDS if key in by_piece[pid]}
                from preparation_progress_dates import employee_assignment_at, progress_timestamp
                # Resolve the assigned employee's work date, not supplier custody
                # transfer time or file registration. Store only the resolved date.
                snapshots[pid]["assigned_at"] = old_assignment_at if receipt_transfer_completed(
                    by_piece[pid], assignee, selected[pid].get("stage")) and old_assignment_at else progress_timestamp(
                    employee_assignment_at({**by_piece[pid], "responsible_employee_id": assignee}, {}))
        snapshots = [snapshots[pid] for pid in sorted(selected) if pid in snapshots]
        terminal = instruction.get("status") in TERMINAL_LABELS
        terminal_outcome = ("closed" if instruction.get("status") == "archived" or
            instruction.get("status") == "completed" and instruction.get("action_type") not in BLOCKING_ACTION_TYPES
            else instruction.get("status"))
        labels = [completion_for(by_piece.get(task["piece_id"], {}), task.get("stage")) for task in own]
        observed = (instruction.get("action_type") not in BLOCKING_ACTION_TYPES and bool(labels) and all(labels))
        retained_completion = prior.get("state") == "completed" and not new_pieces
        completed = terminal or observed or retained_completion
        outcome = terminal_outcome if terminal else (
            prior.get("outcome", "completed") if retained_completion else "completed" if observed else "active")
        completion_label = (TERMINAL_LABELS[terminal_outcome] if terminal else
            (prior.get("completion_label") if retained_completion else "") or
            (labels[0] if observed and len(set(labels)) == 1 else "اكتمل المطلوب" if completed else ""))
        new_completion = completed and (not retained_completion or prior.get("outcome") != outcome)
        completion_key = identity(cid, outcome, *sorted(selected)) if new_completion else prior.get("completion_key", "")
        completed_at = now() if new_completion else prior.get("completed_at") if completed else None
        terminal_actor = text(instruction.get("completed_by") or instruction.get("closed_by") or
            instruction.get("cancelled_by") or instruction.get("rejected_by"))
        alert_creator = not (terminal and terminal_actor == creator)
        first = snapshots[0] if snapshots else {}
        summary = {"name": text(first.get("product_name") or first.get("item_name")) or "منتجات الطلب",
            "image_url": text(first.get("selected_image_url") or first.get("resolved_image_url") or first.get("image_url")),
            "quantity": len(snapshots)}
        changes = {"merchant_id": merchant, "instruction_id": iid, "creator_id": creator, "creator_name": names[creator],
            "assignee_id": assignee, "assignee_name": names[assignee], "assignee_active": True,
            "order_number": text(instruction.get("order_number")), "tasks": own, "pieces": snapshots,
            "instruction": _snapshot_instruction(instruction, own), "state": "completed" if completed else "active",
            "completion_label": completion_label, "outcome": outcome, "product_summary": summary,
            "completion_key": completion_key, "completed_at": completed_at,
            "completion_alert_creator": alert_creator,
            "terminal_history_authorized": bool(prior.get("terminal_history_authorized") or
                terminal and assignee in historical_recipients),
            "receipt_transfer_authorized": bool(prior.get("receipt_transfer_authorized") or completed and any(
                receipt_transfer_completed(by_piece.get(task["piece_id"], {}), assignee, task.get("stage")) for task in own)),
            "routing_task_ids": sorted({task["id"] for task in grouped[assignee]} | set(prior.get("routing_task_ids", []))),
            "initial_assignment_key": prior.get("initial_assignment_key") or identity(
                *sorted(task["id"] for task in grouped[assignee] if task.get("current", True))),
            "search_text": " ".join([text(instruction.get("note")), text(instruction.get("order_number")),
                *[text(piece.get(key)) for piece in snapshots for key in ("product_name", "item_name", "file_number")]])}
        if new_completion:
            changes["completion_notification_pending"] = True
        if not prior or any(prior.get(key) != value for key, value in changes.items()):
            try:
                await db[CONVERSATIONS].update_one({"_id": cid,
                    "_revision": prior["_revision"] if "_revision" in prior else {"$exists": False}},
                    {"$set": changes, "$inc": {"_revision": 1}, "$setOnInsert": {"id": cid,
                        "created_at": stamp(instruction.get("created_at"))}}, upsert=not prior)
            except DuplicateKeyError:
                pass  # A concurrent first observer established this same thread.
        # The document CAS chooses one completion timestamp. A delayed observer
        # must not resurrect an older active state or replace that timestamp.
        row = await db[CONVERSATIONS].find_one({"_id": cid})
        if row["state"] == "completed":
            completion_key = row["completion_key"]
            await append_event(db, row, key="outcome:" + completion_key, kind="completed", body=row["completion_label"],
                author_id=SYSTEM, author_name="حالة المنتج", at=row["completed_at"],
                alert_recipients={creator} if row.get("completion_alert_creator", True) else set())
            if row.get("completion_notification_pending"):
                if row.get("completion_alert_creator", True):
                    from operational_instruction_notifications import emit_instruction_notification
                    await emit_instruction_notification(db, merchant_id=merchant,
                        instruction={**instruction, "notification_revision": "conversation-completed:" + completion_key,
                            "notification_metadata": {"conversation_id": cid, "conversation_completion_key": completion_key}, "note": row["completion_label"]},
                        event_type="progress", recipient_ids={creator})
                await db[CONVERSATIONS].update_one({"_id": cid, "completion_key": completion_key},
                    {"$set": {"completion_notification_pending": False}})
        result.append(row)
    active_ids = {row["id"] for row in result}
    for prior in previous:
        if prior["id"] not in active_ids and prior.get("assignee_active", True):
            await db[CONVERSATIONS].update_one({"_id": prior["id"], "_revision": prior.get("_revision")},
                {"$set": {"assignee_active": False}, "$inc": {"_revision": 1}})
    return result


async def append_event(db, conversation, *, key, kind, body, author_id, author_name="", at=None,
        alert_recipients=None, source=None, metadata=None):
    cid, merchant = conversation["id"], conversation["merchant_id"]
    event_id = identity(cid, key)
    at = stamp(at or now())
    participants = {conversation["creator_id"], conversation["assignee_id"]}
    alerts = participants - {author_id} if alert_recipients is None else alert_recipients
    for recipient in sorted(participants):
        row_id = identity(event_id, recipient)
        read_at = at if recipient == author_id else None
        if source and source.get("recipient_id") == recipient and source.get("read_at"):
            read_at = stamp(source["read_at"])
        row = {"id": row_id, "event_id": event_id, "merchant_id": merchant, "conversation_id": cid,
            "instruction_id": conversation["instruction_id"], "recipient_id": recipient,
            "kind": kind, "body": body, "author_id": author_id, "author_name": author_name,
            "preview": body[:240],
            "created_at": at, "read_at": read_at, "silent": kind == "read" or recipient not in alerts,
            "metadata": metadata or {}}
        if source and source.get("recipient_id") == recipient:
            row["source_notice_id"] = source["id"]
        await db[EVENTS].update_one({"_id": row_id}, {"$setOnInsert": row}, upsert=True)
        if source and source.get("recipient_id") == recipient:
            await db[EVENTS].update_one({"_id": row_id, "source_notice_id": {"$exists": False}},
                {"$set": {"source_notice_id": source["id"]}})
        # A previously projected legacy event may have been read by the old app.
        if read_at and source and source.get("recipient_id") == recipient:
            await db[EVENTS].update_one({"_id": row_id, "read_at": None}, {"$set": {"read_at": read_at}})
    return event_id


def _applies(source, conversation):
    metadata = source.get("metadata") or {}
    if metadata.get("conversation_id"):
        return metadata["conversation_id"] == conversation["id"]
    assignee, creator = conversation["assignee_id"], conversation["creator_id"]
    recipient, sender = text(source.get("recipient_id")), text(source.get("sender_id"))
    if recipient not in {assignee, creator}:
        return False
    task_ids = {task["id"] for task in conversation.get("tasks", [])} | set(conversation.get("routing_task_ids", []))
    piece_ids = {task["piece_id"] for task in conversation.get("tasks", [])}
    if metadata.get("task_id") and metadata["task_id"] not in task_ids:
        return False
    if metadata.get("piece_id") and metadata["piece_id"] not in piece_ids:
        return False
    actor = text(metadata.get("actor_id") or (metadata.get("current") or {}).get("assignee_id"))
    if actor and actor not in {assignee, creator}:
        return False
    if sender not in {"", SYSTEM, assignee, creator}:
        return False
    # A legacy unscoped system update cannot be attributed to an employee by
    # guessing. Only existing task/piece/actor routing may place it in a thread.
    if source.get("event_type") == "progress" and not (metadata.get("task_id") or metadata.get("piece_id") or actor):
        return False
    return True


async def capture_notifications(db, instruction, sources, *, conversations=None):
    if conversations is None:
        conversations = await db[CONVERSATIONS].find({"merchant_id": instruction["user_id"], "instruction_id": instruction["id"]}).to_list(5000)
        if not conversations or any(row.get("event_type") in {"created", "assigned", "completed", "closed", "approved"} for row in sources):
            conversations = await reconcile_instruction(db, instruction)
    for source in sources:
        for conversation in conversations:
            if not _applies(source, conversation):
                continue
            event_type = source.get("event_type")
            kind = ("instruction" if event_type in {"created", "assigned", "edit_sent"} else
                "message" if event_type == "comment" else "read" if event_type == "acknowledged" else
                "completed" if event_type in {"completed", "closed", "approved"} else "progress")
            metadata = source.get("metadata") or {}
            key = "initial" if event_type in {"created", "assigned"} else text(source.get("event_key")) or identity(
                event_type, stamp(source.get("created_at")).isoformat(), source.get("message"),
                json.dumps(source.get("metadata") or {}, sort_keys=True, default=str))
            if event_type == "assigned" and source.get("assignment_key") and source["assignment_key"] != conversation.get("initial_assignment_key"):
                key = "assigned:" + source["assignment_key"]
            if metadata.get("conversation_message_id"):
                key = "message:" + metadata["conversation_message_id"]
            if metadata.get("conversation_completion_key"):
                key, kind = "outcome:" + metadata["conversation_completion_key"], "completed"
            if event_type in {"approved", "completed", "closed"} and conversation.get("state") == "completed":
                key, kind = "outcome:" + conversation["completion_key"], "completed"
            author = text(source.get("sender_id")) or conversation["creator_id"]
            await append_event(db, conversation, key=key, kind=kind,
                body=(conversation.get("completion_label") if kind == "completed" else "") or text(source.get("message")) or text(source.get("title")), author_id=author,
                author_name=text(source.get("sender_name")) or conversation["creator_name"], at=source.get("created_at"),
                source=source, metadata=source.get("metadata"),
                alert_recipients={conversation["creator_id"]} if event_type == "progress" else None)


async def backfill_actor(db, merchant_id, actor_id):
    """At most 40 old notices per call; an exhausted cursor is never rewound."""
    key = identity(merchant_id, actor_id)
    bookmark = await db[BOOKMARKS].find_one({"_id": key}) or {}
    query = {"merchant_id": merchant_id, "recipient_id": actor_id, "conversation_pending": {"$ne": False}}
    if bookmark.get("last_id") is not None:
        query["_id"] = {"$gt": bookmark["last_id"]}
    rows = await db[NOTIFICATIONS].find(query).sort("_id", 1).limit(40).to_list(40)
    if not rows:
        return
    by_instruction = {}
    for source in rows:
        by_instruction.setdefault(source["instruction_id"], []).append(source)
    for iid, sources in by_instruction.items():
        instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one({"user_id": merchant_id, "id": iid}, {"_id": 0})
        if not instruction:
            continue
        from operational_instruction_notifications import ensure_instruction_tasks
        await ensure_instruction_tasks(db, merchant_id, instruction)
        conversations = await reconcile_instruction(db, instruction,
            historical_recipients={source["recipient_id"] for source in sources})
        await capture_notifications(db, instruction, sources, conversations=conversations)
        await db[NOTIFICATIONS].update_many({"merchant_id": merchant_id,
            "id": {"$in": [source["id"] for source in sources]}}, {"$set": {"conversation_pending": False}})
    await db[BOOKMARKS].update_one({"_id": key}, {"$set": {"last_id": rows[-1]["_id"]}}, upsert=True)


async def retry_projection(db):
    """Retry only pending new events inside the existing notification loop."""
    rows = await db[NOTIFICATIONS].find({"conversation_pending": True}).sort("_id", 1).to_list(40)
    grouped = {}
    for source in rows:
        grouped.setdefault((source["merchant_id"], source["instruction_id"]), []).append(source)
    for (merchant, iid), sources in grouped.items():
        instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one({"user_id": merchant, "id": iid})
        if not instruction:
            continue
        await capture_notifications(db, instruction, sources)
        await db[NOTIFICATIONS].update_many({"merchant_id": merchant,
            "id": {"$in": [source["id"] for source in sources]}}, {"$set": {"conversation_pending": False}})


def _pipeline(merchant_id, actor_id, *, view="alerts", sender_id="", q="", read_state="all"):
    stages = [{"$match": {"merchant_id": merchant_id, "recipient_id": actor_id}},
        {"$project": {"conversation_id": 1, "event_id": 1, "kind": 1, "preview": 1, "created_at": 1,
            **({"body": 1} if q else {}),
            "read_at": 1, "silent": 1, "author_id": 1}},
        {"$sort": {"created_at": -1, "event_id": -1}},
        {"$group": {"_id": "$conversation_id", "latest_at": {"$first": "$created_at"},
            "latest_message": {"$first": "$preview"}, "last_event_id": {"$first": "$event_id"},
            "message_matches_q": {"$max": {"$cond": [{"$regexMatch": {
                "input": {"$ifNull": ["$body", ""]}, "regex": re.escape(q.strip()), "options": "i"}}, 1, 0]}} if q else {"$max": 0},
            "latest_unread_completion_at": {"$max": {"$cond": [{"$and": [
                {"$eq": ["$kind", "completed"]}, {"$eq": [{"$ifNull": ["$read_at", None]}, None]},
                {"$ne": ["$silent", True]}]}, "$created_at", EPOCH]}},
            "latest_unread_message_at": {"$max": {"$cond": [{"$and": [
                {"$eq": ["$kind", "message"]}, {"$eq": [{"$ifNull": ["$read_at", None]}, None]},
                {"$ne": ["$author_id", actor_id]}]}, "$created_at", EPOCH]}},
            "unread_count": {"$sum": {"$cond": [{"$and": [
                {"$eq": [{"$ifNull": ["$read_at", None]}, None]}, {"$ne": ["$silent", True]},
                {"$ne": ["$author_id", actor_id]}]}, 1, 0]}}}},
        {"$lookup": {"from": CONVERSATIONS, "localField": "_id", "foreignField": "_id", "as": "_conversation",
            "pipeline": [{"$project": {"merchant_id": 1, "instruction_id": 1, "creator_id": 1, "creator_name": 1,
                "assignee_id": 1, "assignee_name": 1, "assignee_active": 1, "state": 1, "completion_label": 1, "outcome": 1,
                "completed_at": 1, "product_summary": 1, **({"search_text": 1} if q else {}), "order_number": 1}}]}},
        {"$unwind": "$_conversation"},
        {"$match": {"_conversation.merchant_id": merchant_id, "$or": [
            {"_conversation.creator_id": actor_id},
            {"_conversation.assignee_id": actor_id, "_conversation.assignee_active": True}]}},
        {"$set": {"_creator": {"$eq": ["$_conversation.creator_id", actor_id]}}},
        {"$set": {"peer": {"id": {"$cond": ["$_creator", "$_conversation.assignee_id", "$_conversation.creator_id"]},
            "name": {"$cond": ["$_creator", "$_conversation.assignee_name", "$_conversation.creator_name"]}},
            "alert_active": {"$or": [{"$and": [{"$eq": ["$_conversation.state", "active"]}, "$_conversation.assignee_active"]},
                {"$and": ["$_creator", {"$ne": ["$_conversation.completed_at", None]},
                    {"$gte": ["$latest_unread_completion_at", "$_conversation.completed_at"]}]},
                {"$gt": ["$latest_unread_message_at", {"$ifNull": ["$_conversation.completed_at", EPOCH]}]}]}}}]
    if view == "alerts":
        stages.append({"$match": {"alert_active": True}})
    if sender_id:
        stages.append({"$match": {"peer.id": sender_id}})
    if q:
        pattern = re.escape(q.strip())
        stages.append({"$match": {"$or": [{"_conversation.search_text": {"$regex": pattern, "$options": "i"}},
            {"message_matches_q": 1}]}})
    if read_state != "all":
        stages.append({"$match": {"unread_count": {"$gt": 0} if read_state == "unread" else 0}})
    return stages


async def conversation_listing(db, *, merchant_id, actor_id, directory_only=False, count_only=False,
        sender_id="", view="alerts", read_state="all", cursor=None, q=""):
    await backfill_actor(db, merchant_id, actor_id)
    stages = _pipeline(merchant_id, actor_id, view=view, sender_id=sender_id, q=q, read_state=read_state)
    if count_only:
        rows = await db[EVENTS].aggregate(stages + [{"$group": {"_id": None, "unread_count": {"$sum": {
            "$cond": ["$alert_active", "$unread_count", 0]}}}}]).to_list(1)
        return {"conversation_only": True, "count_only": True, "unread_count": rows[0]["unread_count"] if rows else 0}
    if directory_only:
        rows = await db[EVENTS].aggregate(stages + [{"$sort": {"latest_at": -1, "_id": -1}},
            {"$group": {"_id": "$peer.id", "name": {"$first": "$peer.name"}, "total": {"$sum": 1},
                "unread_count": {"$sum": {"$cond": ["$alert_active", "$unread_count", 0]}},
                "latest_at": {"$first": "$latest_at"}, "latest_message": {"$first": "$latest_message"}}},
            {"$sort": {"latest_at": -1, "_id": -1}}]).to_list(None)
        items = [{"id": row.pop("_id"), **row} for row in rows]
        return {"conversation_only": True, "directory_only": True, "items": items,
            "unread_count": sum(item["unread_count"] for item in items)}
    if cursor:
        from operational_notification_directory import _decode_cursor
        at, last_id = _decode_cursor(cursor)
        stages.append({"$match": {"$or": [{"latest_at": {"$lt": at}}, {"latest_at": at, "_id": {"$lt": last_id}}]}})
    rows = await db[EVENTS].aggregate(stages + [{"$sort": {"latest_at": -1, "_id": -1}}, {"$limit": 11}]).to_list(11)
    items = []
    for row in rows[:10]:
        conv = row["_conversation"]
        items.append({"id": row["_id"], "conversation_id": row["_id"], "instruction_id": conv["instruction_id"],
            "peer": row["peer"], "state": conv["state"], "completion_label": conv.get("completion_label", ""),
            "outcome": conv.get("outcome", "active"),
            "product_summary": conv["product_summary"], "order_number": conv["order_number"],
            **{key: row[key] for key in ("latest_at", "latest_message", "last_event_id", "unread_count", "alert_active")}})
    next_cursor = base64.urlsafe_b64encode(json.dumps([stamp(rows[9]["latest_at"]).isoformat(), rows[9]["_id"]]).encode()).decode().rstrip("=") if len(rows) > 10 else None
    return {"conversation_only": True, "items": items, "sender_id": sender_id, "next_cursor": next_cursor}


async def context(db, merchant_id, actor_id, instruction_id, cid="", notice_id="", *, refresh=True):
    instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one({"user_id": merchant_id, "id": instruction_id}, {"_id": 0})
    participating = await db[CONVERSATIONS].find_one({"merchant_id": merchant_id, "instruction_id": instruction_id,
        "$or": [{"creator_id": actor_id}, {"assignee_id": actor_id, "assignee_active": True}]}, {"_id": 1})
    assigned = await db[TASKS].find_one({"merchant_id": merchant_id, "instruction_id": instruction_id,
        "assignee_id": actor_id}, {"_id": 1}) if not participating else None
    historical_notice = await db[NOTIFICATIONS].find_one({"merchant_id": merchant_id, "instruction_id": instruction_id,
        "recipient_id": actor_id, "event_type": {"$in": ["created", "assigned", "edit_sent"]}}, {"_id": 1}) if not participating else None
    if not participating and not assigned and not historical_notice and (not instruction or instruction.get("created_by") != actor_id):
        raise HTTPException(404, detail={"code": "instruction_conversation_not_found"})
    if instruction and refresh:
        from operational_instruction_notifications import ensure_instruction_tasks
        tasks = await ensure_instruction_tasks(db, merchant_id, instruction)
        await reconcile_instruction(db, instruction, tasks=tasks,
            historical_recipients={actor_id} if historical_notice else set())
    query = {"merchant_id": merchant_id, "instruction_id": instruction_id,
        "$or": [{"creator_id": actor_id}, {"assignee_id": actor_id, "assignee_active": True}]}
    if notice_id:
        source = await db[NOTIFICATIONS].find_one({"id": notice_id, "merchant_id": merchant_id,
            "recipient_id": actor_id, "instruction_id": instruction_id})
        projected = await db[EVENTS].find_one({"merchant_id": merchant_id, "recipient_id": actor_id,
            "instruction_id": instruction_id, "$or": [{"id": notice_id}, {"event_id": notice_id}]})
        if not source and not projected:
            raise HTTPException(404, detail={"code": "instruction_conversation_not_found"})
        if source and instruction:
            await capture_notifications(db, instruction, [source])
        if not cid:
            matches = await db[EVENTS].find({"merchant_id": merchant_id, "recipient_id": actor_id,
                "instruction_id": instruction_id, **({"source_notice_id": notice_id} if source else {"_id": projected["_id"]})}).to_list(2)
            if len(matches) == 1:
                cid = matches[0]["conversation_id"]
    if cid:
        query["id"] = cid
    elif not instruction or instruction.get("created_by") != actor_id:
        query["assignee_id"] = actor_id
    rows = await db[CONVERSATIONS].find(query).sort([("created_at", -1), ("id", 1)]).to_list(5000)
    if not rows:
        raise HTTPException(404, detail={"code": "instruction_conversation_not_found"})
    if not cid and len(rows) > 1 and rows[0]["creator_id"] == actor_id:
        return {"choices": [{"id": row["id"], "peer": {"id": row["assignee_id"], "name": row["assignee_name"]}} for row in rows]}
    row = rows[0]
    if notice_id and ((source and not _applies(source, row)) or (not source and projected["conversation_id"] != row["id"])):
        raise HTTPException(404, detail={"code": "instruction_conversation_not_found"})
    # Fill only this authorized thread's old messages/events. It is bounded and
    # uses immutable event identities; metadata polling does not call this.
    if instruction:
        sources = await db[NOTIFICATIONS].find({"merchant_id": merchant_id, "instruction_id": instruction_id,
            "recipient_id": {"$in": [row["creator_id"], row["assignee_id"]]}}).sort("created_at", -1).to_list(200)
        await capture_notifications(db, instruction, sources, conversations=[row])
    return row


async def conversation_detail(db, *, merchant_id, actor_id, instruction_id, cid="", notice_id=""):
    row = await context(db, merchant_id, actor_id, instruction_id, cid, notice_id)
    if "choices" in row:
        return {"conversation_only": True, "conversation": None, "conversation_choices": row["choices"],
            "timeline": [], "product_cards": [], "capabilities": {}, "evidence": [], "events": []}
    events = await db[EVENTS].find({"merchant_id": merchant_id, "conversation_id": row["id"],
        "recipient_id": actor_id}).sort([("created_at", -1), ("event_id", -1)]).to_list(100)
    unread = await db[EVENTS].count_documents({"merchant_id": merchant_id, "conversation_id": row["id"],
        "recipient_id": actor_id, "read_at": None, "silent": False, "author_id": {"$ne": actor_id}})
    creator = actor_id == row["creator_id"]
    unseen_completion = await db[EVENTS].find_one({"merchant_id": merchant_id, "conversation_id": row["id"],
        "recipient_id": actor_id, "kind": "completed", "silent": False, "read_at": None,
        "created_at": {"$gte": row.get("completed_at") or now()}}, {"_id": 1}) if creator else None
    later_message = await db[EVENTS].find_one({"merchant_id": merchant_id, "conversation_id": row["id"],
        "recipient_id": actor_id, "kind": "message", "author_id": {"$ne": actor_id}, "read_at": None,
        "created_at": {"$gt": row.get("completed_at") or EPOCH}}, {"_id": 1}) if row["state"] == "completed" else None
    peer = {"id": row["assignee_id"] if creator else row["creator_id"], "name": row["assignee_name"] if creator else row["creator_name"]}
    from instruction_product_cards import product_cards
    instruction = dict(row["instruction"])
    cards = product_cards(instruction, row["tasks"], row["pieces"], [], creator=False)
    instruction.pop("edit_baseline", None)
    instruction.pop("edit_requested", None)
    timeline = [{"id": event["event_id"], "kind": event["kind"], "body": event["body"],
        "author_id": event["author_id"], "author_name": event["author_name"], "created_at": event["created_at"]}
        for event in reversed(events)]
    return {"conversation_only": True, "instruction": instruction, "product_cards": cards, "timeline": timeline,
        "conversation": {"id": row["id"], "peer": peer, "state": row["state"], "unread_count": unread,
            "outcome": row.get("outcome", "active"),
            "completion_label": row.get("completion_label", ""), "can_reply": bool(row.get("assignee_active")),
            "alert_active": row["state"] == "active" and bool(row.get("assignee_active")) or bool(unseen_completion) or bool(later_message),
            "last_event_id": events[0]["event_id"] if events else ""}}


async def read_conversation(db, *, merchant_id, actor_id, instruction_id, payload):
    row = await context(db, merchant_id, actor_id, instruction_id, payload.conversation_id, payload.notice_id or "")
    query = {"merchant_id": merchant_id, "conversation_id": row["id"], "recipient_id": actor_id}
    if not payload.last_event_id:
        return {"ok": True}
    anchor = await db[EVENTS].find_one({**query, "event_id": payload.last_event_id})
    if not anchor:
        raise HTTPException(422, detail={"code": "conversation_read_cursor_invalid"})
    if payload.event_ids is not None:
        query["event_id"] = {"$in": list(set(payload.event_ids))}
    else:
        # Older clients can safely acknowledge exactly their observed anchor.
        # They must not consume events inserted concurrently with the GET.
        query["event_id"] = payload.last_event_id
    unseen = await db[EVENTS].find({**query, "read_at": None, "kind": {"$ne": "read"}, "author_id": {"$ne": actor_id}},
        {"id": 1, "event_id": 1, "source_notice_id": 1}).to_list(200)
    if not unseen:
        return {"ok": True}
    at = now()
    changed = await db[EVENTS].update_many({**query, "read_at": None}, {"$set": {"read_at": at}})
    if changed.modified_count:
        source_ids = [event["source_notice_id"] for event in unseen if event.get("source_notice_id")]
        if source_ids:
            await db[NOTIFICATIONS].update_many({"merchant_id": merchant_id, "recipient_id": actor_id,
                "id": {"$in": source_ids}, "read_at": None}, {"$set": {"read_at": at}})
        await append_event(db, row, key="read:" + actor_id + ":" + identity(*sorted(event["event_id"] for event in unseen)),
            kind="read", body="تم الاطلاع على الرسائل", author_id=actor_id,
            author_name=row["creator_name"] if actor_id == row["creator_id"] else row["assignee_name"], at=at,
            alert_recipients=set())
    return {"ok": True}


async def create_message(db, *, merchant_id, actor_id, instruction_id, payload):
    row = await context(db, merchant_id, actor_id, instruction_id, payload.conversation_id)
    if not row.get("assignee_active"):
        raise HTTPException(409, detail={"code": "instruction_conversation_read_only"})
    body = payload.body.strip()
    if not body:
        raise HTTPException(422, detail={"code": "comment_body_required"})
    message_id = identity(merchant_id, row["id"], actor_id, payload.client_request_id)
    document = {"_id": message_id, "id": message_id, "merchant_id": merchant_id, "instruction_id": instruction_id,
        "conversation_id": row["id"], "body": body, "author_id": actor_id,
        "author_name": row["creator_name"] if actor_id == row["creator_id"] else row["assignee_name"],
        "created_at": now(), "notification_pending": True}
    await db[COMMENTS].update_one({"_id": message_id}, {"$setOnInsert": document}, upsert=True)
    saved = await db[COMMENTS].find_one({"_id": message_id})
    if saved["body"] != body:
        raise HTTPException(409, detail={"code": "comment_request_reused"})
    try:
        await notify_message(db, saved)
    except Exception:
        # The saved message's pending marker is retried by the existing worker.
        pass
    return {"ok": True}


async def notify_message(db, message):
    if not message.get("notification_pending"):
        return
    row = await db[CONVERSATIONS].find_one({"_id": message["conversation_id"], "merchant_id": message["merchant_id"]})
    if not row or not row.get("assignee_active") or message["author_id"] not in {row["creator_id"], row["assignee_id"]}:
        return
    await append_event(db, row, key="message:" + message["id"], kind="message", body=message["body"],
        author_id=message["author_id"], author_name=message["author_name"], at=message["created_at"])
    # Keep delivery compatible with existing push deep links and the old app.
    from operational_instruction_notifications import emit_instruction_notification
    await emit_instruction_notification(db, merchant_id=row["merchant_id"],
        instruction={**row["instruction"], "user_id": row["merchant_id"], "notification_revision": message["id"],
            "notification_metadata": {"conversation_id": row["id"], "conversation_message_id": message["id"]}, "note": message["body"]},
        event_type="comment", recipient_ids={row["creator_id"], row["assignee_id"]} - {message["author_id"]},
        sender_id=message["author_id"])
    await db[COMMENTS].update_one({"_id": message["id"]}, {"$set": {"notification_pending": False}})


async def push_is_resolved(db, *, merchant_id, recipient_id, event_id):
    projection = await db[EVENTS].find_one({"merchant_id": merchant_id, "recipient_id": recipient_id,
        "source_notice_id": event_id}, {"conversation_id": 1, "read_at": 1, "silent": 1, "kind": 1, "created_at": 1})
    if not projection:
        return False
    if projection.get("read_at") or projection.get("silent"):
        return True
    row = await db[CONVERSATIONS].find_one({"_id": projection["conversation_id"], "merchant_id": merchant_id},
        {"state": 1, "assignee_id": 1, "assignee_active": 1, "completed_at": 1})
    if (row and row.get("assignee_active") and projection.get("kind") == "message"
            and stamp(projection.get("created_at")) > stamp(row.get("completed_at"))):
        return False
    return bool(row and recipient_id == row["assignee_id"] and (
        row.get("state") == "completed" or not row.get("assignee_active")))


async def push_reference(db, *, merchant_id, recipient_id, event_id):
    rows = await db[EVENTS].find({"merchant_id": merchant_id, "recipient_id": recipient_id,
        "source_notice_id": event_id}, {"conversation_id": 1}).to_list(2)
    return {"notice_id": event_id, **({"conversation_id": rows[0]["conversation_id"]} if len(rows) == 1 else {})}


async def ensure_indexes(db):
    await db[CONVERSATIONS].create_index([("merchant_id", 1), ("instruction_id", 1), ("assignee_id", 1)])
    await db[EVENTS].create_index([("merchant_id", 1), ("recipient_id", 1), ("created_at", -1), ("event_id", -1)])
    await db[EVENTS].create_index([("merchant_id", 1), ("conversation_id", 1), ("recipient_id", 1), ("created_at", -1), ("event_id", -1)])
    await db[EVENTS].create_index([("merchant_id", 1), ("recipient_id", 1), ("source_notice_id", 1)])
    await db[NOTIFICATIONS].create_index([("merchant_id", 1), ("recipient_id", 1), ("_id", 1)])
    await db[NOTIFICATIONS].create_index([("conversation_pending", 1), ("_id", 1)])
    await db[NOTIFICATIONS].create_index([("merchant_id", 1), ("instruction_id", 1), ("event_type", 1), ("recipient_id", 1)])
