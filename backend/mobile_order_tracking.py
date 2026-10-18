"""Read-only, tenant-scoped order/physical-piece tracking for the native app.

This projection never starts/reconciles a workflow or calls Salla. Locations
describe current persisted page evidence; milestones remain historical facts.
Missing or conflicting evidence must not turn into an invented employee page.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
import re
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response

from mobile_app_permissions import MOBILE_APP_CLIENT, mobile_app_access_for_user
from order_engine.mapper import _map_item, _parse_datetime, map_salla_order
from preparation_piece_operations import _workflow_assembly_pieces
from preparation_supplier_dispatch import piece_is_available_for_supplier_dispatch
from review_local_policy import LOCAL_COMPLETION_MODE, load_local_review_workflows, local_review_stage_eligible
from review_completion_readback import committed_proof

TRACKING_PERMISSION = "app.action.orders.track"
MAX_ITEMS = 200
MAX_PIECES = 1000
MAX_HISTORY = 100
MAX_ORDER_EVENTS = 500
PIECES = "mezan_preparation_pieces_v1"
REGISTRY = "mezan_preparation_file_registry_v2"
DISPATCHES = "mezan_supplier_dispatches_v1"
WORKFLOWS = "order_review_workflows"

_STATUS_LABELS = {
    "under_review": "بانتظار المراجعة", "reviewed": "تمت المراجعة",
    "in_progress": "قيد التنفيذ", "completed": "تم التنفيذ",
    "shipped": "تم الشحن", "delivering": "جاري التوصيل",
    "delivered": "تم التوصيل", "cancelled": "ملغى", "refunded": "مسترجع",
    "deleted": "محذوف",
}
_ALIASES = {label: slug for slug, label in _STATUS_LABELS.items()}
_ALIASES.update({"تم المراجعة": "reviewed", "تم المراجعه": "reviewed",
                 "تمت المراجعه": "reviewed", "processing": "in_progress",
                 "جاري التنفيذ": "in_progress", "shipping": "shipped",
                 "canceled": "cancelled", "ملغي": "cancelled",
                 "انتظار المراجعة": "under_review", "بإنتظار المراجعة": "under_review",
                 "waiting_review": "under_review", "pending_review": "under_review"})


def _text(value: Any) -> str:
    return str(value or "").strip() if not isinstance(value, (dict, list)) else ""


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _unit_index(value: Any) -> int | None:
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        return None
    try:
        number = int(value)
        if number < 1 or float(value) != number:
            return None
        return number
    except (ValueError, TypeError, OverflowError):
        return None


def _iso(value: Any, *, provider: bool = False) -> str | None:
    # Mongo naive operational datetimes are UTC. Provider wall-clock dates use
    # the existing Salla parser's declared/default Riyadh zone instead.
    date_value = value.get("date") or value.get("datetime") or value.get("value") if isinstance(value, dict) else value
    if isinstance(date_value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value.strip()):
        return None  # A date-only field does not prove a midnight transition.
    parsed = _parse_datetime(value) if provider else _parse_datetime(value, default_timezone=timezone.utc)
    return parsed.isoformat() if parsed else None


def _url(value: Any) -> str | None:
    text = _text(value)
    parsed = urlparse(text)
    return text if parsed.scheme in {"https", "http"} and parsed.netloc and not parsed.username else None


def _status_key(value: Any) -> str:
    text = _text(value).casefold()
    return _ALIASES.get(text, _ALIASES.get(text.replace(" ", "_"), text.replace(" ", "_")))


def _status_values(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {_status_key(value.get(key)) for key in ("slug", "name", "label", "title")
                if _text(value.get(key))}
    return {_status_key(value)} if _text(value) else set()


def _status_facts(source: dict) -> tuple[str, str, str | None]:
    raw = _dict(_dict(source.get("raw_by_source")).get("salla_direct"))
    status = raw.get("status")
    obj = _dict(status)
    custom = obj.get("customized")
    # A customized status is the provider's effective status, not its parent.
    effective = custom if _status_values(custom) else status
    values = _status_values(effective)
    provider_values = set(values)
    for key in ("order_status", "order_status_slug"):
        root_values = _status_values(source.get(key))
        # Match the existing Order Engine treatment of a stale review parent.
        if custom and root_values == {"under_review"}:
            continue
        values |= root_values
    if not custom:
        values |= _status_values(raw.get("status_slug"))
    canonical = next(iter(values)) if provider_values and len(values) == 1 else "unknown"
    if _dict(source.get("g47_salla_snapshot")).get("requires_authoritative_refresh"):
        canonical = "unknown"
    effective_obj = _dict(effective)
    label = (_text(effective_obj.get("name") or effective_obj.get("label") or effective_obj.get("title"))
             or _STATUS_LABELS.get(canonical) or _text(effective) or "حالة سلة غير مؤكدة")
    changed = None
    if canonical != "unknown":
        # Only explicit status-transition timestamps, never order.updated_at.
        changed = _iso(effective_obj.get("changed_at") or obj.get("changed_at")
                       or raw.get("status_changed_at") or source.get("order_status_changed_at"), provider=True)
    return canonical, label, changed


async def _owner_scope(db: Any, principal: dict) -> str:
    if principal.get("_session_client") != MOBILE_APP_CLIENT:
        raise HTTPException(403, detail={"code": "mobile_order_tracking_native_required"})
    actor_id = _text(principal.get("_mobile_actor_id") or principal.get("id"))
    # The bridge is merchant-shaped. Re-read the actual authenticated actor;
    # borrowed owner role or a cached grant cannot authorize this endpoint.
    actor = await db.users.find_one({"id": actor_id}, {"_id": 0}) if actor_id else None
    if not actor:
        raise HTTPException(403, detail={"code": "mobile_order_tracking_permission_required"})
    access = await mobile_app_access_for_user(db, actor)
    if TRACKING_PERMISSION not in set(access.get("permissions") or []):
        raise HTTPException(403, detail={"code": "mobile_order_tracking_permission_required"})
    is_owner = actor.get("role") == "owner" or actor.get("is_owner") is True
    owner = actor_id if is_owner else _text(actor.get("created_by"))
    if not owner:
        employee = await db.mezan_employees_v2.find_one({"account_user_id": actor_id},
                                                       {"_id": 0, "user_id": 1})
        owner = _text((employee or {}).get("user_id"))
    bridge_owner = _text(principal.get("_mobile_owner_id"))
    if not owner or (bridge_owner and bridge_owner != owner):
        raise HTTPException(403, detail={"code": "mobile_order_tracking_owner_scope_required"})
    return owner


def _step(label: str, actor: Any = None, at: Any = None, *, done: bool = True, source: str = "mezan") -> dict:
    return {"label": label, "actor_name": _text(actor) or None,
            "at": _iso(at) if done else None, "state": "done" if done else "pending", "source": source}


def _piece_steps(piece: dict) -> tuple[list[dict], bool]:
    if piece.get("virtual_kind"):
        ready = piece.get("assembly_status") == "ready"
        return [_step("جاهز من التجميع والعنونة", piece.get("assembly_ready_by_name"),
                      piece.get("assembly_ready_at"), done=ready)], False
    assignments = _rows(piece.get("assignment_history"))
    receipts = _rows(piece.get("supplier_receiving_history"))
    truncated = len(assignments) > MAX_HISTORY or len(receipts) > MAX_HISTORY
    assignments = assignments[-MAX_HISTORY:]
    receipts = receipts[-MAX_HISTORY:]
    first = min(assignments, key=lambda row: _iso(row.get("transferred_at")) or "") if assignments else {}
    initial_actor = (first.get("previous_responsible_employee_name")
                     or (piece.get("responsible_employee_name") if not piece.get("reassigned_at") else None))
    steps = []
    if piece.get("assigned_at") or initial_actor:
        steps.append(_step("تم إسناد المنتج لموظف التجهيز", initial_actor, piece.get("assigned_at")))
    for row in assignments:
        steps.append(_step("نُقلت عهدة المنتج إلى موظف التجهيز", row.get("responsible_employee_name"),
                           row.get("transferred_at")))
    supplier_status = _text(piece.get("supplier_dispatch_status"))
    if piece.get("sent_to_supplier_at"):
        steps.append(_step("تم إسناد المنتج إلى المورد", piece.get("supplier_name"), piece.get("sent_to_supplier_at")))
    if piece.get("supplier_ready_at"):
        steps.append(_step("جاهز لدى المورد", piece.get("supplier_name"), piece.get("supplier_ready_at")))
    if receipts:
        for row in receipts:
            steps.append(_step("تم الاستلام من المورد" + (" — " + _text(row.get("supplier_name"))
                               if _text(row.get("supplier_name")) else ""),
                               row.get("received_by_name"), row.get("received_at")))
    elif piece.get("received_at") or supplier_status == "received":
        steps.append(_step("تم الاستلام من المورد", piece.get("received_by_name"), piece.get("received_at")))
    elif supplier_status in {"sent", "ready"}:
        steps.append(_step("تم الاستلام من المورد", done=False))
    received = piece.get("preparation_receipt_status") == "received"
    steps.append(_step("تم الاستلام من موظف التجهيز", piece.get("preparation_received_by_name"),
                       piece.get("preparation_received_at"), done=received))
    steps.append(_step("جاهز من التجميع والعنونة", piece.get("assembly_ready_by_name"),
                       piece.get("assembly_ready_at"), done=piece.get("assembly_status") == "ready"))
    # Future milestones at the top match the existing bottom-to-top timeline.
    # Missing event dates remain null; no generic updated_at fills them in.
    pending = [step for step in reversed(steps) if step["state"] == "pending"]
    done = sorted((step for step in steps if step["state"] == "done"),
                  key=lambda row: row["at"] or "", reverse=True)
    unique = {(row["label"], row["actor_name"], row["at"], row["state"]): row for row in pending + done}
    return list(unique.values()), truncated


def _unknown() -> dict:
    return {"workspace": "تتبع الطلب", "page": "لا يتوفر مكان مؤكد", "employee_name": None,
            "supplier_name": None, "at": None, "known": False}


def _location(workspace: str, page: str, *, employee: Any = None, supplier: Any = None, at: Any = None) -> dict:
    return {"workspace": workspace, "page": page, "employee_name": _text(employee) or None,
            "supplier_name": _text(supplier) or None, "at": _iso(at), "known": True}


def _current_location(piece: dict, status: str, workflow: dict, registry: dict,
                      dispatch: dict, *, conflicting: bool = False, local_review: bool = False) -> dict:
    if (conflicting or status not in {"in_progress", "reviewed"}
            or piece.get("experiment_archived_at") or piece.get("active_hold_id")
            or piece.get("status") in {"blocked", "cancelled"}):
        return _unknown()
    if piece.get("virtual_kind"):
        if workflow.get("stage") not in {"in_progress", "ready_to_ship", "completed"} and not (
                local_review and workflow.get("stage") == "reviewed"):
            return _unknown()
        ready = piece.get("assembly_status") == "ready"
        return _location("التجميع والعنونة", "جاهز للتغليف" if ready else "طلبات قيد التنفيذ",
                         employee=piece.get("assembly_ready_by_name") if ready else None,
                         at=piece.get("assembly_ready_at") if ready else None)
    if piece.get("assembly_status") == "ready":
        return _location("التجميع والعنونة", "جاهز للتغليف", employee=piece.get("assembly_ready_by_name"),
                         at=piece.get("assembly_ready_at"))
    if piece.get("preparation_receipt_status") == "received":
        if (status != "in_progress" or piece.get("status") != "ready_for_assembly"
                or not _text(piece.get("preparation_received_by"))):
            return _unknown()
        return _location("الاستلام من موظف التجهيز", "الاستلام من موظف التجهيز",
                         employee=piece.get("preparation_received_by_name"), at=piece.get("preparation_received_at"))
    if piece.get("branch_handoff_at") or not _text(piece.get("responsible_employee_id")):
        return _unknown()
    supplier_status = _text(piece.get("supplier_dispatch_status"))
    employee = piece.get("responsible_employee_name")
    if supplier_status in {"partial_received", "received"} or piece.get("status") == "received":
        histories = _rows(piece.get("supplier_receiving_history"))
        last = max(histories, key=lambda row: _iso(row.get("received_at")) or "") if histories else {}
        return _location("إدارة منتجاتي", "تم الاستلام من المورد", employee=employee,
                         supplier=piece.get("supplier_name"), at=piece.get("received_at") or last.get("received_at"))
    if piece_is_available_for_supplier_dispatch(piece):
        return _location("إدارة منتجاتي", "بانتظار الإسناد للمورد", employee=employee,
                         at=registry.get("registered_at"))
    if supplier_status in {"sent", "ready"}:
        piece_id = _text(piece.get("piece_id") or piece.get("id"))
        recorded_ids = {_text(card.get("piece_id")) for source in _rows(dispatch.get("source_files"))
                        for card in _rows(source.get("cards"))}
        actor = _text(piece.get("responsible_employee_id"))
        if (piece_id in recorded_ids and _text(dispatch.get("sent_by_id")) == actor
                and _text(piece.get("sent_to_supplier_by_id")) == actor and piece.get("supplier_id")):
            return _location("إدارة منتجاتي", "قيد التنفيذ", employee=employee,
                             supplier=piece.get("supplier_name"), at=registry.get("registered_at"))
    return _unknown()


async def _fresh_local_review(db: Any, owner: str, number: str, workflow: dict,
                              raw: dict, changed: str | None, status_steps: list[dict]) -> bool:
    """Use committed local approval, not a stage flag or old Salla review.

    The normal preparation policy supplies tenant/version proof and payment
    eligibility. Tracking additionally rejects approval predating a recorded
    return to review; it never reconciles or replays that approval.
    """
    approved = await load_local_review_workflows(
        db, user_id=owner, order_numbers=[number], workflows=[workflow])
    if number not in approved:
        return False
    try:
        order = map_salla_order({**raw, "reference_id": number, "items": []})
        if not local_review_stage_eligible(order, approved[number]):
            return False
    except (ValueError, TypeError, OverflowError):
        return False
    identity = _text(workflow.get("review_completion_operation_id"))
    operation = await db.order_review_completion_operations.find_one({
        "_id": identity, "user_id": owner, "order_number": number,
        "state": "completed", "completion_mode": LOCAL_COMPLETION_MODE,
    }, {"approval_snapshot": 0, "approval_payload": 0})
    if not operation or operation.get("superseded_by"):
        return False
    event = await db.order_review_events.find_one({
        "_id": identity + ":completed", "operation_id": identity,
        "user_id": owner, "order_number": number, "event_type": "order_review_completed",
    })
    if operation.get("readback_binding"):
        try:
            committed_proof(operation, event)
        except (HTTPException, KeyError, TypeError):
            return False
    approved_at = _iso(operation.get("completed_at") or (event or {}).get("occurred_at"))
    if not approved_at:
        return False
    if event and _iso(event.get("occurred_at")) != approved_at:
        return False
    returns = [row["at"] for row in status_steps if row["status"] == "under_review"]
    if changed:
        returns.append(changed)
    return not any(at > approved_at for at in returns)


def _history_status(row: dict) -> tuple[str, str]:
    """Extract a status, never emit an unrestricted provider note as UI text."""
    for key in ("to_status", "new_status", "status"):
        values = _status_values(row.get(key))
        if len(values) == 1:
            status = next(iter(values))
            if status in _STATUS_LABELS:
                return status, _STATUS_LABELS[status]
    title = _text(row.get("title"))
    # The stored activity ledger preserves a provider status-change title.
    # Restrict to a known exact status or an explicit final transition target.
    target = re.split(r"(?:\sإلى\s|\sالى\s|\sto\s|→)", title, flags=re.IGNORECASE)[-1]
    target = target.strip(" \t\n\"'«»:.")
    status = _status_key(target)
    return (status, _STATUS_LABELS[status]) if status in _STATUS_LABELS else ("", "")


def _order_steps(source: dict, workflow: dict, activity: list[dict], reviews: list[dict],
                 names: dict[str, str]) -> tuple[list[dict], list[dict]]:
    raw = _dict(_dict(source.get("raw_by_source")).get("salla_direct"))
    created = _iso(raw.get("date") or raw.get("created_at") or source.get("order_date"), provider=True)
    rows = [_step("إنشاء الطلب في سلة", at=created, source="salla")] if created else []
    status_rows = []
    for event in activity:
        if event.get("source") != "salla_history" or event.get("event_type") != "status_changed":
            continue
        at = _iso(event.get("occurred_at"), provider=True)
        if not at:
            continue
        status, label = _history_status(event)
        if not status:
            # A free-form note may contain customer/financial data. Do not
            # guess a target state from arbitrary substrings or expose it.
            continue
        row = _step(label, event.get("actor_name"), at, source="salla")
        status_rows.append({**row, "status": status})
        rows.append(row)
    for event in reviews:
        if event.get("event_type") != "order_review_completed" or not _iso(event.get("occurred_at")):
            continue
        actor = event.get("actor_name") or names.get(_text(event.get("actor_id")))
        rows.append(_step("تمت المراجعة — ميزان", actor, event.get("occurred_at")))
    if not reviews and workflow.get("reviewed_at") and workflow.get("completion_mode") is None:
        rows.append(_step("تمت المراجعة — ميزان", workflow.get("reviewed_by_name"), workflow["reviewed_at"]))
    fields = (
        ("in_progress_at", "بدأ تجهيز الطلب", "in_progress_by_name"),
        ("ready_to_ship_at", "اكتمل تجهيز القطع", None),
        ("completed_at", "اكتمل التجميع والعنونة", "completed_by_name"),
        ("carrier_label_print_confirmed_at", "طُبعت بوليصة الشحن", "carrier_label_print_confirmed_by_name"),
        ("carrier_handoff_scanned_at", "استلم موظف تسليم الشحن الشحنة", "carrier_handoff_employee_name"),
        ("store_courier_assigned_at", "أُسند الطلب إلى مندوب المتجر", "store_courier_assignee_name"),
        ("store_courier_picked_up_at", "استلم مندوب المتجر الشحنة", "store_courier_assignee_name"),
        ("delivering_at", "جاري توصيل الشحنة", None),
        ("delivered_at", "تم توصيل الشحنة", None),
    )
    for field, label, actor in fields:
        if workflow.get(field):
            rows.append(_step(label, workflow.get(actor) if actor else None, workflow[field]))
    # Deduplicate the same stored fact only, not repeated visits to a status.
    unique = {(row["source"], row["label"], row["at"]): row for row in rows}
    return (sorted(unique.values(), key=lambda row: row["at"] or "", reverse=True),
            sorted(status_rows, key=lambda row: row["at"] or "", reverse=True))


def _route_notice(piece: dict, status: str, label: str, location: dict, steps: list[dict]) -> str | None:
    if status in {"in_progress", "reviewed"}:
        return None
    # A completed/shipped order with actual assembled pieces is a legitimate
    # completed path, not a stale preparation deviation.
    if status in {"completed", "shipped", "delivering", "delivered"} and piece.get("assembly_status") == "ready":
        return None
    if status == "unknown":
        return "تعذّر تأكيد حالة الطلب الحالية في سلة؛ يُعرض سجل المراحل المثبتة فقط."
    if location.get("known"):
        last = location["page"]
        if location.get("employee_name"):
            last += " لدى " + location["employee_name"]
    else:
        completed = [row for row in steps if row["state"] == "done"]
        if not completed:
            return None
        last = completed[0]["label"]
        if completed[0].get("actor_name"):
            last += " — " + completed[0]["actor_name"]
    suffix = ("لا يُعد تغير حالة سلة وحده دليلًا على إنجاز مراحل إضافية في ميزان."
              if piece.get("assembly_status") == "ready"
              else "لا يثبت سجل ميزان استكمال بقية مراحل تجهيز هذه القطعة.")
    return f"آخر مرحلة مسجلة للقطعة: {last}. حالة الطلب في سلة الآن «{label}»؛ {suffix}"


async def read_order_tracking(db: Any, *, owner: str, number: str) -> dict:
    selector = {"user_id": owner, "order_number": number}
    source = await db.unified_orders.find_one(selector, {"_id": 0, "order_number": 1,
        "order_date": 1, "order_status": 1, "order_status_slug": 1, "order_status_changed_at": 1,
        "g47_salla_snapshot.requires_authoritative_refresh": 1,
        "raw_by_source.salla_direct.items": 1, "raw_by_source.salla_direct.status": 1,
        "raw_by_source.salla_direct.status_slug": 1, "raw_by_source.salla_direct.date": 1,
        "raw_by_source.salla_direct.created_at": 1, "raw_by_source.salla_direct.status_changed_at": 1,
        # Private inputs to the existing local-review eligibility policy only.
        # None of these payment fields enters the public tracking projection.
        "raw_by_source.salla_direct.payment": 1, "raw_by_source.salla_direct.payment_method": 1,
        "raw_by_source.salla_direct.payment_actions": 1, "raw_by_source.salla_direct.paid_amount": 1,
        "raw_by_source.salla_direct.remaining_amount": 1, "raw_by_source.salla_direct.has_remaining_amount": 1,
        "raw_by_source.salla_direct.payment_collection_status": 1})
    if source is None:
        raise HTTPException(404, detail={"code": "mobile_order_tracking_not_found", "message": "لم يُعثر على الطلب."})
    workflow = await db[WORKFLOWS].find_one(selector, {"_id": 0}) or {}
    activity = await db.order_activity_events_v2.find({**selector, "source": "salla_history",
        "event_type": "status_changed"}, {"_id": 0, "source": 1, "event_type": 1, "occurred_at": 1,
        "title": 1, "actor_name": 1, "status": 1, "new_status": 1, "to_status": 1}
        ).sort("occurred_at", -1).limit(MAX_ORDER_EVENTS + 1).to_list(MAX_ORDER_EVENTS + 1)
    reviews = await db.order_review_events.find({**selector, "event_type": "order_review_completed"},
        {"_id": 0, "event_type": 1, "occurred_at": 1, "actor_id": 1, "actor_name": 1}
        ).sort("occurred_at", -1).limit(MAX_ORDER_EVENTS + 1).to_list(MAX_ORDER_EVENTS + 1)
    physical = await db[PIECES].find({**selector, "experiment_archived_at": None},
                                    {"_id": 0, "image_b64": 0}).sort("piece_id", 1).limit(MAX_PIECES + 1).to_list(MAX_PIECES + 1)
    truncated = len(physical) > MAX_PIECES or len(activity) > MAX_ORDER_EVENTS or len(reviews) > MAX_ORDER_EVENTS
    physical = physical[:MAX_PIECES]
    activity, reviews = activity[:MAX_ORDER_EVENTS], reviews[:MAX_ORDER_EVENTS]
    actor_ids = sorted({_text(row.get("actor_id")) for row in reviews} - {""})
    names = {}
    if actor_ids:
        employees = await db.mezan_employees_v2.find({"user_id": owner,
            "$or": [{"id": {"$in": actor_ids}}, {"account_user_id": {"$in": actor_ids}}]},
            {"_id": 0, "id": 1, "account_user_id": 1, "display_name": 1}).to_list(MAX_ORDER_EVENTS)
        for employee in employees:
            for key in ("id", "account_user_id"):
                if _text(employee.get(key)) in actor_ids and _text(employee.get("display_name")):
                    names[_text(employee[key])] = _text(employee["display_name"])
        users = await db.users.find({"id": {"$in": actor_ids},
            "$or": [{"created_by": owner}, {"id": owner}]},
            {"_id": 0, "id": 1, "name": 1, "display_name": 1}).to_list(MAX_ORDER_EVENTS)
        for actor in users:
            name = _text(actor.get("name") or actor.get("display_name"))
            if name:
                names[_text(actor.get("id"))] = name
    raw = _dict(_dict(source.get("raw_by_source")).get("salla_direct"))
    raw_items = _rows(raw.get("items"))
    truncated |= len(raw_items) > MAX_ITEMS
    items = []
    source_item_counts = Counter()
    for index, row in enumerate(raw_items[:MAX_ITEMS]):
        try:
            item = _map_item(order_number=number, item=row, index=index)
        except (ValueError, TypeError, OverflowError):
            truncated = True
            continue
        if not math.isfinite(item.quantity):
            truncated = True
            continue
        source_item_counts[item.order_item_id] += 1
        if source_item_counts[item.order_item_id] > 1:
            continue  # One public identity, with conflict/partial evidence below.
        items.append({"order_item_id": item.order_item_id, "name": item.name, "sku": item.sku or "",
                      "image_url": _url(item.image_url), "quantity": item.quantity, "pieces": [], "coverage": "no_records"})
    # Bound virtual expansion before using the existing deterministic identity
    # projection: a malformed quantity must not allocate an unbounded list.
    virtual_source = {**workflow, "items": [], "operational_items": []}
    budget = max(0, MAX_PIECES - len(physical))
    for row in _rows(workflow.get("items"))[:MAX_ITEMS]:
        if row.get("preparation_route") != "direct_assembly":
            continue
        try:
            quantity = max(1, int(row.get("quantity") or 1))
        except (ValueError, TypeError, OverflowError):
            truncated = True
            continue
        allowed = min(quantity, budget)
        if allowed < quantity:
            truncated = True
        if allowed:
            virtual_source["items"].append({**row, "quantity": allowed})
            budget -= allowed
    for row in _rows(workflow.get("operational_items")):
        if row.get("blocks_order_completion") is False:
            continue
        if budget <= 0:
            truncated = True
            break
        virtual_source["operational_items"].append(row)
        budget -= 1
    pieces = physical + _workflow_assembly_pieces(virtual_source, order_number=number)
    batch_ids = sorted({_text(row.get("batch_id")) for row in physical} - {""})
    dispatch_ids = sorted({_text(row.get("supplier_dispatch_id")) for row in physical} - {""})
    registries = await db[REGISTRY].find({"user_id": owner, "batch_id": {"$in": batch_ids}},
        {"_id": 0, "batch_id": 1, "registered_at": 1}).to_list(MAX_PIECES + 1) if batch_ids else []
    dispatches = await db[DISPATCHES].find({"user_id": owner, "id": {"$in": dispatch_ids}, "status": {"$ne": "building"}},
        {"_id": 0, "id": 1, "sent_by_id": 1, "source_files.cards.piece_id": 1}).to_list(MAX_PIECES + 1) if dispatch_ids else []
    registry_counts = Counter(_text(row.get("batch_id")) for row in registries)
    registry_by_id = {_text(row.get("batch_id")): row for row in registries if registry_counts[_text(row.get("batch_id"))] == 1}
    dispatch_counts = Counter(_text(row.get("id")) for row in dispatches)
    dispatch_by_id = {_text(row.get("id")): row for row in dispatches if dispatch_counts[_text(row.get("id"))] == 1}
    status, label, changed = _status_facts(source)
    order_steps, status_steps = _order_steps(source, workflow, activity, reviews, names)
    if status_steps and status_steps[0]["status"] == status:
        changed = status_steps[0]["at"]
    local_contract = workflow.get("completion_mode") is not None
    local_review = status in {"under_review", "reviewed", "in_progress"} and await _fresh_local_review(
        db, owner, number, workflow, raw, changed if status == "under_review" else None, status_steps)
    unconfirmed_review = local_contract and status in {"under_review", "reviewed", "in_progress"} and not local_review
    # This effective state is for page eligibility only. The response status,
    # Salla label/date and history remain the actual provider facts.
    location_status = ("reviewed" if workflow.get("stage") == "reviewed" else "in_progress") if local_review else status
    if unconfirmed_review:
        location_status = "unknown"
    by_item = {item["order_item_id"]: item for item in items}
    item_counts = source_item_counts
    piece_counts = Counter(_text(row.get("piece_id") or row.get("id")) for row in pieces)
    unit_counts = Counter((_text(row.get("order_item_id")), _unit_index(row.get("unit_index"))) for row in pieces
                          if row.get("virtual_kind") != "operational" and _unit_index(row.get("unit_index")) is not None)
    seen = set()
    incomplete = set()
    observed_only_items = set()
    for piece in pieces:
        piece_id = _text(piece.get("piece_id") or piece.get("id"))
        item_id = _text(piece.get("order_item_id"))
        if piece.get("virtual_kind") == "operational":
            item_id = "operational:" + piece_id
        if not piece_id or piece_id in seen:
            if item_id:
                incomplete.add(item_id)
            continue
        seen.add(piece_id)
        item = by_item.get(item_id)
        if item is None:
            if len(items) >= MAX_ITEMS:
                truncated = True
                continue
            # Never match siblings by SKU/name; an unmatched durable item stays
            # separate with partial coverage instead of contaminating a line.
            item_id = item_id or "unlinked:" + piece_id
            item = {"order_item_id": item_id, "name": _text(piece.get("product_name")) or "منتج",
                    "sku": _text(piece.get("sku")), "image_url": _url(piece.get("selected_image_url")
                    or piece.get("resolved_image_url") or piece.get("image_url")),
                    "quantity": 1, "pieces": [], "coverage": "partial"}
            items.append(item)
            by_item[item_id] = item
            if piece.get("virtual_kind") != "operational":
                incomplete.add(item_id)
                observed_only_items.add(item_id)
        unit = _unit_index(piece.get("unit_index"))
        conflict = (item_counts[item_id] > 1 or piece_counts[piece_id] > 1
                    or (piece.get("virtual_kind") != "operational" and unit_counts[(item_id, unit)] > 1))
        if conflict or unit is None or unit < 1 or (item_id not in observed_only_items and unit > item["quantity"]):
            incomplete.add(item_id)
            conflict = True
        if unit is not None and (unit < 1 or (item_id not in observed_only_items and unit > item["quantity"])):
            unit = None
        steps, history_cut = _piece_steps(piece)
        truncated |= history_cut
        registry = registry_by_id.get(_text(piece.get("batch_id")), {})
        dispatch = dispatch_by_id.get(_text(piece.get("supplier_dispatch_id")), {})
        location = _current_location(piece, location_status, workflow, registry, dispatch,
                                     conflicting=conflict, local_review=local_review)
        prior_location = _current_location(piece, "in_progress", workflow, registry, dispatch, conflicting=conflict)
        item["pieces"].append({"piece_id": piece_id, "unit_index": unit, "current_location": location,
            "route_steps": steps, "route_notice": None if local_review else _route_notice(piece, status, label, prior_location, steps)})
    for item in items:
        item["pieces"].sort(key=lambda row: (row["unit_index"] or 0, row["piece_id"]))
        if item["order_item_id"] in observed_only_items:
            # Source line absent: quantity is the observed physical count only;
            # coverage remains partial and never claims full Salla coverage.
            item["quantity"] = len(item["pieces"])
            for piece in item["pieces"]:
                piece["current_location"] = _unknown()
        if len(item["pieces"]) > item["quantity"]:
            item["pieces"] = item["pieces"][:int(item["quantity"])]
            incomplete.add(item["order_item_id"])
            truncated = True
            for piece in item["pieces"]:
                piece["current_location"] = _unknown()
        count = len(item["pieces"])
        item["coverage"] = ("no_records" if not count else "complete"
                            if count == item["quantity"] and item["order_item_id"] not in incomplete
                            and item_counts[item["order_item_id"]] <= 1 else "partial")
    deviated = [piece for item in items for piece in item["pieces"] if piece.get("route_notice")]
    mezan_steps = [row for row in order_steps if row["source"] == "mezan"]
    last_order_step = f" آخر مرحلة مثبتة للطلب في ميزان: {mezan_steps[0]['label']}." if mezan_steps else ""
    order_notice = (f"حالة الطلب في سلة الآن «{label}» تختلف عن مسار التجهيز المسجل."
                    + last_order_step + " آخر مرحلة مثبتة لكل قطعة موضحة أدناه.") if deviated else None
    if status == "unknown":
        order_notice = "تعذّر تأكيد حالة الطلب الحالية في سلة؛ تُعرض المراحل المثبتة دون افتراض اكتمال المسار."
    elif unconfirmed_review:
        order_notice = "تعذّر تأكيد صلاحية اعتماد المراجعة الحالي في ميزان؛ تُعرض المراحل المثبتة دون افتراض مكان حالي للقطع."
    return {"schema_version": 1, "order_number": number, "status": status, "status_label": label,
            "order_created_at": _iso(raw.get("date") or raw.get("created_at") or source.get("order_date"), provider=True),
            "status_changed_at": changed, "observed_at": datetime.now(timezone.utc).isoformat(),
            "order_route_steps": order_steps, "route_notice": order_notice,
            "items": items, "truncated": bool(truncated)}


def make_mobile_order_tracking_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/mobile/order-tracking", tags=["mobile-order-tracking"])

    @router.get("/{order_number}")
    async def track(order_number: str, response: Response, user: dict = Depends(current_user)):
        owner = await _owner_scope(db, user)
        if not re.fullmatch(r"[0-9]{1,32}", order_number):
            raise HTTPException(422, detail={"code": "mobile_order_tracking_number_invalid", "message": "أدخل رقم الطلب الصحيح."})
        response.headers["Cache-Control"] = "private, no-store"
        return await read_order_tracking(db, owner=owner, number=order_number)

    return router
