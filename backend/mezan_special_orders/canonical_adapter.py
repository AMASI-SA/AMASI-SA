"""Mezan-owned aggregates -> the existing canonical Order/Item Engine contract.

No provider-shaped payload is fabricated. Financial values remain integer minor
units in storage; conversion to float happens only at the existing display DTO.
This module reads snapshots only: no Salla refresh, ledger write or state change.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import re
from typing import Any

from .contracts import EXPONENTS, FxSnapshot, Product, OptionValue
from .domain import DomainError, balances, digest, policy, source_snapshot, validated_options

STATUS = {
    "pending_review": ("under_review", "بإنتظار المراجعة"),
    "reviewed": ("reviewed", "تمت المراجعة"),
    "processing": ("processing", "قيد التنفيذ"),
    "ready_to_ship": ("processing", "قيد التنفيذ"),
    "completed": ("completed", "تم التنفيذ"),
    "delivering": ("shipping", "جاري التوصيل"),
    "delivered": ("completed", "تم التوصيل"),
    "cancelled": ("cancelled", "ملغي"),
    "refunded": ("refunded", "مسترجع"),
}


def is_local_order_number(number: str) -> bool:
    return isinstance(number, str) and re.fullmatch(r"MZ-[0-9A-F]{32}", number) is not None


def raw_options(rows: list[dict]) -> list[dict]:
    """Preserve one display row per option and each selected value's identity."""
    output = []
    for row in rows:
        value = deepcopy(row.get("values")) if isinstance(row["value"], list) and row.get("values") else deepcopy(row["value"])
        output.append({"name": row["name"], "value": value,
                       **({"option_id": row["option_id"]} if row.get("option_id") else {}),
                       **({"value_id": row["value_id"]} if row.get("value_id") else {})})
    return output


def display_option_value(value):
    if isinstance(value, list):
        return " / ".join(str(v.get("name") or v.get("value")) if isinstance(v, dict) else str(v) for v in value)
    return str(value)


