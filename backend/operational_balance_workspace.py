"""Small, scoped ledger over the existing operational source of truth.

No second balance store, source polling, native accounting activation or provider
payment is introduced here. Reads project the saved aggregate. Ordinary money
uses create_movement; linked external payments use the same aggregate CAS.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from math import ceil
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from operational_balance_service import (
    KINDS, active_gate, create_movement, entity, fmt, money, outstanding, report,
)
from operational_balance_store import (
    audit, claim_movement, digest, fail, mutate, now, read, remember, replay,
)
from operational_financial_activity_runtime import prepare_in_thread
from operational_visible_report_read import run_visible_report_read


RESOURCE_KINDS = (
    "employee", "supplier", "external_person", "store_driver", "courier",
    "provider", "ad_account", "employee_custody", "bank", "cash",
    "pos_receivable", "operating_expense", "owner_withdrawal",
)
ACCOUNT_KINDS = {"bank", "cash", "employee_custody"}
KIND_OPERATIONS = {
    "employee": ["employee.accrual", "employee.payment", "employee.collection", "employee.adjustment"],
    "supplier": ["supplier.accrual", "supplier.payment", "supplier.collection", "supplier.adjustment"],
    "external_person": ["external.receive", "external.pay", "external.salary", "external.charity_company",
                        "external.charity_owner", "external.household", "external.personal"],
    "bank": ["accounts.transfer"], "cash": ["accounts.transfer"],
    "employee_custody": ["custody.fund", "custody.return"],
    "operating_expense": ["expense.pay"], "owner_withdrawal": ["owner.withdraw"],
    "provider": ["provider.settle"], "pos_receivable": ["provider.settle"],
    "ad_account": ["ads.fund"], "courier": ["courier.pay", "courier.receive"],
    "store_driver": ["courier.pay", "courier.receive"],
}
LABELS = {
    "accrual": "تسجيل مستحق", "payment": "دفعة", "collection": "استلام مبلغ",
    "adjustment": "تسوية موثقة", "receipt": "تسوية مديونية",
    "salary_settlement": "صرف راتب موظف", "charity_company": "صدقات المنشأة",
    "charity_owner": "صدقات على حساب المالك", "household": "مصروفات منزلية",
    "personal": "مصروفات شخصية", "transfer": "تحويل بين الحسابات",
}
EXTERNAL_OPERATIONS = {
    "receipt": "external.receive", "salary_settlement": "external.salary",
    "charity_company": "external.charity_company", "charity_owner": "external.charity_owner",
    "household": "external.household", "personal": "external.personal",
}


class WorkspaceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(min_length=8, max_length=160)
    expected_session_scope: str = Field(pattern=r"^[a-f0-9]{64}$")
    party_type: str = Field(min_length=1, max_length=50)
    party_id: str = Field(min_length=1, max_length=200)
    side: Literal["for_party", "for_us"]
    amount: str = Field(min_length=1, max_length=30)
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: str | None = Field(default=None, max_length=60)
    source_type: Literal["bank", "cash", "employee_custody"] | None = None
    source_id: str | None = Field(default=None, max_length=200)
    employee_id: str | None = Field(default=None, max_length=200)
    owner_id: str | None = Field(default=None, max_length=200)
    expense_id: str | None = Field(default=None, max_length=200)
    note: str = Field(default="", max_length=1000)
    reference: str = Field(default="", max_length=160)

    @field_validator("amount")
    @classmethod
    def valid_amount(cls, value):
        digits = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
        return fmt(money(value.translate(digits).replace("٫", ".")))

    @field_validator("business_date")
    @classmethod
    def valid_date(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("invalid_business_date")
        return value


def entry_intent(payload):
    """Infer operation and all affected resources from the validated form."""
    kind, identity, side = payload["party_type"], payload["party_id"], payload["side"]
    reason = payload.get("reason") or ""
    if kind not in KIND_OPERATIONS:
        fail("operational_workspace_kind", "اختر جهة صحيحة", 422)
    resources = [(kind, identity)]
    cash_required = False
    if kind in {"employee", "supplier"}:
        if reason not in {"accrual", "payment", "collection", "adjustment"}:
            fail("operational_workspace_reason", "اختر سبب المبلغ", 422)
        expected = {"accrual": "for_party", "payment": "for_us", "collection": "for_party"}.get(reason)
        if expected and side != expected:
            fail("operational_workspace_side", "السبب لا يطابق له أو عليه", 422)
        if reason == "adjustment" and not payload.get("note"):
            fail("operational_workspace_note", "اكتب سبب التسوية", 422)
        operation = kind + "." + reason
        cash_required = reason in {"payment", "collection"}
    elif kind == "external_person":
        if side == "for_us":
            if reason:
                fail("operational_workspace_hidden_reason", "تسليم المبلغ لا يحتاج اختيار سبب", 422)
            operation, cash_required = "external.pay", True
        else:
            operation = EXTERNAL_OPERATIONS.get(reason)
            if not operation:
                fail("operational_workspace_reason", "اختر سبب المبلغ", 422)
            cash_required = reason == "receipt"
            field, target_kind = {
                "salary_settlement": ("employee_id", "employee"),
                "charity_company": ("expense_id", "operating_expense"),
                "charity_owner": ("owner_id", "owner_withdrawal"),
                "household": ("owner_id", "owner_withdrawal"),
                "personal": ("owner_id", "owner_withdrawal"),
            }.get(reason, (None, None))
            if field:
                if not payload.get(field):
                    fail("operational_workspace_beneficiary", "اختر المستفيد من المبلغ", 422)
                resources.append((target_kind, payload[field]))
    elif kind in {"bank", "cash"}:
        if reason != "transfer" or side != "for_us":
            fail("operational_workspace_transfer", "حدد حساب الاستلام ومصدر التحويل", 422)
        operation, cash_required = "accounts.transfer", True
    else:
        mapping = {
            ("employee_custody", "payment"): ("custody.fund", "for_us"),
            ("employee_custody", "collection"): ("custody.return", "for_party"),
            ("operating_expense", "payment"): ("expense.pay", "for_us"),
            ("owner_withdrawal", "payment"): ("owner.withdraw", "for_us"),
            ("provider", "collection"): ("provider.settle", "for_party"),
            ("pos_receivable", "collection"): ("provider.settle", "for_party"),
            ("ad_account", "payment"): ("ads.fund", "for_us"),
            ("courier", "payment"): ("courier.pay", "for_us"),
            ("courier", "collection"): ("courier.receive", "for_party"),
            ("store_driver", "payment"): ("courier.pay", "for_us"),
            ("store_driver", "collection"): ("courier.receive", "for_party"),
        }
        selected = mapping.get((kind, reason))
        if selected is None or selected[1] != side:
            fail("operational_workspace_operation", "اختر عملية مناسبة لهذا الحساب", 422)
        operation, cash_required = selected[0], True
    if cash_required:
        if not payload.get("source_type") or not payload.get("source_id"):
            fail("operational_workspace_source", "اختر البنك أو الصندوق أو العهدة", 422)
        if payload["source_type"] == "employee_custody" and not (
                side == "for_us" and kind in {"external_person", "supplier", "operating_expense"}):
            fail("operational_workspace_custody", "العهدة غير مناسبة لهذه العملية", 422)
        resources.append((payload["source_type"], payload["source_id"]))
        if len(set(resources)) != len(resources):
            fail("operational_workspace_same_account", "اختر حسابين مختلفين", 422)
    elif payload.get("source_type") or payload.get("source_id"):
        fail("operational_workspace_stale_source", "أعد اختيار سبب المبلغ", 422)
    expected_target = resources[1] if not cash_required and len(resources) == 2 else None
    for field, target_kind in (("employee_id", "employee"), ("owner_id", "owner_withdrawal"), ("expense_id", "operating_expense")):
        if payload.get(field) and expected_target != (target_kind, payload[field]):
            fail("operational_workspace_stale_target", "أعد اختيار سبب المبلغ والمستفيد", 422)
    return operation, resources


def _actor_snapshot(actor):
    return {"id": actor["id"], "name": str(actor.get("name") or "غير موثق")}


async def _linked_external_entry(db, owner, actor, payload, operation, resources, source):
    """One CAS for external credit and its employee/expense/owner counterpart."""
    identity = await claim_movement(db, owner, actor["id"], payload)
    action = "workspace-linked:" + actor["id"]
    stamp = now()

    async def apply(state):
        previous = replay(state, action, payload)
        if previous is not None:
            return previous
        await active_gate(db, owner, state)
        if date.fromisoformat(payload["business_date"]) > datetime.fromisoformat(stamp).astimezone(ZoneInfo("Asia/Riyadh")).date():
            fail("operational_future_business_date", "لا يمكن تسجيل حركة بتاريخ مستقبلي", 422)
        external = await entity(db, owner, "external_person", payload["party_id"], "SAR")
        target_kind, target_id = resources[1]
        target = await entity(db, owner, target_kind, target_id, "SAR")
        amount = money(payload["amount"])
        allocations = []
        if target_kind == "employee":
            balances = report(state)
            employee = next((r for r in balances["parties"] if r["party_type"] == target_kind
                             and r["party_id"] == target_id and r["currency"] == "SAR"), {})
            available = Decimal(employee.get("outstanding_payable", "0")) - Decimal(employee.get("outstanding_receivable", "0"))
            if amount > max(available, Decimal(0)):
                fail("operational_workspace_salary_limit", "المبلغ غير قابل للتسوية لهذا الموظف", 409)
            remaining = amount
            for row in balances["obligations"]:
                if (row.get("party_type"), row.get("party_id"), row.get("currency"), row.get("direction")) != (target_kind, target_id, "SAR", "payable"):
                    continue
                if row.get("pending_confirmation"):
                    continue
                take = min(remaining, Decimal(row.get("outstanding", "0")))
                if take > 0:
                    allocations.append({"obligation_id": row["id"], "amount": fmt(take)})
                    remaining -= take
        shared = {
            "amount": fmt(amount), "currency": "SAR", "request_id": payload["request_id"],
            "actor_id": actor["id"], "recorded_by": _actor_snapshot(actor), "occurred_at": stamp,
            "business_date": payload["business_date"], "source": source, "workspace_group": identity,
            "workspace_operation": operation, "workspace_reason": payload["reason"],
            "workspace_resources": [{"kind": k, "id": i} for k, i in resources],
            "note": payload.get("note", ""), "reference": payload.get("reference", ""),
            "bank_id": None, "bank_kind": None, "bank_name": None, "actual_fee_amount": "0.00",
        }
        external_row = {**shared, "id": identity, "party_type": "external_person", "party_id": external["id"],
                        "name": external["name"], "kind": "correction", "direction": "outgoing", "allocations": []}
        counterpart = {**shared, "id": identity + ":counterpart", "party_type": target_kind,
                       "party_id": target_id, "name": target["name"], "kind": "payment",
                       "direction": "outgoing", "allocations": allocations}
        # Both rows are real legs of this one instruction. There is no bank leg
        # and no new salary expense or accrual. The report's existing allocation
        # logic pays recorded obligations, or offsets a prior manual credit.
        state["movements"].extend([external_row, counterpart])
        audit(state, actor["id"], "workspace_external_payment", None,
              {"id": identity, "movements": [external_row, counterpart]}, LABELS[payload["reason"]], source, stamp)
        result = {"saved": True, "movement_id": identity, "party_type": "external_person", "party_id": external["id"]}
        remember(state, action, payload, result)
        return result

    return await mutate(db, owner, apply)


async def save_entry(db, owner, actor, payload, source):
    from operational_balance_access import guard_access
    operation, resources = entry_intent(payload)
    if payload["expected_session_scope"] != digest([owner, actor["id"]]):
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 409)
    required = [operation, "custody.spend"] if payload.get("source_type") == "employee_custody" else operation
    async with guard_access(db, actor, owner, required, resources):
        if payload["party_type"] == "external_person" and payload["side"] == "for_party" and payload.get("reason") != "receipt":
            return await _linked_external_entry(db, owner, actor, payload, operation, resources, source)
        reason, side = payload.get("reason"), payload["side"]
        correction = reason in {"accrual", "adjustment"}
        direction = ("outgoing" if side == "for_party" else "incoming") if correction else (
            "outgoing" if side == "for_us" else "incoming")
        kind = "correction" if correction else "payment" if direction == "outgoing" else "collection"
        if reason == "transfer":
            kind, direction = "transfer", "outgoing"
        body = {
            "request_id": payload["request_id"], "expected_session_scope": payload["expected_session_scope"],
            "party_type": payload["party_type"], "party_id": payload["party_id"],
            "currency": "SAR", "amount": payload["amount"], "direction": direction, "kind": kind,
            "business_date": payload["business_date"], "bank_id": payload.get("source_id"),
            "source_account_type": payload.get("source_type") or "bank_auto",
            "note": payload.get("note") or LABELS.get(reason) or "تسليم مبلغ للجهة",
            "reference": payload.get("reference", ""), "receipt_id": None, "order_number": None,
            "allocations": [], "actual_fee_amount": "0.00", "workspace_operation": operation,
            "workspace_reason": reason or "payment", "workspace_intent_hash": digest(payload),
            "workspace_resources": [{"kind": k, "id": i} for k, i in resources],
        }
        saved = await create_movement(db, owner, actor["id"], body, source=source)
        return {"saved": True, "movement_id": saved["id"], "party_type": saved["party_type"], "party_id": saved["party_id"]}


def _balance(row, kind):
    if kind in ACCOUNT_KINDS:
        signed = -Decimal(row.get("actual", "0"))
    elif kind in {"operating_expense", "owner_withdrawal"}:
        signed = -Decimal(row.get("actual", "0"))
    else:
        signed = Decimal(row.get("outstanding_payable", "0")) - Decimal(row.get("outstanding_receivable", "0"))
    return {"amount": fmt(abs(signed)), "side": "for_party" if signed > 0 else "for_us" if signed < 0 else "balanced", "visible": True}


async def resource_catalog(db, owner, policy, state):
    from operational_balance_sources import entities
    computed = await prepare_in_thread(report, state)
    balances = {(r["party_type"], r["party_id"], r["currency"]): r for r in computed["parties"]}
    items = []
    for kind in RESOURCE_KINDS:
        if not policy.owner_override and not any(r["kind"] == kind for r in policy.resources):
            continue
        for row in await entities(db, owner, kind, operational_state=state):
            access = policy.resource(kind, row["id"])
            if not (access["use"] or access["balance"] or access["movements"] != "none"):
                continue
            if row.get("ready") is False or row.get("settings_complete") is False:
                continue
            currency = row.get("currency") or "SAR"
            operations = [key for key in KIND_OPERATIONS.get(kind, []) if access["use"] and policy.can_operation(key)]
            # The simple amount input is explicitly SAR. Existing cross-currency
            # funding stays in the original evidenced FX form.
            if currency != "SAR":
                operations = []
            items.append({"kind": kind, "id": row["id"], "name": row.get("name") or row["id"],
                          "currency": currency, "access": access, "operations": operations,
                          "balance": _balance(balances.get((kind, row["id"], currency), {}), kind) if access["balance"] else None})
    return items, computed


def _recorded_by(item):
    actor = item.get("recorded_by")
    if isinstance(actor, dict) and actor.get("id") == item.get("actor_id") and actor.get("name"):
        return {"id": actor["id"], "name": str(actor["name"])}
    return None


def _business_day(item):
    if item.get("business_date"):
        return str(item["business_date"])
    stamp = item.get("occurred_at") or item.get("created_at") or item.get("approved_at") or ""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Riyadh")).date().isoformat()
    except (ValueError, TypeError):
        return ""


def _can_row(policy, resource, item):
    mode = resource["access"]["movements"]
    return mode == "all" or mode == "own" and item.get("actor_id") == policy.actor["id"]


def _row(policy, resource, item, amount, side, description):
    related = item.get("workspace_resources") or [{"kind": item.get("party_type"), "id": item.get("party_id")}]
    if item.get("bank_id"):
        related = [*related, {"kind": item.get("bank_kind"), "id": item["bank_id"]}]
    complete = all(r.get("kind") and r.get("id") and policy.can_reference(r["kind"], r["id"]) for r in related)
    # A free-text description can disclose an otherwise hidden counterpart.
    # Use structured labels whenever a connected resource is no longer allowed.
    note = item.get("note") if complete else None
    label = description if not note or note == description else description + " · " + note
    result = {"id": item["id"], "event_id": item.get("workspace_group") or item["id"],
              "description": label, "business_date": _business_day(item),
              "recorded_at": item.get("occurred_at") or item.get("created_at") or item.get("approved_at") or "",
              "recorded_by": _recorded_by(item), "amount": fmt(amount), "side": side,
              "currency": resource["currency"], "reason": item.get("workspace_reason") or item.get("kind"),
              "order_number": item.get("order_number") if complete else None,
              "party_type": resource["kind"], "party_id": resource["id"], "party_name": resource["name"]}
    if complete and item.get("bank_name"):
        result["source_label"] = item["bank_name"]
    if complete and item.get("reference"):
        result["reference"] = item["reference"]
    return result


def ledger_items(state, computed, resource, policy):
    kind, identity, currency = resource["kind"], resource["id"], resource["currency"]
    result = []
    for item in state.get("openings", {}).values():
        if (item.get("party_type"), item.get("party_id"), item.get("currency")) != (kind, identity, currency) or not _can_row(policy, resource, item):
            continue
        amount = Decimal(item["amount"])
        if amount:
            result.append(_row(policy, resource, item, amount, item["direction"], "رصيد افتتاحي"))
    for item in computed["obligations"]:
        if item.get("kind") in {"baseline", "external_advance", "advertising_balance"} or item.get("virtual"):
            continue
        if (item.get("party_type"), item.get("party_id"), item.get("currency")) != (kind, identity, currency) or not _can_row(policy, resource, item):
            continue
        amount = Decimal(item.get("confirmed", "0"))
        if amount > 0:
            result.append(_row(policy, resource, item, amount,
                               "for_party" if item["direction"] == "payable" else "for_us", "مستحق من المصدر"))
    for item in state.get("movements", []):
        primary = (item["party_type"], item["party_id"], item["currency"]) == (kind, identity, currency)
        cash = (item.get("bank_kind"), item.get("bank_id"), item.get("bank_currency") or item["currency"]) == (kind, identity, currency)
        if not (primary or cash) or not _can_row(policy, resource, item):
            continue
        amount = Decimal(item.get("bank_amount") or item["amount"]) if cash else Decimal(item["amount"])
        direction = item["direction"]
        if cash:
            side = "for_us" if direction == "incoming" else "for_party"
        elif kind in ACCOUNT_KINDS:
            if item["kind"] == "correction":
                side = "for_us" if direction == "incoming" else "for_party"
            else:
                side = "for_us" if direction == "outgoing" else "for_party"
        elif item["kind"] == "correction":
            side = "for_party" if direction == "outgoing" else "for_us"
        else:
            side = "for_us" if direction == "outgoing" else "for_party"
        description = LABELS.get(item.get("workspace_reason")) or {
            "payment": "دفعة", "collection": "استلام مبلغ", "settlement": "تسوية",
            "transfer": "تحويل بين الحسابات", "correction": "تسوية موثقة",
            "wallet_funding": "تمويل رصيد الإعلانات", "refund": "رد مبلغ",
        }.get(item["kind"], "حركة مالية")
        result.append(_row(policy, resource, item, amount, side, description))
    if kind in {"bank", "provider"}:
        for case in state.get("customer_returns", []):
            if (case.get("refund_source_type"), case.get("refund_source_id"), case.get("currency")) != (kind, identity, currency):
                continue
            if case.get("status") not in {"refunded", "salla_refund_confirmed"} or not _can_row(policy, resource, case):
                continue
            if case["status"] == "salla_refund_confirmed":
                from operational_provider_statement_reconciliation import fact_backed_employee_refund
                if fact_backed_employee_refund(state, case):
                    continue
            elif kind != "bank":
                continue
            item = {**case, "id": "customer_return:" + case["id"], "party_type": kind, "party_id": identity,
                    "occurred_at": case.get("refunded_at") or case.get("updated_at") or case.get("created_at"),
                    "kind": "refund", "note": "", "order_number": None}
            result.append(_row(policy, resource, item, Decimal(case["amount"]), "for_party", "رد مبلغ طلب"))
    result.sort(key=lambda item: (item["business_date"], item["recorded_at"], item["id"]), reverse=True)
    return result


def paginate(items, page, page_size, focus_id=None):
    if focus_id:
        match = next((index for index, row in enumerate(items) if row["id"] == focus_id), None)
        if match is not None:
            page = match // page_size + 1
    total_pages = max(1, ceil(len(items) / page_size))
    page = min(page, total_pages)
    return {"items": items[(page - 1) * page_size:page * page_size], "page": page,
            "page_size": page_size, "total": len(items), "total_pages": total_pages}


def install_workspace_routes(router, db, current_user, scope):
    from operational_balance_access import load_access

    async def load(user):
        actor, owner, source = await scope(db, user, "workspace")
        policy = await load_access(db, actor, owner)
        if not policy.owner_override and not policy.enabled:
            fail("operational_workspace_access_required", "اطلب من المالك تحديد صلاحياتك في التطبيق", 403)
        return actor, owner, source, policy

    async def checked_read(user, callback):
        actor, owner, source, policy = await load(user)
        state = await read(db, owner)
        resources, computed = await resource_catalog(db, owner, policy, state)
        result = await prepare_in_thread(callback, actor, owner, policy, state, resources, computed)
        current_actor, current_owner, _, current = await load(user)
        if (actor["id"], owner, policy.version, policy.owner_override) != (current_actor["id"], current_owner, current.version, current.owner_override):
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
        return result

    @router.get("/workspace/context")
    async def context(request: Request, user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            from operational_balance_access import OPERATION_LABELS
            return {"actor": {**_actor_snapshot(actor), "is_owner": policy.owner_override},
                    "session_scope": digest([owner, actor["id"]]), "permission_version": policy.version,
                    "status": state["status"], "resources": resources,
                    "capabilities": {"manage_access": policy.owner_override,
                                     "create_entries": state["status"] == "active" and any(row["operations"] for row in resources)},
                    "operations": [{"key": key, "label": label} for key, label in OPERATION_LABELS.items() if policy.can_operation(key)]}
        return await run_visible_report_read(request, lambda: checked_read(user, project))

    @router.get("/workspace/parties")
    async def parties(request: Request, kind: str | None = None, q: str = Query(default="", max_length=160), user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            return {"items": [r for r in resources if (not kind or r["kind"] == kind) and (not q or q.casefold() in r["name"].casefold())]}
        return await run_visible_report_read(request, lambda: checked_read(user, project))

    @router.get("/workspace/ledger/{kind}/{identity}")
    async def ledger(request: Request, kind: str, identity: str, page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=5, ge=1, le=5), focus_id: str | None = Query(default=None, max_length=250), user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            resource = next((r for r in resources if r["kind"] == kind and r["id"] == identity), None)
            if not resource or resource["access"]["movements"] == "none":
                fail("operational_workspace_not_found", "الكشف غير متاح", 404)
            return {"resource": resource, **paginate(ledger_items(state, computed, resource, policy), page, page_size, focus_id)}
        return await run_visible_report_read(request, lambda: checked_read(user, project))

    @router.get("/workspace/movements")
    async def movements(request: Request, page: int = Query(default=1, ge=1), page_size: int = Query(default=5, ge=1, le=5), user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            items, seen = [], set()
            for resource in resources:
                if resource["access"]["movements"] == "none":
                    continue
                for row in ledger_items(state, computed, resource, policy):
                    if row["event_id"] in seen:
                        continue
                    seen.add(row["event_id"])
                    items.append(row)
            items.sort(key=lambda row: (row["business_date"], row["recorded_at"], row["id"]), reverse=True)
            return paginate(items, page, page_size)
        return await run_visible_report_read(request, lambda: checked_read(user, project))

    @router.post("/workspace/entries")
    async def entry(payload: WorkspaceEntry, user=Depends(current_user)):
        actor, owner, source, _ = await load(user)
        return await save_entry(db, owner, actor, payload.model_dump(), source)

