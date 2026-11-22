"""Scoped financial refund requests on the existing operational aggregate.

Pending requests reserve only refundable order money. Bank confirmation records
an execution already performed by the user; it calls no bank. Original-provider
requests require matched evidence or a separate accountant execution attestation.
No items, stock, customer bank details or accounting ledger are written here.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from math import ceil
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from operational_balance_access import guard_access, load_access
from operational_balance_service import active_gate, entity, fmt, money, report
from operational_balance_sources import instant
from operational_balance_store import AUTHORIZATION_GUARD, audit, claim_movement, digest, fail, mutate, now, read, remember, replay
from operational_payment_evidence import _amount, _instant, resolve_provider
from operational_provider_transaction_evidence import load_transaction_rows, normalize_operational_payment
from operational_read_runtime import read_source_cursor, source_read_is_owned
from operational_visible_report_read import run_visible_report_read
from salla_payment_collection import provider_order_page_balance, provider_order_page_current


CASE_TYPE = "financial_refund"
PROVIDERS = {"tamara": "تمارا", "tabby": "تابي", "salla": "سلة"}
READ_OPERATIONS = ("customer.refund_request", "customer.refund_execute")
EXECUTED = {"refunded", "salla_refund_confirmed", "bank_matched"}
MATCHED = "provider_matched"
DEFINITIVE_REJECTIONS = {
    "operational_refund_amount_exceeded", "operational_refund_source_insufficient",
    "operational_refund_order_changed", "operational_refund_history_conflict",
    "operational_refund_execution_date", "operational_refund_reference_duplicate",
    "operational_refund_already_confirmed", "operational_refund_not_pending",
    "operational_refund_payment_basis_required", "operational_refund_order_not_found",
    "operational_refund_evidence_changed", "operational_refund_already_matched",
    "operational_refund_external_history_review",
    "operational_refund_receipt_invalid", "operational_customer_collection_evidence_conflict",
    "operational_refund_before_start",
}
REJECTED = "_workspace_refund_rejected"
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _order_number(value):
    return str(value).translate(DIGITS).strip()


class RefundCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=160)
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")


class RefundRequest(RefundCommand):
    order_number: str = Field(min_length=1, max_length=160)
    amount: str = Field(min_length=1, max_length=30)
    method: Literal["bank", "original"]
    bank_id: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=1000)
    reference: str = Field(default="", max_length=160)

    @field_validator("order_number")
    @classmethod
    def normalized_number(cls, value):
        return _order_number(value)

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        return fmt(money(value.translate(DIGITS).replace("٫", ".")))


class RefundConfirm(RefundCommand):
    reference: str = Field(default="", max_length=160)
    executed_at: str = Field(min_length=16, max_length=40)
    confirmed: Literal[True]


class RefundExecution(RefundRequest):
    """Accountant attestation of an already executed refund, never a transfer API."""
    expected_basis_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reference: str = Field(min_length=1, max_length=160)
    executed_at: str = Field(min_length=16, max_length=40)
    confirmed: Literal[True]
    receipt_id: str | None = Field(default=None, min_length=1, max_length=100)
    pending_case_id: str | None = Field(default=None, min_length=1, max_length=160)


class RefundCancel(RefundCommand):
    reason: str = Field(min_length=3, max_length=1000)


class RefundReconcile(RefundCommand):
    evidence_id: str = Field(min_length=1, max_length=400)
    expected_evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def _actor(actor):
    return {"id": actor["id"], "name": actor.get("name") or "غير موثق"}


def _require_session(payload, owner, actor):
    if payload["expected_session_scope"] != digest([owner, actor["id"]]):
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 409)


async def _decide(db, owner, action, payload, operation):
    """Persist a terminal rejection before inviting a new corrected intent.

