"""Privacy-safe operational context derived from the canonical Orders V2 model.

This module gives Mezan AI the same commercial order facts used by the Orders
V2 UI while removing direct customer identifiers and provider secrets. It does
not pre-label fields as tax, shipping, discount, or profit. The model receives
canonical paths and bounded values, then infers the commercial relationships.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from order_engine.repository import MongoOrderRepository, OrderRepository
from order_engine.service import get_order, list_orders
from order_item_engine.repository import OrderEngineItemRepository
from order_item_engine.service import OrderItemService

DEFAULT_ORDER_SAMPLE_LIMIT = 8
MAX_ORDER_SAMPLE_LIMIT = 12
MAX_ITEMS_PER_ORDER = 12
MAX_OBSERVED_PATHS = 500

OrderListLoader = Callable[..., Awaitable[Any]]
OrderDetailLoader = Callable[..., Awaitable[Any]]


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return [_model_dump(item) for item in value]
    return value


def _pick(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: source.get(key) for key in keys if key in source}


def _safe_source(source: Any) -> dict[str, Any]:
    raw = _model_dump(source) or {}
    return _pick(
        raw,
        (
            "provider",
            "source_event",
            "fetched_at",
            "received_at",
            "source",
            "channel",
            "platform",
            "source_native",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "campaign_id",
            "campaign_name",
            "device",
        ),
    )


def _safe_payment(payment: Any) -> dict[str, Any]:
    raw = _model_dump(payment) or {}
    return _pick(
        raw,
        (
            "method",
            "method_native",
            "status",
            "paid_amount",
            "remaining_amount",
            "has_remaining_amount",
            "collection_status",
            "receiving_bank_code",
            "receiving_bank_name",
            "paid_at",
            "card_brand",
        ),
    )


def _safe_shipping(shipping: Any) -> dict[str, Any]:
    raw = _model_dump(shipping) or {}
    return _pick(
        raw,
        (
            "company",
            "company_code",
            "method",
            "status",
            "shipped_at",
            "delivered_at",
        ),
    )


def _safe_item(item: Any, fallback_index: int) -> dict[str, Any]:
    raw = _model_dump(item) or {}
    option_names = []
    for option in raw.get("options") or raw.get("options_raw") or []:
        option_raw = _model_dump(option) or {}
        name = option_raw.get("name") or option_raw.get("label") or option_raw.get("title")
        if name:
            option_names.append(str(name)[:80])

    custom_field_names = []
    for field in raw.get("custom_fields") or []:
        field_raw = _model_dump(field) or {}
        name = field_raw.get("name") or field_raw.get("label") or field_raw.get("title")
        if name:
            custom_field_names.append(str(name)[:80])

    safe = _pick(
        raw,
        (
            "schema_version",
            "line_index",
            "product_id",
            "parent_product_id",
            "variant_id",
            "sku",
            "barcode",
            "name",
            "quantity",
            "color",
            "size",
            "material",
            "currency",
            "unit_price",
            "discount",
            "tax_reported_by_source",
            "total",
            "weight",
            "weight_unit",
            "preparation_status",
            "availability_status",
            "fulfillment_source",
        ),
    )
    safe["line_index"] = int(safe.get("line_index", fallback_index) or fallback_index)
    if option_names:
        safe["option_names"] = option_names[:20]
    if custom_field_names:
        safe["custom_field_names"] = custom_field_names[:20]
    return safe


def _safe_order(order: Any, items: list[Any], sample_index: int) -> dict[str, Any]:
    raw = _model_dump(order) or {}
    totals = _model_dump(raw.get("totals")) or {}
    safe_items = [
        _safe_item(item, index)
        for index, item in enumerate(items[:MAX_ITEMS_PER_ORDER])
    ]
    return {
        "sample_id": f"order_sample_{sample_index:02d}",
        "schema_version": raw.get("schema_version"),
        "created_at": raw.get("created_at"),
        "status": raw.get("status"),
        "status_native": raw.get("status_native"),
        "is_new": raw.get("is_new"),
        "is_gift": raw.get("is_gift"),
        "completed_at": raw.get("completed_at"),
        "cancelled_at": raw.get("cancelled_at"),
        "refunded_at": raw.get("refunded_at"),
        "source": _safe_source(raw.get("source")),
        "payment": _safe_payment(raw.get("payment")),
        "shipping": _safe_shipping(raw.get("shipping")),
        "totals": totals,
        "total_weight": raw.get("total_weight"),
        "total_weight_unit": raw.get("total_weight_unit"),
        "item_count": len(items),
        "items_truncated": len(items) > MAX_ITEMS_PER_ORDER,
        "items": safe_items,
    }


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _collect_paths(value: Any, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(f"{path}<{_value_type(child)}>")
            paths.update(_collect_paths(child, path))
    elif isinstance(value, list):
        list_path = f"{prefix}[]" if prefix else "[]"
        for child in value[:3]:
            paths.update(_collect_paths(child, list_path))
    return paths


def _default_item_service(db: Any) -> OrderItemService:
    order_repository = MongoOrderRepository(db)
    item_repository = OrderEngineItemRepository(order_repository)
    return OrderItemService(item_repository)


async def build_orders_v2_operational_context(
    db: Any,
    *,
    user_id: str,
    sample_limit: int = DEFAULT_ORDER_SAMPLE_LIMIT,
    repository_factory: Callable[[Any], OrderRepository] = MongoOrderRepository,
    item_service_factory: Callable[[Any], OrderItemService] = _default_item_service,
    list_loader: OrderListLoader = list_orders,
    detail_loader: OrderDetailLoader = get_order,
) -> dict[str, Any]:
    """Build a bounded Orders V2 context without direct customer identifiers."""

    normalized_limit = max(1, min(int(sample_limit), MAX_ORDER_SAMPLE_LIMIT))
    repository = repository_factory(db)
    item_service = item_service_factory(db)
    page = await list_loader(
        repository,
        user_id=str(user_id),
        limit=normalized_limit,
        cursor=None,
        status_group=None,
        status_exact=None,
    )

    samples: list[dict[str, Any]] = []
    sample_errors: list[dict[str, str]] = []
    for index, listed_order in enumerate(page.items, start=1):
        order_number = str(getattr(listed_order, "order_number", "") or "").strip()
        if not order_number:
            sample_errors.append({"sample_id": f"order_sample_{index:02d}", "code": "missing_order_number"})
            continue
        try:
            detail = await detail_loader(
                repository,
                user_id=str(user_id),
                order_number=order_number,
            )
            items = await item_service.get_items_for_order(
                user_id=str(user_id),
                order_number=order_number,
            )
            if not items:
                items = list(getattr(detail, "items", []) or [])
            samples.append(_safe_order(detail, list(items), index))
        except Exception as exc:  # noqa: BLE001
            sample_errors.append(
                {
                    "sample_id": f"order_sample_{index:02d}",
                    "code": type(exc).__name__,
                }
            )

    observed_paths: set[str] = set()
    for sample in samples:
        observed_paths.update(_collect_paths(sample))

    return {
        "ok": True,
        "contract_version": 2,
        "source": "mezan_orders_v2_canonical",
        "discovery_mode": True,
        "precomputed_business_conclusions": False,
        "read_only": True,
        "privacy": {
            "direct_customer_identifiers_removed": True,
            "order_numbers_pseudonymized": True,
            "addresses_removed": True,
            "payment_and_shipping_references_removed": True,
            "personalized_option_values_removed": True,
        },
        "sample_selection": "latest_canonical_orders_v2",
        "requested_sample_limit": normalized_limit,
        "sample_count": len(samples),
        "skipped_invalid_orders": int(getattr(page, "skipped_invalid", 0) or 0),
        "sample_errors": sample_errors,
        "observed_paths": sorted(observed_paths)[:MAX_OBSERVED_PATHS],
        "orders": samples,
        "analysis_directives": [
            "Infer commercial concepts from the observed canonical paths and values.",
            "Do not assume a concept is missing because one expected field name is absent.",
            "Compare order totals with line-item values before concluding that a value is missing or inconsistent.",
            "For every important conclusion, cite the discovered paths and sample values or relationships.",
            "Treat this context as evidence only; do not claim that any write or external action was performed.",
        ],
    }
