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
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from inventory_receipt_service import (
    InventoryLocationCapacityError,
    place_inventory_receipt,
)
from warehouse_location_routes import CABINETS, LOCATIONS


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

INVENTORY_RESERVATIONS = "mezan_inventory_reservations_v2"
RETURN_RESTOCKS = "mezan_return_inventory_restocks_v2"
RETURN_RESTOCK_REQUESTS = "mezan_return_inventory_restock_requests_v2"


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

    @model_validator(mode="after")
    def validate_unique_items(self) -> "ReturnDecisionInput":
        item_ids = [clean_text(item.order_item_id) for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("duplicate_return_item")
        return self


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

    @model_validator(mode="after")
    def validate_unique_items(self) -> "ReturnInspection":
        item_ids = [clean_text(item.order_item_id) for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("duplicate_inspection_item")
        return self


class ReturnRestockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=160)
    expected_version: int = Field(ge=1)
    order_item_id: str = Field(min_length=1, max_length=240)
    source_target_key: str = Field(min_length=3, max_length=500)
    quantity: int = Field(ge=1, le=100000)
    location_id: str = Field(min_length=1, max_length=160)
    scanned_barcode: str = Field(min_length=1, max_length=200)
    employee_note: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def clean_restock_text(self) -> "ReturnRestockRequest":
        for field in (
            "request_id",
            "order_item_id",
            "source_target_key",
            "location_id",
            "scanned_barcode",
            "employee_note",
        ):
            value = clean_text(getattr(self, field))
            if not value:
                raise ValueError(f"{field}_required")
            setattr(self, field, value)
        return self


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
    await db[RETURN_RESTOCKS].create_index(
        [("user_id", 1), ("return_case_id", 1), ("restocked_at", -1)],
        name="ix_return_restock_case",
    )
    await db[RETURN_RESTOCK_REQUESTS].create_index(
        [("user_id", 1), ("request_id", 1)],
        unique=True,
        name="uq_return_restock_request",
    )


def _restock_source_target_key(allocation: dict[str, Any]) -> str:
    receipt_id = clean_text(allocation.get("receipt_id"))
    if receipt_id:
        return "receipt:" + receipt_id
    lot_id = clean_text(allocation.get("lot_id"))
    if lot_id:
        return "lot:" + lot_id
    location_id = clean_text(allocation.get("location_id"))
    item_index = allocation.get("item_index")
    if location_id and item_index is not None:
        return f"location:{location_id}:item:{item_index}"
    return ""


async def _source_inventory_item(
    db: Any,
    *,
    user_id: str,
    allocation: dict[str, Any],
) -> dict[str, Any] | None:
    location_id = clean_text(allocation.get("location_id"))
    if not location_id:
        return None
    location = await db[LOCATIONS].find_one(
        {"user_id": user_id, "id": location_id},
        {"_id": 0, "occupancy.items": 1},
    )
    items = list(((location or {}).get("occupancy") or {}).get("items") or [])
    receipt_id = clean_text(allocation.get("receipt_id"))
    if receipt_id:
        return next(
            (
                item for item in items
                if clean_text(item.get("receipt_id")) == receipt_id
            ),
            None,
        )
    item_index = allocation.get("item_index")
    if isinstance(item_index, int) and 0 <= item_index < len(items):
        return items[item_index]
    return None


async def _permanent_restock_locations(
    db: Any,
    *,
    user_id: str,
    configuration_key: str,
    quantity: int,
) -> list[dict[str, Any]]:
    locations = await db[LOCATIONS].find(
        {
            "user_id": user_id,
            "state": {"$ne": "disabled"},
        },
        {
            "_id": 0,
            "id": 1,
            "code": 1,
            "barcode_value": 1,
            "warehouse_id": 1,
            "cabinet_id": 1,
            "purpose": 1,
            "max_items": 1,
            "occupancy": 1,
        },
    ).to_list(20000)
    cabinet_ids = {
        clean_text(row.get("cabinet_id"))
        for row in locations
        if clean_text(row.get("cabinet_id"))
    }
    cabinets = await db[CABINETS].find(
        {
            "user_id": user_id,
            "id": {"$in": sorted(cabinet_ids)},
        },
        {"_id": 0, "id": 1, "purpose": 1, "name": 1, "code": 1},
    ).to_list(max(1, len(cabinet_ids)))
    cabinet_map = {
        clean_text(row.get("id")): row
        for row in cabinets
        if clean_text(row.get("id"))
    }
    result = []
    for location in locations:
        cabinet = cabinet_map.get(clean_text(location.get("cabinet_id"))) or {}
        purpose = clean_text(
            location.get("purpose") or cabinet.get("purpose")
        )
        if purpose != "permanent_storage":
            continue
        occupancy = location.get("occupancy") or {}
        current_quantity = int(occupancy.get("total_quantity") or 0)
        max_items = location.get("max_items")
        if (
            max_items is not None
            and current_quantity + quantity > int(max_items)
        ):
            continue
        items = [
            item
            for item in occupancy.get("items") or []
            if int(item.get("quantity") or 0) > 0
        ]
        if items:
            if not configuration_key:
                continue
            item_keys = {
                clean_text(item.get("configuration_key"))
                for item in items
            }
            if item_keys != {configuration_key}:
                continue
        result.append({
            "id": location.get("id"),
            "code": location.get("code"),
            "barcode_value": (
                location.get("barcode_value") or location.get("code")
            ),
            "warehouse_id": location.get("warehouse_id"),
            "cabinet_id": location.get("cabinet_id"),
            "cabinet_name": cabinet.get("name"),
            "cabinet_code": cabinet.get("code"),
            "current_quantity": current_quantity,
            "remaining_capacity": (
                None
                if max_items is None
                else max(0, int(max_items) - current_quantity)
            ),
        })
    return result


async def get_return_restock_options(
    db: Any,
    *,
    user_id: str,
    case_id: str,
) -> dict[str, Any]:
    await ensure_return_indexes(db)
    case = await db.return_cases.find_one(
        {"user_id": user_id, "id": clean_text(case_id)},
        {"_id": 0},
    )
    if not case:
        raise LookupError("return_case_not_found")
    if case.get("status") != "inspected":
        raise ValueError("return_case_not_inspected")
    gate = (case.get("execution_gates") or {}).get("inventory")
    if gate not in {
        "ready_for_sellable_quantity_movement",
        "restock_in_progress",
        "restock_posting",
        "restocked_sellable_inventory",
    }:
        raise ValueError("return_inventory_not_ready_for_restock")

    inspection_by_item = {
        clean_text(row.get("order_item_id")): row
        for row in ((case.get("inspection") or {}).get("items") or [])
        if clean_text(row.get("order_item_id"))
    }
    selected_by_item = {
        clean_text(row.get("order_item_id")): row
        for row in case.get("selected_items") or []
        if clean_text(row.get("order_item_id"))
    }
    item_ids = sorted(inspection_by_item)
    reservations = await db[INVENTORY_RESERVATIONS].find(
        {
            "user_id": user_id,
            "order_number": case.get("order_number"),
            "status": "consumed",
            "line_key": {"$in": item_ids},
        },
        {"_id": 0},
    ).to_list(10000)
    prior = await db[RETURN_RESTOCKS].find(
        {
            "user_id": user_id,
            "return_case_id": case.get("id"),
            "status": "posted",
        },
        {"_id": 0},
    ).to_list(10000)

    prior_by_item: dict[str, int] = {}
    prior_by_source: dict[tuple[str, str], int] = {}
    for row in prior:
        item_id = clean_text(row.get("order_item_id"))
        source_key = clean_text(row.get("source_target_key"))
        quantity = int(row.get("quantity") or 0)
        prior_by_item[item_id] = prior_by_item.get(item_id, 0) + quantity
        prior_by_source[(item_id, source_key)] = (
            prior_by_source.get((item_id, source_key), 0) + quantity
        )

    reservations_by_item: dict[str, list[dict[str, Any]]] = {}
    for reservation in reservations:
        reservations_by_item.setdefault(
            clean_text(reservation.get("line_key")),
            [],
        ).append(reservation)

    items = []
    for item_id in item_ids:
        inspection = inspection_by_item[item_id]
        sellable = int(inspection.get("sellable_quantity") or 0)
        restocked = prior_by_item.get(item_id, 0)
        remaining_sellable = max(0, sellable - restocked)
        selected = selected_by_item.get(item_id) or {}
        source_map: dict[str, dict[str, Any]] = {}
        for reservation in reservations_by_item.get(item_id, []):
            for allocation in reservation.get("allocations") or []:
                source_key = _restock_source_target_key(allocation)
                if not source_key:
                    continue
                row = source_map.setdefault(source_key, {
                    "source_target_key": source_key,
                    "source_location_id": allocation.get("location_id"),
                    "source_warehouse_id": allocation.get("warehouse_id"),
                    "receipt_id": clean_text(allocation.get("receipt_id")) or None,
                    "lot_id": clean_text(allocation.get("lot_id")) or None,
                    "configuration_key": clean_text(
                        allocation.get("configuration_key")
                    ) or None,
                    "allocated_quantity": 0,
                    "product_id": reservation.get("product_id"),
                    "mezan_product_id": reservation.get("mezan_product_id"),
                    "sku": reservation.get("sku"),
                    "reservation_id": reservation.get("id"),
                })
                row["allocated_quantity"] += int(
                    float(allocation.get("quantity") or 0)
                )
        sources = []
        for source_key in sorted(source_map):
            row = source_map[source_key]
            prior_qty = prior_by_source.get((item_id, source_key), 0)
            remaining_source = max(
                0,
                int(row["allocated_quantity"]) - prior_qty,
            )
            source_item = None
            source_reservations = reservations_by_item.get(item_id, [])
            for reservation in source_reservations:
                for allocation in reservation.get("allocations") or []:
                    if _restock_source_target_key(allocation) == source_key:
                        source_item = await _source_inventory_item(
                            db,
                            user_id=user_id,
                            allocation=allocation,
                        )
                        break
                if source_item:
                    break
            configuration_key = (
                clean_text((source_item or {}).get("configuration_key"))
                or clean_text(row.get("configuration_key"))
            )
            compatible_locations = await _permanent_restock_locations(
                db,
                user_id=user_id,
                configuration_key=configuration_key,
                quantity=max(1, min(remaining_sellable, remaining_source)),
            )
            sources.append({
                **row,
                "configuration_key": configuration_key or None,
                "preparation_state": (
                    (source_item or {}).get("preparation_state")
                    or "ready_complete"
                ),
                "specifications": (
                    (source_item or {}).get("specifications") or {}
                ),
                "salla_variant_id": (
                    (source_item or {}).get("salla_variant_id")
                ),
                "product_name": (
                    (source_item or {}).get("product_name")
                    or selected.get("name")
                ),
                "already_restocked_quantity": prior_qty,
                "remaining_source_quantity": remaining_source,
                "compatible_locations": compatible_locations,
            })
        items.append({
            "order_item_id": item_id,
            "name": selected.get("name"),
            "sku": selected.get("sku"),
            "sellable_quantity": sellable,
            "already_restocked_quantity": restocked,
            "remaining_sellable_quantity": remaining_sellable,
            "sources": sources,
            "source_evidence_complete": (
                sum(int(row["allocated_quantity"]) for row in sources)
                >= sellable
            ),
        })
    return {
        "case_id": case.get("id"),
        "order_number": case.get("order_number"),
        "version": int(case.get("version") or 0),
        "inventory_gate": gate,
        "items": items,
        "complete": all(
            int(row["remaining_sellable_quantity"]) == 0
            for row in items
        ),
    }


async def restock_return_inventory(
    db: Any,
    *,
    user_id: str,
    user: dict[str, Any],
    case_id: str,
    request: ReturnRestockRequest,
) -> dict[str, Any]:
    await ensure_return_indexes(db)
    request_key = clean_text(request.request_id)
    facts = {
        "case_id": clean_text(case_id),
        "order_item_id": clean_text(request.order_item_id),
        "source_target_key": clean_text(request.source_target_key),
        "quantity": int(request.quantity),
        "location_id": clean_text(request.location_id),
        "scanned_barcode": clean_text(request.scanned_barcode).upper(),
        "employee_note": clean_text(request.employee_note),
    }
    existing_request = await db[RETURN_RESTOCK_REQUESTS].find_one(
        {"user_id": user_id, "request_id": request_key},
        {"_id": 0},
    )
    if existing_request:
        if existing_request.get("facts") != facts:
            raise RuntimeError("return_restock_request_conflict")
        if existing_request.get("status") == "posted":
            return {**existing_request, "duplicate": True}

    options = await get_return_restock_options(
        db,
        user_id=user_id,
        case_id=case_id,
    )
    item = next(
        (
            row for row in options["items"]
            if row["order_item_id"] == request.order_item_id
        ),
        None,
    )
    if not item:
        raise ValueError("return_restock_item_not_found")
    source = next(
        (
            row for row in item["sources"]
            if row["source_target_key"] == request.source_target_key
        ),
        None,
    )
    if not source:
        raise ValueError("return_restock_source_not_found")
    if request.quantity > int(item["remaining_sellable_quantity"]):
        raise ValueError("return_restock_exceeds_sellable_quantity")
    if request.quantity > int(source["remaining_source_quantity"]):
        raise ValueError("return_restock_exceeds_source_quantity")
    location = next(
        (
            row for row in source["compatible_locations"]
            if clean_text(row.get("id")) == request.location_id
        ),
        None,
    )
    if not location:
        raise ValueError("return_restock_location_not_compatible")
    expected_barcode = clean_text(
        location.get("barcode_value") or location.get("code")
    ).upper()
    if expected_barcode != facts["scanned_barcode"]:
        raise ValueError("return_restock_location_barcode_mismatch")

    case = await db.return_cases.find_one(
        {"user_id": user_id, "id": clean_text(case_id)},
        {"_id": 0},
    )
    if not case:
        raise LookupError("return_case_not_found")
    if int(case.get("version") or 0) != request.expected_version:
        if not (
            existing_request
            and case.get("return_restock_active_request_id") == request_key
        ):
            raise RuntimeError("version_conflict")

    now = utc_now()
    actor = _actor(user)
    if not existing_request:
        prepared = {
            "user_id": user_id,
            "request_id": request_key,
            "return_case_id": clean_text(case_id),
            "order_number": case.get("order_number"),
            "status": "prepared",
            "facts": facts,
            "created_at": now,
            "created_by": actor,
        }
        try:
            await db[RETURN_RESTOCK_REQUESTS].insert_one(prepared)
        except DuplicateKeyError:
            existing_request = await db[RETURN_RESTOCK_REQUESTS].find_one(
                {"user_id": user_id, "request_id": request_key},
                {"_id": 0},
            )
            if not existing_request or existing_request.get("facts") != facts:
                raise RuntimeError("return_restock_request_conflict")

    if case.get("return_restock_active_request_id") != request_key:
        claimed = await db.return_cases.find_one_and_update(
            {
                "user_id": user_id,
                "id": clean_text(case_id),
                "version": request.expected_version,
                "status": "inspected",
                "execution_gates.inventory": {
                    "$in": [
                        "ready_for_sellable_quantity_movement",
                        "restock_in_progress",
                    ]
                },
                "$or": [
                    {"return_restock_active_request_id": {"$exists": False}},
                    {"return_restock_active_request_id": None},
                    {"return_restock_active_request_id": ""},
                ],
            },
            {
                "$set": {
                    "execution_gates.inventory": "restock_posting",
                    "return_restock_active_request_id": request_key,
                    "updated_at": now,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            raise RuntimeError("version_conflict")
        case = claimed

    await db[RETURN_RESTOCK_REQUESTS].update_one(
        {"user_id": user_id, "request_id": request_key},
        {"$set": {
            "status": "posting",
            "claimed_case_version": case.get("version"),
            "updated_at": now,
        }},
    )

    receipt_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"mezan-return-restock:{user_id}:{request_key}",
    ))
    inventory_item = {
        "receipt_id": receipt_id,
        "product_id": source.get("product_id"),
        "mezan_product_id": source.get("mezan_product_id"),
        "salla_variant_id": source.get("salla_variant_id"),
        "product_name": source.get("product_name"),
        "sku": source.get("sku"),
        "quantity": int(request.quantity),
        "preparation_state": "ready_complete",
        "specifications": source.get("specifications") or {},
        "configuration_key": source.get("configuration_key"),
        "lot_id": f"return:{case_id}:{request.order_item_id}:{receipt_id}",
        "source_type": "customer_return",
        "source_id": clean_text(case_id),
        "source_line_id": request.order_item_id,
        "return_source_target_key": request.source_target_key,
        "original_receipt_id": source.get("receipt_id"),
        "original_lot_id": source.get("lot_id"),
        "placed_at": now,
        "placed_by": clean_text(actor.get("id")),
    }
    try:
        duplicate_inventory = await place_inventory_receipt(
            db,
            merchant_id=user_id,
            location_id=request.location_id,
            receipt_id=receipt_id,
            inventory_item=inventory_item,
            quantity=int(request.quantity),
            scanned_barcode=facts["scanned_barcode"],
            occurred_at=now,
        )
    except InventoryLocationCapacityError as exc:
        await db.return_cases.update_one(
            {
                "user_id": user_id,
                "id": clean_text(case_id),
                "return_restock_active_request_id": request_key,
            },
            {"$set": {
                "execution_gates.inventory": "restock_in_progress",
                "return_restock_active_request_id": None,
                "updated_at": utc_now(),
            }},
        )
        await db[RETURN_RESTOCK_REQUESTS].update_one(
            {"user_id": user_id, "request_id": request_key},
            {"$set": {
                "status": "failed",
                "failure_code": "inventory_location_capacity_exceeded",
                "updated_at": utc_now(),
            }},
        )
        raise RuntimeError("inventory_location_capacity_exceeded") from exc

    restock_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"mezan-return-restock-event:{user_id}:{request_key}",
    ))
    restock = {
        "id": restock_id,
        "user_id": user_id,
        "status": "posted",
        "return_case_id": clean_text(case_id),
        "order_number": case.get("order_number"),
        "order_item_id": request.order_item_id,
        "source_target_key": request.source_target_key,
        "source_receipt_id": source.get("receipt_id"),
        "source_lot_id": source.get("lot_id"),
        "source_configuration_key": source.get("configuration_key"),
        "quantity": int(request.quantity),
        "inventory_receipt_id": receipt_id,
        "restock_lot_id": inventory_item["lot_id"],
        "destination_location_id": request.location_id,
        "destination_location_code": location.get("code"),
        "destination_warehouse_id": location.get("warehouse_id"),
        "location_scan_verified": True,
        "employee_note": request.employee_note,
        "restocked_at": now,
        "restocked_by": actor,
        "accounting_status": "waiting_cogs_reversal",
        "created_at": now,
        "updated_at": now,
    }
    await db[RETURN_RESTOCKS].replace_one(
        {"user_id": user_id, "id": restock_id},
        restock,
        upsert=True,
    )
    await db[RETURN_RESTOCK_REQUESTS].update_one(
        {"user_id": user_id, "request_id": request_key},
        {"$set": {
            "status": "posted",
            "restock_id": restock_id,
            "inventory_receipt_id": receipt_id,
            "duplicate_inventory_receipt": bool(duplicate_inventory),
            "posted_at": now,
            "updated_at": now,
        }},
    )

    all_restocked = await db[RETURN_RESTOCKS].find(
        {
            "user_id": user_id,
            "return_case_id": clean_text(case_id),
            "status": "posted",
        },
        {"_id": 0, "order_item_id": 1, "quantity": 1},
    ).to_list(10000)
    restocked_by_item: dict[str, int] = {}
    for row in all_restocked:
        key = clean_text(row.get("order_item_id"))
        restocked_by_item[key] = (
            restocked_by_item.get(key, 0)
            + int(row.get("quantity") or 0)
        )
    inspection_items = (
        (case.get("inspection") or {}).get("items") or []
    )
    complete = all(
        restocked_by_item.get(clean_text(row.get("order_item_id")), 0)
        >= int(row.get("sellable_quantity") or 0)
        for row in inspection_items
    )
    next_gate = (
        "restocked_sellable_inventory"
        if complete
        else "restock_in_progress"
    )
    finalized_at = utc_now()
    await db.return_cases.update_one(
        {
            "user_id": user_id,
            "id": clean_text(case_id),
            "return_restock_active_request_id": request_key,
        },
        {
            "$set": {
                "execution_gates.inventory": next_gate,
                "return_restock_active_request_id": None,
                "return_restock_last_at": finalized_at,
                "updated_at": finalized_at,
            },
            "$push": {
                "events": {
                    "type": "return_inventory_restocked",
                    "at": finalized_at,
                    "actor": actor,
                    "version": case.get("version"),
                    "restock_id": restock_id,
                    "order_item_id": request.order_item_id,
                    "source_target_key": request.source_target_key,
                    "quantity": int(request.quantity),
                    "inventory_receipt_id": receipt_id,
                    "destination_location_id": request.location_id,
                }
            },
        },
    )
    return {
        **restock,
        "duplicate": bool(existing_request),
        "inventory_gate_after": next_gate,
        "case_version": case.get("version"),
    }


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
    order = await db.unified_orders.find_one(
        {
            "user_id": {"$in": [user_id, "main"]},
            "order_number": order_number,
        },
    )
    if not order:
        raise LookupError("order_not_found")

    # The request selects Mezan's immutable order-item identities. Never use
    # Salla return-shipment packages for this validation because Salla may
    # repeat all lines of the original order in a partial-return AWB.
    from order_item_engine.mapper import map_order_item_identities
    from order_engine.mapper import map_salla_order

    raw_by_source = order.get("raw_by_source") or {}
    raw_salla_order = (
        raw_by_source.get("salla_direct")
        if isinstance(raw_by_source, dict)
        else None
    )
    if not isinstance(raw_salla_order, dict):
        raise ValueError("canonical_order_snapshot_missing")
    canonical_order = map_salla_order(raw_salla_order)
    canonical_items = {
        item.order_item_id: item
        for item in map_order_item_identities(canonical_order)
    }
    for selected_item in request.items:
        canonical_item = canonical_items.get(selected_item.order_item_id)
        if canonical_item is None:
            raise ValueError("return_item_not_in_order")
        ordered_quantity = int(canonical_item.quantity)
        if selected_item.quantity_return > ordered_quantity:
            raise ValueError("return_quantity_exceeds_ordered")

        # Client-provided commercial values are never allowed to replace the
        # historical order snapshot used by accounting and future AI.
        selected_item.quantity_ordered = ordered_quantity
        selected_item.product_id = canonical_item.product_id
        selected_item.sku = canonical_item.sku
        selected_item.name = canonical_item.name
        selected_item.unit_sale_amount = canonical_item.unit_price
        selected_item.unit_tax_amount = (
            canonical_item.tax_reported_by_source / ordered_quantity
            if ordered_quantity
            else 0.0
        )

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
        return_document=ReturnDocument.AFTER,
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
    inspection_item_ids = {item.order_item_id for item in request.items}
    if inspection_item_ids != set(selected_quantities):
        raise ValueError("inspection_items_mismatch")
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
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise RuntimeError("version_conflict")
    return _public_doc(result)