An old in-flight duplicate must replay its denial if a reservation is later
released or funds arrive. Transport/CAS uncertainty is never labelled safe.
"""
    async def apply(state):
        prior = replay(state, action, payload)
        if prior is not None and prior.get(REJECTED):
            return prior
        try:
            return await operation(state)
        except HTTPException as exc:
            if not isinstance(exc.detail, dict) or exc.detail.get("code") not in DEFINITIVE_REJECTIONS:
                raise
            rejected = {REJECTED: True, "status": exc.status_code,
                        "detail": {**exc.detail, "not_applied": True}}
            remember(state, action, payload, rejected)
            return rejected

    result = await mutate(db, owner, apply)
    if result.get(REJECTED):
        raise HTTPException(result["status"], detail=result["detail"])
    return result


async def _external_history(db, owner, order, state):
    """Duplicate guard only: never import old accounting/provider money."""
    ids = list({order["id"], order["order_number"]})
    order_match = {"$or": [{"order_reference_id": {"$in": ids}}, {"order_number": {"$in": ids}},
                           {"order_id": order["id"]}]}
    records = []
    fields = {"_id": 0, **{key: 1 for key in ("id", "user_id", "order_id", "order_number", "order_reference_id",
              "provider", "provider_refund_id", "amount", "currency", "status", "execution_channel",
              "bank_account_id", "bank_reference")}}
    for collection, additional in [("payment_refunds", {}), ("mz2_customer_refund_payments", {"status": "posted"})]:
        found = await db[collection].find({"user_id": owner, **order_match, **additional}, fields).to_list(1001)
        if len(found) > 1000:
            fail("operational_refund_external_history_review", "سجل الاسترجاع الخارجي يحتاج مراجعة قبل رد مبلغ آخر", 409)
        for row in found:
            if row.get("user_id") != owner:
                fail("operational_refund_external_history_review", "تعذر التحقق من نطاق سجل الاسترجاع", 409)
            channel = row.get("provider") if collection == "payment_refunds" else row.get("execution_channel")
            kind = "bank" if channel == "bank" else "provider"
            identity = row.get("bank_account_id") if kind == "bank" else channel
            reference = row.get("bank_reference") if kind == "bank" else row.get("provider_refund_id")
            if not isinstance(reference, str) or not reference.strip():
                reference = None
            try:
                amount = fmt(money(row.get("amount")))
            except HTTPException:
                amount = None
            recognized = bool(reference and amount and row.get("currency") in (None, "SAR") and (
                kind == "provider" and state.get("engine", {}).get("facts", {}).get(
                    "refund:" + str(identity) + ":" + reference) == {"order_id": order["id"], "amount": amount}
                or any(case.get("status") in EXECUTED and case.get("order_id") == order["id"]
                       and case.get("refund_source_type") == kind and case.get("refund_source_id") == identity
                       and case.get("refund_reference") == reference and case.get("amount") == amount
                       for case in state.get("customer_returns", []))))
            if not recognized:
                fail("operational_refund_external_history_review", "يوجد استرجاع من مصدر آخر لم تتم مطابقته؛ راجعه قبل تسجيل رد جديد", 409)
            records.append(digest([collection, row]))
    return digest(sorted(records))


@asynccontextmanager
async def _refund_guard(db, owner, actor, operation, resources, *, replay_action=None, replay_payload=None):
    expected = {"order": None, "external": None, "basis_hash": None}
    async with guard_access(db, actor, owner, operation, resources):
        previous = AUTHORIZATION_GUARD.get()

        async def verify():
            await previous()
            order = expected["order"]
            if order is not None:
                current_state = await read(db, owner)
                if replay_action is not None and replay(current_state, replay_action, replay_payload) is not None:
                    return  # An identical concurrent writer already decided this immutable intent.
                current = await refund_order(db, owner, order["order_number"], state=current_state)
                if any(current[key] != order[key] for key in ("id", "provider", "currency", "total", "paid_amount")):
                    fail("operational_refund_order_changed", "تغير إثبات دفع الطلب أثناء الحفظ؛ أعد القراءة", 409)
                fingerprint = await _external_history(db, owner, current, current_state)
                if fingerprint != expected["external"]:
                    fail("operational_refund_external_history_review", "ظهر سجل استرجاع خارجي أثناء الحفظ؛ أعد القراءة", 409)
                if expected["basis_hash"] is not None:
                    from operational_customer_adjustments import adjustment_from_order
                    if adjustment_from_order(current, current_state)["basis_hash"] != expected["basis_hash"]:
                        fail("operational_refund_order_changed", "تغير رصيد الطلب أثناء الحفظ؛ أعد القراءة", 409)

        token = AUTHORIZATION_GUARD.set(verify)
        try:
            yield expected
        finally:
            AUTHORIZATION_GUARD.reset(token)


def validate_provider_match(state, case):
    """Prove a link to an existing effect; this function never posts one."""
    key = case.get("provider_evidence_id")
    reference = case.get("provider_evidence_reference")
    fact = state.get("engine", {}).get("facts", {}).get(key)
    if (case.get("status") != MATCHED or case.get("method") != "original"
            or case.get("refund_source_type") != "provider" or not reference
            or key != "refund:" + str(case.get("refund_source_id")) + ":" + reference
            or fact != {"order_id": case.get("order_id"), "amount": case.get("amount")}
            or digest(fact) != case.get("provider_evidence_hash")):
        fail("operational_refund_evidence_changed", "تغير دليل الاسترجاع المرتبط؛ يلزم مراجعته", 409)
    return fact


def recorded_provider_refund_match(state, case):
    from operational_customer_adjustments import provider_refund_match
    from operational_customer_collection_effects import provider_refund_recognized
    key = provider_refund_match(state, case)
    return key if key is not None and provider_refund_recognized(state, case) else None


def _bank_refund_movement(state, order_id, bank_id, currency, amount, reference):
    from operational_customer_collection_effects import bank_movement_effect
    try:
        matches = [movement for movement in state.get("movements", [])
                   if reference in {movement.get("reference"), movement.get("bank_transaction_reference")}
                   and bank_movement_effect(movement, bank_id, currency) < 0]
        if not matches:
            return None
        if (len(matches) != 1 or str(matches[0].get("order_id")) != str(order_id)
                or bank_movement_effect(matches[0], bank_id, currency) != -money(amount)):
            fail("operational_refund_evidence_changed", "مرجع البنك لا يطابق الطلب والأثر النقدي المؤكد", 409)
        return matches[0]
    except (ValueError, TypeError, KeyError):
        fail("operational_refund_evidence_changed", "تعذر التحقق من الأثر البنكي المرتبط", 409)


def recorded_bank_refund_match(state, case):
    """Replace only the new manual bridge with an exact later bank cash leg."""
    if (case.get("status") != "refunded" or case.get("refund_source_type") != "bank"
            or case.get("execution_mode") != "accountant_recorded"
            or case.get("financial_effect") != "confirmed_bank_refund_debit"):
        return None
    reference = case.get("refund_reference")
    if not isinstance(reference, str) or not reference.strip():
        fail("operational_refund_evidence_changed", "مرجع تنفيذ الاسترجاع غير مكتمل", 409)
    movement = _bank_refund_movement(state, case["order_id"], case["refund_source_id"],
                                     case["currency"], case["amount"], reference)
    return "bank-movement:" + str(movement["id"]) if movement is not None else None


def validate_bank_match(state, case):
    from operational_customer_collection_effects import bank_movement_effect
    movement = next((row for row in state.get("movements", []) if row.get("id") == case.get("bank_evidence_id")), None)
    try:
        effect = bank_movement_effect(movement, case.get("refund_source_id"), case.get("currency")) if movement else None
    except (ValueError, TypeError, KeyError):
        fail("operational_refund_evidence_changed", "تعذر التحقق من الأثر البنكي المرتبط", 409)
    if (case.get("status") != "bank_matched" or case.get("refund_source_type") != "bank"
            or movement is None or digest(movement) != case.get("bank_evidence_hash")
            or str(movement.get("order_id")) != str(case.get("order_id"))
            or effect != -money(case["amount"])
            or case.get("refund_reference") not in {movement.get("reference"), movement.get("bank_transaction_reference")}):
        fail("operational_refund_evidence_changed", "تغير دليل التحويل المرتبط؛ يلزم مراجعته", 409)
    return movement


def _provider_evidence(state, case):
    if case.get("method") != "original" or case.get("status") != "pending":
        return []
    prefix = "refund:" + case["refund_source_id"] + ":"
    unavailable = set(case.get("known_provider_refunds", []))
    for other in state.get("customer_returns", []):
        if other.get("id") == case["id"]:
            continue
        if other.get("status") == MATCHED:
            unavailable.add(other.get("provider_evidence_id"))
        elif other.get("status") in EXECUTED and other.get("refund_source_type") == "provider" and other.get("refund_reference"):
            unavailable.add("refund:" + str(other.get("refund_source_id")) + ":" + other["refund_reference"])
    result = []
    for key, fact in state.get("engine", {}).get("facts", {}).items():
        if (not key.startswith(prefix) or key in unavailable
                or fact != {"order_id": case["order_id"], "amount": case["amount"]}):
            continue
        reference = key[len(prefix):]
        if not reference.strip():
            continue
        executed_at = None
        payment = state.get("engine", {}).get("provider_fee_inputs", {}).get("orders", {}).get(case["order_id"], {}).get("payment", {})
        for event in payment.get("refunds", []):
            if str(event.get("id")) == reference and event.get("amount") == case["amount"] and event.get("refunded_at"):
                try:
                    executed_at = _instant(event["refunded_at"]).isoformat()
                except ValueError:
                    pass
        result.append({"id": key, "reference": reference, "amount": case["amount"],
                       "fingerprint": digest(fact), "executed_at": executed_at})
    return sorted(result, key=lambda row: row["id"])


def _history_usage(state, order_id, *, exclude_case_id=None):
    """Deduplicate an executed provider case and its canonical refund fact."""
    effects, reserved = {}, Decimal(0)

    def effect(key, value):
        amount = money(value)
        if key in effects and effects[key] != amount:
            fail("operational_refund_history_conflict", "سجلات الاسترجاع متعارضة؛ راجعها قبل تسجيل مبلغ آخر", 409)
        effects[key] = amount

    for case in state.get("customer_returns", []):
        if str(case.get("order_id")) != order_id or case.get("id") == exclude_case_id:
            continue
        if case.get("status") == "salla_refund_recorded" and case.get("resolved_by_case_id"):
            linked = next((row for row in state.get("customer_returns", [])
                           if row.get("id") == case["resolved_by_case_id"]), None)
            if (linked is not None and linked.get("status") in {*EXECUTED, MATCHED}
                    and linked.get("order_id") == case.get("order_id")
                    and linked.get("amount") == case.get("amount")
                    and (linked.get("refund_reference") or linked.get("provider_evidence_reference")) == case.get("refund_reference")):
                continue
            fail("operational_refund_history_conflict", "تعذر التحقق من مطابقة توثيق الاسترجاع", 409)
        if case.get("case_type") == CASE_TYPE and case.get("status") == "pending":
            reserved += money(case["amount"])
        elif case.get("status") == MATCHED:
            validate_provider_match(state, case)
            effect(("provider", case["refund_source_id"], case["provider_evidence_reference"]), case["amount"])
        elif case.get("status") in EXECUTED or case.get("status") == "salla_refund_recorded":
            ref = case.get("refund_reference")
            key = (case.get("refund_source_type"), case.get("refund_source_id"), ref) if ref else ("case", case["id"])
            effect(key, case["amount"])
    for key, fact in state.get("engine", {}).get("facts", {}).items():
        if not key.startswith("refund:") or not isinstance(fact, dict) or str(fact.get("order_id")) != order_id:
            continue
        identity = key[len("refund:"):].split(":", 1)
        if len(identity) != 2 or not all(identity):
            fail("operational_refund_history_conflict", "هوية استرجاع سابقة غير مكتملة", 409)
        effect(("provider", identity[0], identity[1]), fact["amount"])
    return sum(effects.values(), Decimal(0)), reserved


def _capacity(state, order, *, exclude_case_id=None):
    from operational_customer_collection_effects import source_uncovered_collected_amount
    refunded, reserved = _history_usage(state, order["id"], exclude_case_id=exclude_case_id)
    try:
        paid = money(order["paid_amount"], zero=True) + source_uncovered_collected_amount(state, order)
    except (ValueError, KeyError, TypeError):
        fail("operational_customer_collection_evidence_conflict", "تعارض دليل تحصيل محفوظ؛ يلزم مراجعته", 409)
    return {"paid_amount": fmt(paid), "refunded_amount": fmt(refunded), "reserved_amount": fmt(reserved),
            "available_amount": fmt(max(Decimal(0), paid - refunded - reserved))}


def guard_workspace_refund_capacity(state, order_id, legacy_limit, amount, *, exclude_case_id=None):
    """Called inside legacy CAS so old routes cannot spend a new reservation.