def to_canonical_order(document: dict, *, tenant_id: str):
    from order_engine.models import (AddressDTO, CustomerDTO, MoneyTotalsDTO, OrderDTO,
                                     OrderItemDTO, OrderSourceDTO, PaymentDTO, ShippingDTO)
    if document.get("tenant_id") != tenant_id or document.get("provider") != "mezan":
        raise DomainError("local_order_scope_mismatch", 403)
    if not is_local_order_number(document.get("order_number")):
        raise DomainError("local_order_identity_invalid")
    if digest(source_snapshot(document)) != document.get("snapshot_digest"):
        raise DomainError("local_order_snapshot_mismatch")
    original = document.get("original")
    purpose = document["purpose"]
    expected_policy = policy(purpose, document["policy"]["expense_bucket"])
    if document["policy"] != expected_policy:
        raise DomainError("local_order_policy_invalid")
    state = STATUS.get(document["stage"])
    if state is None:
        raise DomainError("local_order_stage_invalid")
    fx = FxSnapshot.model_validate(document["fx"])
    divisor = Decimal(10) ** EXPONENTS[fx.currency]
    def major(value: int) -> float:
        if type(value) is not int or value < 0:
            raise DomainError("local_order_money_invalid")
        return float(Decimal(value) / divisor)
    customer = document["recipient"]
    address = AddressDTO.model_validate(customer["address"]) if customer.get("address") else None
    lines = []
    for item in document["items"]:
        product = Product.model_validate(item["product"])
        if product.tenant_id != tenant_id:
            raise DomainError("local_product_scope_mismatch", 403)
        values = tuple(OptionValue(key=r["key"], value=r["value"]) for r in item["options"])
        # Required choices and variant membership are checked again at the read boundary.
        options = raw_options(validated_options(product, values))
        quantity = item["quantity"]
        if type(quantity) is not int or not 1 <= quantity <= 999:
            raise DomainError("local_order_quantity_invalid")
        by_label = {r["name"]: display_option_value(r["value"]) for r in options}
        lines.append(OrderItemDTO(order_item_id=item["order_item_id"], source_item_id=None,
            product_id=product.product_id, parent_product_id=product.product_id,
            variant_id=product.variant_id, sku=product.sku, barcode=product.barcode,
            name=product.name, quantity=quantity, image_url=product.image_url,
            image_urls=list(product.image_urls), options_raw=options, options_normalized=by_label,
            # The existing inventory helper reads custom_fields on the item
            # identity DTO; the shared mapper deduplicates equivalent options.
            custom_fields=[{"name": name, "value": value} for name, value in by_label.items()],
            unit_price=major(item["customer_charge_minor"]) / quantity,
            total=major(item["customer_charge_minor"]),
            size=by_label.get("المقاس") or by_label.get("المقاسات"),
            color=by_label.get("اللون"), material=by_label.get("الخامة")))
    balance = balances(document)
    delivery = document["delivery"]
    cod = document["collection"]["cod_minor"] > 0
    transfer = document["collection"]["bank_transfer_minor"] > 0
    free = document["customer_agreed_minor"] == 0
    bank_and_cod = cod and transfer
    return OrderDTO(order_id=document["order_id"], order_number=document["order_number"],
        created_at=document["created_at"], status=state[0], status_native=state[1],
        is_new=document["stage"] == "pending_review", is_gift=purpose == "gift",
        order_purpose=purpose, special_order_id=document["order_id"],
        original_order_number=original["order_number"] if original else None,
        source=OrderSourceDTO(provider="mezan", source_order_id=None, source="mezan",
                             source_native="طلب ميزان", match_status="unattributed",
                             unmatched_reason="non_sales_operational_order"),
        customer=CustomerDTO(name=customer["name"], mobile=customer["mobile"], shipping_address=address),
        shipping=ShippingDTO(method="store_courier" if delivery["method"] == "courier" else "carrier",
            company="مندوب المتجر" if delivery["method"] == "courier" else delivery["carrier_key"],
            company_code="store_courier" if delivery["method"] == "courier" else delivery["carrier_key"],
            tracking_number=delivery.get("tracking_number"), address=address,
            recipient={"name": customer["name"], "mobile": customer["mobile"]}),
        payment=PaymentDTO(method="free" if free else "cod" if cod else "bank_transfer",
            method_native="مجاني" if free else "تحويل بنكي + عند الاستلام" if bank_and_cod else "عند الاستلام" if cod else "تحويل بنكي",
            status="not_required" if free else "paid" if balance["remaining_minor"] == 0 else "pending",
            paid_amount=major(balance["net_collected_minor"]), remaining_amount=major(balance["remaining_minor"]),
            has_remaining_amount=balance["remaining_minor"] > 0,
            collection_status="paid" if balance["remaining_minor"] == 0 else "partial" if balance["net_collected_minor"] else "unpaid"),
        totals=MoneyTotalsDTO(currency=fx.currency, accounting_currency="SAR",
            total_sar=float(Decimal(document["customer_agreed_sar_minor"]) / 100),
            exchange_rate_to_sar=fx.rate_to_sar, conversion_status="verified_snapshot",
            conversion_source=fx.evidence_id, subtotal=major(sum(i["customer_charge_minor"] for i in document["items"])),
            options=major(sum(s["customer_charge_minor"] for s in document["services"])),
            shipping=major(delivery["customer_charge_minor"]), total=major(document["customer_agreed_minor"])),
        items=lines, staff_notes=document["reason"], tags=[document["badge"], "mezan:" + purpose])


