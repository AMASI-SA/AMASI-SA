"""Deterministic Product V2 fulfillment rules.

This module is intentionally free of database and provider I/O.  Product and
order routes pass verified Mezan V2 facts into these functions so the same
decision can be tested, audited and replayed.
"""
from __future__ import annotations

from typing import Any


PRODUCT_RESOURCE_BINDINGS = "mezan_product_resource_bindings_v2"
PRODUCT_OPERATION_PROFILES = "mezan_product_operation_profiles_v2"
FULFILLMENT_DECISIONS = "mezan_fulfillment_decisions_v2"

FULFILLMENT_TYPE_INSTANT = "instant"
FULFILLMENT_TYPE_PREPARATION = "requires_preparation"
FULFILLMENT_TYPES = {
    FULFILLMENT_TYPE_INSTANT,
    FULFILLMENT_TYPE_PREPARATION,
}
INVENTORY_POLICY_BRANCH_STOCK = "branch_stock_required"
INVENTORY_POLICY_FINISHED_GOODS_UNLIMITED = (
    "finished_goods_inventory_not_tracked"
)
INVENTORY_POLICIES = {
    INVENTORY_POLICY_BRANCH_STOCK,
    INVENTORY_POLICY_FINISHED_GOODS_UNLIMITED,
}
STOCKOUT_POLICY_CLOSE = "close_when_out_of_stock"
STOCKOUT_POLICY_PREORDER = "allow_preorder"
STOCKOUT_POLICIES = {
    STOCKOUT_POLICY_CLOSE,
    STOCKOUT_POLICY_PREORDER,
}
DEFAULT_LOW_STOCK_THRESHOLD = 3

_COD_MARKERS = {
    "cod",
    "cash on delivery",
    "الدفع عند الاستلام",
    "دفع عند الاستلام",
}
_PAYMENT_BLOCKERS = {
    "awaiting payment",
    "pending",
    "unpaid",
    "partial",
    "unknown",
    "بانتظار الدفع",
    "في انتظار الدفع",
}
_PAYMENT_ALLOWED = {
    "paid",
    "completed",
    "complete",
    "collected",
    "success",
    "successful",
    "تم الدفع",
    "مدفوع",
}
_ORDER_BLOCKERS = {
    "cancelled",
    "canceled",
    "cancel",
    "refunded",
    "on hold",
    "on_hold",
    "held",
    "ملغي",
    "ملغى",
    "معلق",
    "موقوف",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("_", " ").split())


def normalize_fulfillment_type(value: Any) -> str:
    normalized = _norm(value)
    aliases = {
        "instant": FULFILLMENT_TYPE_INSTANT,
        "instant shipping": FULFILLMENT_TYPE_INSTANT,
        "ready": FULFILLMENT_TYPE_INSTANT,
        "شحن فوري": FULFILLMENT_TYPE_INSTANT,
        "جاهز": FULFILLMENT_TYPE_INSTANT,
        "requires preparation": FULFILLMENT_TYPE_PREPARATION,
        "preparation": FULFILLMENT_TYPE_PREPARATION,
        "needs preparation": FULFILLMENT_TYPE_PREPARATION,
        "يحتاج تجهيز": FULFILLMENT_TYPE_PREPARATION,
        "تجهيز": FULFILLMENT_TYPE_PREPARATION,
    }
    result = aliases.get(normalized)
    if not result:
        raise ValueError("invalid_fulfillment_type")
    return result


def normalize_inventory_policy(value: Any) -> str:
    normalized = _norm(value)
    aliases = {
        "branch stock required": INVENTORY_POLICY_BRANCH_STOCK,
        "tracked": INVENTORY_POLICY_BRANCH_STOCK,
        "track inventory": INVENTORY_POLICY_BRANCH_STOCK,
        "يتتبع المخزون": INVENTORY_POLICY_BRANCH_STOCK,
        "مخزون الفروع": INVENTORY_POLICY_BRANCH_STOCK,
        (
            "finished goods inventory not tracked"
        ): INVENTORY_POLICY_FINISHED_GOODS_UNLIMITED,
        "not tracked": INVENTORY_POLICY_FINISHED_GOODS_UNLIMITED,
        "unlimited": INVENTORY_POLICY_FINISHED_GOODS_UNLIMITED,
        "لا يتتبع المخزون": INVENTORY_POLICY_FINISHED_GOODS_UNLIMITED,
    }
    result = aliases.get(normalized)
    if not result:
        raise ValueError("invalid_inventory_policy")
    return result


def normalize_stockout_policy(value: Any) -> str:
    normalized = _norm(value)
    aliases = {
        "close when out of stock": STOCKOUT_POLICY_CLOSE,
        "close": STOCKOUT_POLICY_CLOSE,
        "block": STOCKOUT_POLICY_CLOSE,
        "يغلق عند النفاد": STOCKOUT_POLICY_CLOSE,
        "إيقاف البيع": STOCKOUT_POLICY_CLOSE,
        "allow preorder": STOCKOUT_POLICY_PREORDER,
        "preorder": STOCKOUT_POLICY_PREORDER,
        "حجز مسبق": STOCKOUT_POLICY_PREORDER,
        "السماح بالحجز المسبق": STOCKOUT_POLICY_PREORDER,
    }
    result = aliases.get(normalized)
    if not result:
        raise ValueError("invalid_stockout_policy")
    return result