Only orders participating in this workspace get this extra restriction. Existing
item-return/record-only rules keep their original behavior on all other orders.
"""
    cases = [case for case in state.get("customer_returns", [])
             if str(case.get("order_id")) == str(order_id) and case.get("case_type") == CASE_TYPE
             and case.get("status") in {"pending", MATCHED, *EXECUTED}]
    if not cases:
        return
    limit = min([money(legacy_limit, zero=True), *[money(case["paid_basis"], zero=True) for case in cases]])
    used, reserved = _history_usage(state, str(order_id), exclude_case_id=exclude_case_id)
    if money(amount) > max(Decimal(0), limit - used - reserved):
        fail("operational_refund_reserved_amount", "المبلغ يتجاوز المتاح بعد الاسترجاعات والطلبات المعلّقة", 409)


async def refund_order(db, owner, number_text, *, stamp=None, state=None):
    """Financial evidence only; no product quantities or customer PII are read out."""
    stamp = stamp or now()
    number_text = _order_number(number_text)
    number_values = [number_text]
    if number_text.isascii() and number_text.isdecimal() and len(number_text) <= 19:
        numeric = int(number_text)
        if str(numeric) == number_text and numeric <= 2**63 - 1:
            number_values.append(numeric)
    reference_field = "raw_by_source.salla_direct.reference_id"
    fallback_field = "raw_by_source.salla_direct.order_number"
    selector = {"user_id": owner, "$or": [
        {reference_field: {"$in": number_values}},
        {reference_field: {"$in": [None, "", 0]}, fallback_field: {"$in": number_values}},
    ]}
    # Two matching documents already establish ambiguity. No unrelated order
    # payload reaches Python when an employee opens a single refund.
    projection = {"_id": 0, "raw_by_source.salla_direct.items": 0, "raw_by_source.salla_direct.customer": 0}
    cursor = db["unified_orders"].find(selector, projection).limit(2)
    matches = await read_source_cursor(cursor, 2) if source_read_is_owned() else await cursor.to_list(2)
    if len(matches) != 1 or matches[0].get("user_id") != owner:
        fail("operational_refund_order_not_found", "الطلب غير متاح أو هويته غير مكتملة", 404)
    source = matches[0]
    raw = (source.get("raw_by_source") or {}).get("salla_direct")
    if not isinstance(raw, dict) or str(raw.get("reference_id") or raw.get("order_number") or "") != number_text:
        fail("operational_refund_order_not_found", "الطلب غير متاح أو هويته غير مكتملة", 404)
    try:
        created_at = _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
        # Read only saved transaction evidence, never start a provider/network
        # refresh while a user is entering a refund.
        transactions = await load_transaction_rows(db, owner, [raw], created_at - timedelta(seconds=1),
            _instant(stamp), to_instant=instant, max_rows=20000)
    except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError):
        fail("operational_refund_payment_basis_required", "تعذر إثبات المبلغ المدفوع أو تغيّرت بيانات الطلب؛ راجع مصدر الدفع أولًا", 409)
    from operational_customer_adjustments import source_order_basis
    state = await read(db, owner) if state is None else state
    return source_order_basis(source, owner, stamp=stamp, transaction_rows=transactions.get(number_text, []), state=state)


async def _bank_choices(db, owner, policy):
    from operational_balance_sources import entities
    try:
        rows_ = await entities(db, owner, "bank")
    except ValueError:
        fail("operational_refund_bank_catalog", "تعذر التحقق من إعداد الحسابات البنكية", 409)
    return [{"id": row["id"], "name": row["name"], "currency": row["currency"]} for row in rows_
            if row.get("currency") == "SAR" and row.get("ready") is not False
            and row.get("settings_complete") is not False and policy.resource("bank", row["id"])["use"]]


def _visible(policy, case):
    kind, identity = case.get("refund_source_type"), case.get("refund_source_id")
    access = policy.resource(kind, identity)
    if policy.owner_override or access["movements"] == "all":
        return True
    own = policy.actor["id"] in (case.get("created_by"), case.get("confirmed_by"), case.get("matched_by"))
    # A user may review the immutable request they submitted without receiving
    # the account's ledger or balance. Other users' requests require all rows.
    return own and (access["use"] or access["movements"] == "own")


def _case_dto(case, policy, state):
    pending = case["status"] == "pending"
    use = policy.resource(case["refund_source_type"], case["refund_source_id"])["use"]
    if case["status"] == MATCHED:
        validate_provider_match(state, case)
    elif case["status"] == "bank_matched":
        validate_bank_match(state, case)
    evidence = _provider_evidence(state, case) if use and policy.can_operation("customer.refund_execute") else []
    return {"id": case["id"], "order_number": case["order_number"], "currency": case["currency"],
            "amount": case["amount"], "method": case["method"],
            "source": {"kind": case["refund_source_type"], "id": case["refund_source_id"], "name": case["refund_source_name"]},
            "status": "refunded" if case["status"] in {*EXECUTED, MATCHED} else case["status"],
            "reason": case.get("note", ""), "request_reference": case.get("request_reference", ""),
            "recorded_by": deepcopy(case.get("recorded_by") or {"id": None, "name": "غير موثق"}),
            "created_at": case["created_at"], "reference": case.get("provider_evidence_reference") or case.get("refund_reference") or "",
            "executed_at": case.get("refunded_at"),
            "confirmed_by": deepcopy(case.get("matched_by_snapshot") or case.get("confirmed_by_snapshot")),
            "matched_at": case.get("matched_at"), "provider_evidence": evidence,
            "can_reconcile": bool(evidence) and pending and use and policy.can_operation("customer.refund_execute"),
            "internal_confirmation_id": case.get("internal_confirmation_id"),
            "receipt_id": case.get("receipt_id"), "execution_mode": case.get("execution_mode"),
            "cancel_reason": case.get("cancel_reason"),
            "can_confirm": pending and use and case["method"] == "bank" and policy.can_operation("customer.refund_execute"),
            "can_record_execution": pending and use and policy.can_operation("customer.refund_execute"),
            "can_cancel": pending and use and policy.can_operation("customer.refund_request")
                and (policy.owner_override or case.get("created_by") == policy.actor["id"])}


def _find_case(state, case_id, policy):
    case = next((case for case in state.get("customer_returns", [])
                 if case.get("id") == case_id and case.get("case_type") == CASE_TYPE), None)
    if case is None or not _visible(policy, case):
        fail("operational_refund_not_found", "طلب الاسترجاع غير متاح", 404)
    return case


async def request_refund(db, owner, actor, payload, *, source="mezan2", stamp=None):
    stamp = stamp or now()
    _require_session(payload, owner, actor)
    prior = replay(await read(db, owner), "workspace_refund_request:" + actor["id"], payload)
    if prior is not None and prior.get(REJECTED):
        raise HTTPException(prior["status"], detail=prior["detail"])
    initial_order = None if prior is not None else await refund_order(db, owner, payload["order_number"], stamp=stamp)
    if prior is not None:
        kind, identity = prior["refund_source_type"], prior["refund_source_id"]
    elif payload["method"] == "bank":
        if not payload.get("bank_id"):
            fail("operational_refund_bank_required", "اختر البنك الذي سيُرد منه المبلغ", 422)
        kind, identity = "bank", payload["bank_id"]
    else:
        if payload.get("bank_id"):
            fail("operational_refund_hidden_bank", "طريقة الاسترجاع المختارة لا تستخدم بنكًا", 422)
        if initial_order["provider"] not in PROVIDERS:
            fail("operational_refund_original_unavailable", "لا يوجد مزود دفع أصلي متاح لهذا الطلب", 422)
        kind, identity = "provider", initial_order["provider"]
    async with _refund_guard(db, owner, actor, "customer.refund_request", [(kind, identity)]) as expected:
        await claim_movement(db, owner, actor["id"], {**payload, "_operation": "workspace_refund_request"})

        async def apply(state):
            previous = replay(state, "workspace_refund_request:" + actor["id"], payload)
            if previous is not None:
                return next((case for case in state.get("customer_returns", []) if case["id"] == previous["id"]), previous)
            await active_gate(db, owner, state)
            order = await refund_order(db, owner, payload["order_number"], stamp=stamp, state=state)
            if (order["id"], order["provider"]) != (initial_order["id"], initial_order["provider"]):
                fail("operational_refund_order_changed", "تغيرت جهة دفع الطلب؛ أعد فتحه", 409)
            account = await entity(db, owner, kind, identity, "SAR")
            expected.update(order=order, external=await _external_history(db, owner, order, state))
            capacity = _capacity(state, order)
            if money(payload["amount"]) > money(capacity["available_amount"], zero=True):
                fail("operational_refund_amount_exceeded", "المبلغ يتجاوز المتاح بعد الاسترجاعات والطلبات المعلّقة", 409)
            case = {"id": digest([owner, actor["id"], "financial_refund", payload["request_id"]]),
                    "case_type": CASE_TYPE, "request_id": payload["request_id"],
                    "order_id": order["id"], "order_number": order["order_number"],
                    "order_total": order["total"], "order_created_at": order["created_at"],
                    "paid_basis": order["paid_amount"], "original_provider": order["provider"],
                    "amount": fmt(money(payload["amount"])), "currency": "SAR", "status": "pending",
                    "method": payload["method"], "refund_source_type": kind, "refund_source_id": identity,
                    "refund_source_name": account["name"], "refund_reference": "",
                    "request_reference": payload.get("reference", ""), "note": payload["reason"],
                    "items": [], "shipping": {"kind": "none", "amount": "0.00", "status": "none", "reference": ""},
                    "created_at": stamp, "created_by": actor["id"], "actor_id": actor["id"],
                    "recorded_by": _actor(actor), "source": source, "financial_effect": "none_pending"}
            case["known_provider_refunds"] = sorted(key for key, fact in state.get("engine", {}).get("facts", {}).items()
                if key.startswith("refund:") and isinstance(fact, dict) and str(fact.get("order_id")) == order["id"])
            state.setdefault("customer_returns", []).append(case)
            audit(state, actor["id"], "financial_refund_requested", None, case, payload["reason"], source, stamp)
            remember(state, "workspace_refund_request:" + actor["id"], payload, case)
            return case

        return await _decide(db, owner, "workspace_refund_request:" + actor["id"], payload, apply)


async def confirm_refund(db, owner, actor, case_id, payload, *, source="mezan2", stamp=None):
    stamp = stamp or now()
    _require_session(payload, owner, actor)
    policy = await load_access(db, actor, owner)
    case = _find_case(await read(db, owner), case_id, policy)
    if case["method"] != "bank":
        fail("operational_refund_provider_execution_pending", "طلب المنصة معلّق حتى يصل تأكيد تنفيذ حقيقي من المصدر", 409)
    resource = (case["refund_source_type"], case["refund_source_id"])
    async with _refund_guard(db, owner, actor, "customer.refund_execute", [resource]) as expected:
        await claim_movement(db, owner, actor["id"], {**payload, "_operation": "workspace_refund_confirm", "_case_id": case_id})

        async def apply(state):
            previous = replay(state, "workspace_refund_confirm:" + actor["id"] + ":" + case_id, payload)
            if previous is not None:
                return previous
            await active_gate(db, owner, state)
            current = _find_case(state, case_id, await load_access(db, actor, owner))
            if (current.get("refund_source_type"), current.get("refund_source_id")) != resource or current.get("method") != "bank":
                fail("operational_refund_order_changed", "تغير مصدر الطلب؛ أعد فتحه", 409)
            reference = payload.get("reference", "")
            try:
                executed = _instant(payload["executed_at"])
            except ValueError:
                fail("operational_refund_execution_date", "أدخل وقت تنفيذ التحويل الصحيح", 422)
            if not _instant(current["order_created_at"]) <= executed <= _instant(stamp):
                fail("operational_refund_execution_date", "وقت التنفيذ خارج فترة الطلب أو في المستقبل", 422)
            if current["status"] == "refunded":
                if (reference, executed.isoformat()) != (current.get("refund_reference", ""), current.get("refunded_at")):
                    fail("operational_refund_already_confirmed", "رد المبلغ مؤكد؛ لا يمكن تغيير إثباته", 409)
                return current
            if current["status"] != "pending":
                fail("operational_refund_not_pending", "طلب الاسترجاع ليس معلّقًا", 409)
            order = await refund_order(db, owner, current["order_number"], stamp=stamp, state=state)
            expected.update(order=order, external=await _external_history(db, owner, order, state))
            if (order["id"], order["provider"], order["total"], order["paid_amount"]) != (
                    current["order_id"], current["original_provider"], current["order_total"], current["paid_basis"]):
                fail("operational_refund_order_changed", "تغير إثبات دفع الطلب؛ راجعه قبل تأكيد التحويل", 409)
            capacity = _capacity(state, order, exclude_case_id=case_id)
            value = money(current["amount"])
            if value > money(capacity["available_amount"], zero=True):
                fail("operational_refund_amount_exceeded", "لم يعد مبلغ الطلب متاحًا للاسترجاع", 409)
            account = await entity(db, owner, "bank", current["refund_source_id"], "SAR")
            balance = next((row for row in report(state)["parties"]
                            if row["party_type"] == "bank" and row["party_id"] == current["refund_source_id"]
                            and row["currency"] == "SAR"), {})
            if value > Decimal(balance.get("actual", "0.00")):
                fail("operational_refund_source_insufficient", "المبلغ غير متاح في حساب التحويل المحدد", 409)
            if reference:
                if any(other.get("id") != case_id and other.get("status") in EXECUTED
                       and other.get("refund_source_type") == "bank" and other.get("refund_source_id") == account["id"]
                       and other.get("refund_reference") == reference for other in state.get("customer_returns", [])):
                    fail("operational_refund_reference_duplicate", "مرجع التحويل مسجل مسبقًا", 409)
                if any(movement.get("bank_id") == account["id"] and movement.get("reference") == reference
                       and movement.get("direction") == "outgoing" for movement in state["movements"]):
                    fail("operational_refund_reference_duplicate", "التحويل مسجل كحركة بنكية بالفعل", 409)
                if await db.mz2_customer_refund_payments.find_one({"user_id": owner, "status": "posted", "bank_reference": reference}):
                    fail("operational_refund_reference_duplicate", "التحويل مسجل محاسبيًا؛ يلزم مطابقته", 409)
            before = deepcopy(current)
            current.update(status="refunded", refund_source_name=account["name"], refund_reference=reference,
                           refunded_at=executed.isoformat(), confirmed_at=stamp,
                           confirmed_by=actor["id"], confirmed_by_snapshot=_actor(actor),
                           internal_confirmation_id=digest([owner, case_id, "bank_confirmation"]),
                           financial_effect="confirmed_bank_refund_debit")
            # report(state) already posts refunded bank cases. Do not append a
            # second movement and do not touch provider obligations/settlements.
            audit(state, actor["id"], "financial_refund_bank_confirmed", before, current, current["note"], source, stamp)
            remember(state, "workspace_refund_confirm:" + actor["id"] + ":" + case_id, payload, current)
            return current

        return await _decide(db, owner, "workspace_refund_confirm:" + actor["id"] + ":" + case_id, payload, apply)


async def record_refund_execution(db, owner, actor, payload, *, source="mezan2", stamp=None):
    """Record the accountant's attestation once, or match an existing effect."""
    from operational_customer_adjustments import adjustment_from_order
    from operational_workspace_refund_receipts import validate_refund_receipt

    stamp = stamp or now()
    _require_session(payload, owner, actor)
    action = "workspace_refund_record_execution:" + actor["id"]
    prior = replay(await read(db, owner), action, payload)
    if prior is not None and prior.get(REJECTED):
        raise HTTPException(prior["status"], detail=prior["detail"])
    initial_order = None if prior is not None else await refund_order(db, owner, payload["order_number"], stamp=stamp)
    if prior is not None:
        kind, identity = prior["refund_source_type"], prior["refund_source_id"]
    elif payload["method"] == "bank":
        if not payload.get("bank_id"):
            fail("operational_refund_bank_required", "اختر البنك الذي خرج منه المبلغ", 422)
        kind, identity = "bank", payload["bank_id"]
    else:
        if payload.get("bank_id"):
            fail("operational_refund_hidden_bank", "طريقة الاسترجاع المختارة لا تستخدم بنكًا", 422)
        if initial_order["provider"] not in PROVIDERS:
            fail("operational_refund_original_unavailable", "لا يوجد مزود دفع أصلي متاح لهذا الطلب", 422)
        kind, identity = "provider", initial_order["provider"]
    async with _refund_guard(db, owner, actor, "customer.refund_execute", [(kind, identity)],
                             replay_action=action, replay_payload=payload) as expected:
        await claim_movement(db, owner, actor["id"], {**payload, "_operation": "workspace_refund_record_execution"})

        async def apply(state):
            # A CAS retry may now see the exact execution committed by another
            # worker. Its immutable replay must not retain the old basis guard.
            expected.update(order=None, external=None, basis_hash=None)
            previous = replay(state, action, payload)
            if previous is not None:
                return previous
            await active_gate(db, owner, state)
            order = await refund_order(db, owner, payload["order_number"], stamp=stamp, state=state)
            if (order["id"], order["provider"]) != (initial_order["id"], initial_order["provider"]):
                fail("operational_refund_order_changed", "تغيرت هوية الطلب أو جهة الدفع؛ أعد القراءة", 409)
            value = money(payload["amount"])
            reference = payload["reference"]
            try:
                executed = _instant(payload["executed_at"])
            except ValueError:
                fail("operational_refund_execution_date", "أدخل تاريخ تنفيذ الاسترجاع الصحيح", 422)
            if not _instant(order["created_at"]) <= executed <= _instant(stamp):
                fail("operational_refund_execution_date", "وقت التنفيذ خارج فترة الطلب أو في المستقبل", 422)
            if state.get("started_at") and executed < _instant(state["started_at"]):
                fail("operational_refund_before_start", "رد ما قبل بداية الأرصدة يحتاج مطابقة الرصيد الافتتاحي", 409)
            account = await entity(db, owner, kind, identity, "SAR")
            receipt = await validate_refund_receipt(db, owner, actor, order["order_number"], payload.get("receipt_id"))
            cases = state.setdefault("customer_returns", [])
            pending = None
            if payload.get("pending_case_id"):
                pending = _find_case(state, payload["pending_case_id"], await load_access(db, actor, owner))
                if (pending.get("order_id"), pending.get("order_number"), pending.get("method"),
                        pending.get("refund_source_type"), pending.get("refund_source_id"), pending.get("amount")) != (
                        order["id"], order["order_number"], payload["method"], kind, identity, fmt(value)):
                    fail("operational_refund_order_changed", "بيانات التنفيذ لا تطابق طلب الرد المعلّق", 409)
            for existing in cases:
                existing_ref = existing.get("refund_reference") or existing.get("provider_evidence_reference")
                if (existing.get("status") in {*EXECUTED, MATCHED}
                        and (existing.get("refund_source_type"), existing.get("refund_source_id"), existing_ref) == (kind, identity, reference)):
                    if pending is not None and existing["id"] != pending["id"]:
                        fail("operational_refund_reference_duplicate", "مرجع التنفيذ مرتبط بطلب رد آخر", 409)
                    if (existing.get("order_id"), existing.get("amount"), existing.get("refunded_at")) != (
                            order["id"], fmt(value), executed.isoformat()):
                        fail("operational_refund_reference_duplicate", "مرجع التنفيذ مسجل بمعلومات أخرى؛ راجع العملية الأصلية", 409)
                    if existing.get("case_type") != CASE_TYPE:
                        fail("operational_refund_reference_duplicate", "عملية الاسترجاع مسجلة سابقًا؛ راجع سجلها", 409)
                    if existing["status"] == MATCHED:
                        validate_provider_match(state, existing)
                    elif existing["status"] == "bank_matched":
                        validate_bank_match(state, existing)
                    elif kind == "provider":
                        recorded_provider_refund_match(state, existing)
                    elif kind == "bank":
                        recorded_bank_refund_match(state, existing)
                    remember(state, action, payload, existing)
                    return existing
            if pending is not None and pending.get("status") != "pending":
                fail("operational_refund_not_pending", "طلب الاسترجاع ليس معلّقًا", 409)
            adjustment = adjustment_from_order(order, state)
            if adjustment["basis_hash"] != payload["expected_basis_hash"]:
                fail("operational_refund_order_changed", "تغير رصيد الطلب أو دليله؛ أعد القراءة قبل التأكيد", 409)
            if value > money(adjustment["paid_amount"], zero=True):
                fail("operational_refund_amount_exceeded", "المبلغ يتجاوز التحصيل المثبت للطلب", 409)
            expected.update(order=order, external=await _external_history(db, owner, order, state),
                            basis_hash=adjustment["basis_hash"])
            fact_key = "refund:" + identity + ":" + reference if kind == "provider" else None
            fact = state.get("engine", {}).get("facts", {}).get(fact_key) if fact_key else None
            if fact is not None and fact != {"order_id": order["id"], "amount": fmt(value)}:
                fail("operational_refund_evidence_changed", "مرجع المنصة يخص طلبًا أو مبلغًا مختلفًا", 409)
            matched_movement = None
            if kind == "bank":
                matched_movement = _bank_refund_movement(state, order["id"], identity, "SAR", fmt(value), reference)
                if await db.mz2_customer_refund_payments.find_one({"user_id": owner, "status": "posted", "bank_reference": reference}):
                    fail("operational_refund_reference_duplicate", "التحويل مسجل محاسبيًا؛ يلزم مطابقته", 409)
            documents = [case for case in cases if case.get("status") == "salla_refund_recorded"
                and not case.get("resolved_by_case_id") and case.get("order_id") == order["id"]
                and case.get("refund_reference") == reference]
            if len(documents) > 1 or documents and documents[0].get("amount") != fmt(value):
                fail("operational_refund_history_conflict", "توثيق الاسترجاع السابق يخالف المبلغ؛ راجعه أولًا", 409)
            if fact is None and matched_movement is None:
                excluded = pending["id"] if pending is not None else documents[0]["id"] if documents else None
                capacity = _capacity(state, order, exclude_case_id=excluded)
                if value > money(capacity["available_amount"], zero=True):
                    fail("operational_refund_amount_exceeded", "المبلغ يتجاوز التحصيل المتاح بعد الردود والحجوزات", 409)
                if kind == "bank":
                    balance = next((row for row in report(state)["parties"] if row["party_type"] == "bank"
                                    and row["party_id"] == identity and row["currency"] == "SAR"), {})
                    if value > Decimal(balance.get("actual", "0.00")):
                        fail("operational_refund_source_insufficient", "المبلغ غير متاح في حساب التحويل المحدد", 409)
                else:
                    # An additional bank receipt cannot enlarge the original
                    # provider's refundable principal.
                    from operational_customer_collection_effects import source_uncovered_collected_amount
                    from operational_customer_adjustments import refund_totals
                    provider_state = {**state,
                        "customer_collections": [row for row in state.get("customer_collections", [])
                            if (row.get("destination_type"), row.get("destination_id")) == ("provider", identity)],
                        "customer_returns": [row for row in cases
                            if (row.get("refund_source_type"), row.get("refund_source_id")) == ("provider", identity)],
                        "engine": {**state.get("engine", {}), "facts": {key: row for key, row in state.get("engine", {}).get("facts", {}).items()
                            if not key.startswith("refund:") or key.startswith("refund:" + identity + ":")}}}
                    principal = money(order["paid_amount"], zero=True) + source_uncovered_collected_amount(provider_state, order)
                    used = refund_totals(provider_state, order["id"])["executed"]
                    if value > max(Decimal(0), principal - used):
                        fail("operational_refund_amount_exceeded", "المبلغ يتجاوز التحصيل المتبقي لدى منصة الدفع الأصلية", 409)
            case = {"id": digest([owner, actor["id"], "recorded_financial_refund", payload["request_id"]]),
                    "case_type": CASE_TYPE, "request_id": payload["request_id"],
                    "order_id": order["id"], "order_number": order["order_number"], "currency": "SAR",
                    "order_total": order["total"], "order_created_at": order["created_at"],
                    "paid_basis": adjustment["paid_amount"], "original_provider": order["provider"],
                    "amount": fmt(value), "status": "refunded", "method": payload["method"],
                    "refund_source_type": kind, "refund_source_id": identity, "refund_source_name": account["name"],
                    "refund_reference": reference, "request_reference": "", "refunded_at": executed.isoformat(),
                    "note": payload["reason"], "created_at": stamp, "created_by": actor["id"], "actor_id": actor["id"],
                    "recorded_by": _actor(actor), "confirmed_at": stamp, "confirmed_by": actor["id"],
                    "confirmed_by_snapshot": _actor(actor), "source": source,
                    "execution_mode": "accountant_recorded", "receipt_id": payload.get("receipt_id"),
                    "receipt_hash": receipt["sha256"] if receipt else None,
                    "confirmed_basis_hash": adjustment["basis_hash"], "source_proof_hash": order["proof_hash"],
                    "internal_confirmation_id": digest([owner, kind, identity, reference]),
                    "financial_effect": "confirmed_bank_refund_debit" if kind == "bank" else "confirmed_provider_refund_debit",
                    "items": [], "shipping": {"kind": "none", "amount": "0.00", "status": "none", "reference": ""}}
            before = deepcopy(pending)
            if pending is not None:
                case.update({key: deepcopy(pending[key]) for key in (
                    "id", "request_id", "created_at", "created_by", "recorded_by", "actor_id", "source",
                    "note", "request_reference", "order_total", "order_created_at", "paid_basis", "original_provider") if key in pending})
                case.update(execution_request_id=payload["request_id"], execution_note=payload["reason"],
                            confirmation_source=source, confirmation_paid_basis=adjustment["paid_amount"],
                            confirmation_order_total=order["total"])
            if kind == "provider":
                case["payment_snapshot"] = deepcopy(order["_payment_snapshot"])
                if fact is not None:
                    case.update(provider_evidence_id=fact_key, provider_evidence_hash=digest(fact),
                                provider_evidence_reference=reference, matched_at=stamp, matched_by=actor["id"],
                                matched_by_snapshot=_actor(actor))
                    # A fact can precede its financial projection. Keep the
                    # manual bridge until the original claim reflects it.
                    if recorded_provider_refund_match(state, case) is not None:
                        case["matched_evidence_at_confirmation"] = fact_key
            elif matched_movement is not None:
                case.update(status="bank_matched", bank_evidence_id=matched_movement["id"],
                            bank_evidence_hash=digest(matched_movement), matched_at=stamp,
                            financial_effect="none_existing_bank_movement")
                validate_bank_match(state, case)
            if pending is None:
                cases.append(case)
            else:
                pending.clear()
                pending.update(case)
            for document in documents:
                document.update(resolved_by_case_id=case["id"], matched_at=stamp, matched_by=actor["id"])
            audit(state, actor["id"], "financial_refund_execution_recorded", before, case, payload["reason"], source, stamp)
            remember(state, action, payload, case)
            return case

        return await _decide(db, owner, action, payload, apply)


