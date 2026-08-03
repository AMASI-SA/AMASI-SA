"""Line-granular service plans and safe piece reconciliation.

The same Salla product may appear in one preparation file with different option
values. Product-only service-plan keys would let the last line overwrite the
others. The visible supplier-file wording may also be edited without changing
the original Salla option that controls services. This installer snapshots the
original option fields, scopes services to the exact order item, and lazily
materialises older ready files when the work views are opened.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from order_item_engine.mapper import map_order_item_identities
from order_review_routes import _text
from order_review_spec_replacements import extract_item_specs
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES


_INSTALLED = False
_ORIGINAL_BUILD_PIECES = None
_ORIGINAL_BUILD_BATCH_LINES = None
_ORIGINAL_MY_WORK_VIEW = None
_ORIGINAL_MANAGER_SUMMARY = None


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


def original_service_spec_fields(identity: Any) -> list[dict[str, str]]:
    return [
        {
            "spec_key": _text(row.get("spec_key")),
            "name": _text(row.get("name")),
            "value": _text(row.get("value")),
        }
        for row in extract_item_specs(identity)
        if _text(row.get("name")) and _text(row.get("value"))
    ]


async def _build_batch_lines_with_service_snapshot(
    context: dict[str, Any],
    planned: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep original option values beside editable PDF field wording."""
    assert _ORIGINAL_BUILD_BATCH_LINES is not None
    rows = await _ORIGINAL_BUILD_BATCH_LINES(context, planned)
    identities: dict[tuple[str, str], Any] = {}
    for order, _workflow in context.get("pairs") or []:
        order_number = _text(getattr(order, "order_number", None))
        for identity in map_order_item_identities(order):
            identities[(order_number, _text(identity.order_item_id))] = identity
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = identities.get((
            _text(row.get("order_number")),
            _text(row.get("order_item_id")),
        ))
        row["service_spec_fields"] = (
            original_service_spec_fields(identity) if identity is not None else []
        )
    return rows


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
        service_line = dict(line)
        if line.get("service_spec_fields"):
            service_line["file_spec_fields"] = list(line["service_spec_fields"])
            service_line["product_options"] = {}
            service_line["size"] = None
            service_line["color"] = None
            service_line["customer_name"] = None
        plans[preparation_line_service_key(line)] = {
            "product_id": product_id or None,
            "services": inherit_services(
                line=service_line,
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
        line_documents = _ORIGINAL_BUILD_PIECES(
            user_id=user_id,
            registry=registry,
            batch={**batch, "lines": [line]},
            services_by_product={product_id: plan},
            assigned_at=assigned_at,
            duration_by_signature=duration_by_signature,
        )
        for document in line_documents:
            document["service_specifications_snapshot"] = list(
                line.get("service_spec_fields") or []
            )
        documents.extend(line_documents)
    return documents


async def _materialize_missing_ready_files(
    db: Any,
    *,
    user_id: str,
    employee_id: str | None = None,
    limit: int = 200,
) -> list[str]:
    """Backfill historical registered files without changing external systems."""
    import preparation_piece_operations as base

    query: dict[str, Any] = {
        "user_id": user_id,
        "status": "ready",
        "$or": [
            {"piece_registry_materialized_at": {"$exists": False}},
            {"piece_registry_materialized_at": None},
        ],
    }
    if employee_id:
        query["responsible_employee_id"] = employee_id
    rows = await db[base.REGISTRY].find(
        query,
        {"_id": 0},
    ).sort("registered_at", -1).limit(limit).to_list(limit)
    failures: list[str] = []
    for row in rows:
        try:
            await base.materialize_preparation_pieces(
                db,
                user_id=user_id,
                registry=row,
            )
        except Exception:
            failures.append(_text(row.get("file_number")) or _text(row.get("batch_id")))
    return [value for value in failures if value]


async def _my_work_with_backfill(
    db: Any,
    *,
    user_id: str,
    employee_id: str,
    limit: int,
) -> dict[str, Any]:
    assert _ORIGINAL_MY_WORK_VIEW is not None
    failures = await _materialize_missing_ready_files(
        db,
        user_id=user_id,
        employee_id=employee_id,
        limit=max(limit, 100),
    )
    result = await _ORIGINAL_MY_WORK_VIEW(
        db,
        user_id=user_id,
        employee_id=employee_id,
        limit=limit,
    )
    result["materialization_warnings"] = failures
    return result


async def _manager_summary_with_backfill(
    db: Any,
    *,
    user_id: str,
    date: str,
) -> dict[str, Any]:
    assert _ORIGINAL_MANAGER_SUMMARY is not None
    failures = await _materialize_missing_ready_files(
        db,
        user_id=user_id,
        limit=200,
    )
    result = await _ORIGINAL_MANAGER_SUMMARY(
        db,
        user_id=user_id,
        date=date,
    )
    result["materialization_warnings"] = failures
    return result


def install_preparation_piece_line_services() -> None:
    global _INSTALLED
    global _ORIGINAL_BUILD_PIECES, _ORIGINAL_BUILD_BATCH_LINES
    global _ORIGINAL_MY_WORK_VIEW, _ORIGINAL_MANAGER_SUMMARY
    if _INSTALLED:
        return
    import preparation_piece_operations as base
    import reviewed_preparation_batches as batch_module

    _ORIGINAL_BUILD_PIECES = base.build_piece_documents
    _ORIGINAL_BUILD_BATCH_LINES = batch_module._build_batch_lines
    _ORIGINAL_MY_WORK_VIEW = base._my_work_view
    _ORIGINAL_MANAGER_SUMMARY = base._manager_summary
    batch_module._build_batch_lines = _build_batch_lines_with_service_snapshot
    base._service_context_for_batch = _line_service_context_for_batch
    base.build_piece_documents = _build_piece_documents_by_line
    base._my_work_view = _my_work_with_backfill
    base._manager_summary = _manager_summary_with_backfill
    _INSTALLED = True


__all__ = [
    "build_line_service_plans",
    "install_preparation_piece_line_services",
    "original_service_spec_fields",
    "preparation_line_service_key",
]
