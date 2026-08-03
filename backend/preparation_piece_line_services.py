"""Line-granular service plans for preparation pieces.

The same Salla product may appear in one preparation file with different option
values. Product-only service-plan keys would let the last line overwrite the
others. This installer keeps inherited option services scoped to the exact
order item while reusing the durable piece builder.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from order_review_routes import _text
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES


_INSTALLED = False
_ORIGINAL_BUILD_PIECES = None


def preparation_line_service_key(line: dict[str, Any]) -> str:
    order_number = _text(line.get("order_number"))
    order_item_id = _text(line.get("order_item_id"))
    if order_number or order_item_id:
        return f"{order_number}|{order_item_id}"
    return "|".join((
        _text(line.get("group_key")),
        _text(line.get("product_id")),
        _text(line.get("line_number")),
    ))


def build_line_service_plans(
    *,
    batch: dict[str, Any],
    product_links: list[dict[str, Any]],
    option_bindings: list[dict[str, Any]],
    resources_by_id: dict[str, dict[str, Any]],
    inherit_services,
) -> dict[str, dict[str, Any]]:
    links_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bindings_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in product_links:
        links_by_product[_text(row.get("salla_product_id"))].append(row)
    for row in option_bindings:
        bindings_by_product[_text(row.get("salla_product_id"))].append(row)

    plans: dict[str, dict[str, Any]] = {}
    for line in batch.get("lines") or []:
        if not isinstance(line, dict):
            continue
        product_id = _text(line.get("product_id"))
        plans[preparation_line_service_key(line)] = {
            "product_id": product_id or None,
            "services": inherit_services(
                line=line,
                product_links=links_by_product.get(product_id, []),
                option_bindings=bindings_by_product.get(product_id, []),
                resources_by_id=resources_by_id,
            ),
        }
    return plans


async def _line_service_context_for_batch(
    db: Any,
    *,
    user_id: str,
    batch: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    import preparation_piece_operations as base

    product_ids = sorted({
        _text(line.get("product_id"))
        for line in batch.get("lines") or []
        if isinstance(line, dict) and _text(line.get("product_id"))
    })
    if not product_ids:
        return {}
    product_links = await db[PRODUCT_RESOURCE_BINDINGS].find(
        {"user_id": user_id, "salla_product_id": {"$in": product_ids}},
        {"_id": 0},
    ).to_list(length=10000)
    option_bindings = await db[BINDINGS].find(
        {
            "user_id": user_id,
            "salla_product_id": {"$in": product_ids},
            "mode": "resource",
        },
        {"_id": 0},
    ).to_list(length=20000)
    resource_ids = sorted({
        _text(row.get("resource_id"))
        for row in [*product_links, *option_bindings]
        if _text(row.get("resource_id"))
    })
    resources = (
        await db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": resource_ids}},
            {"_id": 0},
        ).to_list(length=max(1, len(resource_ids)))
        if resource_ids
        else []
    )
    resources_by_id = {
        _text(row.get("id")): row
        for row in resources
        if _text(row.get("id"))
    }
    return build_line_service_plans(
        batch=batch,
        product_links=product_links,
        option_bindings=option_bindings,
        resources_by_id=resources_by_id,
        inherit_services=base.inherit_required_services,
    )


def _build_piece_documents_by_line(
    *,
    user_id: str,
    registry: dict[str, Any],
    batch: dict[str, Any],
    services_by_product: dict[str, dict[str, Any]],
    assigned_at,
    duration_by_signature=None,
) -> list[dict[str, Any]]:
    """Delegate one line at a time so option services cannot cross lines."""
    assert _ORIGINAL_BUILD_PIECES is not None
    documents: list[dict[str, Any]] = []
    for line in batch.get("lines") or []:
        if not isinstance(line, dict):
            continue
        product_id = _text(line.get("product_id"))
        plan = services_by_product.get(preparation_line_service_key(line)) or {
            "services": [],
        }
        documents.extend(_ORIGINAL_BUILD_PIECES(
            user_id=user_id,
            registry=registry,
            batch={**batch, "lines": [line]},
            services_by_product={product_id: plan},
            assigned_at=assigned_at,
            duration_by_signature=duration_by_signature,
        ))
    return documents


def install_preparation_piece_line_services() -> None:
    global _INSTALLED, _ORIGINAL_BUILD_PIECES
    if _INSTALLED:
        return
    import preparation_piece_operations as base

    _ORIGINAL_BUILD_PIECES = base.build_piece_documents
    base._service_context_for_batch = _line_service_context_for_batch
    base.build_piece_documents = _build_piece_documents_by_line
    _INSTALLED = True


__all__ = [
    "build_line_service_plans",
    "install_preparation_piece_line_services",
    "preparation_line_service_key",
]
