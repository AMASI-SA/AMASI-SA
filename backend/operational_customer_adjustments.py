"""Read-only customer adjustment inbox over saved order and financial evidence.

An observed price difference is neither a refund nor a receipt. Only recorded
executions affect the paid balance; no read reserves money or calls a provider.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from math import ceil

from fastapi import Depends, HTTPException, Query, Request

from operational_balance_access import load_access
from operational_balance_store import digest, fail, now, read
from operational_payment_evidence import _amount, _instant, resolve_provider
from operational_read_runtime import read_source_cursor, source_read_is_owned
from operational_visible_report_read import run_visible_report_read
from salla_payment_collection import provider_order_page_balance, provider_order_page_current


ZERO = Decimal("0.00")
MAX_ORDERS = 20000
REFUND_READ_OPERATIONS = ("customer.refund_read", "customer.refund_request", "customer.refund_execute")
READ_OPERATIONS = (*REFUND_READ_OPERATIONS, "customer.collection")
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
FINANCIAL_FIELDS = ("id", "reference_id", "order_number", "currency", "amounts", "total", "total_amount",
                    "date", "created_at", "order_date", "updated_at", "date_updated", "status",
                    "payment", "payment_method", "payment_methods", "payment_actions", "paid_amount",
                    "remaining_amount", "refunded_amount", "pending_refund_amount", "has_remaining_amount",
                    "payment_status", "payment_collection_status", "is_pending_payment", "cancellations")
SOURCE_PROJECTION = {"_id": 0, **dict.fromkeys(("user_id", "order_id", "order_number", "salla_order_id",
    "g47_salla_snapshot", "order_status_slug", "last_salla_direct_status_source_updated_at"), 1),
    **{"raw_by_source.salla_direct." + field: 1 for field in FINANCIAL_FIELDS}}


def _fmt(value):
    return format(value, ".2f")


def normalized_order_number(value):
    return str(value).translate(DIGITS).strip()


def source_order_basis(source, owner, *, stamp=None, transaction_rows=(), state=None):
    """Return an independently proved paid basis; private fields never enter DTOs."""
    from operational_customer_payment_basis import confirmed_paid_basis, retained_confirmed_paid_basis
    from operational_provider_transaction_evidence import normalize_operational_payment

    stamp = stamp or now()
    raw = (source.get("raw_by_source") or {}).get("salla_direct")
    try:
        if not isinstance(raw, dict):
            raise ValueError("order_missing")
        identity = str(raw.get("id") or "")
        number = str(raw.get("reference_id") or raw.get("order_number") or "")
        amounts = raw.get("amounts") or {}
        total_value = amounts.get("total", raw.get("total")) if isinstance(amounts, dict) else None
        currency = raw.get("currency") or (total_value.get("currency") if isinstance(total_value, dict) else None)
        if not identity or not number or currency != "SAR":
            raise ValueError("currency_or_identity")
        total = _amount(total_value, currency)
        created = _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
        if created > _instant(stamp) or not provider_order_page_current(source, raw, owner=owner,
                order_number=number, provider_order_id=identity, customer_balance_verified=True):
            raise ValueError("stale_source")
        provider = resolve_provider(raw, require_known=True)
        proof = confirmed_paid_basis(raw, currency, total, created, stamp, owner=owner,
            order_number=number, provider_order_id=identity, source=source, rows=transaction_rows)
        if proof is None and state is not None:
            proof = retained_confirmed_paid_basis(raw, currency, total, created, stamp, owner=owner,
                order_number=number, provider_order_id=identity, source=source, state=state)
        if proof is not None:
            payment = proof["payment"]
            paid = _amount(proof["paid_amount"], currency)
            hint = _amount(proof["refund_hint_amount"], currency)
            proof_hash = proof["proof_hash"]
        else:
            payment, issues = normalize_operational_payment(raw, currency, total, created, stamp,
                owner=owner, order_number=number, provider_order_id=identity,
                rows=transaction_rows, source=source)
            if (payment and payment.get("split") or
                    any(issue.get("code") != "provider_capture_evidence_incomplete" for issue in issues)):
                raise ValueError("unproved_payment")
            captures = payment.get("captures", []) if payment else []
            if captures:
                paid = sum((_amount(capture["amount"], currency) for capture in captures), ZERO)
            else:
                page = provider_order_page_balance(raw, currency, total, allow_paid_only=True)
                if page is None:
                    raise ValueError("paid_amount_missing")
                paid = _amount(page["paid"], currency)
            if not ZERO <= paid <= total:
                raise ValueError("paid_amount_conflict")
            hint = ZERO
            proof_hash = digest([identity, number, provider, currency, _fmt(total), _fmt(paid), payment,
                                 raw.get("updated_at") or raw.get("date_updated")])
    except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError):
        fail("operational_refund_payment_basis_required",
             "تعذر إثبات المبلغ المدفوع أو تغيّرت بيانات الطلب؛ راجع مصدر الدفع أولًا", 409)
    return {"id": identity, "order_id": identity, "order_number": number, "currency": currency,
            "total": _fmt(total), "current_total": _fmt(total), "paid_amount": _fmt(paid),
            "source_paid_amount": _fmt(paid), "provider": provider, "created_at": created.isoformat(),
            "refund_hint_amount": _fmt(hint), "proof_hash": proof_hash,
            "_payment_snapshot": deepcopy(payment), "_source_fingerprint": digest(source)}


def refund_totals(state, order_id):
    """Separate executed money, documentary reservations, and pending requests."""
    effects, documents, reservations = {}, {}, {}
    facts = state.get("engine", {}).get("facts", {})

    def effect(key, value):
        amount = _amount(value, "SAR")
        if key in effects and effects[key] != amount:
            fail("operational_refund_history_conflict", "سجلات الاسترجاع متعارضة؛ راجعها قبل تسجيل مبلغ آخر", 409)
        effects[key] = amount

    index = state.get("_customer_adjustment_index")
    cases = (index["returns"].get(str(order_id), []) if index is not None else
             [case for case in state.get("customer_returns", []) if str(case.get("order_id")) == str(order_id)])
    fact_rows = index["refunds"].get(str(order_id), []) if index is not None else facts.items()
    for key, fact in fact_rows:
        if not key.startswith("refund:") or not isinstance(fact, dict) or str(fact.get("order_id")) != str(order_id):
            continue
        identity = key[len("refund:"):].split(":", 1)
        if len(identity) != 2 or not all(identity):
            fail("operational_refund_history_conflict", "هوية استرجاع سابقة غير مكتملة", 409)
        effect(("provider", *identity), fact["amount"])
    for case in cases:
        status, reference = case.get("status"), case.get("refund_reference")
        if status == "pending" and case.get("case_type") == "financial_refund":
            reservations[case["id"]] = _amount(case["amount"], "SAR")
        elif status == "salla_refund_recorded":
            linked = next((row for row in cases if row.get("id") == case.get("resolved_by_case_id")), None)
            if case.get("resolved_by_case_id"):
                if (linked is None or linked.get("status") not in {"refunded", "salla_refund_confirmed", "provider_matched", "bank_matched"}
                        or linked.get("amount") != case.get("amount")
                        or (linked.get("refund_reference") or linked.get("provider_evidence_reference")) != reference):
                    fail("operational_refund_history_conflict", "تعذر التحقق من مطابقة توثيق الاسترجاع", 409)
            else:
                documents[case["id"]] = _amount(case["amount"], "SAR")
        elif status == "provider_matched":
            key = case.get("provider_evidence_id")
            fact = facts.get(key)
            reference = case.get("provider_evidence_reference")
            if (not reference or key != "refund:" + str(case.get("refund_source_id")) + ":" + reference
                    or fact != {"order_id": case.get("order_id"), "amount": case.get("amount")}
                    or digest(fact) != case.get("provider_evidence_hash")):
                fail("operational_refund_evidence_changed", "تغير دليل الاسترجاع المرتبط؛ يلزم مراجعته", 409)
            effect(("provider", case["refund_source_id"], reference), case["amount"])
        elif status in {"refunded", "salla_refund_confirmed", "bank_matched"}:
            if status == "bank_matched":
                from operational_workspace_refunds import validate_bank_match
                validate_bank_match(state, case)
            key = ((case.get("refund_source_type"), case.get("refund_source_id"), reference)
                   if reference else ("case", case["id"]))
            effect(key, case["amount"])
    return {"executed": sum(effects.values(), ZERO), "unconfirmed": sum(documents.values(), ZERO),
            "reserved": sum(reservations.values(), ZERO),
            "fingerprint": digest([sorted((list(k), _fmt(v)) for k, v in effects.items()),
                                   sorted((k, _fmt(v)) for k, v in documents.items()),
                                   sorted((k, _fmt(v)) for k, v in reservations.items())])}


def executed_refund_amount(state, order_id):
    return refund_totals(state, order_id)["executed"]


def provider_refund_match(state, case):
    """A later canonical fact replaces, never adds to, a manual provider debit."""
    if case.get("status") != "refunded" or case.get("refund_source_type") != "provider":
        return None
    reference = case.get("refund_reference")
    if not isinstance(reference, str) or not reference.strip():
        fail("operational_refund_evidence_changed", "مرجع تنفيذ الاسترجاع غير مكتمل", 409)
    key = "refund:" + str(case.get("refund_source_id")) + ":" + reference
    fact = state.get("engine", {}).get("facts", {}).get(key)
    if fact is None:
        return None
    if fact != {"order_id": case.get("order_id"), "amount": case.get("amount")}:
        fail("operational_refund_evidence_changed", "دليل المنصة يخالف الاسترجاع المؤكد؛ يلزم مطابقته", 409)
    return key


def adjustment_from_order(order, state):
    from operational_customer_collection_effects import source_uncovered_collected_amount, collection_match

    totals = refund_totals(state, order["id"])
    index = state.get("_customer_adjustment_index")
    collections = (index["collections"].get(order["id"], []) if index is not None else
        [case for case in state.get("customer_collections", [])
         if str(case.get("order_id")) == order["id"] and case.get("status") == "confirmed"])
    try:
        unmatched = source_uncovered_collected_amount({**state, "customer_collections": collections}, order)
        collection_proof = [(case["id"], case["amount"], collection_match(state, case)) for case in collections]
    except (ValueError, KeyError, TypeError):
        fail("operational_customer_collection_evidence_conflict", "تعارض دليل تحصيل محفوظ؛ يلزم مراجعته", 409)
    paid = _amount(order["paid_amount"], "SAR") + unmatched
    total = _amount(order["total"], "SAR")
    refunded = totals["executed"]
    hint = _amount(order.get("refund_hint_amount", "0.00"), "SAR")
    pending_refund = max(paid - refunded - total, hint - refunded, totals["unconfirmed"], totals["reserved"], ZERO)
    # A refund is not a fresh sale. Until a commercial change is proved it
    # cannot create customer debt merely by reducing net cash received.
    pending_collection = max(total - paid, ZERO)
    collection_ambiguous = pending_collection > 0 and max(refunded, hint, totals["unconfirmed"]) > 0
    reason = ("يوجد مبلغ يحتاج تأكيد ردّه ومصدر خروجه" if pending_refund > 0 else
              "يوجد مبلغ متبقٍ على العميل يحتاج تأكيد التحصيل" if pending_collection > 0 else
              "لا يوجد فرق مالي معلّق")
    if collection_ambiguous:
        reason = "توجد إشارة رد وتعديل يزيد قيمة الطلب؛ المعروض فرق إضافي مثبت فقط، وتفصيل التعديلات يحتاج مراجعة"
    result = {"order_number": order["order_number"], "order_id": order["id"], "provider": order["provider"],
              "currency": "SAR", "current_total": order["total"], "paid_amount": _fmt(paid),
              "source_paid_amount": order["paid_amount"], "created_at": order["created_at"],
              "confirmed_refunded_amount": _fmt(refunded),
              "confirmed_collected_amount": _fmt(sum((_amount(case["amount"], "SAR") for case in collections), ZERO)),
              "pending_refund_amount": _fmt(pending_refund), "pending_collection_amount": _fmt(pending_collection),
              "unconfirmed_refund_amount": _fmt(totals["unconfirmed"]),
              "refund_hint_amount": _fmt(hint), "collection_basis_ambiguous": collection_ambiguous,
              "reserved_amount": _fmt(totals["reserved"]), "reason": reason, "status": "needs_confirmation",
              "suggested_refund_amount": _fmt(pending_refund),
              "basis_hash": digest([order["proof_hash"], order["total"], order["paid_amount"],
                                    order.get("refund_hint_amount"), totals["fingerprint"], sorted(collection_proof)])}
    return result


async def adjustment_for_order(db, owner, number, state, *, stamp=None):
    from operational_workspace_refunds import refund_order
    return adjustment_from_order(await refund_order(db, owner, number, stamp=stamp, state=state), state)


def _visible(policy, provider, has_bank):
    return policy.owner_override or has_bank or bool(provider and policy.resource("provider", provider)["use"])


def _indexed_state(state):
    index = {"returns": {}, "collections": {}, "refunds": {}}
    for case in state.get("customer_returns", []):
        index["returns"].setdefault(str(case.get("order_id")), []).append(case)
    for case in state.get("customer_collections", []):
        if case.get("status") == "confirmed":
            index["collections"].setdefault(str(case.get("order_id")), []).append(case)
    for key, fact in state.get("engine", {}).get("facts", {}).items():
        if key.startswith("refund:") and isinstance(fact, dict):
            index["refunds"].setdefault(str(fact.get("order_id")), []).append((key, fact))
    return {**state, "_customer_adjustment_index": index}


def _candidate_selector(owner, state):
    prefix = "raw_by_source.salla_direct."

    def present(*fields):
        expression = None
        for field in reversed(fields):
            expression = {"$ifNull": ["$" + prefix + field, expression]}
        return expression

    paid = present("payment.paid_amount.amount", "payment.paid_amount", "paid_amount.amount", "paid_amount",
                   "payment_actions.refund_action.paid_amount.amount", "payment_actions.refund_action.paid_amount",
                   "payment_actions.remaining_action.paid_amount.amount", "payment_actions.remaining_action.paid_amount")
    total = present("amounts.total.amount", "amounts.total", "total.amount", "total", "total_amount")
    signals = [{prefix + key: {"$exists": True, "$nin": [None, "", 0, "0", "0.0", "0.00"]}} for key in (
        "refunded_amount", "pending_refund_amount", "remaining_amount", "payment.refunded_amount",
        "payment.remaining_amount", "payment_actions.refund_action.refund_amount",
        "payment_actions.refund_action.pending_refund_amount", "payment_actions.remaining_action.remaining_amount")]
    signals.append({prefix + "payment_actions.refund_action.refunded_transactions": {"$exists": True, "$nin": [None, []]}})
    signals.append({"$expr": {"$and": [{"$ne": [paid, None]}, {"$ne": [total, None]}, {"$ne": [paid, total]}]}})
    documented = [str(case.get("order_id")) for case in state.get("customer_returns", [])
                  if (case.get("status") == "salla_refund_recorded" and not case.get("resolved_by_case_id"))
                  or (case.get("status") == "pending" and case.get("case_type") == "financial_refund")]
    if documented:
        signals.append({prefix + "id": {"$in": [*documented, *[int(v) for v in documented if v.isdecimal()]]}})
    selector = {"user_id": owner, "$and": [{"$or": signals}]}
    start = state.get("started_at")
    if start:
        post_start = set()
        for case in [*state.get("customer_returns", []), *state.get("customer_collections", [])]:
            stamp = case.get("refunded_at") or case.get("executed_at")
            if case.get("status") in {"refunded", "salla_refund_confirmed", "provider_matched", "bank_matched", "confirmed"}:
                try:
                    if stamp and _instant(stamp) >= _instant(start):
                        post_start.add(str(case.get("order_id")))
                except ValueError:
                    pass
        # A small query margin accommodates stored timezone offsets. The pure
        # reader below applies the exact instant boundary before any totals.
        query_start = (_instant(start) - timedelta(days=2)).isoformat()
        dates = [{prefix + key: {"$gte": query_start}} for key in (
            "created_at", "order_date", "date", "date.date", "payment.paid_at")]
        if post_start:
            dates.append({prefix + "id": {"$in": [*post_start, *[int(v) for v in post_start if v.isdecimal()]]}})
        selector["$and"].append({"$or": dates})
    return selector


def _after_start(order, state):
    if not state.get("started_at"):
        return False
    start = _instant(state["started_at"])
    if _instant(order["created_at"]) >= start:
        return True
    for capture in (order.get("_payment_snapshot") or {}).get("captures", []):
        try:
            if _instant(capture.get("captured_at")) >= start:
                return True
        except ValueError:
            pass
    index = state["_customer_adjustment_index"]
    for case in [*index["returns"].get(order["id"], []), *index["collections"].get(order["id"], [])]:
        if case.get("status") not in {"refunded", "salla_refund_confirmed", "provider_matched", "bank_matched", "confirmed"}:
            continue
        try:
            if _instant(case.get("refunded_at") or case.get("executed_at")) >= start:
                return True
        except ValueError:
            pass
    return False


async def customer_adjustments_page(db, owner, policy, state, *, direction=None, page=1, page_size=8, order_number=None, stamp=None):
    stamp = stamp or now()
    selector = _candidate_selector(owner, state)
    cursor = db["unified_orders"].find(selector, SOURCE_PROJECTION).limit(MAX_ORDERS + 1)
    sources = await read_source_cursor(cursor, MAX_ORDERS + 1) if source_read_is_owned() else await cursor.to_list(MAX_ORDERS + 1)
    if len(sources) > MAX_ORDERS:
        fail("operational_customer_adjustments_capacity", "عدد الطلبات يتطلب قراءة مقسمة؛ تعذر احتساب إجمالي موثوق", 503)
    state = _indexed_state(state)
    has_bank = any(r.get("kind") == "bank" and r.get("use") for r in policy.resources)
    can_refund = any(policy.can_operation(key) for key in REFUND_READ_OPERATIONS)
    can_collect = policy.can_operation("customer.collection")
    summary = {"refund_pending_amount": ZERO, "refund_pending_count": 0,
               "collection_pending_amount": ZERO, "collection_pending_count": 0, "unknown_count": 0}
    items = []
    identities = {}
    numbers = set()
    for source in sources:
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        number = str(raw.get("reference_id") or raw.get("order_number") or "")
        if number:
            numbers.add(number)
    if numbers:
        values = [*numbers, *[int(number) for number in numbers if number.isdecimal() and len(number) <= 19 and int(number) <= 2**63 - 1]]
        reference = "raw_by_source.salla_direct.reference_id"
        fallback = "raw_by_source.salla_direct.order_number"
        cursor = db["unified_orders"].find({"user_id": owner, "$or": [
            {reference: {"$in": values}}, {reference: {"$in": [None, "", 0]}, fallback: {"$in": values}}]},
            {"_id": 0, reference: 1, fallback: 1}).limit(MAX_ORDERS + 1)
        matches = await read_source_cursor(cursor, MAX_ORDERS + 1) if source_read_is_owned() else await cursor.to_list(MAX_ORDERS + 1)
        if len(matches) > MAX_ORDERS:
            fail("operational_customer_adjustments_capacity", "تعذر التحقق من هويات الطلبات ضمن حد القراءة", 503)
        for match in matches:
            raw = (match.get("raw_by_source") or {}).get("salla_direct") or {}
            number = str(raw.get("reference_id") or raw.get("order_number") or "")
            identities[number] = identities.get(number, 0) + 1
    for source in sources:
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        number = str(raw.get("reference_id") or raw.get("order_number") or "")
        try:
            provider = resolve_provider(raw, require_known=True)
        except (ValueError, TypeError, AttributeError):
            provider = None
        if not _visible(policy, provider, has_bank):
            continue
        try:
            if not number or identities[number] != 1:
                raise ValueError("order_identity")
            order = source_order_basis(source, owner, stamp=stamp, state=state)
            if not _after_start(order, state):
                continue
            row = adjustment_from_order(order, state)
        except (HTTPException, ValueError, TypeError, KeyError, ArithmeticError, AttributeError):
            summary["unknown_count"] += 1
            continue
        refund, collection = Decimal(row["pending_refund_amount"]), Decimal(row["pending_collection_amount"])
        if can_refund and refund > 0:
            summary["refund_pending_count"] += 1
            summary["refund_pending_amount"] += refund
        if can_collect and collection > 0:
            summary["collection_pending_count"] += 1
            summary["collection_pending_amount"] += collection
        allowed = (can_refund and refund > 0 and direction in (None, "refund") or
                   can_collect and collection > 0 and direction in (None, "collection"))
        if allowed and (not order_number or number == normalized_order_number(order_number)):
            items.append(row)
    items.sort(key=lambda row: (row["created_at"], row["order_number"]), reverse=True)
    pages = max(1, ceil(len(items) / page_size))
    page = min(page, pages)
    for key in ("refund_pending_amount", "collection_pending_amount"):
        summary[key] = _fmt(summary[key])
    return {"items": items[(page - 1) * page_size:page * page_size], "page": page, "page_size": page_size,
            "total": len(items), "total_pages": pages, "summary": summary}


def install_customer_adjustment_routes(router, db, current_user, scope):
    @router.get("/workspace/customer-adjustments")
    async def inbox(request: Request, direction: str | None = Query(default=None, pattern="^(refund|collection)$"),
                    page: int = Query(default=1, ge=1), page_size: int = Query(default=8, ge=1, le=8),
                    order_number: str | None = Query(default=None, max_length=160), user=Depends(current_user)):
        async def project():
            actor, owner, _ = await scope(db, user, "workspace")
            policy = await load_access(db, actor, owner)
            policy.require_enabled()
            required = REFUND_READ_OPERATIONS if direction == "refund" else ("customer.collection",) if direction == "collection" else READ_OPERATIONS
            if not any(policy.can_operation(operation) for operation in required):
                fail("operational_operation_forbidden", "ليست لديك صلاحية مراجعة فروق الطلبات", 403)
            result = await customer_adjustments_page(db, owner, policy, await read(db, owner),
                direction=direction, page=page, page_size=page_size, order_number=order_number)
            fresh_actor, fresh_owner, _ = await scope(db, user, "workspace")
            current = await load_access(db, fresh_actor, fresh_owner)
            if (owner, actor["id"], policy.version, policy.owner_override) != (
                    fresh_owner, fresh_actor["id"], current.version, current.owner_override):
                fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
            return result
        return await run_visible_report_read(request, project)
