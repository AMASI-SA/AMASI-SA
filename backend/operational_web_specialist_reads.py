"""Employee read projections for three operational workflows, restricted to assigned resources."""
from operational_balance_store import STATES, fail
from operational_financial_activity_runtime import checked_rows, prepare_in_thread

PROVIDER_NAMES = {"salla": "سلة", "tabby": "تابي", "tamara": "تمارا", "emkan": "إمكان", "paypal": "باي بال"}
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


REPORT_GROUPS = {
    "summary": None, "expected": None, "providers": {"provider"}, "couriers": {"courier"},
    "drivers": {"store_driver"}, "employees": {"employee", "employee_custody"},
    "accounts": {"bank", "cash", "pos_receivable"}, "suppliers": {"supplier"},
    "advertising": {"ad_account"}, "other": {"external_person", "operating_expense", "owner_withdrawal"},
}
SETTING_GROUPS = {"overview", "openings", "opening-stock", "salary-start", "movements", "reports"}
SIDE = {"for_us": "للمؤسسة", "for_party": "على المؤسسة", "balanced": "متوازن"}


def _display(identity, label, detail, status="", date=None):
    return {"id": identity, "label": label, "detail": detail, "status": status, "date": date}


def _account_rows(resources, computed, group, state):
    from operational_balance_access import RESOURCE_KIND_LABELS
    forecasts = {(r["party_type"], r["party_id"], r["currency"]): r for r in computed["parties"]} if group == "expected" else {}
    rows = []
    for r in checked_rows(resources):
        if not r["access"]["balance"] or not r["access"]["report"]:
            continue
        if REPORT_GROUPS[group] and r["kind"] not in REPORT_GROUPS[group]:
            continue
        key = (r["kind"], r["id"], r["currency"])
        balance = r.get("balance") or {}
        if group == "expected":
            forecast = forecasts.get(key)
            if forecast is None:
                continue
            detail = f'متوقع للمؤسسة {forecast["expected_receivable"]} {r["currency"]} · متوقع عليها {forecast["expected_payable"]} {r["currency"]}'
        elif r["kind"] == "ad_account":
            amounts = r.get("ad_amounts")
            if amounts:
                detail = f'رصيد مقدم {amounts["prepaid"]} {amounts["currency"]} · علينا {amounts["payable"]} {amounts["currency"]}'
                if amounts["currency"] == "USD":
                    detail += " · التحويل إلى الريال غير متاح"
            else:
                detail = "رصيد الحساب الإعلاني غير متاح"
        else:
            detail = f'{balance["amount"]} {r["currency"]} · {SIDE[balance["side"]]}'
        rows.append(_display(":".join(key), r["name"], detail, RESOURCE_KIND_LABELS[r["kind"]], state["engine"].get("as_of")))
    return rows


def _own_permissions(resources, policy):
    from operational_balance_access import OPERATION_LABELS, RESOURCE_KIND_LABELS
    names = {(r["kind"], r["id"]): r["name"] for r in resources}
    rights = []
    for grant in policy.resources:
        kind, identity = grant["kind"], grant["id"]
        # Stale/removed resource identifiers are not assigned a display identity.
        if (kind, identity) not in names:
            continue
        caps = [title for key, title in (("use", "استخدام الحساب"), ("balance", "عرض الرصيد"),
                ("report", "عرض الكشف"), ("print", "طباعة الكشف")) if grant.get(key)]
        if grant["movements"] != "none":
            caps.append("حركاتي فقط" if grant["movements"] == "own" else "جميع حركات الحساب")
        rights.append(_display(kind + ":" + identity, names[kind, identity], " · ".join(caps), RESOURCE_KIND_LABELS[kind]))
    rights.extend(_display("operation:" + key, OPERATION_LABELS[key], "تتاح على الحسابات والجهات المخصصة لك", "عملية مالية")
                  for key in policy.operations if key in OPERATION_LABELS)
    return rights