async def cancel_refund(db, owner, actor, case_id, payload, *, source="mezan2", stamp=None):
    stamp = stamp or now()
    _require_session(payload, owner, actor)
    policy = await load_access(db, actor, owner)
    case = _find_case(await read(db, owner), case_id, policy)
    if not policy.owner_override and case.get("created_by") != actor["id"]:
        fail("operational_refund_cancel_forbidden", "إلغاء الطلب متاح لمن سجله أو للمالك", 403)
    async with guard_access(db, actor, owner, "customer.refund_request", [(case["refund_source_type"], case["refund_source_id"])]):
        await claim_movement(db, owner, actor["id"], {**payload, "_operation": "workspace_refund_cancel", "_case_id": case_id})

        async def apply(state):
            previous = replay(state, "workspace_refund_cancel:" + actor["id"] + ":" + case_id, payload)
            if previous is not None:
                return previous
            await active_gate(db, owner, state)
            current = _find_case(state, case_id, await load_access(db, actor, owner))
            if current["status"] != "pending":
                fail("operational_refund_not_pending", "يمكن إلغاء الطلب المعلّق فقط", 409)
            before = deepcopy(current)
            current.update(status="cancelled", cancelled_at=stamp, cancelled_by=_actor(actor),
                           cancel_reason=payload["reason"], financial_effect="none_cancelled")
            audit(state, actor["id"], "financial_refund_cancelled", before, current, payload["reason"], source, stamp)
            remember(state, "workspace_refund_cancel:" + actor["id"] + ":" + case_id, payload, current)
            return current

        return await _decide(db, owner, "workspace_refund_cancel:" + actor["id"] + ":" + case_id, payload, apply)


