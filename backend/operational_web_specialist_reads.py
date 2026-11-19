"""Employee read projections for three operational workflows, restricted to assigned resources."""
from operational_balance_store import STATES, fail
from operational_financial_activity_runtime import checked_rows, prepare_in_thread

PROVIDER_NAMES = {"salla": "سلة", "tabby": "تابي", "tamara": "تمارا", "emkan": "إمكان"}
RETURN_STATES = {"pending": "بانتظار استكمال المرتجع", "refunded": "تم رد المبلغ",
                 "salla_refund_recorded": "استرجاع مسجل في سلة", "salla_refund_confirmed": "مصدر الاسترجاع مؤكد"}


def _can_read_case(case, policy, which):
    creator = case.get("created_by") or case.get("actor_id")
    shipping = case.get("shipping") or {}
    candidates = [(shipping.get("kind"), shipping.get("id"), creator)]
    if which == "returns":
        candidates += [(case.get("refund_source_type"), case.get("refund_source_id"), case.get("confirmed_by") or creator),
                       ("provider", case.get("original_provider"), creator)]
    else:
        candidates.extend(("supplier", row.get("supplier_id"), row.get("actor_id")) for row in case.get("purchases", []))
        candidates.extend(("bank", row.get("bank_id"), row.get("actor_id")) for row in case.get("contributions", []))
    return any(isinstance(identity, str) and identity and policy.can_read_movement(kind, identity, actor)
               for kind, identity, actor in candidates)


def _case_rows(cases, which, policy):
    result = []
    for case in checked_rows(cases):
        if not case.get("id") or not case.get("order_number") or not _can_read_case(case, policy, which):
            continue
        if which == "returns":
            if case.get("case_type") == "order_compensation":
                continue
            status = RETURN_STATES.get(case.get("status"), "مسجل")
            detail = "مرتجع أصناف"
        else:
            status = "اكتمل شحن البدل" if (case.get("shipping") or {}).get("status") == "completed" else "بانتظار شحن البدل"
            detail = "استبدال أصناف"
        result.append({"id": case["id"], "label": "طلب " + str(case["order_number"]),
                       "status": status, "detail": detail, "date": case.get("created_at")})
    return result


async def _fees(db, owner, policy):
    from accounting_onboarding_ssot import POLICIES
    allowed = [p for p in PROVIDER_NAMES if policy.resource("provider", p)["report"] and policy.can_reference("provider", p)]
    if not allowed:
        return []
    selector = {"user_id": owner, "$or": [
        {"provider": {"$in": allowed}}, {"provider": {"$exists": False}, "policies.provider": {"$in": allowed}},
    ]}
    documents = await db[POLICIES].find(selector, {
        "_id": 0, "provider": 1, "policies.id": 1, "policies.provider": 1, "policies.status": 1,
        "policies.currency": 1, "policies.percentage": 1, "policies.fixed_amount": 1, "policies.effective_from": 1,
        "policies.event_type": 1, "policies.payment_method": 1, "policies.plan_id": 1}).to_list(1001)
    if len(documents) > 1000:
        fail("provider_fee_policy_scope_too_large", "تجاوز سجل الرسوم حد القراءة", 409)
    events = {"capture": "تحصيل", "refund": "استرجاع", "payout": "تحويل"}
    items = []
    for document in documents:
        for row in checked_rows(document.get("policies", [])):
            provider = row.get("provider")
            if provider not in allowed or document.get("provider", provider) != provider or not row.get("id") or row.get("status") == "inactive":
                continue
            terms = [events.get(row.get("event_type") or "capture", "رسم")]
            for val in (row.get("payment_method"), row.get("plan_id")):
                if val:
                    terms.append(str(val))
            if row.get("percentage") is not None:
                terms.append(str(row["percentage"]) + "٪")
            if row.get("fixed_amount") is not None:
                terms.append("رسم ثابت " + str(row["fixed_amount"]) + " " + str(row.get("currency") or ""))
            items.append({"id": row["id"], "label": PROVIDER_NAMES[provider], "detail": " · ".join(terms),
                          "status": "مسجلة", "date": row.get("effective_from")})
    return items


CASE_FIELDS = ("id", "order_number", "case_type", "status", "created_by", "actor_id", "created_at",
               "shipping.kind", "shipping.id", "shipping.status", "refund_source_type", "refund_source_id",
               "confirmed_by", "original_provider", "purchases.supplier_id", "purchases.actor_id",
               "contributions.bank_id", "contributions.actor_id")


async def specialist_page(db, user, load, which, page):
    from operational_balance_workspace import paginate
    actor, owner, _, policy = await load(user)
    from operational_web_page_policy import request_modes
    if not policy.owner_override and request_modes(actor) is None:
        fail("web_page_permission_required", "لا توجد صلاحية لهذه الصفحة", 403)
    if which == "fees":
        rows = await _fees(db, owner, policy)
    else:
        # Read only membership identifiers and case headers from the merchant state.
        field = "customer_returns" if which == "returns" else "customer_exchanges"
        fields = {field + "." + name: 1 for name in CASE_FIELDS}
        state = await db[STATES].find_one({"_id": owner, "owner_id": owner}, {"_id": 0, **fields})
        rows = await prepare_in_thread(_case_rows, (state or {}).get(field, []), which, policy)
    def page_result():
        rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
        return paginate(rows, page, 8)
    value = await prepare_in_thread(page_result)
    latest, latest_owner, _, current = await load(user)
    if (actor["id"], owner, policy.version, policy.owner_override) != (latest["id"], latest_owner, current.version, current.owner_override):
        fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
    return value
