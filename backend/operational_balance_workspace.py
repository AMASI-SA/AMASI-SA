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

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from operational_balance_service import (
    KINDS, active_gate, create_movement, entity, fmt, money, outstanding, report,
)
from operational_balance_store import (
    audit, claim_movement, digest, fail, mutate, now, read, remember, replay,
)
from operational_financial_activity_runtime import checked_rows, preparation_checkpoint, prepare_in_thread
from operational_visible_report_read import run_visible_report_read
from operational_workspace_projection import (
    automatic_source, balance_note, compact_rows, history as automatic_history, projected_balance_status, row_order,
    statement as period_statement, with_running_balances,
)


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
    "operating_expense": ["expense.pay"], "owner_withdrawal": ["owner.withdraw", "owner.return"],
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


class AdQuoteRequest(BaseModel):
    party_id: str = Field(min_length=1, max_length=200)
    currency: Literal["SAR", "USD"]
    amount: str = Field(min_length=1, max_length=30)


class AdBankFunding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    bank_amount: str = Field(min_length=1, max_length=30)
    bank_fee_amount: str = Field(min_length=1, max_length=30)
    kind: Literal["payment", "wallet_funding"]


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

    bank_arrival_confirmed: Literal[True] | None = None
    actual_fee_amount: str | None = Field(default=None, max_length=30)
    fee_reference: str | None = Field(default=None, min_length=1, max_length=160)

    ad_bank_funding: AdBankFunding | None = None

    def command(self):
        # Omit newly introduced fields from pre-existing pending-command hashes.
        exclude = {key for key in ("bank_arrival_confirmed", "actual_fee_amount", "fee_reference", "ad_bank_funding")
                   if getattr(self, key) is None}
        return self.model_dump(exclude=exclude)

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
    if payload.get("ad_bank_funding") and (
            (kind, side, reason, payload.get("source_type")) != ("ad_account", "for_us", "payment", "bank")
            or any(payload.get(key) is not None for key in ("actual_fee_amount", "fee_reference", "bank_arrival_confirmed"))):
        fail("operational_ad_fx_invalid", "اختر حسابًا إعلانيًا بالدولار وبنكًا بالريال", 422)
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
            ("owner_withdrawal", "collection"): ("owner.return", "for_party"),
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

    async def validate_and_apply(state):
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
            if any(key.startswith("salary:" + target_id + ":")
                   for key in state.get("engine", {}).get("salary_conflicts", {})):
                fail("operational_salary_adjustment_required",
                     "تغير مصدر استحقاق محفوظ؛ يلزم حل فرق الراتب بتسوية موثقة قبل تسجيل حركة جديدة")
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

    async def apply(state):
        try:
            return await validate_and_apply(state)
        except HTTPException as exc:
            if not isinstance(exc.detail, dict) or exc.detail.get("code") not in {
                    "operational_workspace_salary_limit", "operational_salary_adjustment_required",
                    "operational_future_business_date"}:
                raise
            rejection = {"_operational_rejected": True, "status": exc.status_code,
                         "detail": {**exc.detail, "not_applied": True}}
            remember(state, action, payload, rejection)
            return rejection

    result = await mutate(db, owner, apply)
    if result.get("_operational_rejected"):
        raise HTTPException(result["status"], detail=result["detail"])
    return result


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
        body.update({key: payload[key] for key in ("bank_arrival_confirmed", "actual_fee_amount", "fee_reference")
                     if key in payload})
        if funding := payload.get("ad_bank_funding"):
            body.update(currency="USD", bank_currency="SAR", **funding)
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