async def reconcile_refund(db, owner, actor, case_id, payload, *, source="mezan2", stamp=None):
    stamp = stamp or now()
    _require_session(payload, owner, actor)
    policy = await load_access(db, actor, owner)
    case = _find_case(await read(db, owner), case_id, policy)
    if case["method"] != "original":
        fail("operational_refund_original_unavailable", "مطابقة دليل المنصة تخص طلب الاسترجاع عبر المزود الأصلي", 422)
    resource = ("provider", case["refund_source_id"])
    action = "workspace_refund_reconcile:" + actor["id"] + ":" + case_id
    async with guard_access(db, actor, owner, "customer.refund_execute", [resource]):
        await claim_movement(db, owner, actor["id"], {**payload, "_operation": "workspace_refund_reconcile", "_case_id": case_id})

        async def apply(state):
            previous = replay(state, action, payload)
            if previous is not None:
                return previous
            await active_gate(db, owner, state)
            current = _find_case(state, case_id, await load_access(db, actor, owner))
            if (current.get("refund_source_type"), current.get("refund_source_id")) != resource:
                fail("operational_refund_order_changed", "تغير مصدر الطلب؛ أعد فتحه", 409)
            if current["status"] == MATCHED:
                validate_provider_match(state, current)
                if (payload["evidence_id"], payload["expected_evidence_hash"]) != (
                        current["provider_evidence_id"], current["provider_evidence_hash"]):
                    fail("operational_refund_already_matched", "الطلب مرتبط بدليل مصدر محفوظ؛ لا يمكن تغييره", 409)
                return current
            if current["status"] != "pending":
                fail("operational_refund_not_pending", "طلب الاسترجاع ليس معلّقًا", 409)
            proof = next((row for row in _provider_evidence(state, current)
                          if row["id"] == payload["evidence_id"] and row["fingerprint"] == payload["expected_evidence_hash"]), None)
            if proof is None:
                fail("operational_refund_evidence_changed", "الدليل غير متاح أو لا يطابق الطلب أو سبق ربطه؛ أعد القراءة", 409)
            before = deepcopy(current)
            current.update(status=MATCHED, provider_evidence_id=proof["id"],
                           provider_evidence_hash=proof["fingerprint"], provider_evidence_reference=proof["reference"],
                           matched_by=actor["id"], matched_by_snapshot=_actor(actor), matched_at=stamp,
                           refunded_at=proof["executed_at"], financial_effect="none_existing_provider_refund_fact")
            validate_provider_match(state, current)
            audit(state, actor["id"], "financial_refund_source_matched", before, current,
                  "ربط طلب معلّق بدليل استرجاع مسجل؛ لا خصم جديد", source, stamp)
            remember(state, action, payload, current)
            return current

        return await _decide(db, owner, action, payload, apply)