class LocalOrderReader:
    """Read the source snapshot with the existing shared workflow's current stage.

    Queue membership never depends on an asynchronous copy of workflow state.
    Source identity/options remain local-owned and immutable after review freeze.
    """
    def __init__(self, db: Any):
        from .repository import COLLECTION
        self.collection = db[COLLECTION]

    @staticmethod
    def discovery_pipeline(user_id: str, *, order_numbers=None, before_order_date=None,
                           before_order_number=None, status_group=None, status_exact=None):
        query: dict = {"tenant_id": str(user_id), "provider": "mezan"}
        if order_numbers is not None:
            query["order_number"] = {"$in": list(dict.fromkeys(order_numbers))}
        if before_order_date and before_order_number:
            query["$or"] = [{"created_at": {"$lt": before_order_date}},
                {"created_at": before_order_date, "order_number": {"$lt": before_order_number}}]
        pipeline = [{"$match": query}, {"$lookup": {
            "from": "order_review_workflows",
            "let": {"tenant": "$tenant_id", "number": "$order_number"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$user_id", "$$tenant"]}, {"$eq": ["$order_number", "$$number"]},
            ]}}}, {"$limit": 1}, {"$project": {"_id": 0, "order_id": 1, "source_provider": 1,
                "special_source_digest": 1, "stage": 1}}], "as": "__shared_workflows",
        }}, {"$addFields": {"__workflow": {"$arrayElemAt": ["$__shared_workflows", 0]},
            "__effective_stage": {"$ifNull": [{"$arrayElemAt": ["$__shared_workflows.stage", 0]}, "$stage"]}}},
            {"$addFields": {"stage": {"$cond": [{"$eq": ["$__effective_stage", "in_progress"]}, "processing", "$__effective_stage"]}}}]
        if status_exact:
            key = " ".join(status_exact.replace("_", " ").casefold().split())
            stages = [stage for stage, (group, label) in STATUS.items() if key in {label.casefold(), group.replace("_", " "), stage.replace("_", " ")}]
            if key in {"بانتظار المراجعة", "انتظار المراجعة", "waiting review", "pending review"}:
                stages.append("pending_review")
            if key == "تم المراجعة":
                stages.append("reviewed")
            pipeline.append({"$match": {"stage": {"$in": list(set(stages))}}})
        elif status_group:
            pipeline.append({"$match": {"stage": {"$in": [stage for stage, (group, _) in STATUS.items() if group == status_group]}}})
        pipeline.append({"$project": {"command_log": 0, "outbox": 0, "costs": 0, "financial_uses": 0}})
        return pipeline

    @staticmethod
    def checked(row: dict) -> dict:
        row = deepcopy(row)
        workflow = row.pop("__workflow", None)
        row.pop("__shared_workflows", None)
        row.pop("__effective_stage", None)
        row.pop("_id", None)
        if workflow:
            if workflow.get("order_id") != row["order_id"] or workflow.get("source_provider", "mezan") != "mezan":
                raise DomainError("shared_workflow_source_identity_mismatch")
            if row["stage"] not in {"pending_review", "cancelled"} and not row["source_frozen"]:
                raise DomainError("shared_workflow_source_not_frozen")
            if workflow.get("special_source_digest") and workflow["special_source_digest"] != row["snapshot_digest"]:
                raise DomainError("shared_workflow_snapshot_mismatch")
        return row

    async def get(self, *, user_id: str, order_number: str) -> dict | None:
        rows = await self.get_many(user_id=user_id, order_numbers=[order_number])
        return rows[0] if rows else None

    async def get_many(self, *, user_id: str, order_numbers: list[str]) -> list[dict]:
        if not order_numbers:
            return []
        pipeline = self.discovery_pipeline(user_id, order_numbers=order_numbers)
        rows = await self.collection.aggregate(pipeline).to_list(len(order_numbers))
        return [self.checked(row) for row in rows]

    async def list(self, *, user_id: str, limit: int, before_order_date: str | None = None,
                   before_order_number: str | None = None, status_group: str | None = None,
                   status_exact: str | None = None) -> list[dict]:
        pipeline = self.discovery_pipeline(user_id, before_order_date=before_order_date,
            before_order_number=before_order_number, status_group=status_group, status_exact=status_exact)
        pipeline.extend([{"$sort": {"created_at": -1, "order_number": -1}}, {"$limit": limit}])
        rows = await self.collection.aggregate(pipeline).to_list(limit)
        return [self.checked(row) for row in rows]
