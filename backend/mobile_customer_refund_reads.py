"""Read-only employee presentation of the canonical operational refund source.

No money formulas, reservations, provider calls or confirmation writers live
here. The existing inbox still examines up to 20,001 candidate orders before
pagination; this adapter does not claim that eight response rows bound that
upstream work. Its additional status query is limited to the displayed orders.
Exact search uses the existing single-order proof reader instead of that scan.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, Response

from customer_refund_access import (
    customer_refund_access_signature, load_customer_refund_access,
)
from mobile_order_tracking import _status_facts
from operational_balance_store import digest, fail, read
from operational_customer_adjustments import (
    adjustment_from_order, customer_adjustments_page, normalized_order_number,
)
from operational_read_runtime import read_source_cursor, source_read_is_owned
from operational_visible_report_read import run_visible_report_read
from operational_workspace_refunds import (
    PROVIDERS, _bank_choices, _capacity, _external_history, refund_history_page, refund_order,
)


PREFIX = "/operational-balances/workspace/mobile-refunds"
PAGE_SIZE = 8
READ_TIMEOUT_SECONDS = 12
STATUS_PROJECTION = {"_id": 0, "user_id": 1, "order_status": 1, "order_status_slug": 1,
    "g47_salla_snapshot.requires_authoritative_refresh": 1,
    **{"raw_by_source.salla_direct." + key: 1 for key in (
        "id", "reference_id", "order_number", "status", "status_slug")}}


async def _access(db, principal, owner=None):
    policy = await load_customer_refund_access(db, principal, owner)
    policy.require_enabled()
    policy.require_operation("customer.refund_read")
    return policy


async def _recheck(db, principal, policy):
    current = await _access(db, principal, policy.owner_id)
    if customer_refund_access_signature(current) != customer_refund_access_signature(policy):
        fail("customer_refund_access_changed", "تغيرت صلاحيات الاسترجاع؛ أعد فتح الصفحة", 409)


def _context(policy):
    return {"session_scope": digest([policy.owner_id, policy.actor["id"]]),
        "permission_version": policy.version,
        "can_record_execution": policy.can_operation("customer.refund_execute")}


def _values(identities):
    values = list(dict.fromkeys(str(value) for value in identities))
    return [*values, *[int(value) for value in values if value.isascii() and value.isdecimal()
        and len(value) <= 19 and str(int(value)) == value and int(value) <= 2**63 - 1]]


async def _with_current_status(db, owner, rows):
    if not rows:
        return []
    # Identity is tied to BOTH the canonical order ID and reference number.
    # Ambiguous or stale snapshots produce an explicit unknown status.
    selector = {"user_id": owner, "raw_by_source.salla_direct.id": {
        "$in": _values(row["order_id"] for row in rows)}}
    limit = len(rows) * 2 + 1
    cursor = db["unified_orders"].find(selector, STATUS_PROJECTION).limit(limit).max_time_ms(1500)
    sources = await read_source_cursor(cursor, limit) if source_read_is_owned() else await cursor.to_list(limit)
    by_identity = {}
    for source in sources:
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        if source.get("user_id") != owner:
            continue
        identity = (str(raw.get("id") or ""), str(raw.get("reference_id") or raw.get("order_number") or ""))
        by_identity.setdefault(identity, []).append(source)
    result = []
    for row in rows:
        matches = by_identity.get((row["order_id"], row["order_number"]), [])
        if len(matches) == 1 and len(sources) < limit:
            slug, label, _ = _status_facts(matches[0])
            if slug == "unknown":
                label = "حالة الطلب غير مؤكدة"
        else:
            slug, label = "unknown", "حالة الطلب غير مؤكدة"
        result.append({**row, "order_status": {"slug": slug, "label": label}})
    return result


async def _order_context(db, policy, state, number):
    order = await refund_order(db, policy.owner_id, number, state=state)
    banks = await _bank_choices(db, policy.owner_id, policy)
    methods = [{"id": "bank", "label": "تحويل بنكي"}] if banks else []
    if order["provider"] in PROVIDERS and policy.resource("provider", order["provider"])["use"]:
        methods.append({"id": "original", "label": PROVIDERS[order["provider"]]})
    if not methods:
        fail("customer_refund_order_unavailable", "الطلب غير متاح ضمن مصادر الاسترجاع المصرح بها", 404)
    await _external_history(db, policy.owner_id, order, state)
    adjustment = adjustment_from_order(order, state)
    capacity = _capacity(state, order)
    result = {key: order[key] for key in ("id", "order_number", "currency", "total")}
    result.update(adjustment, executed_refunded_amount=adjustment["confirmed_refunded_amount"])
    result.update(capacity, methods=methods, banks=banks,
        execution_amount_limit=capacity["paid_amount"],
        can_request=bool(methods) and Decimal(capacity["available_amount"]) > 0
            and policy.can_operation("customer.refund_request"),
        can_record_execution=bool(methods) and policy.can_operation("customer.refund_execute"),
        # This describes the frozen canonical record-execution contract. It
        # must not imply that an internal identity is a bank/provider reference.
        reference_required=True)
    result.update(_execution_constraints(state, policy, result))
    result["can_record_execution"] = result["can_record_execution"] and not result["recording_blocked_reason"]
    return adjustment, result


def _execution_constraints(state, policy, order):
    """Describe the unchanged writer's exact reservation/document contract.

    No allocation or remainder is invented. If a single authorized pending
    case cannot be identified, the employee must resolve it in the shared
    workflow instead of submitting an unrelated execution from this form.
    """
    result = {"pending_case_id": None, "exact_amount_required": None,
        "required_method": None, "required_source": None, "required_reference": None,
        "recording_blocked_reason": None}
    cases = [case for case in state.get("customer_returns", [])
        if str(case.get("order_id")) == order["order_id"]]
    pending = [case for case in cases if case.get("case_type") == "financial_refund" and case.get("status") == "pending"]
    documents = [case for case in cases if case.get("status") == "salla_refund_recorded" and not case.get("resolved_by_case_id")]
    if len(pending) + len(documents) > 1:
        result["recording_blocked_reason"] = "توجد ردود موثقة أو طلبات معلّقة متعددة؛ راجعها في ميزان قبل التأكيد."
    elif pending:
        visible = refund_history_page(state, policy, page=1, page_size=8,
            order_number=order["order_number"], status="pending")["items"]
        current = next((case for case in visible if case["id"] == pending[0]["id"]), None)
        methods = {method["id"] for method in order["methods"]}
        banks = {bank["id"] for bank in order["banks"]}
        if (current is None or current["method"] not in methods
                or not policy.resource(current["source"]["kind"], current["source"]["id"])["use"]
                or current["method"] == "bank" and current["source"]["id"] not in banks):
            result["recording_blocked_reason"] = "طلب الرد المعلّق غير متاح ضمن مصادر صلاحياتك؛ راجعه في ميزان."
        else:
            result.update(pending_case_id=current["id"], exact_amount_required=current["amount"],
                required_method=current["method"], required_source=current["source"])
    elif documents:
        reference = documents[0].get("refund_reference")
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 160:
            result["recording_blocked_reason"] = "رد المبلغ الموثق يحتاج مطابقة مرجعه في ميزان قبل التأكيد."
        else:
            result.update(exact_amount_required=documents[0]["amount"], required_reference=reference)
    return result


async def mobile_refund_page(db, principal, *, page=1, order_number=None):
    policy = await _access(db, principal)
    state = await read(db, policy.owner_id)
    number = normalized_order_number(order_number) if order_number else ""
    if number:
        row, _ = await _order_context(db, policy, state, number)
        # These are the canonical row's existing amounts, not a second
        # calculation of paid/refunded/captured money.
        result = {"items": [row], "page": 1, "page_size": PAGE_SIZE, "total": 1, "total_pages": 1,
            "summary": {"refund_pending_amount": row["pending_refund_amount"],
                "refund_pending_count": int(Decimal(row["pending_refund_amount"]) > 0),
                "collection_pending_amount": "0.00", "collection_pending_count": 0, "unknown_count": 0}}
    else:
        result = await customer_adjustments_page(db, policy.owner_id, policy, state,
            direction="refund", page=page, page_size=PAGE_SIZE)
    result["items"] = await _with_current_status(db, policy.owner_id, result["items"])
    await _recheck(db, principal, policy)
    return {**result, **_context(policy)}


async def mobile_refund_order(db, principal, number):
    policy = await _access(db, principal)
    state = await read(db, policy.owner_id)
    _, result = await _order_context(db, policy, state, normalized_order_number(number))
    result = (await _with_current_status(db, policy.owner_id, [result]))[0]
    await _recheck(db, principal, policy)
    return {**_context(policy), **result}


async def _visible_read(request, operation):
    try:
        return await asyncio.wait_for(run_visible_report_read(request, operation), timeout=READ_TIMEOUT_SECONDS)
    except TimeoutError:
        fail("customer_refund_read_timeout", "تعذّر تحميل الاسترجاعات في الوقت المحدد؛ أعد المحاولة.", 504)


def make_mobile_customer_refund_read_router(db, current_user):
    router = APIRouter(prefix=PREFIX, tags=["employee-customer-refunds"])

    @router.get("")
    async def page(request: Request, response: Response, page: int = Query(default=1, ge=1),
                   page_size: int = Query(default=PAGE_SIZE, ge=PAGE_SIZE, le=PAGE_SIZE),
                   order_number: str | None = Query(default=None, min_length=1, max_length=160),
                   user=Depends(current_user)):
        response.headers["Cache-Control"] = "no-store"
        return await _visible_read(request, lambda: mobile_refund_page(
            db, user, page=page, order_number=order_number))

    @router.get("/order/{number}")
    async def order(number: str, request: Request, response: Response, user=Depends(current_user)):
        if not number.strip() or len(number) > 160:
            fail("customer_refund_order_invalid", "أدخل رقم الطلب الصحيح", 422)
        response.headers["Cache-Control"] = "no-store"
        return await _visible_read(request, lambda: mobile_refund_order(db, user, number))

    return router
