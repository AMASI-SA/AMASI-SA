"""Return decision engine for Mezan.

The engine intentionally separates five facts that must never be collapsed:

1. the immutable original order and order-item quantities,
2. the employee-selected items requested for return,
3. a Salla return shipment/AWB (logistics only),
4. warehouse receipt and inspection,
5. credit notes and settled refunds (accounting/cash).

Salla may repeat every order package on a return shipment. Therefore shipment
``packages`` are NEVER used as authoritative returned-item selections.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReturnReason = Literal[
    "defective",
    "wrong_item",
    "shipping_damage",
    "customer_changed_mind",
    "size_or_fit",
    "other",
]
RequestedResolution = Literal["refund", "replacement", "either"]
DecisionKey = Literal[
    "return_refund",
    "keep_refund",
    "return_replace",
    "keep_replace",
    "keep_partial_refund",
]
ReturnCaseStatus = Literal[
    "draft",
    "approved",
    "label_issued",
    "in_transit",
    "received",
    "inspected",
    "refund_pending",
    "completed",
    "rejected",
    "cancelled",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def money(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, dict):
        for key in ("amount", "value", "total", "cost"):
            if key in value:
                return money(value.get(key))
        return 0.0
    try:
        return round(float(value), 4)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def clean_text(value: Any) -> str:
    return str(value or "").strip()


class ReturnItemSelection(BaseModel):
    """One explicit order line selected by the employee."""

    model_config = ConfigDict(extra="forbid")

    order_item_id: str = Field(min_length=1)
    product_id: Optional[str] = None
    sku: Optional[str] = None
    name: Optional[str] = None
    quantity_ordered: int = Field(ge=1)
    quantity_return: int = Field(ge=1)

    # Original sale snapshot. Never recalculate using today's catalogue price.
    unit_sale_amount: float = Field(default=0.0, ge=0)
    unit_tax_amount: float = Field(default=0.0, ge=0)
    unit_cost: Optional[float] = Field(default=None, ge=0)

    # Economic value expected if the unit is physically recovered.
    expected_recoverable_value: Optional[float] = Field(default=None, ge=0)
    sellable_probability: float = Field(default=1.0, ge=0, le=1)
    refurbishment_cost_per_unit: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_quantity(self) -> "ReturnItemSelection":
        if self.quantity_return > self.quantity_ordered:
            raise ValueError(
                "return quantity cannot exceed the original ordered quantity"
            )
        return self


class ReturnDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(default="SAR", min_length=3, max_length=3)
    reason_code: ReturnReason
    requested_resolution: RequestedResolution = "either"
    items: list[ReturnItemSelection] = Field(min_length=1)

    # Amounts are incremental from the decision point, not historical totals.
    refund_amount: Optional[float] = Field(default=None, ge=0)
    partial_refund_amount: Optional[float] = Field(default=None, ge=0)
    return_shipping_quote: Optional[float] = Field(default=None, ge=0)
    customer_return_shipping_charge: float = Field(default=0.0, ge=0)
    inspection_handling_cost: Optional[float] = Field(default=None, ge=0)
    replacement_item_cost: Optional[float] = Field(default=None, ge=0)
    replacement_shipping_cost: Optional[float] = Field(default=None, ge=0)
    refund_processing_fee: float = Field(default=0.0, ge=0)

    merchant_fault: bool = False
    legal_or_policy_return_required: bool = False
    notes: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=160)


class DecisionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: DecisionKey
    label: str
    available: bool
    incremental_cost: float
    retrieves_item: bool
    refunds_customer: bool
    replaces_item: bool
    reasons: list[str] = Field(default_factory=list)


class ReturnDecisionReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    selected_quantity: int
    selected_sale_value: float
    selected_historical_cost: float
    expected_gross_recoverable_value: float
    recovery_cost: float
    retrieval_net_benefit: float
    merchant_return_shipping_cost: float
    customer_return_shipping_charge_allowed: bool
    confidence: Literal["low", "medium", "high"]
    missing_inputs: list[str]
    options: list[DecisionOption]
    recommended_option: DecisionKey
    recommendation_reasons: list[str]
    guardrails: list[str]


class ReturnCaseCreate(ReturnDecisionInput):
    """Draft persisted for employee review."""

    source_return_shipment_id: Optional[str] = None
    source_return_tracking_number: Optional[str] = None
    source_return_label_url: Optional[str] = None


class ReturnCaseApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_option: DecisionKey
    expected_version: int = Field(ge=1)
    employee_note: str = Field(min_length=3, max_length=2000)


class InspectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_item_id: str = Field(min_length=1)
    received_quantity: int = Field(ge=0)
    accepted_quantity: int = Field(ge=0)
    sellable_quantity: int = Field(ge=0)
    damaged_quantity: int = Field(ge=0)
    note: Optional[str] = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_inspection(self) -> "InspectionItem":
        if self.accepted_quantity > self.received_quantity:
            raise ValueError("accepted quantity cannot exceed received quantity")
        if self.sellable_quantity + self.damaged_quantity > self.accepted_quantity:
            raise ValueError(
                "sellable plus damaged quantity cannot exceed accepted quantity"
            )
        return self


class ReturnInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    items: list[InspectionItem] = Field(min_length=1)
    employee_note: str = Field(min_length=3, max_length=2000)


def _default_refund_amount(data: ReturnDecisionInput) -> float:
    return round(
        sum(
            (item.unit_sale_amount + item.unit_tax_amount)
            * item.quantity_return
            for item in data.items
        ),
        4,
    )


def build_return_decision_report(
    data: ReturnDecisionInput,
) -> ReturnDecisionReport:
    selected_quantity = sum(item.quantity_return for item in data.items)
    selected_sale_value = round(
        sum(
            (item.unit_sale_amount + item.unit_tax_amount)
            * item.quantity_return
            for item in data.items
        ),
        4,
    )
    historical_cost = round(
        sum((item.unit_cost or 0.0) * item.quantity_return for item in data.items),
        4,
    )

    gross_recoverable = 0.0
    refurbishment = 0.0
    missing: list[str] = []
    for item in data.items:
        recoverable = item.expected_recoverable_value
        if recoverable is None:
            recoverable = item.unit_cost
        if recoverable is None:
            missing.append(
                f"expected_recoverable_value:{item.order_item_id}"
            )
            recoverable = 0.0
        gross_recoverable += (
            recoverable
            * item.sellable_probability
            * item.quantity_return
        )
        refurbishment += (
            item.refurbishment_cost_per_unit
            * item.quantity_return
        )

    if data.return_shipping_quote is None:
        missing.append("return_shipping_quote")
    if data.inspection_handling_cost is None:
        missing.append("inspection_handling_cost")
    if (
        data.requested_resolution in {"replacement", "either"}
        and data.replacement_item_cost is None
    ):
        missing.append("replacement_item_cost")
    if (
        data.requested_resolution in {"replacement", "either"}
        and data.replacement_shipping_cost is None
    ):
        missing.append("replacement_shipping_cost")

    # Merchant fault / mandatory-right cases must not optimize by silently
    # charging return freight to the customer.
    customer_charge_allowed = not (
        data.merchant_fault or data.legal_or_policy_return_required
    )
    customer_charge = (
        data.customer_return_shipping_charge
        if customer_charge_allowed
        else 0.0
    )
    merchant_return_shipping = max(
        money(data.return_shipping_quote) - customer_charge,
        0.0,
    )
    inspection = money(data.inspection_handling_cost)
    recovery_cost = round(
        merchant_return_shipping + inspection + refurbishment,
        4,
    )
    gross_recoverable = round(gross_recoverable, 4)
    retrieval_benefit = round(gross_recoverable - recovery_cost, 4)

    refund_amount = (
        money(data.refund_amount)
        if data.refund_amount is not None
        else _default_refund_amount(data)
    )
    refund_total = refund_amount + data.refund_processing_fee
    partial_refund = money(data.partial_refund_amount)
    replacement_total = (
        money(data.replacement_item_cost)
        + money(data.replacement_shipping_cost)
    )

    allow_refund = data.requested_resolution in {"refund", "either"}
    allow_replacement = data.requested_resolution in {
        "replacement",
        "either",
    }

    options = [
        DecisionOption(
            key="return_refund",
            label="استرجاع القطعة ورد المبلغ",
            available=allow_refund,
            incremental_cost=round(
                refund_total + recovery_cost - gross_recoverable,
                4,
            ),
            retrieves_item=True,
            refunds_customer=True,
            replaces_item=False,
            reasons=[
                "يتطلب بوليصة إرجاع وفحصًا",
                "يعيد القيمة القابلة للاستفادة من القطعة",
            ],
        ),
        DecisionOption(
            key="keep_refund",
            label="ترك القطعة مع العميل ورد المبلغ",
            available=allow_refund,
            incremental_cost=round(refund_total, 4),
            retrieves_item=False,
            refunds_customer=True,
            replaces_item=False,
            reasons=["لا توجد تكلفة إرجاع أو قيمة مخزون مستعادة"],
        ),
        DecisionOption(
            key="return_replace",
            label="استرجاع القطعة وإرسال بديل",
            available=allow_replacement,
            incremental_cost=round(
                replacement_total + recovery_cost - gross_recoverable,
                4,
            ),
            retrieves_item=True,
            refunds_customer=False,
            replaces_item=True,
            reasons=[
                "يتطلب شحن إرجاع وشحن بديل",
                "لا يحتسب البديل المجاني كمبيعات جديدة",
            ],
        ),
        DecisionOption(
            key="keep_replace",
            label="ترك القطعة مع العميل وإرسال بديل",
            available=allow_replacement,
            incremental_cost=round(replacement_total, 4),
            retrieves_item=False,
            refunds_customer=False,
            replaces_item=True,
            reasons=["لا توجد تكلفة إرجاع أو قيمة مخزون مستعادة"],
        ),
        DecisionOption(
            key="keep_partial_refund",
            label="ترك القطعة مع العميل ورد جزئي",
            available=allow_refund and data.partial_refund_amount is not None,
            incremental_cost=round(
                partial_refund + data.refund_processing_fee,
                4,
            ),
            retrieves_item=False,
            refunds_customer=True,
            replaces_item=False,
            reasons=["يتطلب موافقة العميل وتوثيق مبلغ التسوية"],
        ),
    ]

    available = [option for option in options if option.available]
    if not available:
        raise ValueError("at least one decision option must be available")

    recommended = min(
        available,
        key=lambda option: (option.incremental_cost, option.key),
    )

    recommendation_reasons = [
        "المقارنة مبنية على التكلفة الإضافية من لحظة القرار",
    ]
    if recommended.retrieves_item:
        recommendation_reasons.append(
            "القيمة المتوقعة من استعادة القطعة تتجاوز تكلفة استعادتها"
        )
    else:
        recommendation_reasons.append(
            "تكلفة استعادة القطعة مقاربة أو أعلى من قيمتها المتوقعة"
        )

    guardrails = [
        "حزم بوليصة سلة ليست مصدر حقيقة للقطع المرتجعة",
        "لا يتغير المخزون عند إنشاء البوليصة أو أثناء النقل",
        "لا تُخفض المبيعات قبل اعتماد إشعار دائن",
        "لا يُسجل رد نقدي قبل نجاح معاملة الاسترداد",
        "القرار النهائي للموظف ويجب تسجيل سببه",
    ]
    if not customer_charge_allowed:
        guardrails.append(
            "لا يجوز تحميل شحن الإرجاع على العميل في هذه الحالة دون مراجعة السياسة النظامية"
        )

    confidence = (
        "high"
        if not missing
        else "medium"
        if len(missing) <= 2
        else "low"
    )

    return ReturnDecisionReport(
        currency=data.currency.upper(),
        selected_quantity=selected_quantity,
        selected_sale_value=selected_sale_value,
        selected_historical_cost=historical_cost,
        expected_gross_recoverable_value=gross_recoverable,
        recovery_cost=recovery_cost,
        retrieval_net_benefit=retrieval_benefit,
        merchant_return_shipping_cost=round(
            merchant_return_shipping,
            4,
        ),
        customer_return_shipping_charge_allowed=customer_charge_allowed,
        confidence=confidence,
        missing_inputs=sorted(set(missing)),
        options=options,
        recommended_option=recommended.key,
        recommendation_reasons=recommendation_reasons,
        guardrails=guardrails,
    )


def _public_doc(doc: dict[str, Any]) -> dict[str, Any]:
    result = dict(doc)
    result.pop("_id", None)
    return result


def _actor(user: dict[str, Any]) -> dict[str, str]:
    return {
        "id": clean_text(user.get("id")),
        "name": clean_text(
            user.get("name")
            or user.get("full_name")
            or user.get("email")
            or user.get("id")
        ),
    }


def _raw_candidates(order: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [order]
    raw_by_source = order.get("raw_by_source")
    if isinstance(raw_by_source, dict):
        candidates.extend(
            raw for raw in raw_by_source.values()
            if isinstance(raw, dict)
        )
    return candidates


def extract_salla_return_shipments(
    order: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract return AWBs without treating packages as returned items."""
    found: dict[str, dict[str, Any]] = {}
    for candidate in _raw_candidates(order):
        shipments = candidate.get("shipments")
        if not isinstance(shipments, list):
            continue
        for shipment in shipments:
            if not isinstance(shipment, dict):
                continue
            shipment_type = clean_text(
                shipment.get("type")
                or shipment.get("shipment_type")
            ).lower()
            if shipment_type != "return":
                continue
            shipment_id = clean_text(shipment.get("id"))
            key = shipment_id or clean_text(
                shipment.get("tracking_number")
                or shipment.get("shipping_number")
            )
            if not key:
                key = uuid.uuid4().hex
            packages = shipment.get("packages")
            found[key] = {
                "shipment_id": shipment_id or None,
                "type": "return",
                "status": clean_text(shipment.get("status")) or None,
                "courier_name": clean_text(
                    shipment.get("courier_name")
                    or (shipment.get("courier") or {}).get("name")
                    if isinstance(shipment.get("courier"), dict)
                    else shipment.get("courier_name")
                ) or None,
                "tracking_number": clean_text(
                    shipment.get("tracking_number")
                    or shipment.get("shipping_number")
                ) or None,
                "tracking_url": clean_text(
                    shipment.get("tracking_link")
                    or shipment.get("tracking_url")
                ) or None,
                "label": shipment.get("label"),
                "created_at": shipment.get("created_at"),
                "package_count_from_salla": (
                    len(packages) if isinstance(packages, list) else 0
                ),
                "packages_are_authoritative_items": False,
            }
    return list(found.values())


