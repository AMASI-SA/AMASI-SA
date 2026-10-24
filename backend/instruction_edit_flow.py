"""Versioned stop -> Salla revision -> execution contract. No Salla writes."""
import hashlib
import json
from fastapi import HTTPException


def snapshot(items, item_ids):
    selected = set(item_ids)
    result = {}
    for item in items:
        iid = str(item.get("order_item_id") or item.get("id") or "")
        if iid not in selected:
            continue
        result[iid] = {key: item.get(key) for key in ("name", "quantity", "image_url", "options_normalized", "product_id", "variant_id", "sku")}
    if set(result) != selected:
        raise HTTPException(409, detail={"code": "edit_flow_items_changed", "message": "تغيرت قائمة المنتجات؛ استخدم مسار الإلغاء أو راجع الطلب أولًا."})
    return result


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def candidate(instruction, current):
    before = instruction.get("edit_baseline") or {}
    if not before or set(before) != set(current):
        raise HTTPException(409, detail={"code": "edit_flow_items_changed"})
    if any(before[key].get("quantity") != current[key].get("quantity") for key in before):
        raise HTTPException(409, detail={"code": "edit_flow_quantity_changed", "message": "تغيير الكمية يحتاج معالجة القطع أولًا؛ لا يمكن إرسال هذا التعديل تلقائيًا."})
    return {"revision": revision(current), "changed": revision(before) != revision(current)}


def require_sent(instruction, current, expected):
    if instruction.get("edit_phase") != "revision_sent" or not expected or expected != instruction.get("edit_revision") or revision(current) != expected:
        raise HTTPException(409, detail={"code": "edit_flow_revision_mismatch", "message": "نسخة التعديل تغيرت أو لم تُرسل بعد؛ حدّث الإشعار قبل التأكيد."})


def can_resume(instruction, tasks, actor_id):
    return bool(instruction.get("edit_flow") == 1 and instruction.get("edit_phase") == "revision_sent"
        and instruction.get("action_type") in {"edit_product", "edit_order"}
        and tasks and any(task.get("assignee_id") == actor_id for task in tasks)
        and all(task.get("status") == "submitted" and task.get("submitted_revision") == instruction.get("edit_revision") for task in tasks))
