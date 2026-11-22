"""Committed employee movements → bounded private notices → existing push outbox.

Financial writers append compact intents to their existing revision CAS. The
push worker reads only that indexed queue, never balances or financial history.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import re
import unicodedata
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from mobile_app_permissions import mobile_app_access_for_user
from operational_balance_access import POLICY_FIELD
from operational_balance_store import STATES, digest
from review_session_fence import merchant_for_actor

NOTICES = "employee_finance_notifications_v1"
PENDING = "employee_finance_notification_pending"
PENDING_ACTIVE = "employee_finance_notification_pending_active"
BATCH_SIZE = 10
NOTICE_ID = re.compile(r"^fn_[a-f0-9]{64}$")
log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


def _plain(value, maximum=160):
    value = "".join(c for c in str(value or "") if unicodedata.category(c) not in {"Cc", "Cf"})
    return " ".join(value.split())[:maximum]


def _instant(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("financial_notice_timestamp_invalid")
    return stamp.astimezone(timezone.utc).isoformat()


def _active(row):
    return bool(row) and not any(row.get(k) for k in ("disabled", "deleted_at", "archived", "is_archived", "deleted", "is_deleted")) and row.get("is_active") is not False and row.get("active") is not False


async def _binding(db, actor_id, *, merchant_id=None, employee_id=None, require_app=True, required_permission=None):
    actor = await db["users"].find_one({"id": actor_id}, {
        "_id": 0, "id": 1, "role": 1, "is_owner": 1, "created_by": 1,
        "disabled": 1, "active": 1, "is_active": 1, "deleted_at": 1,
        "archived": 1, "is_archived": 1, "deleted": 1, "is_deleted": 1,
        POLICY_FIELD: 1,
    })
    if not _active(actor) or actor.get("role") == "store_driver":
        return None
    owner = await merchant_for_actor(db, actor)
    if not owner or merchant_id is not None and owner != merchant_id:
        return None
    employees = await db["mezan_employees_v2"].find({
        "user_id": owner, "account_user_id": actor_id,
    }, {"_id": 0, "id": 1, "user_id": 1, "account_user_id": 1, "display_name": 1, "name": 1,
        "status": 1, "disabled": 1, "active": 1, "is_active": 1, "deleted_at": 1, "archived": 1,
        "is_archived": 1, "deleted": 1, "is_deleted": 1}).limit(2).to_list(2)
    if len(employees) != 1:
        return None
    employee = employees[0]
    if (not _active(employee) or employee.get("status") != "active"
            or employee_id is not None and employee.get("id") != employee_id):
        return None
    if require_app and (required_permission or actor.get("role") != "owner" and actor.get("is_owner") is not True):
        access = await mobile_app_access_for_user(db, actor)
        if (not access.get("enabled") or not access.get("permissions")
                or required_permission and required_permission not in access.get("permissions", [])):
            return None
    return {"merchant_id": owner, "recipient_id": actor_id, "employee_id": employee["id"],
        "employee_name": _plain(employee.get("display_name") or employee.get("name") or "الموظف")}


async def _target_binding(db, merchant_id, employee_id):
    rows = await db["mezan_employees_v2"].find({"user_id": merchant_id, "id": employee_id},
        {"_id": 0, "account_user_id": 1}).limit(2).to_list(2)
    if len(rows) != 1 or not rows[0].get("account_user_id"):
        return None
    return await _binding(db, rows[0]["account_user_id"], merchant_id=merchant_id,
        employee_id=employee_id, require_app=False)


def _kind(movement, account_kind, leg):
    direction, kind = movement["direction"], movement["kind"]
    if leg == "source_custody":
        return ("custody_spend", "صرف من العهدة", "صرف من عهدتك") if direction == "outgoing" else (
            "custody_addition", "إضافة إلى العهدة", "إضافة إلى عهدتك")
    if account_kind == "employee_custody":
        addition = direction == ("incoming" if kind == "correction" else "outgoing")
        if kind == "correction":
            return ("custody_credit_adjustment", "تسوية زيادة العهدة", "تسوية بزيادة عهدتك") if addition else (
                "custody_debit_adjustment", "تسوية نقص العهدة", "تسوية بنقص عهدتك")
        return ("custody_grant", "تسليم عهدة", "إضافة إلى عهدتك") if addition else (
            "custody_return", "إرجاع عهدة", "إرجاع من عهدتك")
    if kind == "correction":
        if movement.get("workspace_reason") == "accrual":
            return "accrual", "استحقاق", "استحقاق لك"
        return ("credit_adjustment", "تسوية لصالحك", "تسوية لصالحك") if direction == "outgoing" else (
            "debit_adjustment", "تسوية عليك", "تسوية عليك")
    if kind == "refund":
        return ("refund_to_employee", "استرداد لك", "استرداد لك") if direction == "outgoing" else (
            "refund_from_employee", "رد مبلغ منك", "رد مبلغ منك")
    if kind == "settlement":
        return ("settlement_payment", "تسوية دفعة لك", "تسوية دفعة لك") if direction == "outgoing" else (
            "settlement_collection", "تسوية تحصيل منك", "تسوية تحصيل منك")
    if direction == "outgoing":
        return "payment", "دفعة لك", "دفعة لك"
    return "collection", "تحصيل منك", "تحصيل منك"


async def stage_employee_financial_notifications(db, owner, state, movement):
    """Read binding and append only inside the financial writer's proposed CAS.

    No notification/provider writes occur here. Existing receipts return before
    this seam, so an old transaction replay never manufactures a new notice.
    """
    targets = []
    if movement.get("party_type") in {"employee", "employee_custody"}:
        targets.append((movement["party_type"], movement["party_id"], "target"))
    if movement.get("bank_kind") == "employee_custody" and movement.get("bank_id"):
        targets.append(("employee_custody", movement["bank_id"], "source_custody"))
    if not targets or movement.get("source") not in {"mezan2", "employee_app"}:
        return
    recorded = movement.get("recorded_by")
    if not isinstance(recorded, dict) or not recorded.get("id") or not recorded.get("name"):
        return
    amount = Decimal(str(movement["amount"]))
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal(".01")):
        return
    # The employee and custody contracts currently accept SAR exclusively.
    if movement.get("currency") != "SAR":
        return
    created_at = _instant(movement["occurred_at"])
    business_date = movement.get("business_date") or datetime.fromisoformat(created_at).astimezone(ZoneInfo("Asia/Riyadh")).date().isoformat()
    pending = state.setdefault(PENDING, [])
    existing = {row["id"] for row in pending}
    for account_kind, employee_id, leg in targets:
        bound = await _target_binding(db, owner, employee_id)
        if not bound:
            continue
        identity = "fn_" + digest([owner, movement["id"], employee_id, account_kind, leg])
        if identity in existing:
            continue
        kind, label, phrase = _kind(movement, account_kind, leg)
        registrar = {"id": str(recorded["id"]), "name": _plain(recorded["name"], 100)}
        formatted = format(amount, ".2f")
        pending.append({"id": identity, **bound, "movement_id": str(movement["id"]),
            "account_kind": account_kind, "kind": kind, "kind_label": label,
            "amount": formatted, "currency": "SAR", "recorded_by": registrar,
            "business_date": business_date, "occurred_at": created_at, "created_at": created_at,
            "description": _plain(movement.get("note"), 500),
            "title": "أماسي — إشعار مالي",
            "body": f"تم تسجيل {phrase} بمبلغ {formatted} ريال بواسطة {registrar['name']}.",
        })
        existing.add(identity)
        state[PENDING_ACTIVE] = True


async def ensure_financial_notification_indexes(db):
    await db[STATES].create_index([(PENDING_ACTIVE, 1), ("_id", 1)],
        name="employee_finance_notice_pending", partialFilterExpression={PENDING_ACTIVE: True})
    await db[NOTICES].create_index([("merchant_id", 1), ("recipient_id", 1), ("employee_id", 1),
        ("created_at", -1), ("id", -1)], name="employee_finance_notice_feed")
    await db[NOTICES].create_index([("merchant_id", 1), ("recipient_id", 1), ("employee_id", 1),
        ("read_at", 1)], name="employee_finance_notice_unread")
    await db[NOTICES].create_index([("dispatch_pending", 1), ("created_at", 1), ("id", 1)],
        name="employee_finance_notice_dispatch", partialFilterExpression={"dispatch_pending": True})


async def _drain_committed_intents(db):
    states = await db[STATES].find({PENDING_ACTIVE: True}, {
        "_id": 1, "owner_id": 1, "revision": 1, PENDING: {"$slice": BATCH_SIZE + 1},
    }).sort("_id", 1).limit(1).max_time_ms(1500).to_list(1)
    if not states:
        return 0
    state = states[0]
    owner = state.get("owner_id")
    if state.get("_id") != owner or type(state.get("revision")) is not int:
        raise ValueError("financial_notice_owner_state_invalid")
    pending = state.get(PENDING, [])
    rows = pending[:BATCH_SIZE]
    for row in rows:
        if not isinstance(row, dict) or not NOTICE_ID.fullmatch(str(row.get("id") or "")) or row.get("merchant_id") != owner:
            raise ValueError("financial_notice_intent_invalid")
        await db[NOTICES].update_one({"_id": row["id"]}, {"$setOnInsert": {
            **row, "_id": row["id"], "schema_version": 1, "read_at": None, "dispatch_pending": True,
        }}, upsert=True)
    # Increment the SAME revision used by replace_one financial writers. A
    # concurrent save/drain cannot overwrite an append or resurrect pulled work.
    await db[STATES].update_one({"_id": owner, "owner_id": owner, "revision": state["revision"]}, {
        "$pull": {PENDING: {"id": {"$in": [row["id"] for row in rows]}}},
        "$set": {PENDING_ACTIVE: len(pending) > BATCH_SIZE}, "$inc": {"revision": 1},
    })
    return len(rows)


async def retry_employee_financial_notifications(db):
    """One bounded existing-worker pass; no balance calculations or new timer."""
    materialized = await _drain_committed_intents(db)
    from operational_push import enabled, enqueue_operational_push
    if not enabled():
        return {"materialized": materialized, "dispatched": 0}
    notices = await db[NOTICES].find({"dispatch_pending": True}, {
        "_id": 1, "id": 1, "merchant_id": 1, "recipient_id": 1, "employee_id": 1,
        "created_at": 1, "read_at": 1,
    }).sort([("created_at", 1), ("id", 1)]).limit(BATCH_SIZE).max_time_ms(1500).to_list(BATCH_SIZE)
    dispatched = 0
    for notice in notices:
        try:
            bound = await _binding(db, notice["recipient_id"], merchant_id=notice["merchant_id"], employee_id=notice["employee_id"])
            fresh = datetime.fromisoformat(_instant(notice["created_at"])) >= _now() - timedelta(days=2)
            if bound and fresh and not notice.get("read_at"):
                await enqueue_operational_push(db, recipient_id=notice["recipient_id"], event_id=notice["id"],
                    merchant_id=notice["merchant_id"], notification_type="employee_finance",
                    financial_notice_id=notice["id"], created_at=datetime.fromisoformat(_instant(notice["created_at"])))
            await db[NOTICES].update_one({"_id": notice["id"], "merchant_id": notice["merchant_id"],
                "recipient_id": notice["recipient_id"]}, {"$set": {"dispatch_pending": False}})
            dispatched += 1
        except Exception:
            # Preserve retryable work; never log amounts, names or credentials.
            log.warning("employee_finance_notification_dispatch_retry")
    return {"materialized": materialized, "dispatched": dispatched}


def _scope(bound):
    return {key: bound[key] for key in ("merchant_id", "recipient_id", "employee_id")}


async def financial_actor_context(db, user, *, required_permission=None):
    actor_id = user.get("_mobile_actor_id") if user.get("_session_client") == "amasi_mobile" else None
    bound = await _binding(db, actor_id or user.get("id"), required_permission=required_permission)
    if not bound:
        raise HTTPException(403, detail={"code": "employee_financial_account_unavailable", "message": "حساب الموظف المالي غير متاح"})
    return bound


async def financial_unread_count(db, merchant_id, actor_id):
    bound = await _binding(db, actor_id, merchant_id=merchant_id)
    if not bound:
        return 0
    count = await db[NOTICES].count_documents({**_scope(bound), "read_at": None}, maxTimeMS=1500)
    latest = await _binding(db, actor_id, merchant_id=merchant_id, employee_id=bound["employee_id"])
    return count if latest else 0


def _public(row, detail=False):
    keys = ("id", "title", "body", "kind_label", "amount", "currency", "created_at", "read_at")
    if detail:
        keys = ("id", "movement_id", "employee_name", "kind", "kind_label", "amount", "currency",
            "recorded_by", "business_date", "occurred_at", "created_at", "description", "read_at")
    return {key: row.get(key) for key in keys}


async def _still_bound(db, bound):
    latest = await _binding(db, bound["recipient_id"], merchant_id=bound["merchant_id"], employee_id=bound["employee_id"])
    if not latest:
        raise HTTPException(403, detail={"code": "employee_financial_scope_changed"})


def _decode_cursor(value):
    try:
        if len(value) > 400:
            raise ValueError()
        raw = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if not isinstance(raw, list) or len(raw) != 2 or not NOTICE_ID.fullmatch(raw[1]):
            raise ValueError()
        return _instant(raw[0]), raw[1]
    except (TypeError, ValueError, KeyError, IndexError, UnicodeDecodeError):
        raise HTTPException(422, detail={"code": "employee_financial_cursor_invalid"}) from None


async def financial_notification_list(db, user, *, limit=10, cursor=None):
    bound = await financial_actor_context(db, user)
    query = _scope(bound)
    if cursor:
        created_at, identity = _decode_cursor(cursor)
        query["$or"] = [{"created_at": {"$lt": created_at}}, {"created_at": created_at, "id": {"$lt": identity}}]
    fields = {key: 1 for key in ("id", "title", "body", "kind_label", "amount", "currency", "created_at", "read_at")}
    fields["_id"] = 0
    rows = await db[NOTICES].find(query, fields).sort([("created_at", -1), ("id", -1)]).limit(limit + 1).max_time_ms(1500).to_list(limit + 1)
    await _still_bound(db, bound)
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([last["created_at"], last["id"]], separators=(",", ":")).encode()).decode().rstrip("=")
    return {"items": [_public(row) for row in items], "next_cursor": next_cursor}


async def financial_notification_detail(db, user, identity):
    if not NOTICE_ID.fullmatch(identity):
        raise HTTPException(404, detail={"code": "employee_financial_notification_not_found"})
    bound = await financial_actor_context(db, user)
    row = await db[NOTICES].find_one({"_id": identity, **_scope(bound)}, {"_id": 0, "dispatch_pending": 0})
    if not row:
        raise HTTPException(404, detail={"code": "employee_financial_notification_not_found"})
    await _still_bound(db, bound)
    return _public(row, detail=True)


async def mark_financial_notification_read(db, user, identity):
    if not NOTICE_ID.fullmatch(identity):
        raise HTTPException(404, detail={"code": "employee_financial_notification_not_found"})
    bound = await financial_actor_context(db, user)
    query = {"_id": identity, **_scope(bound)}
    if not await db[NOTICES].find_one(query, {"_id": 1}):
        raise HTTPException(404, detail={"code": "employee_financial_notification_not_found"})
    await _still_bound(db, bound)
    await db[NOTICES].update_one({**query, "read_at": None}, {"$set": {"read_at": _now().isoformat()}})
    return {"ok": True}


async def financial_push_payload(db, device, job):
    identity = str(job.get("financial_notice_id") or "")
    if (not NOTICE_ID.fullmatch(identity) or job.get("event_id") != identity
            or job.get("notification_type") != "employee_finance"
            or device.get("kind") != "expo" or device.get("financial_notifications_version") != 1
            or device.get("actor_id") != job.get("actor_id") or device.get("merchant_id") != job.get("merchant_id")):
        return None
    bound = await _binding(db, job["actor_id"], merchant_id=job["merchant_id"])
    if not bound:
        return None
    row = await db[NOTICES].find_one({"_id": identity, **_scope(bound), "read_at": None},
        {"title": 1, "body": 1, "created_at": 1})
    if not row or datetime.fromisoformat(_instant(row["created_at"])) < _now() - timedelta(days=2):
        return None
    if not await _binding(db, bound["recipient_id"], merchant_id=bound["merchant_id"], employee_id=bound["employee_id"]):
        return None
    return {"notification_type": "employee_finance", "financial_notice_id": identity,
        "event_id": identity, "recipient_id": bound["recipient_id"], "priority": "normal",
        "title": row["title"], "body": row["body"]}