async def ensure_return_indexes(db: Any) -> None:
    await db.return_cases.create_index(
        [("user_id", 1), ("order_number", 1), ("created_at", -1)]
    )
    await db.return_cases.create_index(
        [("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
    )


async def get_return_workspace(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    order_number = clean_text(order_number)
    order = await db.unified_orders.find_one(
        {
            "user_id": {"$in": [user_id, "main"]},
            "order_number": order_number,
        },
        sort=[("updated_at", -1)],
    )
    if not order:
        raise LookupError("order_not_found")

    cases = [
        _public_doc(row)
        async for row in db.return_cases.find(
            {"user_id": user_id, "order_number": order_number}
        ).sort("created_at", -1)
    ]
    return {
        "order_number": order_number,
        "detected_return_shipments": extract_salla_return_shipments(order),
        "cases": cases,
        "source_rules": {
            "shipment_packages_are_authoritative_items": False,
            "selected_items_source": "mezan_employee_selection",
            "accepted_items_source": "mezan_warehouse_inspection",
            "financial_refund_source": "settled_payment_transaction",
        },
    }


async def create_return_case(
    db: Any,
    *,
    user_id: str,
    user: dict[str, Any],
    order_number: str,
    request: ReturnCaseCreate,
) -> dict[str, Any]:
    await ensure_return_indexes(db)
    order_number = clean_text(order_number)
    order_exists = await db.unified_orders.find_one(
        {
            "user_id": {"$in": [user_id, "main"]},
            "order_number": order_number,
        },
        {"_id": 1},
    )
    if not order_exists:
        raise LookupError("order_not_found")

    if request.idempotency_key:
        existing = await db.return_cases.find_one(
            {
                "user_id": user_id,
                "idempotency_key": request.idempotency_key,
            }
        )
        if existing:
            return _public_doc(existing)

    now = utc_now()
    actor = _actor(user)
    report = build_return_decision_report(request)
    case_id = uuid.uuid4().hex
    doc = {
        "id": case_id,
        "user_id": user_id,
        "order_number": order_number,
        "status": "draft",
        "version": 1,
        "currency": request.currency.upper(),
        "reason_code": request.reason_code,
        "requested_resolution": request.requested_resolution,
        "selected_items": [
            item.model_dump(mode="json")
            for item in request.items
        ],
        "decision_input": request.model_dump(mode="json"),
        "decision_report": report.model_dump(mode="json"),
        "source_return_shipment": {
            "shipment_id": request.source_return_shipment_id,
            "tracking_number": request.source_return_tracking_number,
            "label_url": request.source_return_label_url,
        },
        "approval": None,
        "inspection": None,
        "execution_gates": {
            "return_label": "requires_employee_approval",
            "inventory": "blocked_until_inspection",
            "credit_note": "blocked_until_accepted_return",
            "cash_refund": "blocked_until_settled_transaction",
        },
        "events": [
            {
                "type": "return_case_created",
                "at": now,
                "actor": actor,
                "version": 1,
            }
        ],
        "idempotency_key": request.idempotency_key,
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
    }
    await db.return_cases.insert_one(doc)
    return _public_doc(doc)


async def approve_return_case(
    db: Any,
    *,
    user_id: str,
    user: dict[str, Any],
    case_id: str,
    request: ReturnCaseApproval,
) -> dict[str, Any]:
    existing = await db.return_cases.find_one(
        {"user_id": user_id, "id": clean_text(case_id)}
    )
    if not existing:
        raise LookupError("return_case_not_found")
    if existing.get("status") != "draft":
        raise ValueError("return_case_not_draft")
    if int(existing.get("version") or 0) != request.expected_version:
        raise RuntimeError("version_conflict")

    report = existing.get("decision_report") or {}
    available = {
        option.get("key")
        for option in report.get("options") or []
        if option.get("available") is True
    }
    if request.selected_option not in available:
        raise ValueError("decision_option_not_available")

    selected = next(
        option
        for option in report["options"]
        if option.get("key") == request.selected_option
    )
    now = utc_now()
    actor = _actor(user)
    new_version = request.expected_version + 1
    gates = dict(existing.get("execution_gates") or {})
    gates["return_label"] = (
        "ready_for_employee_execution"
        if selected.get("retrieves_item")
        else "not_required_customer_keeps_item"
    )
    gates["inventory"] = (
        "blocked_until_inspection"
        if selected.get("retrieves_item")
        else "not_applicable"
    )
    gates["credit_note"] = (
        "ready_for_financial_review"
        if selected.get("refunds_customer") and not selected.get("retrieves_item")
        else "blocked_until_accepted_return"
        if selected.get("refunds_customer")
        else "not_required"
    )

    result = await db.return_cases.find_one_and_update(
        {
            "user_id": user_id,
            "id": clean_text(case_id),
            "status": "draft",
            "version": request.expected_version,
        },
        {
            "$set": {
                "status": "approved",
                "version": new_version,
                "approval": {
                    "selected_option": request.selected_option,
                    "employee_note": request.employee_note,
                    "approved_at": now,
                    "approved_by": actor,
                },
                "execution_gates": gates,
                "updated_at": now,
            },
            "$push": {
                "events": {
                    "type": "return_case_approved",
                    "at": now,
                    "actor": actor,
                    "version": new_version,
                    "selected_option": request.selected_option,
                    "note": request.employee_note,
                }
            },
        },
        return_document=True,
    )
    if not result:
        raise RuntimeError("version_conflict")
    return _public_doc(result)


async def inspect_return_case(
    db: Any,
    *,
    user_id: str,
    user: dict[str, Any],
    case_id: str,
    request: ReturnInspection,
) -> dict[str, Any]:
    existing = await db.return_cases.find_one(
        {"user_id": user_id, "id": clean_text(case_id)}
    )
    if not existing:
        raise LookupError("return_case_not_found")
    if existing.get("status") not in {
        "approved",
        "label_issued",
        "in_transit",
        "received",
    }:
        raise ValueError("return_case_not_ready_for_inspection")
    if int(existing.get("version") or 0) != request.expected_version:
        raise RuntimeError("version_conflict")

    selected_quantities = {
        clean_text(item.get("order_item_id")): int(
            item.get("quantity_return") or 0
        )
        for item in existing.get("selected_items") or []
    }
    for item in request.items:
        maximum = selected_quantities.get(item.order_item_id)
        if maximum is None:
            raise ValueError("inspection_item_not_selected")
        if item.received_quantity > maximum:
            raise ValueError("received_quantity_exceeds_selected_quantity")

    now = utc_now()
    actor = _actor(user)
    new_version = request.expected_version + 1
    inspection = {
        "items": [item.model_dump(mode="json") for item in request.items],
        "employee_note": request.employee_note,
        "inspected_at": now,
        "inspected_by": actor,
        "accepted_quantity": sum(
            item.accepted_quantity for item in request.items
        ),
        "sellable_quantity": sum(
            item.sellable_quantity for item in request.items
        ),
        "damaged_quantity": sum(
            item.damaged_quantity for item in request.items
        ),
    }
    gates = dict(existing.get("execution_gates") or {})
    gates["inventory"] = (
        "ready_for_sellable_quantity_movement"
        if inspection["sellable_quantity"] > 0
        else "no_sellable_inventory"
    )
    selected_option = (
        (existing.get("approval") or {}).get("selected_option") or ""
    )
    gates["credit_note"] = (
        "ready_for_financial_review"
        if inspection["accepted_quantity"] > 0
        and selected_option in {"return_refund", "keep_refund"}
        else gates.get("credit_note")
    )

    result = await db.return_cases.find_one_and_update(
        {
            "user_id": user_id,
            "id": clean_text(case_id),
            "version": request.expected_version,
        },
        {
            "$set": {
                "status": "inspected",
                "version": new_version,
                "inspection": inspection,
                "execution_gates": gates,
                "updated_at": now,
            },
            "$push": {
                "events": {
                    "type": "return_case_inspected",
                    "at": now,
                    "actor": actor,
                    "version": new_version,
                    "accepted_quantity": inspection["accepted_quantity"],
                    "sellable_quantity": inspection["sellable_quantity"],
                    "damaged_quantity": inspection["damaged_quantity"],
                }
            },
        },
        return_document=True,
    )
    if not result:
        raise RuntimeError("version_conflict")
    return _public_doc(result)
