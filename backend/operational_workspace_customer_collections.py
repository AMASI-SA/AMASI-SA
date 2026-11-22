"""Record an executed customer receipt against an edited order's remainder.

No gateway or bank is called. The immutable receipt, source, fee quote, audit
and idempotency decision share the operational aggregate CAS. Source matching
later replaces the manual cash projection; it never records a second receipt.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal
from math import ceil
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from operational_balance_access import guard_access, load_access
from operational_balance_service import active_gate, entity, fmt, money
from operational_balance_store import (
    AUTHORIZATION_GUARD, audit, claim_movement, digest, fail, mutate,
    now, read, remember, replay,
)
from operational_customer_collection_effects import (
    collection_match, collection_projection, collection_reference_match, destination_paid_basis, source_uncovered_collected_amount,
)
from operational_payment_evidence import _instant
from operational_visible_report_read import run_visible_report_read


OPERATION = "customer.collection"
REJECTED = "_customer_collection_rejected"
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
DEFINITIVE = {
    "operational_collection_amount_exceeded", "operational_collection_basis_changed",
    "operational_collection_quote_changed", "operational_collection_reference_duplicate",
    "operational_collection_evidence_conflict", "operational_collection_execution_date",
    "operational_collection_receipt_invalid", "operational_collection_before_start",
}


class CollectionQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    order_number: str = Field(min_length=1, max_length=160)
    amount: str = Field(min_length=1, max_length=30)
    source_type: Literal["bank", "provider"]
    source_id: str = Field(min_length=1, max_length=200)
    payment_method: str | None = Field(default=None, max_length=80)
    fee_mode: Literal["new_transaction", "same_transaction"] = "new_transaction"
    original_reference: str | None = Field(default=None, max_length=160)
    reference: str = Field(min_length=1, max_length=160)
    executed_at: str = Field(min_length=10, max_length=40)

    @field_validator("order_number", "amount")
    @classmethod
    def localized(cls, value):
        return value.translate(DIGITS)


class CollectionRecord(CollectionQuote):
    request_id: str = Field(min_length=8, max_length=160)
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_basis_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_quote_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=3, max_length=1000)
    receipt_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]


def _quote_fields(payload):
    return {key: payload.get(key) for key in CollectionQuote.model_fields}


def _actor(actor):
    return {"id": actor["id"], "name": actor.get("name") or ""}


def _visible(policy, case):
    access = policy.resource(case["destination_type"], case["destination_id"])
    return policy.owner_override or access["movements"] == "all" or (
        case.get("created_by") == policy.actor["id"] and (access["use"] or access["movements"] == "own"))


def collection_dto(state, policy, case):
    try:
        effect = collection_projection(state, case)
        matched = effect["matched_evidence_id"]
    except ValueError:
        matched = None
        match_status = "needs_review"
    else:
        match_status = "needs_review" if effect["review_code"] else "matched" if matched else "awaiting_source_match"
    return {"id": case["id"], "order_number": case["order_number"], "amount": case["amount"],
            "currency": case["currency"], "status": case["status"],
            "source": {"kind": case["destination_type"], "id": case["destination_id"], "name": case["destination_name"]},
            "reference": case["reference"], "executed_at": case["executed_at"],
            "reason": case["reason"], "created_at": case["created_at"],
            "recorded_by": deepcopy(case["created_by_snapshot"]),
            "receipt_id": case.get("receipt_id"), "fee_mode": case["fee_mode"],
            "fee_amount": case["fee_amount"], "net_amount": case["net_amount"], "fee_status": case["fee_status"],
            "match_status": match_status, "matched_evidence_id": matched}


def collection_history(state, policy, *, page=1, page_size=8, order_number=None):
    items = [case for case in state.get("customer_collections", [])
             if _visible(policy, case) and (order_number is None or case["order_number"] == order_number)]
    items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    total = len(items)
    total_pages = max(1, ceil(total / page_size))
    page = min(page, total_pages)
    return {"items": [collection_dto(state, policy, item) for item in items[(page - 1) * page_size:page * page_size]],
            "page": page, "page_size": page_size, "total": total, "total_pages": total_pages}


async def collection_sources(db, owner, policy):
    from operational_balance_sources import entities
    result = []
    for kind in ("bank", "provider"):
        try:
            rows = await entities(db, owner, kind)
        except ValueError:
            fail("operational_collection_source_setup", "تعذر التحقق من حسابات التحصيل", 409)
        for row in rows:
            if (row.get("currency") == "SAR" and row.get("ready") is not False
                    and row.get("settings_complete") is not False and policy.resource(kind, row["id"])["use"]):
                result.append({"kind": kind, "id": row["id"], "name": row["name"]})
    return result


async def _basis(db, owner, number, state, stamp):
    from operational_customer_adjustments import adjustment_for_order
    return await adjustment_for_order(db, owner, number, state, stamp=stamp)


def _original_principal(state, basis, payload):
    reference = payload.get("original_reference")
    if not reference or payload["source_id"] != basis["provider"]:
        fail("operational_collection_original_reference_required", "حدد مرجع عملية الدفع الأصلية لدى المنصة نفسها", 422)
    facts = state.get("engine", {}).get("facts", {})
    fact = facts.get("capture:" + reference)
    if fact:
        if (str(fact.get("order_id")), fact.get("provider")) != (str(basis["order_id"]), payload["source_id"]):
            fail("operational_collection_evidence_conflict", "مرجع الدفع لا يخص هذا الطلب والمنصة", 409)
        principal = money(fact["amount"])
    else:
        page = facts.get("provider_order_balance:" + str(basis["order_id"])) or {}
        if page.get("transaction_reference") != reference or page.get("provider") != payload["source_id"]:
            fail("operational_collection_original_reference_required", "يلزم إثبات العملية الأصلية لحساب فرق العمولة دون تكرار الرسم الثابت", 409)
        principal = money(page["paid"])
    for case in state.get("customer_collections", []):
        if (case.get("status") == "confirmed" and case.get("order_id") == basis["order_id"]
                and case.get("destination_id") == payload["source_id"] and case.get("fee_mode") == "same_transaction"
                and case.get("original_reference") == reference):
            principal = max(principal, money(case.get("source_paid_basis", case["paid_basis"]), zero=True)
                            + money(case["amount"]))
    return principal


def _fee_quote(state, basis, payload, stamp):
    value = money(payload["amount"])
    if payload["source_type"] == "bank":
        if payload.get("payment_method") or payload.get("original_reference") or payload["fee_mode"] != "new_transaction":
            fail("operational_collection_hidden_provider_fields", "التحويل البنكي لا يستخدم حقول عملية المنصة", 422)
        return {"fee_amount": "0.00", "net_amount": fmt(value), "fee_status": "none", "fee_basis": "bank_transfer"}
    from operational_configured_provider_fees import estimate_fee
    from operational_fee_methods import fee_rail
    provider = payload["source_id"]
    method = payload.get("payment_method")
    if provider == "salla" and fee_rail(method) is None:
        fail("operational_collection_payment_method_required", "اختر وسيلة الدفع الفعلية في سلة لحساب العمولة", 422)
    if payload["fee_mode"] == "new_transaction" and payload.get("original_reference"):
        fail("operational_collection_hidden_provider_fields", "عملية الدفع الجديدة لها مرجعها المستقل", 422)
    inputs = state.get("engine", {}).get("provider_fee_inputs", {})
    arguments = {"method": method or provider, "policies": inputs.get("policies", []),
                 "created_at": payload["executed_at"], "observed_at": payload["executed_at"],
                 "source_mode": inputs.get("source_mode", "legacy_settings"), "as_of": stamp, "currency": "SAR"}
    settings = inputs.get("settings", {})
    if payload["fee_mode"] == "same_transaction":
        if provider == "salla":
            original_payment = (inputs.get("orders", {}).get(str(basis["order_id"])) or {}).get("payment") or {}
            original_rail = fee_rail(original_payment.get("payment_method"))
            if original_rail is None or original_rail != fee_rail(method):
                fail("operational_collection_original_method_required", "تعديل العملية نفسها يتطلب وسيلة الدفع الأصلية المثبتة", 409)
        original = _original_principal(state, basis, payload)
        previous = estimate_fee(settings, provider, original, **arguments)
        updated = estimate_fee(settings, provider, original + value, **arguments)
        fee = None if previous is None or updated is None or updated < previous else updated - previous
    else:
        fee = estimate_fee(settings, provider, value, **arguments)
    return {"fee_amount": fmt(fee) if fee is not None else None,
            "net_amount": fmt(value - fee) if fee is not None else None,
            "fee_status": "estimated" if fee is not None else "unavailable",
            "fee_basis": "configured_incremental_estimate" if payload["fee_mode"] == "same_transaction" else "configured_transaction_estimate"}


async def quote_collection(db, owner, actor, payload, *, state=None, stamp=None):
    stamp = stamp or now()
    state = state if state is not None else await read(db, owner)
    policy = await load_access(db, actor, owner)
    policy.require_operation(OPERATION)
    policy.require_use(payload["source_type"], payload["source_id"])
    account = await entity(db, owner, payload["source_type"], payload["source_id"], "SAR")
    basis = await _basis(db, owner, payload["order_number"], state, stamp)
    try:
        executed = _instant(payload["executed_at"])
        created = _instant(basis["created_at"])
    except (ValueError, KeyError, TypeError):
        fail("operational_collection_execution_date", "أدخل تاريخ تنفيذ التحصيل الصحيح", 422)
    if not created <= executed <= _instant(stamp):
        fail("operational_collection_execution_date", "وقت التحصيل خارج فترة الطلب أو في المستقبل", 422)
    if state.get("started_at") and executed < _instant(state["started_at"]):
        fail("operational_collection_before_start", "تحصيل ما قبل بداية الأرصدة يحتاج مطابقة الرصيد الافتتاحي", 409)
    quoted = _fee_quote(state, basis, payload, stamp)
    fingerprint = digest({"command": _quote_fields(payload), "basis": basis["basis_hash"], "fee": quoted})
    return {**quoted, "quote_hash": fingerprint, "expected_basis_hash": basis["basis_hash"],
            "remaining_amount": basis["pending_collection_amount"],
            "source": {"kind": payload["source_type"], "id": account["id"], "name": account["name"]}}, basis


@asynccontextmanager
async def _collection_guard(db, owner, actor, payload):
    async with guard_access(db, actor, owner, OPERATION, [(payload["source_type"], payload["source_id"])]):
        previous = AUTHORIZATION_GUARD.get()
        expected = {}
        async def verify():
            if previous is not None:
                await previous()
            if expected:
                current_state = await read(db, owner)
                if replay(current_state, "customer_collection:" + actor["id"], payload) is not None:
                    return  # An identical concurrent writer already decided this intent.
                current, _ = await quote_collection(db, owner, actor, payload, state=current_state, stamp=expected["stamp"])
                if current["quote_hash"] != expected["quote_hash"]:
                    fail("operational_collection_basis_changed", "تغير الطلب أو دليل التحصيل؛ أعد مراجعته قبل التأكيد", 409)
        token = AUTHORIZATION_GUARD.set(verify)
        try:
            yield expected
        finally:
            AUTHORIZATION_GUARD.reset(token)


async def _receipt(db, owner, actor, payload):
    from operational_workspace_refund_receipts import validate_refund_receipt
    return await validate_refund_receipt(db, owner, actor, payload["order_number"], payload.get("receipt_id"))


async def record_collection(db, owner, actor, payload, *, source="mezan2", stamp=None):
    stamp = stamp or now()
    if payload["expected_session_scope"] != digest([owner, actor["id"]]):
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 409)
    action = "customer_collection:" + actor["id"]
    async with _collection_guard(db, owner, actor, payload) as expected:
        await claim_movement(db, owner, actor["id"], {**payload, "_operation": "customer_collection"})
        async def apply(state):
            expected.clear()
            previous = replay(state, action, payload)
            if previous is not None:
                return previous
            await active_gate(db, owner, state)
            quoted, basis = await quote_collection(db, owner, actor, payload, state=state, stamp=stamp)
            if payload["expected_basis_hash"] != basis["basis_hash"]:
                fail("operational_collection_basis_changed", "تغير المتبقي أو إثبات الدفع؛ أعد البحث عن الطلب", 409)
            if payload["expected_quote_hash"] != quoted["quote_hash"]:
                fail("operational_collection_quote_changed", "تغير حساب العمولة؛ أعد مراجعة صافي التحصيل", 409)
            value = money(payload["amount"])
            destination_basis = Decimal(0)
            source_paid_basis = money(basis["source_paid_amount"], zero=True)
            if payload["source_type"] == "provider":
                destination_basis = destination_paid_basis(state, basis["order_id"], payload["source_id"])
                if payload["source_id"] == basis["provider"]:
                    from operational_workspace_refunds import refund_order
                    source_order = await refund_order(db, owner, basis["order_number"], stamp=stamp, state=state)
                    scoped = {**state, "customer_collections": [case for case in state.get("customer_collections", [])
                              if case.get("destination_type") == "provider" and case.get("destination_id") == payload["source_id"]]}
                    destination_basis = max(destination_basis, source_paid_basis + source_uncovered_collected_amount(scoped, source_order))
                if payload["fee_mode"] == "same_transaction":
                    source_paid_basis = _original_principal(state, basis, payload)
            case = {"id": digest([owner, "customer_collection", actor["id"], payload["request_id"]]),
                    "order_id": basis["order_id"], "order_number": basis["order_number"], "currency": "SAR",
                    "amount": fmt(value), "status": "confirmed", "destination_type": payload["source_type"],
                    "destination_id": payload["source_id"], "destination_name": quoted["source"]["name"],
                    "reference": payload["reference"], "original_reference": payload.get("original_reference"),
                    "executed_at": _instant(payload["executed_at"]).isoformat(), "reason": payload["reason"],
                    "paid_basis": basis["paid_amount"], "basis_hash": basis["basis_hash"],
                    "original_provider": basis["provider"], "source_paid_basis": fmt(source_paid_basis),
                    "destination_paid_basis": fmt(destination_basis),
                    "payment_method": payload.get("payment_method"), "fee_mode": payload["fee_mode"],
                    **{key: quoted[key] for key in ("fee_amount", "net_amount", "fee_status", "fee_basis")},
                    "quote_hash": quoted["quote_hash"], "created_by": actor["id"], "created_by_snapshot": _actor(actor),
                    "created_at": stamp, "receipt_id": payload.get("receipt_id"), "source": source}
            try:
                matched = collection_reference_match(state, case)
            except ValueError:
                fail("operational_collection_evidence_conflict", "مرجع التحصيل مرتبط بإثبات مختلف؛ راجع المصدر", 409)
            if matched and matched.startswith("capture:"):
                # Recording an existing receipt must use the paid boundary
                # before that receipt, not today's already increased total.
                # Otherwise its later match would require another receipt of
                # the same amount and the manual bridge would never disappear.
                from operational_customer_collection_effects import provider_recognition
                recognized = provider_recognition(state, basis["order_id"], payload["source_id"])
                other_captures = [fact for key, fact in state.get("engine", {}).get("facts", {}).items()
                                  if key.startswith("capture:") and key != matched and isinstance(fact, dict)
                                  and (str(fact.get("order_id")), fact.get("provider")) ==
                                  (str(basis["order_id"]), payload["source_id"])]
                if recognized:
                    case["destination_paid_basis"] = fmt(max(recognized["gross"] - value, Decimal(0)))
                elif other_captures:
                    case["destination_paid_basis"] = fmt(sum((money(fact["amount"], zero=True) for fact in other_captures), Decimal(0)))
            if matched is None and value > money(basis["pending_collection_amount"], zero=True):
                fail("operational_collection_amount_exceeded", "المبلغ يزيد عن المتبقي غير المحصل على العميل", 409)
            if any(row.get("status") == "confirmed" and
                   (row.get("destination_type"), row.get("destination_id"), row.get("reference")) ==
                   (payload["source_type"], payload["source_id"], payload["reference"])
                   for row in state.get("customer_collections", [])):
                fail("operational_collection_reference_duplicate", "مرجع التحصيل مسجل مسبقًا؛ افتح سجله", 409)
            receipt = await _receipt(db, owner, actor, payload)
            case["receipt_hash"] = receipt["sha256"] if receipt else None
            case["provider_evidence_id"] = matched
            expected.update(stamp=stamp, quote_hash=quoted["quote_hash"])
            state.setdefault("customer_collections", []).append(case)
            audit(state, actor["id"], "customer_collection_recorded", None, case, payload["reason"], source, stamp)
            remember(state, action, payload, case)
            return case
        try:
            result = await mutate(db, owner, apply)
        except HTTPException as exc:
            # A definitive business denial stays denied on retries of the same
            # intent, even if a later order edit makes its amount affordable.
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if code not in DEFINITIVE or expected:
                raise
            rejection = {REJECTED: True, "status": exc.status_code, "detail": {**exc.detail, "not_applied": True}}
            async def reject(state):
                prior = replay(state, action, payload)
                if prior is not None:
                    return prior
                remember(state, action, payload, rejection)
                return rejection
            result = await mutate(db, owner, reject)
        if result.get(REJECTED):
            raise HTTPException(result["status"], detail=result["detail"])
        return result


def install_customer_collection_routes(router, db, current_user, scope):
    async def load(user):
        actor, owner, source = await scope(db, user, "workspace")
        policy = await load_access(db, actor, owner)
        policy.require_enabled()
        policy.require_operation(OPERATION)
        return actor, owner, source, policy

    async def recheck(user, owner, policy):
        actor, current_owner, _, current = await load(user)
        if (owner, policy.actor["id"], policy.version, policy.owner_override, policy.resources) != (
                current_owner, actor["id"], current.version, current.owner_override, current.resources):
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
        return current

    @router.get("/workspace/customer-collections/order/{number}")
    async def lookup(number: str, request: Request, include_history: bool = False, user=Depends(current_user)):
        async def project():
            actor, owner, _, policy = await load(user)
            state = await read(db, owner)
            basis = await _basis(db, owner, number.translate(DIGITS), state, now())
            sources = await collection_sources(db, owner, policy)
            result = {**basis, "suggested_collection_amount": basis["pending_collection_amount"], "sources": sources,
                      "can_record": bool(sources) and state["status"] == "active"}
            if include_history:
                result["history"] = collection_history(state, policy, order_number=basis["order_number"])
            await recheck(user, owner, policy)
            return result
        return await run_visible_report_read(request, project)

    @router.get("/workspace/customer-collections")
    async def history(request: Request, page: int = Query(default=1, ge=1),
                      page_size: int = Query(default=8, ge=1, le=8),
                      order_number: str | None = Query(default=None, max_length=160), user=Depends(current_user)):
        async def project():
            _, owner, _, policy = await load(user)
            state = await read(db, owner)
            result = collection_history(state, policy, page=page, page_size=page_size,
                                        order_number=order_number.translate(DIGITS) if order_number else None)
            await recheck(user, owner, policy)
            return result
        return await run_visible_report_read(request, project)

    @router.post("/workspace/customer-collections/quote")
    async def quote(payload: CollectionQuote, user=Depends(current_user)):
        actor, owner, _, policy = await load(user)
        result, _ = await quote_collection(db, owner, actor, payload.model_dump())
        await recheck(user, owner, policy)
        return result

    @router.post("/workspace/customer-collections")
    async def record(payload: CollectionRecord, user=Depends(current_user)):
        actor, owner, source, policy = await load(user)
        case = await record_collection(db, owner, actor, payload.model_dump(), source=source)
        state = await read(db, owner)
        current = await recheck(user, owner, policy)
        saved = next((row for row in state.get("customer_collections", []) if row["id"] == case["id"] and _visible(current, row)), None)
        if saved is None:
            fail("operational_collection_not_found", "سجل التحصيل غير متاح", 404)
        return {"saved": True, "item": collection_dto(state, current, saved)}