def normalize_low_stock_threshold(value: Any) -> int:
    try:
        threshold = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_low_stock_threshold") from exc
    if threshold < 0 or threshold > 100000:
        raise ValueError("invalid_low_stock_threshold")
    return threshold


def inventory_policy_details(value: Any) -> dict[str, Any]:
    """Return inventory behavior without deriving it from preparation.

    Products remain global to the merchant catalog. Only operational stock
    records carry a warehouse/branch, so this policy never returns or accepts
    a product-level warehouse identifier.
    """
    inventory_policy = normalize_inventory_policy(value)
    if inventory_policy == INVENTORY_POLICY_BRANCH_STOCK:
        return {
            "mode": INVENTORY_POLICY_BRANCH_STOCK,
            "requires_branch_inventory": True,
            "sell_without_finished_goods_inventory": False,
            "initial_salla_status": "out",
            "unlimited_quantity": False,
        }
    return {
        "mode": INVENTORY_POLICY_FINISHED_GOODS_UNLIMITED,
        "requires_branch_inventory": False,
        "sell_without_finished_goods_inventory": True,
        "initial_salla_status": "sale",
        "unlimited_quantity": True,
    }


def inventory_policy_for_fulfillment(value: Any) -> dict[str, Any]:
    """Infer the old coupled policy for legacy rows only.

    New writes must store inventory_policy explicitly. This compatibility
    helper keeps already-saved product profiles deterministic until they are
    edited under the independent inventory/preparation model.
    """
    fulfillment_type = normalize_fulfillment_type(value)
    mode = (
        INVENTORY_POLICY_BRANCH_STOCK
        if fulfillment_type == FULFILLMENT_TYPE_INSTANT
        else INVENTORY_POLICY_FINISHED_GOODS_UNLIMITED
    )
    return inventory_policy_details(mode)


def resource_requires_preparation(resource: dict[str, Any] | None) -> bool:
    """Only an explicit service rule forces preparation.

    Stock components describe the recipe/cost and do not force preparation on
    their own.  This preserves the merchant's rule that a component link alone
    is not proof that the product needs a preparation workflow.
    """
    resource = resource or {}
    kind = _norm(resource.get("kind"))
    return kind == "service" and resource.get("requires_preparation") is True