async def resource_catalog(db, owner, policy, state, *, ad_bank_fx=False):
    from operational_balance_service import prepaid_shipping_cash
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
            # Older clients only understand same-currency entries.
            ad_funding = {}
            if ad_bank_fx and kind == "ad_account" and currency == "USD" and "ads.fund" in operations:
                basis = state.get("engine", {}).get("ad_balance_sar_basis", {}).get(row["id"]) or {}
                ad_funding = {"ad_bank_funding": {
                    "mode": row.get("funding_mode"), "wallet_first": row.get("funding_policy") == "wallet_first",
                    "terms": {key: basis[key] for key in (
                        "exchange_rate_to_sar", "bank_commission_pct", "apply_bank_commission",
                        "bank_commission_vat_pct") if key in basis} or None,
                }}
            elif currency != "SAR":
                operations = []
            items.append({"kind": kind, "id": row["id"], "name": row.get("name") or row["id"],
                          "currency": currency, "access": access, "operations": operations, **ad_funding,
                          **({"prepaid_shipping_cash": True} if kind == "cash" and prepaid_shipping_cash(state, row["id"]) else {}),
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
    stamp = item.get("occurred_at") or item.get("created_at") or item.get("approved_at") or item.get("recorded_at") or item.get("delivered_at") or ""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Riyadh")).date().isoformat()
    except (ValueError, TypeError):
        return ""


def _can_row(policy, resource, item):
    mode = resource["access"]["movements"]
    return mode == "all" or mode == "own" and item.get("actor_id") == policy.actor["id"]


def _original_note(item):
    note = item.get("note")
    if not isinstance(note, str) or not note.strip():
        return None
    if item.get("actor_id") in {"operational_order_worker", "operational_worker"}:
        return None
    # Older workspace writes stored this system label when no note was entered.
    # Do not manufacture a fifth report column from that fallback description.
    if item.get("workspace_operation") and note in {*LABELS.values(), "تسليم مبلغ للجهة"}:
        return None
    return note


def _row(policy, resource, item, amount, side, description):
    related = item.get("workspace_resources") or [{"kind": item.get("party_type"), "id": item.get("party_id")}]
    if item.get("bank_id"):
        related = [*related, {"kind": item.get("bank_kind"), "id": item["bank_id"]}]
    complete = all(r.get("kind") and r.get("id") and policy.can_reference(r["kind"], r["id"]) for r in related)
    # A free-text description can disclose an otherwise hidden counterpart.
    # Use structured labels whenever a connected resource is no longer allowed.
    note = _original_note(item) if complete else None
    result = {"id": item["id"], "event_id": item.get("workspace_group") or item["id"],
              "description": description, "business_date": _business_day(item),
              "recorded_at": item.get("occurred_at") or item.get("created_at") or item.get("approved_at") or item.get("recorded_at") or item.get("delivered_at") or "",
              "recorded_by": _recorded_by(item), "amount": fmt(amount), "side": side,
              "currency": resource["currency"], "reason": item.get("workspace_reason") or item.get("kind"),
              "order_number": item.get("order_number") if complete else None,
              "party_type": resource["kind"], "party_id": resource["id"], "party_name": resource["name"]}
    if note is not None:
        result["note"] = note
    if complete and item.get("bank_name"):
        result["source_label"] = item["bank_name"]
    if complete and item.get("reference"):
        result["reference"] = item["reference"]
    if complete:
        customer = item.get("customer_name_snapshot") or item.get("customer_name")
        if isinstance(customer, dict):
            customer = customer.get("name")
        if isinstance(customer, str) and customer.strip():
            result["customer_name"] = customer.strip()
        if ((item.get("party_type"), item.get("party_id")) != (resource["kind"], resource["id"])
                and isinstance(item.get("name"), str) and item["name"].strip()):
            result["counterparty_name"] = item["name"].strip()
            if (resource["kind"] == "cash" and item.get("automatic_prepaid_shipping") is True
                    and item.get("party_type") == "courier" and item.get("direction") == "outgoing"
                    and item.get("kind") == "payment" and result.get("order_number")):
                result["prepaid_shipping"] = True
    return result


def _index_ledger_sources(state, computed, resources):
    """Group this request's rows once; ledger projection retains every access check."""
    sources = {"openings": (), "obligations": (), "movements": (), "customer_returns": ()}
    indexed = {(row["kind"], row["id"], row["currency"]): {name: [] for name in sources}
               for row in checked_rows(resources) if row["access"]["movements"] != "none"}
    if not indexed:
        return indexed

    def append(name, key, item):
        # A malformed unrelated identity could not equal a catalog resource in
        # the original scan. Do not turn it into an unhashable-key failure.
        if all(isinstance(value, str) for value in key):
            selected = indexed.get(key)
            if selected is not None:
                selected[name].append(item)

    for name, rows in (("openings", state.get("openings", {}).values()),
                       ("obligations", computed["obligations"])):
        for item in checked_rows(rows):
            append(name, (item.get("party_type"), item.get("party_id"), item.get("currency")), item)
    for item in checked_rows(state.get("movements", [])):
        primary = (item["party_type"], item["party_id"], item["currency"])
        cash = (item.get("bank_kind"), item.get("bank_id"), item.get("bank_currency") or item["currency"])
        append("movements", primary, item)
        if cash != primary:
            append("movements", cash, item)
    for item in checked_rows(state.get("customer_returns", [])):
        append("customer_returns", (item.get("refund_source_type"), item.get("refund_source_id"), item.get("currency")), item)
    preparation_checkpoint()
    return indexed


def ledger_items(state, computed, resource, policy, *, sources=None):
    preparation_checkpoint()
    kind, identity, currency = resource["kind"], resource["id"], resource["currency"]
    if sources is None:
        sources = {"openings": state.get("openings", {}).values(), "obligations": computed["obligations"],
                   "movements": state.get("movements", []), "customer_returns": state.get("customer_returns", [])}
    result = []
    for item in checked_rows(sources["openings"]):
        if resource["access"]["movements"] != "all" or not resource["access"].get("balance", False):
            continue  # Neither own activity nor a hidden balance exposes the opening.
        if (item.get("party_type"), item.get("party_id"), item.get("currency")) != (kind, identity, currency) or not _can_row(policy, resource, item):
            continue
        amount = Decimal(item["amount"])
        if amount:
            opening = {**item, "kind": "opening"}
            if state.get("started_at"):
                opening["business_date"] = _business_day({"occurred_at": state["started_at"]})
            result.append(_row(policy, resource, opening, amount, item["direction"], "رصيد افتتاحي"))
    for item in checked_rows(sources["obligations"]):
        if (item.get("kind") in {"baseline", "external_advance", "advertising_balance"}
                or item.get("virtual") or item.get("derived_credit")):
            # A derived credit is an allocation identity for the excess already
            # present in the original claim and cash. It is not another event.
            continue
        if (item.get("party_type"), item.get("party_id"), item.get("currency")) != (kind, identity, currency) or not _can_row(policy, resource, item):
            continue
        amount = Decimal(item.get("confirmed", "0"))
        if amount > 0:
            family, group = automatic_source(state, item)
            source_item = item
            if family == "delivery":
                facts = state.get("engine", {}).get("facts", {})
                event = facts.get("delivery_event:" + group.removeprefix("shipping:"), {})
                frozen = facts[group]
                source_item = {**item, "delivered_at": frozen.get("delivered_at"),
                               "recorded_at": item.get("recorded_at") or frozen.get("recorded_at")}
                if (event.get("party_type"), event.get("party_id")) == (kind, identity):
                    source_item["order_number"] = item.get("order_number") or event.get("order_number")
            row = _row(policy, resource, source_item, amount,
                       "for_party" if item["direction"] == "payable" else "for_us", "مستحق من المصدر")
            if family:
                row.update(automatic_history=family, automatic_group=group)
                row["description"] = "راتب تلقائي" if family == "salary" else "أجرة توصيل"
            result.append(row)
    for item in checked_rows(sources["movements"]):
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
        fee = Decimal(item.get("actual_fee_amount", "0"))
        if (primary and (kind == "provider" and item["kind"] == "settlement"
                         or kind == "pos_receivable" and item["kind"] in {"collection", "settlement"}) and direction == "incoming"
                and fee > 0 and sum((Decimal(a["amount"]) for a in item.get("allocations", [])), Decimal(0)) == amount + fee):
            # Canonical settlement allocations clear cash plus the evidenced fee
            # from the provider claim. The bank received only cash above.
            fee_item = {**item, "id": item["id"] + ":actual_fee", "kind": "settlement_fee",
                        "workspace_reason": "settlement_fee"}
            fee_row = _row(policy, resource, fee_item, fee, "for_party", "رسوم تسوية فعلية")
            fee_row.update(event_id=fee_item["id"], related_event_id=item.get("workspace_group") or item["id"])
            result.append(fee_row)
    if kind in {"bank", "provider"}:
        for case in checked_rows(sources["customer_returns"]):
            if (case.get("refund_source_type"), case.get("refund_source_id"), case.get("currency")) != (kind, identity, currency):
                continue
            if case.get("status") == "provider_matched":
                if kind != "provider":
                    continue
                from operational_workspace_refunds import validate_provider_match
                validate_provider_match(state, case)
                # Matching acknowledges an existing provider effect. It must
                # neither post another movement nor invent an execution date.
                item = {**case, "id": "customer_return:" + case["id"],
                        "party_type": kind, "party_id": identity,
                        "actor_id": case.get("matched_by"),
                        "recorded_by": case.get("matched_by_snapshot"),
                        "business_date": None, "occurred_at": case.get("matched_at"),
                        "kind": "refund", "note": "", "order_number": None}
                if _can_row(policy, resource, item):
                    row = _row(policy, resource, item, Decimal(case["amount"]), "informational", "مطابقة استرداد موثق")
                    row["informational"] = True
                    row["business_date"] = _business_day({"occurred_at": case["refunded_at"]}) if case.get("refunded_at") else ""
                    result.append(row)
                continue
            if case.get("status") not in {"refunded", "salla_refund_confirmed"}:
                continue
            if case["status"] == "salla_refund_confirmed":
                from operational_provider_statement_reconciliation import fact_backed_employee_refund
                if fact_backed_employee_refund(state, case):
                    continue
            elif kind != "bank":
                continue
            execution_stamp = case.get("refunded_at") or case.get("updated_at") or case.get("created_at")
            confirmer = case.get("confirmed_by")
            item = {**case, "id": "customer_return:" + case["id"], "party_type": kind, "party_id": identity,
                    "actor_id": confirmer or case.get("actor_id"),
                    "recorded_by": case.get("confirmed_by_snapshot") if confirmer else case.get("recorded_by"),
                    "business_date": None, "occurred_at": execution_stamp,
                    "kind": "refund", "note": "", "order_number": None}
            if not _can_row(policy, resource, item):
                continue
            result.append(_row(policy, resource, item, Decimal(case["amount"]), "for_party", "رد مبلغ طلب"))
    if kind == "store_driver":
        from operational_driver_review_report import validated_review_facts
        facts, _ = validated_review_facts(state)
        for fact in checked_rows(facts):
            if fact["driver_id"] != identity or fact["currency"] != currency:
                continue
            target_kind = "bank" if fact["destination_type"] == "bank" else "pos_receivable"
            item = {"id": "driver-review-info:" + fact["id"], "kind": "driver_payment_review",
                    "party_type": kind, "party_id": identity, "actor_id": fact.get("approved_by"),
                    "approved_at": fact.get("approved_at"), "order_number": fact.get("order_number"),
                    "reference": fact.get("transaction_reference"), "bank_name": fact.get("destination_name"),
                    "workspace_resources": [{"kind": kind, "id": identity},
                                            {"kind": target_kind, "id": fact["destination_id"]}]}
            if _can_row(policy, resource, item):
                row = _row(policy, resource, item, Decimal(fact["amount"]), "informational", "إثبات تحصيل للمؤسسة")
                row["informational"] = True
                result.append(row)
    preparation_checkpoint()
    result, _ = with_running_balances(resource, result)
    preparation_checkpoint()
    return result


def expected_collections(state, computed, resource, policy):
    """Saved, unconfirmed COD forecasts are separate from the confirmed ledger."""
    if resource["kind"] not in {"store_driver", "courier"} or resource["access"]["movements"] != "all":
        return {"items": [], "total": 0, "amount": "0.00"}
    rows = []
    for item in checked_rows(computed["obligations"]):
        if ((item.get("party_type"), item.get("party_id"), item.get("currency")) !=
                (resource["kind"], resource["id"], resource["currency"]) or item.get("kind") != "cod"):
            continue
        if Decimal(item.get("confirmed", "0")) == 0 and Decimal(item.get("expected", "0")) > 0:
            row = _row(policy, resource, item, Decimal(item["expected"]), "for_us", "تحصيل متوقع")
            row.update(expected=True, balance_after=None)
            rows.append(row)
    rows.sort(key=row_order, reverse=True)
    return {"items": rows[:5], "total": len(rows),
            "amount": fmt(sum((Decimal(row["amount"]) for row in rows), Decimal(0)))}


def paginate(items, page, page_size, focus_id=None):
    preparation_checkpoint()
    if focus_id:
        match = next((index for index, row in enumerate(checked_rows(items)) if row["id"] == focus_id), None)
        if match is not None:
            page = match // page_size + 1
    total_pages = max(1, ceil(len(items) / page_size))
    page = min(page, total_pages)
    return {"items": items[(page - 1) * page_size:page * page_size], "page": page,
            "page_size": page_size, "total": len(items), "total_pages": total_pages}


def install_workspace_routes(router, db, current_user, scope):
    from operational_balance_access import load_access
    from operational_balance_reviews import install_workspace_review_routes

    install_workspace_review_routes(router, db, current_user, scope)

    async def load(user):
        actor, owner, source = await scope(db, user, "workspace")
        policy = await load_access(db, actor, owner)
        if not policy.owner_override and not policy.enabled:
            fail("operational_workspace_access_required", "اطلب من المالك تحديد صلاحياتك في التطبيق", 403)
        return actor, owner, source, policy

    async def checked_read(user, callback, *, ad_bank_fx=False):
        from operational_financial_position_state import read_financial_position_state
        actor, owner, source, policy = await load(user)
        # Workspace reports need current financial proofs, not historical audit
        # pages or idempotency receipts. Writers keep the complete store reader.
        state = await read_financial_position_state(db, owner)
        resources, computed = await resource_catalog(db, owner, policy, state, ad_bank_fx=ad_bank_fx)
        result = await prepare_in_thread(callback, actor, owner, policy, state, resources, computed)
        current_actor, current_owner, _, current = await load(user)
        if (actor["id"], owner, policy.version, policy.owner_override) != (current_actor["id"], current_owner, current.version, current.owner_override):
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
        return result

    @router.get("/workspace/context")
    async def context(request: Request, ad_bank_fx: bool = False, user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            from operational_balance_access import OPERATION_LABELS
            return {"actor": {**_actor_snapshot(actor), "is_owner": policy.owner_override},
                    "session_scope": digest([owner, actor["id"]]), "permission_version": policy.version,
                    "status": state["status"], "resources": resources,
                    "capabilities": {"manage_access": policy.owner_override,
                                     "create_entries": state["status"] == "active" and any(row["operations"] for row in resources)},
                    "operations": [{"key": key, "label": label} for key, label in OPERATION_LABELS.items() if policy.can_operation(key)]}
        return await run_visible_report_read(request, lambda: checked_read(user, project, ad_bank_fx=ad_bank_fx))

    @router.post("/workspace/ad-funding-quote")
    async def ad_quote(payload: AdQuoteRequest, user=Depends(current_user)):
        from operational_ad_balance_sar import funding_quote

        from operational_balance_sources import entities
        from operational_balance_store import STATES
        actor, owner, _, policy = await load(user)
        policy.require_operation("ads.fund")
        policy.require_use("ad_account", payload.party_id)
        # Prices and bindings are top-level live terms, independent of ledger history.
        state = await db[STATES].find_one(
            {"_id": owner, "owner_id": owner}, {"owner_id": 1, "ad_funding": 1, "engine.ad_balance_sar_basis": 1})
        accounts = await entities(db, owner, "ad_account", operational_state=state)
        if not any(row["id"] == payload.party_id and row.get("currency") == "USD"
                   and row.get("ready") is not False and row.get("settings_complete") is not False for row in accounts):
            fail("operational_ad_fx_forbidden", "الحساب الإعلاني غير متاح", 403)
        current_actor, current_owner, _, current_policy = await load(user)
        if (current_actor["id"], current_owner) != (actor["id"], owner):
            fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 409)
        current_policy.require_operation("ads.fund")
        current_policy.require_use("ad_account", payload.party_id)
        basis = (state or {}).get("engine", {}).get("ad_balance_sar_basis", {}).get(payload.party_id)
        try:
            return funding_quote(money(payload.amount), payload.currency, basis)
        except ValueError:
            fail("operational_ad_fx_receipt_required", "تعذر حساب المقابل بإعدادات الحساب. اختر إدخال مبالغ الإيصال.", 422)

    @router.get("/workspace/parties")
    async def parties(request: Request, kind: str | None = None, q: str = Query(default="", max_length=160), user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            return {"items": [r for r in resources if (not kind or r["kind"] == kind) and (not q or q.casefold() in r["name"].casefold())]}
        return await run_visible_report_read(request, lambda: checked_read(user, project))

    @router.get("/workspace/ledger/{kind}/{identity}")
    async def ledger(request: Request, kind: str, identity: str, page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=5, ge=1, le=5), focus_id: str | None = Query(default=None, max_length=250), ad_bank_fx: bool = False, user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            resource = next((r for r in resources if r["kind"] == kind and r["id"] == identity), None)
            if not resource or resource["access"]["movements"] == "none":
                fail("operational_workspace_not_found", "الكشف غير متاح", 404)
            rows = ledger_items(state, computed, resource, policy)
            summary = {}
            if kind == "pos_receivable" and resource["access"]["balance"] and resource["access"]["movements"] == "all":
                from operational_driver_review_report import validated_review_facts
                facts, _ = validated_review_facts(state)
                approved = sum((Decimal(f["amount"]) for f in facts if f["destination_type"] == kind
                                and f["destination_id"] == identity), Decimal(0))
                transfers = [m for m in state["movements"] if m.get("party_type") == kind and m.get("party_id") == identity
                             and m.get("kind") in {"collection", "settlement"} and m.get("direction") == "incoming"
                             and m.get("bank_kind") == "bank"]
                summary = {"pos_summary": {"approved": fmt(approved),
                           "bank_received": fmt(sum((Decimal(m["amount"]) for m in transfers), Decimal(0))),
                           "fees": fmt(sum((Decimal(m.get("actual_fee_amount", "0")) for m in transfers), Decimal(0)))}}
            return {"resource": resource, **summary, **paginate(compact_rows(rows), page, page_size, focus_id),
                    "balance_status": projected_balance_status(resource, rows),
                    "balance_note": balance_note(projected_balance_status(resource, rows)),
                    "expected_collections": expected_collections(state, computed, resource, policy)}
        return await run_visible_report_read(request, lambda: checked_read(user, project, ad_bank_fx=ad_bank_fx))

    @router.get("/workspace/movements")
    async def movements(request: Request, page: int = Query(default=1, ge=1), page_size: int = Query(default=5, ge=1, le=5),
                        kind: str | None = Query(default=None, max_length=50), id: str | None = Query(default=None, max_length=200),
                        user=Depends(current_user)):
        if (kind is None) != (id is None):
            fail("operational_account_filter_invalid", "اختر نوع الحساب والحساب معًا", 422)
        def project(actor, owner, policy, state, resources, computed):
            items, seen = [], set()
            selected = [r for r in resources if kind is None or (r["kind"], r["id"]) == (kind, id)]
            if kind is not None and not any(r["access"]["movements"] != "none" for r in selected):
                fail("operational_workspace_not_found", "الكشف غير متاح", 404)
            indexed = _index_ledger_sources(state, computed, selected)
            for resource in checked_rows(selected):
                if resource["access"]["movements"] == "none":
                    continue
                sources = indexed[(resource["kind"], resource["id"], resource["currency"])]
                for row in checked_rows(compact_rows(ledger_items(state, computed, resource, policy, sources=sources))):
                    if row["event_id"] in seen:
                        continue
                    seen.add(row["event_id"])
                    items.append(row)
            items.sort(key=row_order, reverse=True)
            return paginate(items, page, page_size)
        return await run_visible_report_read(request, lambda: checked_read(user, project))

    @router.get("/workspace/statements/{kind}/{identity}")
    async def statement(request: Request, kind: str, identity: str,
                        date_from: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
                        date_to: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
                        purpose: Literal["view", "print"] = "view", user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            resource = next((r for r in resources if (r["kind"], r["id"]) == (kind, identity)), None)
            if not resource or resource["access"]["movements"] == "none":
                fail("operational_workspace_not_found", "الكشف غير متاح", 404)
            return period_statement(resource, ledger_items(state, computed, resource, policy),
                date_from=date_from, date_to=date_to, actor=_actor_snapshot(actor), generated_at=now(),
                purpose=purpose, started_at=state.get("started_at"))
        return await run_visible_report_read(request, lambda: checked_read(user, project))

    @router.get("/workspace/history/{kind}/{identity}")
    async def history(request: Request, kind: str, identity: str, page: int = Query(default=1, ge=1),
                      page_size: int = Query(default=5, ge=1, le=5), user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            resource = next((r for r in resources if (r["kind"], r["id"]) == (kind, identity)), None)
            if (not resource or resource["access"]["movements"] == "none"
                    or kind not in {"employee", "store_driver", "courier"}):
                fail("operational_workspace_not_found", "الكشف غير متاح", 404)
            return automatic_history(resource, ledger_items(state, computed, resource, policy),
                                     page=page, page_size=page_size, generated_at=now())
        return await run_visible_report_read(request, lambda: checked_read(user, project))

    @router.post("/workspace/entries")
    async def entry(payload: WorkspaceEntry, user=Depends(current_user)):
        actor, owner, source, _ = await load(user)
        return await save_entry(db, owner, actor, payload.command(), source)