def refund_history_page(state, policy, *, page, page_size, order_number=None, status="all"):
    cases = [case for case in state.get("customer_returns", [])
             if case.get("case_type") == CASE_TYPE and (order_number is None or case.get("order_number") == order_number)
             and _visible(policy, case) and (status == "all" or
                 (case.get("status") in {*EXECUTED, MATCHED} if status == "refunded" else case.get("status") == status))]
    cases.sort(key=lambda case: (case["created_at"], case["id"]), reverse=True)
    total_pages = max(1, ceil(len(cases) / page_size))
    page = min(page, total_pages)
    return {"items": [_case_dto(case, policy, state) for case in cases[(page - 1) * page_size:page * page_size]],
            "page": page, "page_size": page_size, "total": len(cases), "total_pages": total_pages}


def install_refund_routes(router, db, current_user, scope):
    from operational_customer_adjustments import install_customer_adjustment_routes
    from operational_workspace_refund_receipts import install_refund_receipt_routes
    install_customer_adjustment_routes(router, db, current_user, scope)
    install_refund_receipt_routes(router, db, current_user, scope)
    async def load(user):
        actor, owner, source = await scope(db, user, "workspace")
        policy = await load_access(db, actor, owner)
        policy.require_enabled()
        if not any(policy.can_operation(operation) for operation in READ_OPERATIONS):
            fail("operational_operation_forbidden", "ليست لديك صلاحية مراجعة طلبات الاسترجاع", 403)
        return actor, owner, source, policy

    async def recheck(user, owner, policy):
        _, fresh_owner, _, current = await load(user)
        if (owner, policy.actor["id"], policy.version, policy.owner_override) != (
                fresh_owner, current.actor["id"], current.version, current.owner_override):
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
        return current

    async def saved_reply(user, owner, policy, case):
        state = await read(db, owner)
        current = await recheck(user, owner, policy)
        return {"saved": True, "item": _case_dto(_find_case(state, case["id"], current), current, state)}

    @router.get("/workspace/refunds/order/{number}")
    async def order_lookup(number: str, request: Request, include_history: bool = False, user=Depends(current_user)):
        async def project():
            _, owner, _, policy = await load(user)
            state = await read(db, owner)
            order = await refund_order(db, owner, number, state=state)
            banks = await _bank_choices(db, owner, policy)
            methods = [{"id": "bank", "label": "تحويل بنكي"}] if banks else []
            if order["provider"] in PROVIDERS and policy.resource("provider", order["provider"])["use"]:
                methods.append({"id": "original", "label": PROVIDERS[order["provider"]]})
            result = {key: order[key] for key in ("id", "order_number", "currency", "total")}
            await _external_history(db, owner, order, state)
            capacity = _capacity(state, order)
            from operational_customer_adjustments import adjustment_from_order
            adjustment = adjustment_from_order(order, state)
            result.update(adjustment, executed_refunded_amount=adjustment["confirmed_refunded_amount"])
            result.update(capacity, methods=methods, banks=banks,
                          execution_amount_limit=capacity["paid_amount"],
                          can_request=bool(methods) and money(capacity["available_amount"], zero=True) > 0
                              and policy.can_operation("customer.refund_request"),
                          can_record_execution=bool(methods) and policy.can_operation("customer.refund_execute"))
            if include_history:
                result["history"] = refund_history_page(state, policy, page=1, page_size=8,
                                                       order_number=order["order_number"])
            await recheck(user, owner, policy)
            return result
        return await run_visible_report_read(request, project)

    @router.get("/workspace/refunds")
    async def refund_list(request: Request, page: int = Query(default=1, ge=1),
                          page_size: int = Query(default=5, ge=1, le=8), order_number: str | None = Query(default=None, max_length=160),
                          status: Literal["all", "pending", "refunded", "cancelled"] = "all",
                          user=Depends(current_user)):
        async def project():
            _, owner, _, policy = await load(user)
            state = await read(db, owner)
            result = refund_history_page(state, policy, page=page, page_size=page_size,
                                         order_number=_order_number(order_number) if order_number else None, status=status)
            await recheck(user, owner, policy)
            return result
        return await run_visible_report_read(request, project)

    @router.get("/workspace/refunds/{case_id}")
    async def refund_detail(case_id: str, request: Request, user=Depends(current_user)):
        async def project():
            _, owner, _, policy = await load(user)
            state = await read(db, owner)
            result = _case_dto(_find_case(state, case_id, policy), policy, state)
            await recheck(user, owner, policy)
            return result
        return await run_visible_report_read(request, project)

    @router.post("/workspace/refunds")
    async def create(payload: RefundRequest, user=Depends(current_user)):
        actor, owner, source, policy = await load(user)
        policy.require_operation("customer.refund_request")
        case = await request_refund(db, owner, actor, payload.model_dump(), source=source)
        return await saved_reply(user, owner, policy, case)


    @router.post("/workspace/refunds/record-execution")
    async def record_execution(payload: RefundExecution, user=Depends(current_user)):
        actor, owner, source, policy = await load(user)
        policy.require_operation("customer.refund_execute")
        case = await record_refund_execution(db, owner, actor, payload.model_dump(), source=source)
        return await saved_reply(user, owner, policy, case)

    @router.post("/workspace/refunds/{case_id}/confirm")
    async def confirm(case_id: str, payload: RefundConfirm, user=Depends(current_user)):
        actor, owner, source, policy = await load(user)
        policy.require_operation("customer.refund_execute")
        case = await confirm_refund(db, owner, actor, case_id, payload.model_dump(), source=source)
        return await saved_reply(user, owner, policy, case)

    @router.post("/workspace/refunds/{case_id}/cancel")
    async def cancel(case_id: str, payload: RefundCancel, user=Depends(current_user)):
        actor, owner, source, policy = await load(user)
        policy.require_operation("customer.refund_request")
        case = await cancel_refund(db, owner, actor, case_id, payload.model_dump(), source=source)
        return await saved_reply(user, owner, policy, case)

    @router.post("/workspace/refunds/{case_id}/reconcile")
    async def reconcile(case_id: str, payload: RefundReconcile, user=Depends(current_user)):
        actor, owner, source, policy = await load(user)
        policy.require_operation("customer.refund_execute")
        case = await reconcile_refund(db, owner, actor, case_id, payload.model_dump(), source=source)
        return await saved_reply(user, owner, policy, case)
