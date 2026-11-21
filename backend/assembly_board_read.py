"""Projected, read-only employee queues over current order evidence.

The canonical mapper still decides status/payment eligibility. It receives no
customer, product media, timeline, or full provider payload. Only the current
active source rows are streamed; workflows and pieces are joined in bounded
batches, with no historical truncation before membership or counting.
"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from assembly_board_groups import assembly_queue_group
from order_engine.mapper import OrderMappingError, _first, _map_item, _stable_order_item_id, _text, map_salla_order
from order_engine.repository import MongoOrderRepository, _NATIVE_PAYMENT_FIELDS
from order_engine.service import _provider_status_native
from review_local_policy import (
    LOCAL_COMPLETION_MODE, WORKFLOWS, _EXTERNAL_PREPARATION, is_known_review_mode,
    load_local_review_workflows, local_review_stage_eligible,
)
from supplier_receipt_ready_policy import POLICY as SUPPLIER_RECEIPT_READY_POLICY


BATCH_SIZE = 100
PIECES = "mezan_preparation_pieces_v1"
GROUPS = ("direct_preparation", "supplier_received")
SOURCE_NAME = "mezan_preparation_current_salla_status"
RAW = "raw_by_source.salla_direct"

_ROOT_FIELDS = (
    "user_id", "order_number", "order_date", "order_status", "order_status_slug",
    "salla_order_id", "g47_salla_snapshot", "last_salla_direct_status_source_updated_at",
    "payment_method", "paid_amount", "remaining_amount", "has_remaining_amount",
    "payment_collection_status", "shipping_company", "shipping_company_code",
)
_RAW_FIELDS = (
    "id", "reference_id", "order_number", "date", "created_at", "order_date",
    "updated_at", "date_updated", "fetched_at", "received_at", "status_slug",
    "shipping_company", "shipping_company_name", "shipping_company_code",
    "courier_name", "courier_id", "courier_code", "delivery_method",
    *_NATIVE_PAYMENT_FIELDS,
)
_CARRIER_FIELDS = (
    "company_name", "company_code", "shipping_company", "shipping_company_code",
    "courier_name", "courier_code", "courier_id", "company_id", "external_company_name",
    "courier", "company",
)
SOURCE_PROJECTION = {
    "_id": 0, **{field: 1 for field in _ROOT_FIELDS},
    **{f"{RAW}.{field}": 1 for field in _RAW_FIELDS},
    **{f"{RAW}.items.{field}": 1 for field in ("id", "item_id", "order_item_id", "quantity")},
    **{f"{RAW}.{container}.{field}": 1
       for container in ("shipping", "shipping_address", "shipments", "shipment")
       for field in _CARRIER_FIELDS},
    **{f"{RAW}.{container}.{field}": 1 for container in ("shipments", "shipment")
       for field in ("id", "type", "status", "created_at", "updated_at")},
    **{f"{prefix}salla_shipping_current.{field}": 1 for prefix in ("", f"{RAW}.")
       for field in ("company_name", "company_code", "shipment_id", "status")},
}
WORKFLOW_PROJECTION = {
    "_id": 0, **{field: 1 for field in (
        "user_id", "order_number", "completion_mode", "review_completion_operation_id",
        "stage", "assembly_status", "assembly_completion_basis", "carrier_name",
    )},
    **{f"items.{field}": 1 for field in (
        "order_item_id", "quantity", "preparation_route", "direct_assembly_piece_ids", "assembly_ready_piece_ids",
    )},
    **{f"operational_items.{field}": 1 for field in (
        "operational_item_id", "blocks_order_completion", "assembly_status", "preparation_status",
    )},
}
PIECE_PROJECTION = {"_id": 0, **{field: 1 for field in (
    "order_number", "piece_id", "unit_index", "assembly_status", "order_item_id", "supplier_dispatch_status", "received_at",
)}}
# Provider rows without an explicit line ID use the canonical mapper's hashed
# identity. Fetch its inputs only for those candidate orders, never every order.
_IDENTITY_FIELDS = (
    "id", "item_id", "order_item_id", "quantity", "product_id", "variant_id", "product_sku_id",
    "sku", "name", "options", "choices", "attributes", "product_options", "selected_options", "customer_options",
    "custom_fields", "customizations", "personalization", "fields", "questions", "attachments", "files",
    "product.id", "product.variant_id", "product.sku", "product.name", "product.options",
    "product.variant.id", "product.variant.sku", "product.variant.name", "product.variant.options",
    "variant.id", "variant.sku", "variant.name", "variant.options",
)
IDENTITY_PROJECTION = {"_id": 0, "order_number": 1,
                       **{f"{RAW}.items.{field}": 1 for field in _IDENTITY_FIELDS}}


def _active_source_query(user_id: str) -> dict:
    # Conservative prefilter only. Local review still permits its existing
    # review/processing envelope; group membership uses the stricter source guard.
    aliases = sorted(_EXTERNAL_PREPARATION | {"in_progress"})
    pattern = r"^\s*(?:" + "|".join(
        r"[ _]+".join(re.escape(part) for part in value.replace("_", " ").split())
        for value in aliases
    ) + r")\s*$"
    fields = ["order_status", "order_status_slug", f"{RAW}.status", f"{RAW}.status_slug"]
    fields.extend(f"{RAW}.status.{key}" for key in ("name", "slug", "customized"))
    fields.extend(f"{RAW}.status.customized.{key}" for key in ("name", "label", "slug"))
    return {"user_id": user_id, f"{RAW}": {"$exists": True},
            "$or": [{field: {"$regex": pattern, "$options": "i"}} for field in fields]}


def projected_order(source: dict) -> Any | None:
    """Reuse repository fallbacks/native payment gates and the canonical mapper.

    The skinny item rows are coverage evidence, not item DTO inputs: retaining
    them here would manufacture anonymous identities from incomplete metadata.
    """
    raw = (source.get("raw_by_source") or {}).get("salla_direct")
    if not isinstance(raw, dict):
        return None
    projected = {**source, "raw_by_source": {"salla_direct": {key: value for key, value in raw.items() if key != "items"}}}
    discovery = MongoOrderRepository._to_discovery_row(projected)
    if discovery is None:
        return None
    try:
        order = map_salla_order(discovery.salla_raw, native_payment_source=discovery.native_payment_source)
    except OrderMappingError:
        return None
    if order.order_number != discovery.order_number:
        return None
    return order.model_copy(update={
        "status_native": _provider_status_native(discovery.salla_raw, current_status=discovery.current_status) or order.status_native,
    })


def _whole_quantity(value: Any) -> int | None:
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number > 0 and number == number.to_integral_value() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _source_items(source: dict) -> list:
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    return raw.get("items") if isinstance(raw.get("items"), list) else []


def _has_source_id(item: dict) -> bool:
    return bool(_text(_first(item.get("id"), item.get("item_id"), item.get("order_item_id"))))


def _current_items_covered(source: dict, workflow: dict, *, identity_complete: bool = False) -> bool:
    number = str(source.get("order_number") or "").strip()
    current = {}
    expected = {}
    for row in workflow.get("items") or []:
        key = str(row.get("order_item_id") or "").strip()
        quantity = _whole_quantity(row.get("quantity"))
        if not key or key in expected or quantity is None:
            return False
        expected[key] = quantity
    for index, item in enumerate(_source_items(source)):
        if not isinstance(item, dict):
            return False
        quantity = _whole_quantity(item.get("quantity"))
        if _has_source_id(item):
            key = _stable_order_item_id(order_number=number, item=item, index=index,
                                       product_id=None, variant_id=None, sku=None, name="", options=[], custom_fields=[])
        elif identity_complete:
            key = _map_item(order_number=number, item=item, index=index).order_item_id
        else:
            return False
        if quantity is None or key in current:
            return False
        current[key] = quantity
    return bool(current) and current == expected


def _supplier_units_covered(workflow: dict, pieces: list[dict]) -> bool:
    """Distinct records in different batches must not impersonate distinct units."""
    expected = {str(item.get("order_item_id") or "").strip(): _whole_quantity(item.get("quantity"))
                for item in workflow.get("items") or []
                if (item.get("preparation_route") or "supplier_file") == "supplier_file"}
    seen = defaultdict(set)
    for piece in pieces:
        key = str(piece.get("order_item_id") or "").strip()
        unit = _whole_quantity(piece.get("unit_index"))
        quantity = expected.get(key)
        if unit is None or quantity is None or unit > quantity or unit in seen[key]:
            return False
        seen[key].add(unit)
    return bool(expected) and all(len(seen[key]) == quantity for key, quantity in expected.items())


async def _read_batch(db: Any, *, user_id: str, sources: list[dict],
                      workflow_pieces: Callable, order_in_progress: Callable,
                      source_in_progress: Callable) -> list[dict]:
    by_number = {str(row.get("order_number") or "").strip(): row for row in sources}
    by_number.pop("", None)
    if not by_number:
        return []
    workflows = [row async for row in db[WORKFLOWS].find(
        {"user_id": user_id, "order_number": {"$in": list(by_number)}}, WORKFLOW_PROJECTION,
    ) if is_known_review_mode(row.get("completion_mode"))]
    if not workflows:
        return []
    numbers = [row["order_number"] for row in workflows]
    physical = defaultdict(list)
    async for piece in db[PIECES].find({
        "user_id": user_id, "order_number": {"$in": numbers}, "status": {"$ne": "cancelled"},
        "$or": [{"experiment_archived_at": {"$exists": False}}, {"experiment_archived_at": None}],
    }, PIECE_PROJECTION):
        physical[str(piece.get("order_number") or "").strip()].append(piece)
    approved = await load_local_review_workflows(db, user_id=user_id, order_numbers=numbers, workflows=workflows)
    rows = []
    anonymous = []
    for workflow in workflows:
        number = workflow["order_number"]
        source = by_number[number]
        order = projected_order(source)
        if order is None:
            continue
        try:
            virtual = workflow_pieces(workflow, order_number=number)
        except (TypeError, ValueError):
            # A malformed quantity cannot make a partial order look complete
            # or prevent other valid orders from being listed.
            continue
        pieces = physical[number] + virtual
        if not pieces:
            continue
        coherent_progress = source_in_progress(source, order)
        if workflow.get("completion_mode") == LOCAL_COMPLETION_MODE:
            stages = {"in_progress", "ready_to_ship"}
            received = any(piece.get("supplier_dispatch_status") == "received" and piece.get("received_at")
                           for piece in physical[number])
            current_progress = order_in_progress(order)
            if virtual or received and current_progress:
                stages.add("reviewed")
            receipt_board = received and current_progress or workflow.get("assembly_completion_basis") == SUPPLIER_RECEIPT_READY_POLICY
            if not local_review_stage_eligible(order, workflow if receipt_board else approved.get(number), stages):
                continue
        elif str(order.status or "").casefold() != "in_progress" and not coherent_progress:
            continue
        group = assembly_queue_group(workflow, physical[number]) if coherent_progress else None
        if group == "supplier_received" and not _supplier_units_covered(workflow, physical[number]):
            group = None
        ready = sum(str(piece.get("assembly_status") or "").strip() == "ready" for piece in pieces)
        row = {
            "queue_group": group, "order_number": number, "order_created_at": order.created_at,
            "status_at": order.engine_updated_at or order.created_at,
            "shipping_company": order.shipping.company or str(workflow.get("carrier_name") or "").strip() or None,
            "total_count": len(pieces), "ready_count": ready, "remaining_count": len(pieces) - ready,
            "order_status": order.status, "order_status_native": order.status_native,
            "workflow_stage": str(workflow.get("stage") or "").strip() or None,
            "assembly_status": str(workflow.get("assembly_status") or "").strip() or None,
            "mezan_preparation": True,
        }
        if group:
            if any(isinstance(item, dict) and not _has_source_id(item) for item in _source_items(source)):
                anonymous.append((row, workflow))
            elif not _current_items_covered(source, workflow):
                row["queue_group"] = None
        rows.append(row)
    if anonymous:
        identities = {source["order_number"]: source async for source in db.unified_orders.find({
            "user_id": user_id, "order_number": {"$in": [row["order_number"] for row, _ in anonymous]},
        }, IDENTITY_PROJECTION)}
        for row, workflow in anonymous:
            if not _current_items_covered(identities.get(row["order_number"]) or {}, workflow, identity_complete=True):
                row["queue_group"] = None
    return rows


async def read_active_assembly_board(db: Any, *, user_id: str, state: str, limit: int,
                                     offset: int, query: str, workflow_pieces: Callable,
                                     order_in_progress: Callable, source_in_progress: Callable) -> dict:
    rows = []
    batch = []
    async for source in db.unified_orders.find(_active_source_query(user_id), SOURCE_PROJECTION):
        batch.append(source)
        if len(batch) == BATCH_SIZE:
            rows.extend(await _read_batch(db, user_id=user_id, sources=batch, workflow_pieces=workflow_pieces,
                                         order_in_progress=order_in_progress, source_in_progress=source_in_progress))
            batch = []
            await asyncio.sleep(0)
    if batch:
        rows.extend(await _read_batch(db, user_id=user_id, sources=batch, workflow_pieces=workflow_pieces,
                                     order_in_progress=order_in_progress, source_in_progress=source_in_progress))
    group_counts = {group: sum(row["queue_group"] == group for row in rows) for group in GROUPS}
    queue_counts = {"in_progress": len(rows), **group_counts}
    if state in GROUPS:
        rows = [row for row in rows if row["queue_group"] == state]
    normalized_query = str(query or "").strip().removeprefix("#").strip()
    if normalized_query:
        rows = [row for row in rows if normalized_query in row["order_number"]]
    total = len(rows)
    # Counts-only reads never build a page or hydrate full display orders.
    if limit:
        rows.sort(key=lambda row: (row["order_created_at"], row["order_number"]))
    return {"state": state, "queue_counts": queue_counts, "group_counts": group_counts,
            "items": rows[offset:offset + limit] if limit else [], "total": total,
            "offset": offset, "limit": limit, "poll_seconds": 15, "source": SOURCE_NAME}