def _settings_rows(group, resources, computed, state, policy):
    if group == "reports":
        return _account_rows(resources, computed, "summary", state)
    if group == "movements":
        from operational_balance_workspace import _movement_rows
        scopes = {(r["kind"], r["id"], r["currency"]): r for r in resources if r["access"]["movements"] != "none"}
        rows = []
        for item in checked_rows(state.get("movements", [])):
            keys = [(item.get("bank_kind"), item.get("bank_id"), item.get("bank_currency") or item.get("currency")),
                    (item.get("party_type"), item.get("party_id"), item.get("currency"))]
            for key in keys:
                r = scopes.get(key)
                if not r:
                    continue
                projected = _movement_rows(r, policy, [item])
                if projected:
                    line = projected[0]
                    rows.append(_display("movement:" + item["id"], r["name"], str(line["amount"]) + " " + r["currency"],
                                         "حركة مالية", line.get("business_date") or line.get("recorded_at")))
                    break
        return rows
    if group == "salary-start":
        if not state.get("started_at") or not any(r["kind"] == "employee" and r["access"]["report"] for r in resources):
            return []
        from operational_salary_start import POLICY_KEY, select_first_day
        from operational_balance_store import now
        try:
            first = select_first_day(state, now()).isoformat()
        except (ValueError, TypeError, KeyError, AttributeError):
            fail("operational_salary_start_policy_invalid", "تعذر التحقق من بداية الرواتب المحفوظة", 409)
        return [_display("salary-start", "بداية احتساب الرواتب", first, "معتمد" if state.get(POLICY_KEY) else "بداية الفترة")]
    names = {(r["kind"], r["id"]): r["name"] for r in resources if r["access"]["balance"]}
    rows = []
    if group == "overview":
        status = {"draft": "إعداد الأرصدة الافتتاحية", "active": "التشغيل نشط"}.get(state.get("status"), "مسجل")
        rows.append(_display("period", "الفترة التشغيلية", status, "إعداد الفترة", state.get("started_at")))
    for opening in checked_rows(state.get("openings", {}).values()):
        name = names.get((opening.get("party_type"), opening.get("party_id")))
        if name and opening.get("id"):
            rows.append(_display("opening:" + opening["id"], name, f'{opening["amount"]} {opening["currency"]}',
                                 "رصيد افتتاحي · " + SIDE.get(opening.get("direction"), ""), opening.get("approved_at")))
    return rows


async def _assigned_catalog(db, owner, policy, state):
    from operational_balance_sources import entities
    scopes = {(r["kind"], r["id"]): r for r in policy.resources}
    resources = []
    for kind in sorted({key[0] for key in scopes}):
        for item in await entities(db, owner, kind, operational_state=state):
            access = scopes.get((kind, item["id"]))
            if access and item.get("ready") is not False and item.get("settings_complete") is not False:
                resources.append({"kind": kind, "id": item["id"], "currency": item.get("currency") or "SAR",
                                  "name": item.get("name") or item["id"], "access": access})
    return resources


async def assigned_finance_page(db, user, load, which, group, page):
    from operational_balance_workspace import paginate, resource_catalog
    from operational_financial_position_state import read_financial_position_state
    from operational_web_page_policy import request_modes
    default = {"reports": "summary", "specialized": "overview", "access": "access"}[which]
    group = group or default
    choices = REPORT_GROUPS if which == "reports" else SETTING_GROUPS if which == "specialized" else {"access"}
    if group not in choices:
        fail("operational_web_section_invalid", "قسم العرض غير متاح", 422)
    actor, owner, _, policy = await load(user, allow_disabled=True)
    modes = request_modes(actor)
    if modes is None:
        fail("web_page_permission_required", "يلزم منح صفحة الموظف", 403)
    if not policy.enabled:
        return {**paginate([], page, 8), "notice": "لم تُفعّل صلاحيات الحسابات والعمليات المالية لهذا الموظف."}
    if group == "opening-stock":
        from operational_opening_inventory import BATCHES
        rows = []
        if modes.get("products.inventory"):
            batch = await db[BATCHES].find_one({"_id": owner, "user_id": owner}, {"id": 1, "recorded_at": 1})
            rows.append(_display("stock", "المخزون الافتتاحي", "تم اعتماد الجرد الافتتاحي" if batch else "بانتظار اعتماد الجرد",
                                 "معتمد" if batch else "قيد الإعداد", (batch or {}).get("recorded_at")))
    else:
        if which == "reports" or group == "reports":
            state = await read_financial_position_state(db, owner)
            resources, computed = await resource_catalog(db, owner, policy, state)
        else:
            columns = {"owner_id": 1, "started_at": 1, "status": 1, "ad_funding": 1, "pos_receivable_binding": 1}
            if group in {"overview", "openings"}:
                columns["openings"] = 1
            elif group == "salary-start":
                columns["salary_accrual_policy"] = 1
            elif group == "movements":
                columns["movements"] = 1
            state = await db[STATES].find_one({"_id": owner, "owner_id": owner}, columns) or {"owner_id": owner, "status": "draft"}
            resources = await _assigned_catalog(db, owner, policy, state)
            computed = {}
        if which == "access":
            rows = await prepare_in_thread(_own_permissions, resources, policy)
        elif which == "reports":
            rows = await prepare_in_thread(_account_rows, resources, computed, group, state)
        else:
            rows = await prepare_in_thread(_settings_rows, group, resources, computed, state, policy)
    latest, latest_owner, _, current = await load(user, allow_disabled=True)
    if (actor["id"], owner, policy.version, policy.enabled) != (latest["id"], latest_owner, current.version, current.enabled):
        fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
    rows.sort(key=lambda row: (row["date"] or "", row["label"], row["id"]), reverse=True)
    return paginate(rows, page, 8)
