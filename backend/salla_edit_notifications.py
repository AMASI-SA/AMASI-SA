"""Recipient-scoped, read-only EDIT detection notifications for employee clients."""
from fastapi import HTTPException
from fulfillment_v2_routes import _actor_context, effective_operation_actor
import fulfillment_lifecycle as controls
import salla_order_change_reconciliation as source
import salla_edit_application as edits


async def employee_edit_notifications(db, *, user):
    if user.get("disabled") is True or user.get("is_active") is False or user.get("deleted_at"):
        raise HTTPException(403, detail={"code": "salla_edit_account_inactive"})
    context = await _actor_context(db, user)
    owner = context["merchant_id"]
    employee = effective_operation_actor(user, context)["id"]
    if not employee:
        raise HTTPException(403, detail={"code": "salla_edit_employee_required"})
    if not edits.enabled():
        return {"enabled": False, "notifications": []}
    notices = await db[controls.AUDIT].aggregate([
        {"$match": {"user_id": owner, "event_type": source.OUTBOX, "recipients": employee}},
        {"$lookup": {"from": controls.AUDIT, "let": {"event": "$event_id", "order": "$order_number", "change": "$change_id"},
            "pipeline": [{"$match": {"user_id": owner, "event_type": source.EVENT, "change_type": "edit_options",
                "$expr": {"$and": [{"$eq": ["$event_id", "$$event"]}, {"$eq": ["$order_number", "$$order"]},
                                   {"$eq": ["$change_id", "$$change"]}]}}}], "as": "edit_event"}},
        {"$match": {"edit_event.0": {"$exists": True}}},
        {"$sort": {"created_at": -1}}, {"$limit": 101},
    ]).to_list(101)
    result = []
    for notice in notices[:100]:
        event = await db[controls.AUDIT].find_one({"user_id": owner, "order_number": notice["order_number"],
            "event_type": source.EVENT, "change_type": "edit_options", "event_id": notice["event_id"],
            "change_id": notice["change_id"]})
        if not event:
            continue
        applied = await db[controls.AUDIT].find_one({"user_id": owner, "order_number": event["order_number"],
            "event_id": event["event_id"], "event_type": edits.APPLIED})
        classification = (applied or {}).get("result", {}).get("classification")
        refs = event.get("affected_units") or []
        ids = [r["piece_id"] for r in refs if r.get("piece_id")]
        pieces = await db[controls.PIECES].find({"user_id": owner, "order_number": event["order_number"],
            "piece_id": {"$in": ids}}, {"_id": 0, "piece_id": 1, "status": 1, "generation": 1,
            "order_item_id": 1, "unit_index": 1, "responsible_employee_id": 1}).to_list(1000)
        result.append({"event_id": event["event_id"], "change_id": event["change_id"],
            "order_number": event["order_number"], "change_type": "edit_options",
            "title": "تم تعديل خيارات المنتج", "old_options": (event.get("old_data") or {}).get("options"),
            "new_options": (event.get("new_data") or {}).get("options"),
            "product": event.get("new_data"), "affected_units": refs, "current_units": pieces,
            "stage": notice.get("stage"), "notification_status": notice.get("status", "pending"),
            "read_at": notice.get("read_at"), "application_state": "applied" if applied else "pending_application",
            "required_action": "continue_current_generation" if classification == "representation_only" else "stop_old_generation",
            "message": "يمكن متابعة النسخة الحالية" if classification == "representation_only" else "أوقف تجهيز النسخة القديمة",
            "created_at": notice.get("created_at")})
    return {"enabled": True, "notifications": result, "has_more": len(notices) > 100}
