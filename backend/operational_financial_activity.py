"""Read-only daily activity from the selected financial authority.

The audit hook records small, immutable after-balance evidence in the same
owner CAS/transaction as the existing effect. It does not post another movement,
change a claim, or recompute old counterparties from their present obligations.
Amounts are decimal strings. Counterparty debt is receivable minus payable:
positive is owed to the merchant; negative is owed by the merchant.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from fastapi import HTTPException

from operational_balance_store import digest, fail, now, read

TIMEZONE = "Asia/Riyadh"
RIYADH = ZoneInfo(TIMEZONE)
SNAPSHOT_SCHEMA = "operational-financial-activity-v1"
FINANCIAL_ACTIONS = frozenset({
    "movement_saved", "order_bank_credited", "prepaid_shipping_paid",
    "driver_payment_review_approved", "customer_return_saved",
    "salla_customer_compensation_source_confirmed", "customer_exchange_saved",
})
SYSTEM_ACTORS = frozenset({"operational_order_worker"})
NON_DEBT_PARTIES = frozenset({"operating_expense", "owner_withdrawal", "bank", "cash"})


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    parsed = _instant(value)
    return parsed.isoformat() if parsed is not None else None


def _amount(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.quantize(Decimal(".01")):
            raise ValueError()
        return format(number, ".2f")
    except (ValueError, TypeError, InvalidOperation):
        fail("financial_movements_source_invalid", "تعذر التحقق من مبالغ سجل الحركات", 503)


def date_range(from_date, to_date):
    try:
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
        if start.isoformat() != from_date or end.isoformat() != to_date:
            raise ValueError()
        if end < start:
            raise ValueError()
        lower = datetime.combine(start, datetime.min.time(), RIYADH)
        upper = datetime.combine(end + timedelta(days=1), datetime.min.time(), RIYADH)
    except (ValueError, TypeError, OverflowError):
        fail("financial_movements_range_invalid", "اختر تاريخ بداية ونهاية صحيحين وبالترتيب", 422)
    return lower, upper


def _base(identity, raw, *, account_type, account_id, account_name,
          amount, currency, direction, party_type=None, party_id=None, party_name=None):
    amount = _amount(amount)
    if Decimal(amount) <= 0:
        fail("financial_movements_source_invalid", "تعذر التحقق من مبلغ الحركة المالية", 503)
    return {
        "id": identity, "occurred_at": _timestamp(raw.get("occurred_at")),
        "recorded_at": None, "activity_at": None, "time_basis": "unavailable",
        "employee_id": raw.get("actor_id") or None,
        "employee_name": None, "employee_source": "unavailable",
        "account_id": account_id or None, "account_name": account_name or None,
        "account_type": account_type or None, "amount": amount,
        "currency": currency, "direction": direction,
        "counterparty_id": party_id or None, "counterparty_name": party_name or None,
        "counterparty_type": party_type or None,
        "account_balance_after": None, "account_balance_status": "unavailable",
        "account_balance_currency": currency,
        "counterparty_balance_after": None,
        "counterparty_balance_status": "not_applicable" if party_type in NON_DEBT_PARTIES else "unavailable",
        "counterparty_balance_basis": None,
        "counterparty_balance_currency": raw.get("currency") or currency,
        "balance_as_of": None, "kind": raw.get("kind") or "movement",
        "reference": raw.get("reference") or None,
        "order_number": str(raw.get("order_number") or raw.get("original_order_number") or "") or None,
        "notes": [raw["note"]] if isinstance(raw.get("note"), str) and raw["note"].strip() else [],
    }


def _seal_basis(row):
    return {key: row.get(key) for key in (
        "id", "occurred_at", "employee_id", "account_id", "account_type", "amount", "currency", "direction",
        "counterparty_id", "counterparty_type", "counterparty_balance_currency", "kind", "reference", "order_number",
    )}


def _movement_rows(movement, facts):
    identity = "movement:" + str(movement["id"])
    kind, account_id = movement.get("bank_kind"), movement.get("bank_id")
    party_type, party_id, party_name = movement.get("party_type"), movement.get("party_id"), movement.get("name")
    fact = facts.get(movement.get("driver_review_event_id"))
    if fact and fact.get("movement_id") == movement["id"]:
        party_type, party_id, party_name = "store_driver", fact["driver_id"], None
    elif (party_type, party_id) == (kind, account_id):
        # A direct receipt records its bank as both technical legs. The bank is
        # not the customer/driver; do not invent their identity or a zero debt.
        party_type = "customer" if movement.get("order_id") or movement.get("exchange_id") else None
        party_id = party_name = None
    if not account_id and movement.get("party_type") in {"bank", "cash", "employee_custody"}:
        kind, account_id = movement["party_type"], movement["party_id"]
        account_name = movement.get("name")
        party_type = party_id = party_name = None
    else:
        account_name = movement.get("bank_name")
    row = _base(identity, movement, account_type=kind, account_id=account_id,
                account_name=account_name, amount=movement.get("bank_amount") or movement["amount"],
                currency=movement.get("bank_currency") or movement["currency"], direction=movement["direction"],
                party_type=party_type, party_id=party_id, party_name=party_name)
    rows = [row]
    if movement.get("kind") == "transfer":
        rows.append(_base(identity + ":destination", movement,
            account_type=movement["party_type"], account_id=movement["party_id"], account_name=movement.get("name"),
            amount=movement["amount"], currency=movement["currency"],
            direction="outgoing" if movement["direction"] == "incoming" else "incoming",
            party_type=kind, party_id=account_id, party_name=movement.get("bank_name")))
    for item in rows:
        # Pin allocation and source-currency effects too: an unchanged SAR
        # debit must not reuse a USD counterparty balance after a source edit.
        financial_fields = {key: movement.get(key) for key in (
            "amount", "currency", "bank_amount", "bank_currency", "bank_fee_amount", "actual_fee_amount",
            "allocations", "external_operation", "funding_mode", "driver_review_event_id", "exchange_id",
            "party_type", "party_id", "bank_kind", "bank_id", "direction", "kind",
        )}
        item["_basis"] = digest([_seal_basis(item), financial_fields])
        item["_movement_id"] = movement["id"]
        item["_actor_recorded_name"] = movement.get("actor_name") or None
    return rows


def _refund_row(case):
    if case.get("status") not in {"refunded", "salla_refund_confirmed"}:
        return None
    if case.get("refund_source_type") not in {"bank", "provider"} or not case.get("refund_source_id"):
        return None
    raw = {"occurred_at": case.get("refunded_at"), "actor_id": case.get("confirmed_by") or (
               case.get("created_by") if case.get("status") == "salla_refund_confirmed" else None),
           "currency": case["currency"], "kind": "customer_refund", "reference": case.get("refund_reference"),
           "order_number": case.get("order_number"), "note": case.get("note")}
    row = _base("refund:" + str(case["id"]), raw, account_type=case["refund_source_type"],
                account_id=case["refund_source_id"], account_name=case.get("refund_source_name"),
                amount=case["amount"], currency=case["currency"], direction="outgoing",
                party_type="customer", party_id=case.get("customer_id"), party_name=case.get("customer_name"))
    row["_basis"] = digest(_seal_basis(row))
    return row


def _pos_row(fact):
    raw = {"occurred_at": fact["approved_at"], "actor_id": fact["approved_by"], "currency": fact["currency"],
           "kind": "pos_collection_approved", "reference": fact.get("transaction_reference"),
           "order_number": fact.get("order_number")}
    row = _base("pos:" + fact["id"], raw, account_type="pos_receivable", account_id=fact["destination_id"],
                account_name=fact.get("destination_name"), amount=fact["amount"], currency=fact["currency"],
                direction="noncash", party_type="store_driver", party_id=fact["driver_id"])
    row["notes"] = ["اعتماد ذمة شبكة؛ لا يمثل إيداعًا في البنك."]
    row["_basis"] = digest(_seal_basis(row))
    return row


def operational_rows(state):
    from operational_driver_review_report import validated_review_facts
    facts, issues = validated_review_facts(state)
    by_id = {row["id"]: row for row in facts}
    rows = [item for movement in state.get("movements", []) for item in _movement_rows(movement, by_id)]
    rows.extend(row for case in state.get("customer_returns", []) if (row := _refund_row(case)) is not None)
    rows.extend(_pos_row(fact) for fact in facts if fact["payment_method"] == "card_terminal")
    identities = [row["id"] for row in rows]
    if len(identities) != len(set(identities)):
        fail("financial_movements_source_invalid", "يوجد تعارض في هوية الحركات المالية", 503)
    return rows, issues


def _affected(state, event):
    after, before = event.get("after") or {}, event.get("before") or {}
    action = event["action"]
    if action in {"movement_saved", "order_bank_credited", "prepaid_shipping_paid"}:
        return {"movement:" + str(after["id"]), "movement:" + str(after["id"]) + ":destination"}
    if action == "driver_payment_review_approved":
        return {"movement:" + str(after["movement_id"])} if after.get("movement_id") else {"pos:" + str(after["id"])}
    if action in {"customer_return_saved", "salla_customer_compensation_source_confirmed"}:
        return {"refund:" + str(after["id"])} if _refund_row(after) and not _refund_row(before) else set()
    if action == "customer_exchange_saved":
        previous = {row["movement_id"] for row in before.get("contributions", [])}
        return {"movement:" + row["movement_id"] for row in after.get("contributions", [])
                if row["movement_id"] not in previous and not row.get("linked_existing")}
    return set()


def capture_after_balances(state, event):
    """Called after each real effect, before its existing atomic audit append.

    Deliberately no database access and no modification of monetary records.
    Each worker append is audited separately, so a batch cannot stamp its final
    balance onto every preceding movement. Historic entries are never backfilled.
    """
    if event["action"] not in FINANCIAL_ACTIONS:
        return
    affected = _affected(state, event)
    if not affected:
        return
    # Normalize only this event. An unrelated malformed historical display row
    # must not turn an otherwise valid new monetary write into a feed failure.
    action, after = event["action"], event.get("after") or {}
    if action in {"movement_saved", "order_bank_credited", "prepaid_shipping_paid"}:
        selected = _movement_rows(after, {})
    elif action == "driver_payment_review_approved":
        from operational_driver_review_report import validated_review_facts
        facts, _ = validated_review_facts({**state, "driver_payment_review_facts": {after["id"]: after}})
        if not facts:
            return
        fact = facts[0]
        selected = ([_pos_row(fact)] if fact["payment_method"] == "card_terminal" else [
            row for movement in state.get("movements", []) if movement.get("id") == fact["movement_id"]
            for row in _movement_rows(movement, {fact["id"]: fact})])
    elif action in {"customer_return_saved", "salla_customer_compensation_source_confirmed"}:
        selected = [_refund_row(after)]
    else:
        selected = [row for movement in state.get("movements", []) if "movement:" + str(movement.get("id")) in affected
                    for row in _movement_rows(movement, {})]
    if not selected:
        return
    from operational_balance_service import report
    # Financial projections do not consume audit/request history. Avoid copying
    # the growing sealed log for every automatic movement in a worker batch.
    try:
        projection = report({**state, "audit": [], "requests": {},
                             "engine": {**state.get("engine", {}), "audit": []}})
    except (ValueError, KeyError, TypeError, ArithmeticError, HTTPException):
        # This hook only adds evidence. The existing writer still owns all of
        # its validations. An unprojectable historical balance is unavailable,
        # and never blocks or changes that writer's already valid amount.
        projection = {"parties": [], "obligations": []}
    parties = {(r["party_type"], r["party_id"], r["currency"]): r for r in projection["parties"]}
    incomplete = {(r["party_type"], r["party_id"], r["currency"])
                  for r in projection["obligations"] if r.get("incomplete")}
    captured = now()
    snapshots = []
    for row in selected:
        account_key = (row["account_type"], row["account_id"], row["currency"])
        party_key = (row["counterparty_type"], row["counterparty_id"], row["counterparty_balance_currency"])
        account, party = parties.get(account_key), parties.get(party_key)
        entry = {"schema": SNAPSHOT_SCHEMA, "owner_id": state["owner_id"], "audit_id": event["id"],
                 "activity_id": row["id"], "basis": row["_basis"], "captured_at": captured,
                 "account_balance_after": None, "account_balance_status": "unavailable",
                 "counterparty_balance_after": None, "counterparty_balance_status": row["counterparty_balance_status"],
                 "counterparty_balance_basis": None}
        has_opening = any((r["party_type"], r["party_id"], r["currency"]) == account_key
                          for r in state.get("openings", {}).values())
        if account and account_key[0] in {"bank", "cash"} and has_opening:
            entry.update(account_balance_after=_amount(account["actual"]), account_balance_status="available")
        elif account and account_key[0] == "pos_receivable" and account_key not in incomplete:
            entry.update(account_balance_after=_amount(Decimal(account["outstanding_receivable"]) - Decimal(account["outstanding_payable"])),
                         account_balance_status="available")
        if party and party_key not in incomplete and row["counterparty_balance_status"] != "not_applicable":
            entry.update(counterparty_balance_after=_amount(Decimal(party["outstanding_receivable"]) - Decimal(party["outstanding_payable"])),
                         counterparty_balance_status="available", counterparty_balance_basis="confirmed_obligations")
        # Names are frozen only if already present in the authoritative state.
        entry["counterparty_name"] = (party or {}).get("name") or row["counterparty_name"]
        entry["account_name"] = (account or {}).get("name") or row["account_name"]
        entry["seal"] = digest(entry)
        snapshots.append(entry)
    event["financial_activity_snapshots"] = snapshots


def attach_snapshots(state, rows):
    indexed = {}
    recorded = {}
    for event in state.get("audit", []):
        if event.get("action") not in FINANCIAL_ACTIONS:
            continue
        for identity in _affected(state, event):
            # Old audit time is useful provenance for manual operations only;
            # worker audit stamps can be historical order completion times.
            if event.get("actor_id") not in SYSTEM_ACTORS:
                recorded.setdefault(identity, _timestamp(event.get("at")))
        for snap in event.get("financial_activity_snapshots", []):
            if (snap.get("schema") == SNAPSHOT_SCHEMA and snap.get("owner_id") == state["owner_id"]
                    and snap.get("audit_id") == event["id"]
                    and snap.get("seal") == digest({k: v for k, v in snap.items() if k != "seal"})):
                indexed.setdefault(snap.get("activity_id"), []).append(snap)
    for row in rows:
        row["recorded_at"] = recorded.get(row["id"])
        matches = [s for s in indexed.get(row["id"], []) if s["basis"] == row["_basis"]]
        if len(matches) != 1:
            continue
        snap = matches[0]
        for field in ("account_balance_after", "account_balance_status", "counterparty_balance_after", "counterparty_balance_status"):
            row[field] = snap[field]
        row["counterparty_balance_basis"] = "confirmed_obligations" if row["counterparty_balance_status"] == "available" else None
        row["balance_as_of"] = row["recorded_at"] = _timestamp(snap["captured_at"])
        row["counterparty_name"] = snap.get("counterparty_name") or row["counterparty_name"]
        row["account_name"] = snap.get("account_name") or row["account_name"]
    for row in rows:
        row["activity_at"] = row["recorded_at"] or row["occurred_at"]
        row["time_basis"] = "recorded" if row["recorded_at"] else "occurred" if row["occurred_at"] else "unavailable"


def apply_account_scope(rows, allowed):
    """None means verified owner; empty set means no assigned accounts."""
    if allowed is None:
        return deepcopy(rows)
    visible = []
    for original in rows:
        if (original["account_type"], original["account_id"]) not in allowed:
            continue
        row = deepcopy(original)
        if row["counterparty_type"] in {"bank", "cash"} and (
                row["counterparty_type"], row["counterparty_id"]) not in allowed:
            # Transfer source access never grants the other account's identity,
            # after balance, reference or free text that could name that account.
            row.update(counterparty_type=None, counterparty_id=None, counterparty_name=None,
                       counterparty_balance_after=None, counterparty_balance_status="unavailable",
                       counterparty_balance_basis=None, counterparty_balance_currency=None, reference=None, order_number=None,
                       notes=["تفاصيل الحساب المقابل خارج صلاحية العرض."])
        visible.append(row)
    return visible


async def _actor_names(db, owner, rows):
    identities = {row["employee_id"] for row in rows if row["employee_id"] and row["employee_id"] not in SYSTEM_ACTORS}
    users = await db.users.find({"$or": [{"id": owner, "role": "owner"}, {"created_by": owner}],
                                "id": {"$in": sorted(identities)}}, {"_id": 0, "id": 1, "name": 1}).to_list(len(identities) + 1) if identities else []
    names = {user["id"]: user.get("name") for user in users}
    for row in rows:
        if row["employee_id"] in SYSTEM_ACTORS:
            row.update(employee_name="النظام", employee_source="system")
        elif names.get(row["employee_id"]):
            frozen = row.get("_actor_recorded_name")
            row.update(employee_name=frozen or names[row["employee_id"]], employee_source="recorded" if frozen else "current_profile")
        else:
            # A foreign/deleted profile is not resolved through the owner's name.
            row.update(employee_id=None, employee_name=None, employee_source="unavailable")


async def _driver_names(db, owner, rows):
    identities = {row["counterparty_id"] for row in rows if row["counterparty_type"] == "store_driver"
                  and row["counterparty_id"] and row["counterparty_name"] in {None, row["counterparty_id"]}}
    if not identities:
        return
    drivers = await db.store_drivers.find({"user_id": owner, "id": {"$in": sorted(identities)}},
                                         {"_id": 0, "id": 1, "name": 1}).to_list(len(identities) + 1)
    names = {row["id"]: row.get("name") for row in drivers}
    for row in rows:
        if row["counterparty_type"] == "store_driver" and row["counterparty_id"] in identities:
            row["counterparty_name"] = names.get(row["counterparty_id"]) or row["counterparty_name"]


async def account_scope(db, owner, actor):
    if actor.get("role") == "owner" and actor.get("id") == owner:
        return None
    from operational_app_banks import assigned_banks
    assigned = await assigned_banks(db, owner, actor)
    return {("bank", identity) for identity in assigned["bank_ids"]} | {("cash", identity) for identity in assigned["cash_ids"]}


async def financial_control(db, owner):
    marker = await db.mz2_atomic_owners.find_one({"_id": owner}, {"ledger_backend_state": 1}) or {}
    settings = await db.settings.find_one({"user_id": owner}, {"mezan2_financial_cutover": 1}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    return {"ledger_backend_state": marker.get("ledger_backend_state"), "cutover": cutover}


async def daily_financial_movements(db, *, owner, actor, from_date, to_date,
                                    page=1, page_size=10, snapshot=None):
    lower, upper = date_range(from_date, to_date)
    if isinstance(page, bool) or isinstance(page_size, bool) or page < 1 or not 1 <= page_size <= 10:
        fail("financial_movements_page_invalid", "حجم الصفحة من حركة واحدة إلى 10 حركات", 422)
    if page > 1 and not snapshot:
        fail("financial_movements_snapshot_required", "أعد قراءة الصفحة الأولى قبل المتابعة", 422)
    allowed = await account_scope(db, owner, actor)
    control = await financial_control(db, owner)
    notes, issues, rows, revision = [], [], [], None
    financial_source = "unavailable"
    native = control["ledger_backend_state"] == "v2_active" or control["cutover"].get("status") == "active"
    if native or control["ledger_backend_state"] == "transition_blocked":
        # A cutover must never silently fall back to the operational snapshot.
        notes.append("المصدر المالي الحالي هو المحاسبة؛ عرض حركاته غير متاح في هذه الصفحة حتى اكتمال التحقق من سجله.")
    else:
        state = await read(db, owner)
        revision = state.get("revision")
        if state.get("status") in {"active", "frozen"}:
            financial_source = "operational"
            rows, issues = operational_rows(state)
            attach_snapshots(state, rows)
            notes.append("الحركات المؤكدة من السجل التشغيلي، والاستردادات المؤكدة، واعتمادات ذمم الشبكة. الأرصدة الافتتاحية والتقديرات والمراجعات المعلقة ليست حركات تحصيل.")
        else:
            notes.append("السجل التشغيلي غير مفعّل؛ لا توجد حركات معتمدة متاحة من هذا المصدر.")
    rows = apply_account_scope(rows, allowed)
    undated = sum(row["activity_at"] is None for row in rows)
    rows = [row for row in rows if row["activity_at"] is not None and lower <= _instant(row["activity_at"]) < upper]
    await _actor_names(db, owner, rows)
    await _driver_names(db, owner, rows)
    rows.sort(key=lambda row: (row["activity_at"], row["id"]), reverse=True)
    missing_actor = sum(row["employee_source"] == "unavailable" for row in rows)
    missing_balance = sum(row["account_balance_status"] == "unavailable" or row["counterparty_balance_status"] == "unavailable" for row in rows)
    if allowed == set():
        notes.append("لا توجد بنوك أو صناديق مسندة لهذا الموظف. يحدد المالك الحسابات المسموح بعرضها.")
    if undated:
        notes.append("توجد حركات بلا توقيت موثق؛ لم تُنسب إلى يوم تخميني.")
    if missing_actor:
        notes.append("اسم مسجل بعض الحركات غير متاح في سجل الموظفين التابع لهذا المالك.")
    if missing_balance:
        notes.append("غير متاح يعني أن الرصيد بعد الحركة لم يُحفظ بدليل كافٍ؛ لا يُستبدل بالرصيد الحالي أو بالصفر.")
    if any(row["balance_as_of"] for row in rows):
        notes.append("الرصيد محفوظ عند تسجيل الحركة. تاريخ حدوث الحركة مستقل عن وقت تسجيلها؛ الرصيد الموجب للجهة لنا والسالب علينا.")
    if any(row["counterparty_balance_basis"] == "confirmed_obligations" for row in rows):
        notes.append("رصيد الجهة هو المستحق المؤكد لنا ناقص المستحق المؤكد علينا بعد التخصيصات المعتمدة. لا يشمل التقديرات أو عمولات المنصات المعلقة غير المثبتة، وليس صافيًا تقديريًا متاحًا للسحب.")
    if any(row["time_basis"] == "occurred" for row in rows):
        notes.append("بعض الحركات القديمة تظهر بتاريخ الحدث الأصلي لأن وقت تسجيلها غير موثق.")
    if issues and allowed is None:
        notes.append("توجد اعتمادات تحصيل تعذر مطابقتها بأثرها المالي؛ لم تضف إلى الحركات المؤكدة.")
    public_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in rows]
    token = digest({"schema": SNAPSHOT_SCHEMA, "owner": owner, "actor": actor["id"], "allowed": None if allowed is None else sorted(allowed),
                    "control": control, "revision": revision if allowed is None else None, "range": [from_date, to_date], "page_size": page_size,
                    "rows": public_rows})
    if snapshot is not None and snapshot != token:
        fail("financial_movements_cursor_changed", "تغيرت الحركات أو الصلاحيات؛ أعد تحميل الصفحة الأولى", 409)
    # A read may overlap a permission/cutover edit. Do not return a response from
    # the previously authorized scope after detecting that change.
    if allowed != await account_scope(db, owner, actor):
        fail("financial_movements_scope_changed", "تغيرت صلاحيات الحسابات؛ أعد تحميل الصفحة", 403)
    if control != await financial_control(db, owner):
        fail("financial_movements_cursor_changed", "تغير المصدر المالي؛ أعد تحميل الصفحة الأولى", 409)
    total = len(rows)
    pages = (total + page_size - 1) // page_size
    items = public_rows[(page - 1) * page_size:page * page_size]
    return {"from_date": from_date, "to_date": to_date, "timezone": TIMEZONE,
            "financial_source": financial_source, "page": page, "page_size": page_size,
            "total": total, "total_pages": pages, "has_previous": page > 1,
            "has_next": page < pages, "has_more": page < pages, "snapshot": token,
            "captured_at": None, "items": items,
            "coverage": {"complete": financial_source != "unavailable" and not undated and not missing_actor and not missing_balance
                                      and not (issues and allowed is None),
                         "undated_count": undated, "missing_actor_count": missing_actor,
                         "missing_balance_count": missing_balance, "notes": notes}}