def classify_line_fulfillment(
    *,
    profile: dict[str, Any] | None,
    product_resources: list[dict[str, Any]] | None = None,
    selected_option_resources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve one order line after applying selected option/service rules."""
    profile = profile or {}
    explicit = profile.get("fulfillment_type") in FULFILLMENT_TYPES
    configured_type = (
        profile.get("fulfillment_type")
        if explicit
        else FULFILLMENT_TYPE_PREPARATION
    )
    explicit_inventory_policy = (
        profile.get("inventory_policy") in INVENTORY_POLICIES
    )
    inventory_policy = (
        profile.get("inventory_policy")
        if explicit_inventory_policy
        else inventory_policy_for_fulfillment(configured_type)["mode"]
    )
    inventory = inventory_policy_details(inventory_policy)
    stockout_policy = (
        profile.get("stockout_policy")
        if profile.get("stockout_policy") in STOCKOUT_POLICIES
        else STOCKOUT_POLICY_CLOSE
    )
    try:
        low_stock_threshold = normalize_low_stock_threshold(
            profile.get(
                "low_stock_threshold",
                DEFAULT_LOW_STOCK_THRESHOLD,
            )
        )
    except ValueError:
        low_stock_threshold = DEFAULT_LOW_STOCK_THRESHOLD
    resources = [
        *(product_resources or []),
        *(selected_option_resources or []),
    ]
    forcing_services = [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "source": row.get("_link_source"),
        }
        for row in resources
        if resource_requires_preparation(row)
    ]
    resolved = (
        FULFILLMENT_TYPE_PREPARATION
        if forcing_services
        else configured_type
    )
    return {
        "configured": explicit,
        "configured_type": configured_type,
        "resolved_type": resolved,
        "requires_preparation": resolved == FULFILLMENT_TYPE_PREPARATION,
        "inventory_policy": inventory["mode"],
        "inventory_policy_configured": explicit_inventory_policy,
        "requires_branch_inventory": inventory[
            "requires_branch_inventory"
        ],
        "sell_without_finished_goods_inventory": inventory[
            "sell_without_finished_goods_inventory"
        ],
        "stockout_policy": stockout_policy,
        "preorder_when_out_of_stock": (
            inventory["requires_branch_inventory"]
            and stockout_policy == STOCKOUT_POLICY_PREORDER
        ),
        "low_stock_threshold": low_stock_threshold,
        "forcing_services": forcing_services,
        "supplier_export_eligible": resolved == FULFILLMENT_TYPE_PREPARATION,
    }


def _is_cod(payment: Any) -> bool:
    method = _norm(getattr(payment, "method", None))
    native = _norm(getattr(payment, "method_native", None))
    return method in _COD_MARKERS or native in _COD_MARKERS


def payment_is_eligible(payment: Any) -> bool:
    if _is_cod(payment):
        return True
    values = {
        _norm(getattr(payment, "status", None)),
        _norm(getattr(payment, "collection_status", None)),
    }
    if any(value in _PAYMENT_BLOCKERS for value in values):
        return False
    paid_amount = float(getattr(payment, "paid_amount", 0) or 0)
    remaining = float(getattr(payment, "remaining_amount", 0) or 0)
    has_remaining = bool(getattr(payment, "has_remaining_amount", False))
    if has_remaining and remaining > 0 and paid_amount <= 0:
        return False
    if any(value in _PAYMENT_ALLOWED for value in values):
        return True
    return paid_amount > 0 and remaining <= 0 and not has_remaining


def shipping_address_is_complete(shipping: Any) -> bool:
    address = getattr(shipping, "address", None)
    if address is None:
        return False
    city = str(getattr(address, "city", None) or "").strip()
    location = next(
        (
            str(value).strip()
            for value in (
                getattr(address, "street", None),
                getattr(address, "formatted", None),
                getattr(address, "short_address", None),
                getattr(address, "district", None),
            )
            if str(value or "").strip()
        ),
        "",
    )
    return bool(city and location)


def order_is_active(order: Any) -> bool:
    values = {
        _norm(getattr(order, "status", None)),
        _norm(getattr(order, "status_native", None)),
    }
    values.discard("")
    if not values:
        return False
    return not any(
        value in _ORDER_BLOCKERS
        or any(marker and marker in value for marker in _ORDER_BLOCKERS)
        for value in values
    )


def evaluate_order_fulfillment(
    *,
    order: Any,
    lines: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the fail-closed routing decision for a canonical order."""
    direct_lines = [
        row for row in lines
        if row.get("resolved_type") == FULFILLMENT_TYPE_INSTANT
    ]
    preparation_lines = [
        row for row in lines
        if row.get("resolved_type") == FULFILLMENT_TYPE_PREPARATION
    ]
    inventory_tracked_lines = [
        row for row in lines
        if row.get("requires_branch_inventory") is True
    ]
    unconfigured = [row for row in lines if not row.get("configured")]
    blockers: list[str] = []
    if not lines:
        blockers.append("order_has_no_items")
    if unconfigured:
        blockers.append("product_fulfillment_type_missing")
    if not order_is_active(order):
        blockers.append("order_cancelled_or_held")
    if not payment_is_eligible(getattr(order, "payment", None)):
        blockers.append("payment_not_eligible")
    if not shipping_address_is_complete(getattr(order, "shipping", None)):
        blockers.append("shipping_address_incomplete")

    inventory_blocked = [
        row for row in inventory_tracked_lines
        if (
            row.get("inventory_available") is not True
            or not row.get("warehouse_ids")
        )
    ]
    preorder_blocked = [
        row for row in inventory_blocked
        if row.get("preorder_when_out_of_stock") is True
    ]
    strict_inventory_blocked = [
        row for row in inventory_blocked
        if row.get("preorder_when_out_of_stock") is not True
    ]
    if strict_inventory_blocked:
        blockers.append("operational_inventory_not_available")
    if preorder_blocked:
        blockers.append("preorder_waiting_for_stock")

    if direct_lines and preparation_lines:
        order_type = "mixed"
    elif preparation_lines:
        order_type = FULFILLMENT_TYPE_PREPARATION
    else:
        order_type = FULFILLMENT_TYPE_INSTANT

    ready = (
        order_type == FULFILLMENT_TYPE_INSTANT
        and not blockers
        and len(direct_lines) == len(lines)
    )
    warehouse_ids = sorted({
        str(warehouse_id)
        for row in lines
        for warehouse_id in (row.get("warehouse_ids") or [])
        if warehouse_id
    })
    return {
        "order_number": str(getattr(order, "order_number", "") or ""),
        "order_type": order_type,
        "route_stage": "ready_to_ship" if ready else "reviewed",
        "ready_to_ship": ready,
        "preparation_stages_required": not ready,
        "supplier_export_order_required": bool(preparation_lines),
        "instant_items_excluded_from_supplier_export": bool(direct_lines),
        "blockers": list(dict.fromkeys(blockers)),
        "preorder_required": bool(preorder_blocked),
        "preorder_line_ids": [
            row.get("order_item_id")
            for row in preorder_blocked
        ],
        "warehouse_ids": warehouse_ids,
        "warehouse_resolution_source": (
            "inventory_location"
            if warehouse_ids
            else (
                "inventory_location_missing"
                if inventory_tracked_lines
                else "not_required"
            )
        ),
        "lines": lines,
    }
