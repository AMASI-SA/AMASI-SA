"""Small, scoped ledger over the existing operational source of truth.

No second balance store, source polling, native accounting activation or provider
payment is introduced here. Reads project the saved aggregate. Ordinary money
uses create_movement; linked external payments use the same aggregate CAS.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal
from heapq import nlargest
from math import ceil
from time import monotonic
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
import read_path_diagnostics as read_diagnostics

from operational_balance_service import (
    KINDS, active_gate, create_movement, entity, fmt, money, report,
)
from operational_balance_store import (
    audit, claim_movement, digest, fail, mutate, now, read, remember, replay,
)
from operational_financial_activity_runtime import checked_rows, preparation_checkpoint, prepare_in_thread
from operational_party_projection_state import party_projection_state
from operational_financial_position_core import position_balance
from operational_supplier_credit_purchase import CreditInvoice, record_purchase
from operational_visible_report_read import run_visible_report_read
from operational_workspace_ledger_filters import check_period, filter_activity, movement_type
from operational_workspace_courier_summary import courier_summary
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
    invoice: CreditInvoice | None = None

    def command(self):
        # Omit newly introduced fields from pre-existing pending-command hashes.
        exclude = {key for key in ("bank_arrival_confirmed", "actual_fee_amount", "fee_reference", "ad_bank_funding", "invoice")
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
    if reason == "credit_purchase" or payload.get("invoice") is not None:
        if (kind != "supplier" or side != "for_party" or reason != "credit_purchase" or not payload.get("invoice")
                or any(payload.get(key) is not None for key in ("source_type", "source_id", "employee_id", "owner_id",
                       "expense_id", "ad_bank_funding", "bank_arrival_confirmed", "actual_fee_amount", "fee_reference"))):
            fail("supplier_purchase_invalid", "اختر موردًا وسجّل بنود فاتورة الشراء الآجل", 422)
        return "supplier.accrual", [(kind, identity)]
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
        if target_kind == "employee":
            if any(key.startswith("salary:" + target_id + ":")
                   for key in state.get("engine", {}).get("salary_conflicts", {})):
                fail("operational_salary_adjustment_required",
                     "تغير مصدر استحقاق محفوظ؛ يلزم حل فرق الراتب بتسوية موثقة قبل تسجيل حركة جديدة")
            employee_state = await prepare_in_thread(party_projection_state, state, target_kind, target_id)
            balances = await prepare_in_thread(report, employee_state)
            employee = next((r for r in balances["parties"] if r["party_type"] == target_kind
                             and r["party_id"] == target_id and r["currency"] == "SAR"), {})
            available = Decimal(employee.get("outstanding_payable", "0")) - Decimal(employee.get("outstanding_receivable", "0"))
            if amount > max(available, Decimal(0)):
                fail("operational_workspace_salary_limit", "المبلغ غير قابل للتسوية لهذا الموظف", 409)
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
                       "direction": "outgoing", "allocations": []}
        if target_kind == "employee":
            counterpart["settlement_scope"] = "account"
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

    result = await mutate(db, owner, apply, threaded_preparation=True)
    if result.get("_operational_rejected"):
        raise HTTPException(result["status"], detail=result["detail"])
    return result


def movement_body(payload, operation, resources):
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
    if payload["party_type"] == "provider" and payload["party_id"] == "paypal":
        body["note"] = payload.get("note") or "تحويل باي بال إلى البنك"
    if funding := payload.get("ad_bank_funding"):
        body.update(currency="USD", bank_currency="SAR", **funding)
    return body


async def save_entry(db, owner, actor, payload, source):
    from operational_balance_access import guard_access
    operation, resources = entry_intent(payload)
    if payload["expected_session_scope"] != digest([owner, actor["id"]]):
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ أعد فتح الصفحة", 409)
    required = [operation, "custody.spend"] if payload.get("source_type") == "employee_custody" else operation
    async with guard_access(db, actor, owner, required, resources):
        if payload.get("reason") == "credit_purchase":
            return await record_purchase(db, owner, actor, payload, source)
        if payload["party_type"] == "external_person" and payload["side"] == "for_party" and payload.get("reason") != "receipt":
            return await _linked_external_entry(db, owner, actor, payload, operation, resources, source)
        body = movement_body(payload, operation, resources)
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


def payment_source_kinds(policy):
    # A use-only grant permits selecting a source without disclosing its amount.
    payment_targets = {"supplier": "supplier.payment", "external_person": "external.pay",
        "employee": "employee.payment", "courier": "courier.pay", "store_driver": "courier.pay",
        "operating_expense": "expense.pay", "owner_withdrawal": "owner.withdraw",
        "employee_custody": "custody.fund", "ad_account": "ads.fund"}
    usable_kinds = {r["kind"] for r in policy.resources if r.get("use")}
    payee_kinds = {kind for kind, operation in payment_targets.items()
                   if (policy.owner_override or kind in usable_kinds) and policy.can_operation(operation)}
    can_spend_custody = policy.can_operation("custody.spend") and bool(
        payee_kinds.intersection({"supplier", "external_person", "operating_expense"}))
    return ({"bank", "cash"} if payee_kinds else set()) | ({"employee_custody"} if can_spend_custody else set())


async def resource_catalog(db, owner, policy, state, *, ad_bank_fx=False, reviewed_counts=None, selected_resource=None, directory_only=False, selected_kind=None, provider_positions=False, provider_orders=False):
    from operational_ad_balance_sar import workspace_amounts
    from operational_workspace_order_issues import issue_badge, navigation_counts
    from operational_balance_service import prepaid_shipping_cash
    from operational_balance_sources import AD_SOURCES, entities
    paypal_scope = (selected_kind in (None, "provider") and (not selected_resource or selected_resource == ("provider", "paypal"))
                    and policy.resource("provider", "paypal")["balance"])
    provider_access = policy.resource(*selected_resource) if selected_resource and selected_resource[0] == "provider" else {}
    order_access = provider_access.get("balance") and provider_access.get("report") and provider_access["movements"] == "all"
    if provider_orders and not order_access:
        fail("operational_workspace_not_found", "طلبات المنصة غير متاحة", 404)
    can_price_cards = provider_positions and (policy.owner_override or any(
        r["kind"] == "provider" and r.get("balance") for r in policy.resources))
    if directory_only:
        computed = {"parties": [], "obligations": [], "details": {}, "issues": []}
    elif selected_resource and selected_resource[0] in ACCOUNT_KINDS:
        from operational_party_projection_state import cash_account_projection
        computed = await prepare_in_thread(read_diagnostics.thread_call("report", cash_account_projection), state, *selected_resource)
    elif (selected_resource and selected_resource[0] != "provider") or selected_kind == "ad_account":
        kind, identity = selected_resource or ("ad_account", None)
        selected_state = await prepare_in_thread(party_projection_state, state, kind, identity)
        computed = await prepare_in_thread(read_diagnostics.thread_call("report", report), selected_state)
        if kind in {"store_driver", "courier"}:
            # The inbox can contain unresolved issues elsewhere in the saved accounting scope.
            computed["issues"] = computed["issues"] or state.get("engine", {}).get("issues", [])
    elif can_price_cards or provider_orders:
        computed = await prepare_in_thread(read_diagnostics.thread_call("report", report), state, provider_balances_sar=True,
            workspace_provider=selected_resource[1] if provider_orders else None)
    else:
        computed = (await prepare_in_thread(read_diagnostics.thread_call("report", report), state, paypal_sar=True)
                    if paypal_scope else await prepare_in_thread(read_diagnostics.thread_call("report", report), state))
    include_bank_issues = (not directory_only and selected_kind in (None, "bank")
                           and (not selected_resource or selected_resource[0] == "bank"))
    if include_bank_issues:
        review_counts = await prepare_in_thread(read_diagnostics.thread_call("navigation", navigation_counts),
                                               computed, state.get("engine", {}).get("issues", []))
    else:
        with read_diagnostics.phase("navigation", cpu=True):
            review_counts = navigation_counts(computed)
    balances = {(r["party_type"], r["party_id"], r["currency"]): r for r in computed["parties"]}
    funding_kinds = payment_source_kinds(policy)
    items = []
    # A detail needs one catalog identity. Counterpart visibility still comes from the complete policy.
    for kind in RESOURCE_KINDS:
        if (selected_resource and kind != selected_resource[0]) or (selected_kind and kind != selected_kind):
            continue
        if not policy.owner_override and not any(r["kind"] == kind for r in policy.resources):
            continue
        with read_diagnostics.phase("catalog." + kind):
            catalog = await entities(db, owner, kind, operational_state=state)
        for row in catalog:
            if selected_resource and row["id"] != selected_resource[1]:
                continue
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
            financials = {}
            if not directory_only and access["use"] and currency == "SAR" and kind in funding_kinds:
                funding = balances.get((kind, row["id"], currency), {})
                field = "custody_remaining" if kind == "employee_custody" else "actual"
                financials["can_fund_payment"] = Decimal(funding.get(field, funding.get("actual", "0"))) > 0
            if not directory_only and (provider_positions or provider_orders) and kind == "provider" and access["balance"]:
                from operational_workspace_provider_orders import provider_position
                financials["provider_position"] = provider_position(computed, row["id"],
                    show_orders=access.get("report") and access["movements"] == "all")
            if not directory_only and kind == "ad_account" and access["balance"]:
                native = balances.get((kind, row["id"], currency))
                basis = state.get("engine", {}).get("ad_balance_sar_basis", {}).get(row["id"]) or {}
                financials = {
                    "ad_wallet_balance": native.get("ad_wallet_balance") if native else None,
                    "ad_amounts": workspace_amounts(native, basis, (kind, row["id"], "SAR") in balances),
                }
            balance = _balance(balances.get((kind, row["id"], currency), {}), kind) if access["balance"] and not directory_only else None
            if balance is not None and kind not in {"provider", "ad_account"}:
                financials["position_balance"] = position_balance(balances.get((kind, row["id"], currency)), kind)
            if kind == "provider" and row["id"] == "paypal" and balance is not None:
                from operational_balance_service import paypal_available
                net = paypal_available(computed)
                balance = ({"amount": fmt(abs(net)), "side": "for_us" if net > 0 else "for_party" if net < 0 else "balanced", "visible": True}
                           if net is not None else None)
            items.append({"kind": kind, "id": row["id"], "name": row.get("name") or row["id"],
                          "currency": currency, "access": access, "operations": operations, **ad_funding,
                          **({"platform": row["platform"]} if kind == "ad_account" and row.get("platform") in AD_SOURCES else {}),
                          **financials,
                          **({} if directory_only else issue_badge(kind, row["id"], access, bool(computed.get("as_of")), bool(computed["issues"]), review_counts, reviewed_counts or {})),
                          **({"prepaid_shipping_cash": True} if kind == "cash" and prepaid_shipping_cash(state, row["id"]) else {}),
                          "balance": balance})
        # Match the former temporary iterator's lifetime before the next read.
        del catalog
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
    if item.get("actor_id") in {"operational_order_worker", "operational_worker"}:
        result["system_recorded"] = True
    if direction := movement_type(item):
        result["movement_type"] = direction
    if note is not None:
        result["note"] = note
    if complete and item.get("bank_name"):
        result["source_label"] = item["bank_name"]
        if item.get("name") and item.get("kind") in {"payment", "collection", "settlement", "transfer", "refund"}:
            if item.get("direction") == "outgoing":
                result.update(from_name=item["bank_name"], to_name=item["name"])
            elif item.get("direction") == "incoming":
                result.update(from_name=item["name"], to_name=item["bank_name"])
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
    sources = {"openings": (), "obligations": (), "movements": (), "customer_returns": (), "driver_receipts": ()}
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
    if any(key[0] == "store_driver" for key in indexed):
        from operational_driver_review_report import validated_review_facts
        facts, _ = validated_review_facts(state)
        for fact in checked_rows(facts):
            append("driver_receipts", ("store_driver", fact["driver_id"], fact["currency"]), fact)
    preparation_checkpoint()
    return indexed


def _movement_rows(resource, policy, movements, make_row=_row):
    kind, identity, currency = resource["kind"], resource["id"], resource["currency"]
    result = []
    for item in checked_rows(movements):
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
        result.append(make_row(policy, resource, item, amount, side, description))
        fee = Decimal(item.get("actual_fee_amount", "0"))
        if (primary and (kind == "provider" and item["kind"] == "settlement"
                         or kind == "pos_receivable" and item["kind"] in {"collection", "settlement"}) and direction == "incoming"
                and fee > 0 and (item.get("settlement_scope") == "account"
                    or sum((Decimal(a["amount"]) for a in item.get("allocations", [])), Decimal(0)) == amount + fee)):
            # Canonical settlement allocations clear cash plus the evidenced fee
            # from the provider claim. The bank received only cash above.
            fee_item = {**item, "id": item["id"] + ":actual_fee", "kind": "settlement_fee",
                        "workspace_reason": "settlement_fee"}
            fee_row = make_row(policy, resource, fee_item, fee, "for_party", "رسوم تسوية فعلية")
            fee_row.update(event_id=fee_item["id"], related_event_id=item.get("workspace_group") or item["id"])
            result.append(fee_row)
    return result


def ledger_items(state, computed, resource, policy, *, sources=None, make_row=None):
    index_only = make_row is not None
    make_row = make_row or _row
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
            result.append(make_row(policy, resource, opening, amount, item["direction"], "رصيد افتتاحي"))
    obligations = sources["obligations"]
    if kind == "supplier":
        from operational_workspace_supplier_invoices import invoice_rows
        obligations = invoice_rows(item for item in checked_rows(obligations) if item.get("party_id") == identity and item.get("party_type") == kind)
    for item in checked_rows(obligations):
        if (item.get("kind") in {"baseline", "external_advance", "advertising_balance"}
                or item.get("virtual") or item.get("derived_credit")):
            # A derived credit is an allocation identity for the excess already
            # present in the original claim and cash. It is not another event.
            continue
        if (item.get("party_type"), item.get("party_id"), item.get("currency")) != (kind, identity, currency) or not _can_row(policy, resource, item):
            continue
        amount = Decimal(item.get("confirmed", "0"))
        if amount > 0 or (item.get("kind") == "supplier_invoice" and Decimal(item["invoice_gross"]) > 0):
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
            row = make_row(policy, resource, source_item, amount,
                       "for_party" if item["direction"] == "payable" else "for_us", "مستحق من المصدر")
            if item.get("kind") == "supplier_invoice":
                row.update(invoice_id=item["invoice_id"], invoice_gross=item["invoice_gross"], invoice_returns=item["invoice_returns"],
                           description=item["invoice_label"])
            if item.get("salla_return_shipment_id"):
                row.update(description="أجرة بوليصة رجيع", salla_return_waybill=True,
                           system_recorded=not item.get("return_confirmed_manually"),
                           return_business_at=item.get("return_business_at"))
            if family:
                row.update(automatic_history=family, automatic_group=group)
                row["description"] = "راتب تلقائي" if family == "salary" else "أجرة توصيل"
            result.append(row)
    result.extend(_movement_rows(resource, policy, sources["movements"], make_row))
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
                    row = make_row(policy, resource, item, Decimal(case["amount"]), "informational", "مطابقة استرداد موثق")
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
            result.append(make_row(policy, resource, item, Decimal(case["amount"]), "for_party", "رد مبلغ طلب"))
    if kind == "store_driver":
        if "driver_receipts" in sources:
            facts = sources["driver_receipts"]
        else:
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
                row = make_row(policy, resource, item, Decimal(fact["amount"]), "informational", "إثبات تحصيل للمؤسسة")
                row["informational"] = True
                result.append(row)
    preparation_checkpoint()
    if not index_only:
        result, _ = with_running_balances(resource, result)
    preparation_checkpoint()
    return result


def _recent_postings(state, resources, policy, limit=4):
    """Four recorded events from the same snapshot, without assembling any account ledger."""
    available = {(r["kind"], r["id"], r["currency"]): r for r in resources
                 if r["access"]["movements"] != "none"}
    if not available:
        return []
    # One bounded pass. Historical opening balances and accrued obligations are
    # not posted movements. A linked posting is shown once in this preview.
    latest = {}
    floor = None
    for item in checked_rows(reversed(state.get("movements", []))):
        # Writes append, but a write can be backdated. Do not stop at the first
        # old item. A saved business date alone can cheaply rule a candidate out.
        day = item.get("business_date")
        if floor is not None and isinstance(day, str) and day and day < floor[0]:
            continue
        stamp = item.get("occurred_at") or item.get("created_at") or ""
        order = row_order({"id": item["id"], "business_date": day or _business_day(item), "recorded_at": stamp})
        if floor is not None and order <= floor:
            continue
        keys = ((item["party_type"], item["party_id"], item["currency"]),
                (item.get("bank_kind"), item.get("bank_id"), item.get("bank_currency") or item["currency"]))
        resource = next((available[key] for key in keys
                         if key in available and _can_row(policy, available[key], item)), None)
        if resource is None:
            continue
        event = item.get("workspace_group") or item["id"]
        if event in latest and latest[event][0] >= order:
            continue
        if len(latest) == limit and event not in latest:
            del latest[min(latest, key=lambda k: latest[k][0])]
        latest[event] = (order, resource, item)
        if len(latest) == limit:
            floor = min(entry[0] for entry in latest.values())
    return [_movement_rows(resource, policy, [item])[0]
            for _, resource, item in sorted(latest.values(), key=lambda entry: entry[0], reverse=True)]


def expected_collections(state, computed, resource, policy):
    """Saved, unconfirmed COD forecasts are separate from the confirmed ledger."""
    if resource["kind"] not in {"store_driver", "courier"} or resource["access"]["movements"] != "all":
        return {"items": [], "total": 0, "amount": "0.00"}
    count, total = 0, Decimal(0)
    def candidates():
        nonlocal count, total
        for item in checked_rows(computed["obligations"]):
            if (item.get("superseded") or item.get("kind") != "cod" or
                    (item.get("party_type"), item.get("party_id"), item.get("currency")) !=
                    (resource["kind"], resource["id"], resource["currency"])):
                continue
            amount = Decimal(item.get("expected", "0"))
            if amount > 0 and Decimal(item.get("confirmed", "0")) == 0:
                count += 1
                total += amount
                yield _register_candidate(policy, resource, item, amount, "for_us", "تحصيل متوقع")
    newest = nlargest(5, candidates(), key=row_order)
    projected = _open_register_page(newest, policy, 1, 5)["items"]
    return {"items": [{**row, "expected": True} for row in projected], "total": count, "amount": fmt(total)}


def paginate(items, page, page_size, focus_id=None, *, total=None):
    preparation_checkpoint()
    if focus_id:
        match = next((index for index, row in enumerate(checked_rows(items)) if row["id"] == focus_id), None)
        if match is not None:
            page = match // page_size + 1
    total = len(items) if total is None else total
    total_pages = max(1, ceil(total / page_size))
    page = min(page, total_pages)
    return {"items": items[(page - 1) * page_size:page * page_size], "page": page,
            "page_size": page_size, "total": total, "total_pages": total_pages}


def register_page(resource_rows, page, page_size):
    """Count each visible posting once and keep only the newest requested prefix."""
    seen = set()

    def unique_rows():
        for rows in resource_rows:
            for row in checked_rows(rows):
                identity = row["event_id"]
                if identity not in seen:
                    seen.add(identity)
                    yield row

    newest = nlargest(page * page_size, unique_rows(), key=row_order)
    return paginate(newest, page, page_size, total=len(seen))


def _pos_summary(state, resource):
    kind, identity = resource["kind"], resource["id"]
    if kind == "pos_receivable" and resource["access"]["balance"] and resource["access"]["movements"] == "all":
        from operational_driver_review_report import validated_review_facts
        facts, _ = validated_review_facts(state)
        approved = sum((Decimal(f["amount"]) for f in facts if f["destination_type"] == kind
                        and f["destination_id"] == identity), Decimal(0))
        transfers = [m for m in state["movements"] if m.get("party_type") == kind and m.get("party_id") == identity
                     and m.get("kind") in {"collection", "settlement"} and m.get("direction") == "incoming"
                     and m.get("bank_kind") == "bank"]
        return {"pos_summary": {"approved": fmt(approved),
                   "bank_received": fmt(sum((Decimal(m["amount"]) for m in transfers), Decimal(0))),
                   "fees": fmt(sum((Decimal(m.get("actual_fee_amount", "0")) for m in transfers), Decimal(0)))}}
    return {}


def register_claims(state, paypal_sar):
    from operational_provider_balance_sar import convert_claim, usable_bases
    engine = state.get("engine", {})
    bases = usable_bases(state) if paypal_sar and engine.get("provider_sar_basis") else {}
    claims = []
    for row in checked_rows(engine.get("obligations", {}).values()):
        if row.get("superseded") or row.get("derived_credit") or row.get("virtual"):
            continue
        if row.get("party_type") == "provider" and row["party_id"] == "paypal" and row["currency"] != "SAR":
            basis = bases.get(row["id"])
            if basis and (basis["provider"], basis["currency"]) == ("paypal", row["currency"]):
                row = dict(row)
                convert_claim(row, Decimal(0), basis)
        claims.append(row)
    return {"obligations": claims}


def _register_candidate(policy, resource, item, amount, side, description):
    # Keep display evidence without retaining allocations or the enclosing snapshot.
    evidence_fields = ("id", "workspace_group", "workspace_resources", "party_type", "party_id", "bank_id", "bank_kind",
        "actor_id", "recorded_by", "workspace_operation", "note", "order_number", "kind", "direction", "status",
        "workspace_reason", "driver_review_event_id", "bank_name", "name", "reference", "customer_name_snapshot",
        "customer_name", "automatic_prepaid_shipping")
    evidence = {key: item[key] for key in evidence_fields if key in item}
    return {"_source": (resource, evidence), "id": item["id"], "event_id": item.get("workspace_group") or item["id"],
            "business_date": _business_day(item), "reason": item.get("workspace_reason") or item.get("kind"),
            "recorded_at": item.get("occurred_at") or item.get("created_at") or item.get("approved_at")
                           or item.get("recorded_at") or item.get("delivered_at") or "",
            "amount": fmt(amount), "side": side, "description": description}


def _register_index(state, computed, resources, policy):
    sources = _index_ledger_sources(state, computed, resources)
    unique = {}
    for resource in checked_rows(resources):
        key = resource["kind"], resource["id"], resource["currency"]
        if key in sources:
            rows = ledger_items(state, computed, resource, policy, sources=sources[key], make_row=_register_candidate)
            for row in checked_rows(compact_rows(rows)):
                unique.setdefault(row["event_id"], row)
    return sorted(unique.values(), key=row_order, reverse=True)


def _courier_history(state, resource, policy):
    """Reconcile just the opened courier against the same financial position as its detail page."""
    selected = party_projection_state(state, "courier", resource["id"])
    computed = report(selected)
    current = next((r for r in computed["parties"] if (r["party_type"], r["party_id"], r["currency"]) ==
                    ("courier", resource["id"], resource["currency"])), None)
    resource = {**resource, "balance": _balance(current or {}, "courier"),
                "position_balance": position_balance(current, "courier")}
    sources = {"openings": selected["openings"].values(), "movements": selected["movements"],
               "obligations": computed["obligations"], "customer_returns": ()}
    rows = ledger_items(state, computed, resource, policy, sources=sources, make_row=_register_candidate)
    rows, status = with_running_balances(resource, rows)
    if status != "available":
        return {}
    return {row["id"]: row["balance_after"] for row in compact_rows(rows) if row.get("balance_after") is not None}


def _open_register_page(index, policy, page, page_size, focus_id=None):
    result = paginate(index, page, page_size, focus_id)
    display = []
    for candidate in result["items"]:
        resource, item = candidate["_source"]
        row = _row(policy, resource, item, Decimal(candidate["amount"]), candidate["side"], candidate["description"])
        row.update((k, v) for k, v in candidate.items() if k != "_source")
        display.append({**row, "balance_after": candidate.get("balance_after")})
    return {**result, "items": display, "page_only": True}


def install_workspace_routes(router, db, current_user, scope):
    from operational_balance_access import load_access
    from operational_balance_reviews import install_workspace_review_routes

    from operational_workspace_order_issues import ReviewSnapshots, read_order_issues, source_key

    review_snapshots = ReviewSnapshots()
    register_indexes = OrderedDict()
    install_workspace_review_routes(router, db, current_user, scope)

    async def load(user, *, allow_disabled=False):
        actor, owner, source = await scope(db, user, "workspace")
        policy = await load_access(db, actor, owner)
        if not allow_disabled and not policy.owner_override and not policy.enabled:
            fail("operational_workspace_access_required", "اطلب من المالك تفعيل صلاحيات الأرصدة وتحديد الحسابات المتاحة لك", 403)
        return actor, owner, source, policy

    async def directory_state(db, owner):
        from operational_balance_store import STATES, initial
        fields = dict.fromkeys(("owner_id", "status", "revision", "started_at", "pos_receivable_binding",
                                "ad_funding", "courier_tariffs.payment_mode", "courier_tariffs.cash_account.id", "engine.ad_balance_sar_basis"), 1)
        with read_diagnostics.phase("directory_state_read"):
            return await db[STATES].find_one({"_id": owner, "owner_id": owner}, fields) or initial(owner)

    async def checked_read(user, callback, *, ad_bank_fx=False, selected_resource=None, invoice_headers=False, invoice_detail=None, directory_only=False, balance_only=False, forecast_only=False, provider_positions=False, provider_orders=False, selected_kind=None):
        from operational_financial_position_state import read_financial_position_state
        with read_diagnostics.phase("access_initial"):
            actor, owner, source, policy = await load(user)
        if selected_resource and (not policy.resource(*selected_resource)["balance"] if balance_only else policy.resource(*selected_resource)["movements"] == "none"):
            fail("operational_workspace_not_found", "الكشف غير متاح", 404)
        # Workspace reports need current financial proofs, not historical audit
        # pages or idempotency receipts. Writers keep the complete store reader.
        state = await directory_state(db, owner) if directory_only else await read_financial_position_state(db, owner)
        resources, computed = await resource_catalog(db, owner, policy, state, ad_bank_fx=ad_bank_fx,
            selected_resource=selected_resource, selected_kind=selected_kind, provider_positions=provider_positions, provider_orders=provider_orders,
            directory_only=directory_only or forecast_only, reviewed_counts=review_snapshots.counts.get((owner, source_key(state))))
        if forecast_only:
            computed = {"obligations": state.get("engine", {}).get("obligations", {}).values()}
        if invoice_headers:
            from operational_workspace_supplier_invoices import fill_historical_headers
            await fill_historical_headers(db, owner, resources, computed, state, policy)
        result = await prepare_in_thread(read_diagnostics.thread_call("project", callback),
                                        actor, owner, policy, state, resources, computed)
        if invoice_detail:
            from operational_workspace_supplier_invoices import read_invoice
            result = await read_invoice(db, owner, selected_resource[1], invoice_detail, policy, result)
        with read_diagnostics.phase("access_final"):
            current_actor, current_owner, _, current = await load(user)
        if (actor["id"], owner, policy.version, policy.owner_override) != (current_actor["id"], current_owner, current.version, current.owner_override):
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
        return result

    @router.get("/workspace/context")
    async def context(request: Request, ad_bank_fx: bool = False, home_preview: bool = False, directory_only: bool = False, provider_positions: bool = False, user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            from operational_balance_access import OPERATION_LABELS
            return {"actor": {**_actor_snapshot(actor), "is_owner": policy.owner_override},
                    "session_scope": digest([owner, actor["id"]]), "permission_version": policy.version,
                    "status": state["status"], "resources": resources,
                    "ledger_start": _business_day({"occurred_at": state["started_at"]}) if state.get("started_at") else None,
                    **({"directory_only": True} if directory_only else {}),
                    **({"recent_movements": _recent_postings(state, resources, policy)} if home_preview and not directory_only else {}),
                    "capabilities": {"manage_access": policy.owner_override,
                                     "create_entries": state["status"] == "active" and any(row["operations"] for row in resources)},
                    "operations": [{"key": key, "label": label} for key, label in OPERATION_LABELS.items() if policy.can_operation(key)]}
        mode = "directory" if directory_only else "home" if home_preview else "full"
        return await run_visible_report_read(request, lambda: read_diagnostics.sample("operational_context",
            checked_read(user, project, ad_bank_fx=ad_bank_fx, directory_only=directory_only, provider_positions=provider_positions),
            mode=mode))

    @router.get("/workspace/specialist/{which}")
    async def specialist(request: Request, which: Literal["returns", "exchanges", "fees", "reports", "specialized", "access"],
                         page: int = Query(default=1, ge=1), group: str | None = Query(default=None, max_length=32),
                         user=Depends(current_user)):
        from operational_web_page_policy import SCOPE_REPORTS
        if which in SCOPE_REPORTS:
            from operational_web_specialist_reads import assigned_finance_page
            return await run_visible_report_read(request, lambda: assigned_finance_page(db, user, load, which, group, page))
        from operational_web_specialist_reads import specialist_page
        return await run_visible_report_read(request, lambda: specialist_page(db, user, load, which, page))

    @router.get("/workspace/order-issues")
    async def order_issues(request: Request, kind: str | None = Query(default=None, max_length=50),
                           id: str | None = Query(default=None, max_length=200), q: str = Query(default="", max_length=160),
                           page: int = Query(default=1, ge=1), user=Depends(current_user)):
        return await run_visible_report_read(request, lambda: read_order_issues(
            db, user, load, review_snapshots, kind, id, q, page))

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
    async def parties(request: Request, kind: str | None = None, q: str = Query(default="", max_length=160),
                      provider_positions: bool = False, ad_bank_fx: bool = False, user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            return {"items": [r for r in resources if not q or q.casefold() in r["name"].casefold()],
                    "session_scope": digest([owner, actor["id"]]), "permission_version": policy.version,
                    "owner_override": policy.owner_override}
        return await run_visible_report_read(request, lambda: checked_read(user, project, selected_kind=kind,
            provider_positions=kind == "provider" and provider_positions, ad_bank_fx=kind == "ad_account" and ad_bank_fx))

    @router.get("/workspace/accounts/{kind}")
    async def account_group(request: Request, kind: Literal["bank", "cash", "employee_custody"], user=Depends(current_user)):
        from operational_party_projection_state import directory_cash_positions
        async def group():
            actor, owner, _, policy = await load(user)
            header = await directory_state(db, owner)
            resources, _ = await resource_catalog(db, owner, policy, header, directory_only=True, selected_kind=kind)
            funding_allowed = kind in payment_source_kinds(policy)
            eligible = {r["id"] for r in resources if funding_allowed and r["access"]["use"] and r["currency"] == "SAR"}
            values = await directory_cash_positions(db, owner, kind, resources, funding_ids=eligible)
            for resource in resources:
                position = values.get((kind, resource["id"], resource["currency"]), {})
                if resource["id"] in eligible:
                    resource["can_fund_payment"] = Decimal(position.get("actual", "0")) > 0
                if resource["access"]["balance"]:
                    resource["balance"] = _balance(position, kind)
                    resource["position_balance"] = position_balance(position, kind)
            current_actor, current_owner, _, current = await load(user)
            if (actor["id"], owner, policy.version, policy.owner_override) != (current_actor["id"], current_owner, current.version, current.owner_override):
                fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
            return {"items": resources, "session_scope": digest([owner, actor["id"]]), "permission_version": policy.version,
                    "owner_override": policy.owner_override}
        return await run_visible_report_read(request, group)

    @router.get("/workspace/resource/{kind}/{identity}")
    async def resource_balance(request: Request, kind: str, identity: str, ad_bank_fx: bool = False, user=Depends(current_user)):
        def selected(actor, owner, policy, state, resources, computed):
            if len(resources) != 1 or not resources[0]["access"]["balance"]:
                fail("operational_workspace_not_found", "الحساب غير متاح", 404)
            return resources[0]
        return await run_visible_report_read(request, lambda: checked_read(user, selected, selected_resource=(kind, identity),
            balance_only=True, ad_bank_fx=ad_bank_fx, provider_positions=kind == "provider"))

    @router.get("/workspace/forecast/{kind}/{identity}")
    async def forecast(request: Request, kind: Literal["courier", "store_driver"], identity: str, user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            if not resources or resources[0]["access"]["movements"] != "all":
                fail("operational_workspace_not_found", "التحصيلات المتوقعة غير متاحة", 404)
            return expected_collections(state, computed, resources[0], policy)
        return await run_visible_report_read(request, lambda: checked_read(user, project, selected_resource=(kind, identity), forecast_only=True))

    @router.get("/workspace/ledger/{kind}/{identity}")
    async def ledger(request: Request, kind: str, identity: str, page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=5, ge=1, le=8), focus_id: str | None = Query(default=None, max_length=250),
                     ad_bank_fx: bool = False, page_only: bool = False, courier_history: bool = False, provider_orders: bool = False, q: str = Query(default="", max_length=160),
                     date_from: str | None = None, date_to: str | None = None, user=Depends(current_user)):
        check_period(date_from, date_to)
        if page_only and not provider_orders and not q and not date_from:
            return await run_visible_report_read(request, lambda: movement_window(user, kind, identity, page, page_size,
                account_detail=True, ad_bank_fx=ad_bank_fx, focus_id=focus_id, courier_history=courier_history))
        def project(actor, owner, policy, state, resources, computed):
            resource = next((r for r in resources if r["kind"] == kind and r["id"] == identity), None)
            if not resource or resource["access"]["movements"] == "none":
                fail("operational_workspace_not_found", "الكشف غير متاح", 404)
            if provider_orders:
                from operational_workspace_provider_orders import filtered_orders
                rows, undated = filtered_orders(computed["details"]["workspace_provider_orders"], q, date_from, date_to)
                groups = computed["details"]["provider_balances"]
                amounts = [{"currency": currency, **{k: item.get(k) for k in ("opening", "settled", "gross", "known_gross",
                            "fees", "known_fees", "refunded", "net", "known_net", "complete",
                            "uncomputed_principal", "uncomputed_principal_complete", "uncomputed_orders")}}
                          for currency, accounts in groups.items() for item in accounts if item["provider"] == identity]
                return {"resource": resource, "provider_orders": True, "amounts": amounts, "undated": undated,
                        **paginate(rows, page, page_size)}
            rows = ledger_items(state, computed, resource, policy)
            summary = _pos_summary(state, resource)
            balance_status = projected_balance_status(resource, rows)
            if date_from and resource["access"].get("report"):
                period = period_statement(resource, rows, date_from=date_from, date_to=date_to,
                    actor=_actor_snapshot(actor), generated_at=now(), purpose="view", started_at=state.get("started_at"),
                    already_projected=True)
                entries = period.pop("items")
                summary["period"] = {key: period[key] for key in ("date_from", "date_to", "currency", "scope",
                    "can_print", "opening_balance", "closing_balance", "totals")}
                summary["period"]["total"] = len(entries)
                display = list(filter_activity(reversed(entries), q=q))
            elif q or date_from:
                display = list(filter_activity(rows, q=q, date_from=date_from, date_to=date_to))
            else:
                display = compact_rows(rows)
            return {"resource": resource, **summary, **paginate(display, page, page_size, focus_id),
                    "balance_status": balance_status, "balance_note": balance_note(balance_status),
                    "courier_summary": courier_summary(state, computed, resource),
                    "expected_collections": expected_collections(state, computed, resource, policy)}
        return await run_visible_report_read(request, lambda: checked_read(user, project, ad_bank_fx=ad_bank_fx,
            selected_resource=(kind, identity), invoice_headers=not provider_orders, provider_orders=provider_orders, provider_positions=kind == "provider"))

    @router.get("/workspace/supplier-invoices/{supplier_id}/{invoice_id}")
    async def supplier_invoice(request: Request, supplier_id: str, invoice_id: str, user=Depends(current_user)):
        def project(actor, owner, policy, state, resources, computed):
            resource = next((r for r in resources if r["kind"] == "supplier" and r["id"] == supplier_id), None)
            from operational_workspace_supplier_invoices import linked_claim
            if resource:
                for item in checked_rows(computed["obligations"]):
                    if (linked_claim(item) and item["source_invoice_id"] == invoice_id and item["party_id"] == supplier_id
                            and (resource["access"]["movements"] == "all" or (resource["access"]["movements"] == "own"
                                 and item.get("source_invoice_actor_id") == actor["id"]))):
                        return {"number": item.get("source_invoice_number"), "credit_detail_key": item.get("credit_detail_key")}
            fail("operational_workspace_not_found", "الفاتورة غير متاحة", 404)
        return await run_visible_report_read(request, lambda: checked_read(user, project,
            selected_resource=("supplier", supplier_id), invoice_headers=True, invoice_detail=invoice_id))

    async def movement_window(user, kind, identity, page, page_size, *, account_detail=False,
                              ad_bank_fx=False, focus_id=None, courier_history=False):
        from operational_financial_position_state import read_financial_position_state
        from operational_workspace_supplier_invoices import fill_historical_headers
        actor, owner, _, policy = await load(user)
        target = (kind, identity) if kind is not None else None
        header = await directory_state(db, owner)
        async def catalog(state, *, directory):
            resources, computed = await resource_catalog(db, owner, policy, state, selected_resource=target,
                directory_only=directory, ad_bank_fx=ad_bank_fx, provider_positions=account_detail and kind == "provider",
                reviewed_counts=review_snapshots.counts.get((owner, source_key(state))))
            if target and not any(r["access"]["movements"] != "none" for r in resources):
                fail("operational_workspace_not_found", "الكشف غير متاح", 404)
            return resources, computed

        resources, _ = await catalog(header, directory=True)
        key = (owner, actor["id"], policy.version, policy.owner_override, target, account_detail, ad_bank_fx,
               header["revision"], digest(resources), courier_history)
        state = None
        cached = register_indexes.get(key)
        if cached is not None and monotonic() - cached[0] < 120:
            register_indexes.move_to_end(key)
            _, index, summary, courier_rows = cached
        else:
            state = await read_financial_position_state(db, owner)
            summary = {}
            courier_rows = {}
            if account_detail:
                resources, computed = await catalog(state, directory=False)
                await fill_historical_headers(db, owner, resources, computed, state, policy)
                resource = resources[0]
                def selected():
                    rows = ledger_items(state, computed, resource, policy, make_row=_register_candidate)
                    if courier_history and kind == "courier" and resource["access"]["balance"] and resource["access"]["movements"] == "all":
                        rows, _ = with_running_balances(resource, rows)
                    return compact_rows(rows), {"resource": resource, **_pos_summary(state, resource),
                        "courier_summary": courier_summary(state, computed, resource)}
                index, summary = await prepare_in_thread(selected)
            else:
                if state["revision"] != header["revision"]:
                    resources, _ = await catalog(state, directory=True)
                claims = await prepare_in_thread(register_claims, state, policy.resource("provider", "paypal")["balance"])
                await fill_historical_headers(db, owner, resources, claims, state, policy)
                index = await prepare_in_thread(_register_index, state, claims, resources, policy)
            if state["revision"] == header["revision"] and len(index) <= 20000:
                register_indexes[key] = (monotonic(), index, summary, courier_rows)
                register_indexes.move_to_end(key)
                while len(register_indexes) > 3 or sum(len(value[1]) for value in register_indexes.values()) > 24000:
                    register_indexes.popitem(last=False)
        result = await prepare_in_thread(_open_register_page, index, policy, page, page_size, focus_id)
        if courier_history and not account_detail:
            visible = {(row["party_id"], row["currency"]) for row in result["items"]
                       if row["party_type"] == "courier" and not row.get("summary")}
            for resource in resources:
                company = resource["id"], resource["currency"]
                if (resource["kind"] != "courier" or company not in visible or not resource["access"]["balance"]
                        or resource["access"]["movements"] != "all" or company in courier_rows):
                    continue
                if state is None:
                    state = await read_financial_position_state(db, owner)
                    if state["revision"] != header["revision"]:
                        break  # Do not attach figures from a different posting revision.
                courier_rows[company] = await prepare_in_thread(_courier_history, state, resource, policy)
            for row in result["items"]:
                if row["party_type"] == "courier" and not row.get("summary"):
                    balances = courier_rows.get((row["party_id"], row["currency"]), {})
                    row["balance_after"] = balances.get(row["id"])
        current_actor, current_owner, _, current = await load(user)
        if (actor["id"], owner, policy.version, policy.owner_override) != (current_actor["id"], current_owner, current.version, current.owner_override):
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
        return {**result, **summary, "session_scope": digest([owner, actor["id"]]),
                "permission_version": policy.version, "owner_override": policy.owner_override}

    @router.get("/workspace/movements")
    async def movements(request: Request, page: int = Query(default=1, ge=1), page_size: int = Query(default=5, ge=1, le=8),
                        kind: str | None = Query(default=None, max_length=50), id: str | None = Query(default=None, max_length=200),
                        q: str = Query(default="", max_length=160), date_from: str | None = None, date_to: str | None = None,
                        direction: Literal["in", "out", "transfer"] | None = None, status: Literal["done", "info"] | None = None,
                        page_only: bool = False, courier_history: bool = False, user=Depends(current_user)):
        check_period(date_from, date_to)
        if (kind is None) != (id is None):
            fail("operational_account_filter_invalid", "اختر نوع الحساب والحساب معًا", 422)
        if page_only and not any((q, date_from, direction, status)):
            return await run_visible_report_read(request, lambda: movement_window(user, kind, id, page, page_size, courier_history=courier_history))
        def project(actor, owner, policy, state, resources, computed):
            selected = [r for r in resources if kind is None or (r["kind"], r["id"]) == (kind, id)]
            if kind is not None and not any(r["access"]["movements"] != "none" for r in selected):
                fail("operational_workspace_not_found", "الكشف غير متاح", 404)
            indexed = _index_ledger_sources(state, computed, selected)

            def resource_rows():
                for resource in checked_rows(selected):
                    if resource["access"]["movements"] == "none":
                        continue
                    sources = indexed[(resource["kind"], resource["id"], resource["currency"])]
                    rows = ledger_items(state, computed, resource, policy, sources=sources)
                    # Search prior to de-duplication. A second authorized account's
                    # projection can be the one that matches a name or reference.
                    display = filter_activity(rows, q=q, date_from=date_from, date_to=date_to, direction=direction, status=status) if (
                        q or date_from or direction or status) else compact_rows(rows)
                    yield display

            return register_page(resource_rows(), page, page_size)
        return await run_visible_report_read(request, lambda: checked_read(user, project, selected_resource=(kind, id) if kind is not None else None, invoice_headers=True))

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
                purpose=purpose, started_at=state.get("started_at"), already_projected=True)
        return await run_visible_report_read(request, lambda: checked_read(user, project, selected_resource=(kind, identity), invoice_headers=True, provider_positions=kind == "provider"))

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
        return await run_visible_report_read(request, lambda: checked_read(user, project, selected_resource=(kind, identity)))

    @router.post("/workspace/entries")
    async def entry(payload: WorkspaceEntry, user=Depends(current_user)):
        actor, owner, source, _ = await load(user)
        return await save_entry(db, owner, actor, payload.command(), source)

