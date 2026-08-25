"""Quantity-selectable preparation batches for the reviewed stage.

A batch allocates immutable physical unit slots from reviewed order lines,
freezes the reviewed image/spec snapshot, generates the existing Mezan prep
card PDF, and moves an order to ``in_progress`` only after every supplier-file
unit in that order has been committed to one or more batches.

This engine is Mezan-only. It performs no Salla or Qoyod writes.
"""
from __future__ import annotations

import base64
import asyncio
import hashlib
import io
import ipaddress
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote, urlparse

import httpx
from PIL import Image
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import BulkWriteError, DuplicateKeyError

from order_engine.repository import MongoOrderRepository
from order_engine.salla_refresh import refresh_order_from_salla
from order_engine.service import OrderNotFoundError, get_order
from order_item_engine.mapper import map_order_item_identities
from order_tracking_notes import enforce_stage_instructions
from order_review_routes import (
    EVENTS,
    WORKFLOWS,
    _merchant_user_id,
    _require_reviewer,
    _text,
)
from order_review_spec_replacements import (
    canonical_spec_key,
    extract_item_specs,
    supplier_file_spec_fields,
)
from preparation_pdf import ProductLine, generate_preparation_pdf
from preparation_piece_barcode import preparation_piece_barcode
from reviewed_products_catalog import (
    ACTIVE_PREPARATION_ALLOCATION_STATUSES,
    MAX_REVIEWED_ORDERS,
    PREPARATION_UNIT_ALLOCATIONS,
    load_reviewed_product_context,
)
from tz_utils import riyadh_now_aware


BATCHES = "mezan_preparation_batches_v2"
BATCH_BUILD_TTL_MINUTES = 20
MAX_BATCH_SELECTIONS = 200
MAX_BATCH_UNITS = 1500
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class PreparationProductSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=1, le=MAX_BATCH_UNITS)


class CreatePreparationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    selections: list[PreparationProductSelection] = Field(
        min_length=1,
        max_length=MAX_BATCH_SELECTIONS,
    )

    @field_validator("selections")
    @classmethod
    def unique_product_groups(
        cls,
        values: list[PreparationProductSelection],
    ) -> list[PreparationProductSelection]:
        keys = [value.group_key.strip() for value in values]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_product_group")
        if sum(value.quantity for value in values) > MAX_BATCH_UNITS:
            raise ValueError("batch_unit_limit_exceeded")
        return values


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _unit_quantity(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    rounded = int(round(number))
    if number <= 0 or abs(number - rounded) > 0.000001:
        return 0
    return rounded


def plan_preparation_allocations(
    products: list[dict[str, Any]],
    selections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Allocate requested product quantities to deterministic free unit slots."""
    by_key = {
        _text(row.get("group_key")): row
        for row in products
        if _text(row.get("group_key"))
    }
    planned: list[dict[str, Any]] = []
    seen: set[str] = set()

    for selection in selections:
        group_key = _text(selection.get("group_key"))
        quantity = _unit_quantity(selection.get("quantity"))
        if not group_key or quantity <= 0:
            raise ValueError("invalid_preparation_selection")
        if group_key in seen:
            raise ValueError("duplicate_product_group")
        seen.add(group_key)
        product = by_key.get(group_key)
        if not product:
            raise ValueError("reviewed_product_not_available")
        remaining = _unit_quantity(
            product.get("remaining_quantity")
            if product.get("remaining_quantity") is not None
            else product.get("quantity")
        )
        if quantity > remaining:
            raise ValueError("preparation_quantity_exceeds_remaining")

        pending = quantity
        for line in product.get("source_lines") or []:
            available = [
                int(value)
                for value in (line.get("available_unit_indices") or [])
                if int(value) > 0
            ]
            if not available:
                line_quantity = _unit_quantity(line.get("quantity"))
                used = {
                    int(value)
                    for value in (line.get("allocated_unit_indices") or [])
                    if int(value) > 0
                }
                available = [
                    index for index in range(1, line_quantity + 1)
                    if index not in used
                ]
            if not available:
                continue
            take = min(pending, len(available))
            planned.append({
                "group_key": group_key,
                "product_name": _text(product.get("name")),
                "product_id": product.get("product_id"),
                "sku": product.get("sku"),
                "order_number": _text(line.get("order_number")),
                "order_item_id": _text(line.get("order_item_id")),
                "quantity": take,
                "unit_indices": available[:take],
                "line": dict(line),
            })
            pending -= take
            if pending <= 0:
                break
        if pending > 0:
            raise ValueError("reviewed_product_allocation_incomplete")

    return planned


def _is_private_literal_host(hostname: str) -> bool:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname.casefold() in {"localhost", "localhost.localdomain"}
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def _safe_image_url(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if _is_private_literal_host(parsed.hostname):
        return ""
    return raw


def _compress_image_bytes(content: bytes) -> tuple[bytes | None, str | None]:
    if not content or len(content) > MAX_IMAGE_BYTES:
        return None, None
    try:
        image = Image.open(io.BytesIO(content))
        image.thumbnail((420, 420))
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=78, optimize=True)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        return None, None


async def _download_card_image(
    client: httpx.AsyncClient,
    value: Any,
    cache: dict[str, tuple[bytes | None, str | None]],
) -> tuple[bytes | None, str | None]:
    url = _safe_image_url(value)
    if not url:
        return None, None
    if url in cache:
        return cache[url]
    result: tuple[bytes | None, str | None] = (None, None)
    try:
        response = await client.get(url)
        final_host = response.url.host or ""
        if (
            response.status_code == 200
            and not _is_private_literal_host(final_host)
            and len(response.content) <= MAX_IMAGE_BYTES
        ):
            result = _compress_image_bytes(response.content)
    except Exception:
        result = (None, None)
    cache[url] = result
    return result


def _card_field_projection(
    fields: list[dict[str, str]],
    preparation_note: Any,
) -> dict[str, Any]:
    customer_name = size = color = None
    note_parts: list[str] = []
    extra: dict[str, str] = {}

    for row in fields:
        name = _text(row.get("name"))
        value = _text(row.get("value"))
        if not name or not value:
            continue
        key = canonical_spec_key(row.get("spec_key") or name)
        normalized_name = _normalized(name)
        if key == "size" and not size:
            size = value
            continue
        if key == "color" and not color:
            color = value
            continue
        if (
            not customer_name
            and (
                normalized_name == "اسم"
                or normalized_name.startswith("الاسم")
                or normalized_name.startswith("اسم ")
                or normalized_name in {"name", "customer name"}
            )
        ):
            customer_name = value
            continue
        if any(
            hint in normalized_name
            for hint in (
                "ملاحظ",
                "رسالة",
                "رساله",
                "كتابة",
                "كتابه",
                "عبارة",
                "عباره",
                "اهداء",
                "إهداء",
                "note",
                "message",
            )
        ):
            note_parts.append(value)
            continue
        extra[name] = value

    operational_note = _text(preparation_note)
    if operational_note:
        note_parts.append(operational_note)
    return {
        "customer_name": customer_name,
        "size": size,
        "color": color,
        "note": " | ".join(dict.fromkeys(note_parts)) or None,
        "product_options": extra,
    }


def _line_from_batch_storage(
    row: dict[str, Any],
    batch: dict[str, Any] | None = None,
) -> ProductLine:
    # Rebuild customer-selected option fields from the immutable reviewed
    # file snapshot when older/stale batch rows are missing the projected
    # convenience fields. Both the employee preparation PDF and the supplier
    # dispatch PDF pass through this function, so one canonical fallback keeps
    # both files consistent without changing the original Salla values.
    stored_spec_fields = [
        field for field in (row.get("file_spec_fields") or [])
        if isinstance(field, dict)
    ]
    fallback_fields = _card_field_projection(stored_spec_fields, row.get("preparation_note"))
    image_bytes = None
    if row.get("image_b64"):
        try:
            image_bytes = base64.b64decode(row["image_b64"])
        except Exception:
            image_bytes = None
    batch = batch or {}
    barcode_payload = None
    unit_index = row.get("unit_index")
    if unit_index in (None, ""):
        unit_indices = row.get("unit_indices") or []
        # A printed preparation card may represent more than one physical
        # piece. Use its first materialised piece as a stable card anchor;
        # supplier receiving expands that anchor to the remaining pieces on
        # the same exact batch/order line before asking for a quantity.
        unit_index = unit_indices[0] if unit_indices else None
    if all((
        _text(batch.get("user_id")),
        _text(batch.get("id")),
        _text(row.get("order_number")),
        _text(row.get("order_item_id")),
        unit_index not in (None, ""),
    )):
        barcode_payload = preparation_piece_barcode(
            user_id=batch.get("user_id"),
            batch_id=batch.get("id"),
            order_number=row.get("order_number"),
            order_item_id=row.get("order_item_id"),
            unit_index=unit_index,
        )
    return ProductLine(
        order_number=_text(row.get("order_number")),
        order_date=_text(row.get("order_date")) or None,
        product_name=_text(row.get("product_name")) or None,
        customer_name=(
            _text(row.get("customer_name"))
            or _text(fallback_fields.get("customer_name"))
            or None
        ),
        note=(
            _text(row.get("note"))
            or _text(fallback_fields.get("note"))
            or None
        ),
        quantity=int(row.get("quantity") or 1),
        total_products_in_order=max(1, int(row.get("total_products_in_order") or 1)),
        item_index=int(row.get("line_index") or 0),
        image_bytes=image_bytes,
        image_mime=_text(row.get("image_mime")) or None,
        shipping_company=_text(row.get("shipping_company")) or None,
        size=(
            _text(row.get("size"))
            or _text(fallback_fields.get("size"))
            or None
        ),
        color=(
            _text(row.get("color"))
            or _text(fallback_fields.get("color"))
            or None
        ),
        product_id=_text(row.get("product_id")) or None,
        sku=_text(row.get("sku")) or None,
        product_options={
            **dict(fallback_fields.get("product_options") or {}),
            **dict(row.get("product_options") or {}),
        },
        barcode_payload=barcode_payload,
    )


def render_preparation_batch_pdf(batch: dict[str, Any]) -> bytes:
    lines = [
        _line_from_batch_storage(row, batch)
        for row in batch.get("lines") or []
        if isinstance(row, dict)
    ]
    if not lines:
        raise ValueError("preparation_batch_has_no_lines")
    return generate_preparation_pdf(
        lines,
        serial_start=1,
        title=_text(batch.get("title")) or "تجهيز المنتجات",
    )


def repair_batch_line_customer_options(
    lines: list[dict[str, Any]],
    *,
    identities_by_order: dict[str, list[Any]],
    workflows_by_order: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Rebuild missing PDF option snapshots from canonical Salla order items."""
    repaired: list[dict[str, Any]] = []
    repaired_count = 0
    unresolved: list[str] = []
    for source in lines:
        row = dict(source)
        order_number = _text(row.get("order_number"))
        order_item_id = _text(row.get("order_item_id"))
        identities = identities_by_order.get(order_number) or []
        identity = next(
            (
                candidate for candidate in identities
                if _text(getattr(candidate, "order_item_id", None)) == order_item_id
            ),
            None,
        )
        if identity is None:
            line_index = int(row.get("line_index") or 0)
            identity = next(
                (
                    candidate for candidate in identities
                    if int(getattr(candidate, "line_index", -1) or 0) == line_index
                    and (
                        not _text(row.get("sku"))
                        or _text(getattr(candidate, "sku", None)) == _text(row.get("sku"))
                    )
                ),
                None,
            )
        if identity is None:
            unresolved.append(f"{order_number}:{order_item_id}")
            repaired.append(row)
            continue
        workflow = workflows_by_order.get(order_number) or {}
        states = {
            _text(state.get("order_item_id")): dict(state)
            for state in workflow.get("items") or []
            if isinstance(state, dict) and _text(state.get("order_item_id"))
        }
        state = states.get(_text(getattr(identity, "order_item_id", None)), {})
        spec_fields = supplier_file_spec_fields(identity, state)
        if not spec_fields:
            repaired.append(row)
            continue
        card_fields = _card_field_projection(
            spec_fields,
            row.get("preparation_note") or state.get("preparation_note"),
        )
        before = (
            row.get("file_spec_fields"), row.get("customer_name"),
            row.get("size"), row.get("color"), row.get("note"),
            row.get("product_options"),
        )
        row.update({
            "file_spec_fields": spec_fields,
            "customer_name": card_fields["customer_name"],
            "size": card_fields["size"],
            "color": card_fields["color"],
            "note": card_fields["note"],
            "product_options": card_fields["product_options"],
        })
        after = (
            row.get("file_spec_fields"), row.get("customer_name"),
            row.get("size"), row.get("color"), row.get("note"),
            row.get("product_options"),
        )
        if before != after:
            repaired_count += 1
        repaired.append(row)
    return repaired, repaired_count, unresolved


def _batch_response(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": batch.get("status") == "ready",
        "batch_id": _text(batch.get("id")),
        "status": _text(batch.get("status")),
        "file_name": _text(batch.get("file_name")),
        "created_at": batch.get("created_at"),
        "selected_product_count": int(batch.get("selected_product_count") or 0),
        "allocated_quantity": int(batch.get("allocated_quantity") or 0),
        "card_count": int(batch.get("card_count") or 0),
        "order_count": int(batch.get("order_count") or 0),
        "transitioned_order_numbers": list(batch.get("transitioned_order_numbers") or []),
        "remaining_review_order_numbers": list(batch.get("remaining_review_order_numbers") or []),
        "reconciliation_required": list(batch.get("reconciliation_required") or []),
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }


async def ensure_preparation_batch_indexes(db: Any) -> None:
    await db[BATCHES].create_index(
        [("user_id", ASCENDING), ("client_request_id", ASCENDING)],
        unique=True,
        name="uq_preparation_batch_request_v2",
    )
    await db[BATCHES].create_index(
        [("user_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_preparation_batches_v2",
    )
    await db[BATCHES].create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_preparation_batch_builds_v2",
    )
    await db[PREPARATION_UNIT_ALLOCATIONS].create_index(
        [
            ("user_id", ASCENDING),
            ("order_number", ASCENDING),
            ("order_item_id", ASCENDING),
            ("unit_index", ASCENDING),
        ],
        unique=True,
        name="uq_preparation_unit_allocation_v2",
    )
    await db[PREPARATION_UNIT_ALLOCATIONS].create_index(
        [("user_id", ASCENDING), ("batch_id", ASCENDING)],
        name="ix_preparation_allocations_batch_v2",
    )
    await db[PREPARATION_UNIT_ALLOCATIONS].create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_preparation_reservations_v2",
    )


async def _cleanup_stale_builds(db: Any, user_id: str) -> None:
    now = _now()
    stale = await db[BATCHES].find(
        {
            "user_id": user_id,
            "status": "building",
            "expires_at": {"$lte": now},
        },
        {"_id": 0, "id": 1},
    ).to_list(500)
    batch_ids = [_text(row.get("id")) for row in stale if _text(row.get("id"))]
    if not batch_ids:
        return
    await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({
        "user_id": user_id,
        "batch_id": {"$in": batch_ids},
        "status": "reserved",
    })
    await db[BATCHES].delete_many({
        "user_id": user_id,
        "id": {"$in": batch_ids},
        "status": "building",
    })


async def _build_batch_lines(
    context: dict[str, Any],
    planned: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    orders_by_number = {
        _text(order.order_number): order
        for order, _workflow in context.get("pairs") or []
    }
    workflows_by_number = {
        _text(workflow.get("order_number")): workflow
        for _order, workflow in context.get("pairs") or []
    }
    identities_by_order = {
        order_number: {
            _text(identity.order_item_id): identity
            for identity in map_order_item_identities(order)
        }
        for order_number, order in orders_by_number.items()
    }

    result: list[dict[str, Any]] = []
    image_cache: dict[str, tuple[bytes | None, str | None]] = {}
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for index, allocation in enumerate(planned, start=1):
            order_number = _text(allocation.get("order_number"))
            order_item_id = _text(allocation.get("order_item_id"))
            order = orders_by_number.get(order_number)
            workflow = workflows_by_number.get(order_number) or {}
            identity = (identities_by_order.get(order_number) or {}).get(order_item_id)
            if order is None or identity is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "reviewed_line_changed_reload_required",
                        "order_number": order_number,
                    },
                )
            states = {
                _text(row.get("order_item_id")): dict(row)
                for row in workflow.get("items") or []
                if isinstance(row, dict) and _text(row.get("order_item_id"))
            }
            state = states.get(order_item_id, {})
            if state.get("supplier_export") is False:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "reviewed_line_no_longer_exportable",
                        "order_number": order_number,
                    },
                )
            spec_fields = supplier_file_spec_fields(identity, state)
            # Fail closed before materialising the employee preparation file:
            # every customer option visible in Waiting Review must be frozen
            # into the immutable file snapshot unless the reviewer explicitly
            # hid that spec from exported files. The supplier PDF later reuses
            # this exact snapshot, so this one guard protects both files.
            hidden_spec_keys = {
                canonical_spec_key(value)
                for value in state.get("supplier_export_excluded_spec_keys", []) or []
                if canonical_spec_key(value)
            }
            required_specs = {
                row["spec_key"]: row
                for row in extract_item_specs(identity)
                if row.get("spec_key") and row["spec_key"] not in hidden_spec_keys
            }
            snapshotted_specs = {
                canonical_spec_key(row.get("spec_key") or row.get("name"))
                for row in spec_fields
                if isinstance(row, dict)
                and canonical_spec_key(row.get("spec_key") or row.get("name"))
                and _text(row.get("value"))
            }
            missing_spec_keys = sorted(set(required_specs) - snapshotted_specs)
            if missing_spec_keys:
                missing_labels = [required_specs[key]["name"] for key in missing_spec_keys]
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "preparation_customer_options_snapshot_incomplete",
                        "message": (
                            "تعذّر إنشاء ملف التجهيز لأن بعض خيارات العميل لم تُحفظ بالكامل: "
                            + "، ".join(missing_labels)
                            + ". حدّث الطلب ثم أعد المحاولة."
                        ),
                        "order_number": order_number,
                        "order_item_id": order_item_id,
                        "missing_spec_keys": missing_spec_keys,
                    },
                )
            card_fields = _card_field_projection(
                spec_fields,
                state.get("preparation_note"),
            )
            image_url = (
                _text(state.get("selected_image_url"))
                or _text(getattr(identity, "image_url", None))
                or _text((allocation.get("line") or {}).get("image_url"))
            )
            image_bytes, image_mime = await _download_card_image(
                client,
                image_url,
                image_cache,
            )
            total_products = sum(
                _unit_quantity(getattr(item, "quantity", 0))
                for item in getattr(order, "items", None) or []
            ) or 1
            result.append({
                "line_number": index,
                "group_key": _text(allocation.get("group_key")),
                "order_number": order_number,
                "order_item_id": order_item_id,
                "unit_indices": list(allocation.get("unit_indices") or []),
                "quantity": int(allocation.get("quantity") or 0),
                "product_name": _text(getattr(identity, "name", None)) or _text(allocation.get("product_name")),
                "product_id": _text(getattr(identity, "product_id", None)) or None,
                "sku": _text(getattr(identity, "sku", None)) or None,
                "line_index": int(getattr(identity, "line_index", 0) or 0),
                "order_date": str(getattr(order, "created_at", "") or ""),
                "shipping_company": _text(getattr(getattr(order, "shipping", None), "company", None)) or None,
                "total_products_in_order": total_products,
                "selected_image_url": image_url or None,
                "image_b64": base64.b64encode(image_bytes).decode("ascii") if image_bytes else None,
                "image_mime": image_mime,
                "customer_name": card_fields["customer_name"],
                "size": card_fields["size"],
                "color": card_fields["color"],
                "note": card_fields["note"],
                "product_options": card_fields["product_options"],
                "file_spec_fields": spec_fields,
                "preparation_note": _text(state.get("preparation_note")) or None,
            })
    return result


async def _reconcile_order_stage(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    batch_id: str,
    actor: dict[str, Any],
) -> tuple[bool, int]:
    workflow = await db[WORKFLOWS].find_one(
        {"user_id": user_id, "order_number": order_number},
        {"_id": 0},
    )
    if not workflow:
        return False, 0
    stage = _text(workflow.get("stage"))
    if stage not in {"reviewed", "in_progress"}:
        return stage == "in_progress", 0

    context = await load_reviewed_product_context(
        db,
        user_id=user_id,
        limit=MAX_REVIEWED_ORDERS,
    )
    order = next(
        (
            candidate for candidate, _row in context.get("pairs") or []
            if _text(candidate.order_number) == order_number
        ),
        None,
    )
    # Recovery can remove a failed batch after the order was moved to
    # in_progress.  Such orders are absent from the reviewed-only context, so
    # load the canonical order directly and recompute coverage from the
    # allocation ledger.  Treating the stage itself as completion evidence
    # strands released units outside both reviewed products and preparation
    # files.
    if order is None and stage == "in_progress":
        try:
            order = await get_order(
                MongoOrderRepository(db),
                user_id=user_id,
                order_number=order_number,
            )
        except OrderNotFoundError:
            return True, 0
    if order is None:
        return False, 0

    states = {
        _text(row.get("order_item_id")): dict(row)
        for row in workflow.get("items") or []
        if isinstance(row, dict) and _text(row.get("order_item_id"))
    }
    allocations = await db[PREPARATION_UNIT_ALLOCATIONS].find(
        {
            "user_id": user_id,
            "order_number": order_number,
            "status": "committed",
        },
        {"_id": 0, "order_item_id": 1, "unit_index": 1},
    ).to_list(10000)
    allocated_by_item: dict[str, set[int]] = defaultdict(set)
    for row in allocations:
        item_id = _text(row.get("order_item_id"))
        try:
            unit_index = int(row.get("unit_index"))
        except (TypeError, ValueError):
            continue
        if item_id and unit_index > 0:
            allocated_by_item[item_id].add(unit_index)

    required = allocated = 0
    for item in getattr(order, "items", None) or []:
        item_id = _text(getattr(item, "order_item_id", None))
        state = states.get(item_id, {})
        if state.get("supplier_export") is False:
            continue
        quantity = _unit_quantity(getattr(item, "quantity", 0))
        required += quantity
        allocated += min(quantity, len(allocated_by_item.get(item_id, set())))
    remaining = max(0, required - allocated)
    now = _now_iso()
    progress = {
        "required_quantity": required,
        "allocated_quantity": allocated,
        "remaining_quantity": remaining,
        "updated_at": now,
        "last_batch_id": batch_id,
    }
    actor_id = _text(actor.get("id"))
    actor_name = _text(actor.get("name") or actor.get("email"))
    update: dict[str, Any] = {
        "$set": {
            "preparation_progress": progress,
            "updated_at": now,
            "updated_by": actor_id,
        },
        "$inc": {"revision": 1},
    }
    if batch_id:
        update["$addToSet"] = {"preparation_batch_ids": batch_id}
    transitioned = remaining == 0 and stage == "reviewed"
    restored = remaining > 0 and stage == "in_progress"
    if transitioned:
        update["$set"].update({
            "stage": "in_progress",
            "in_progress_at": now,
            "in_progress_by": actor_id,
            "in_progress_by_name": actor_name,
            "preparation_fully_allocated_at": now,
        })
    elif restored:
        update["$set"].update({
            "stage": "reviewed",
            "reviewed_at": workflow.get("reviewed_at") or now,
            "reviewed_by": workflow.get("reviewed_by") or actor_id,
        })
        update["$unset"] = {
            "in_progress_at": "",
            "in_progress_by": "",
            "in_progress_by_name": "",
            "preparation_fully_allocated_at": "",
        }
    await db[WORKFLOWS].update_one(
        {"user_id": user_id, "order_number": order_number},
        update,
    )
    if transitioned:
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "batch_id": batch_id,
            "event_type": "order_moved_to_in_progress",
            "occurred_at": now,
            "actor_id": actor_id,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
    elif restored:
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "batch_id": batch_id or None,
            "event_type": "order_restored_to_reviewed_after_failed_preparation_file",
            "remaining_quantity": remaining,
            "occurred_at": now,
            "actor_id": actor_id,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
    return remaining == 0, remaining


async def _reconcile_batch_orders(
    db: Any,
    *,
    user_id: str,
    batch: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, list[str]]:
    transitioned: list[str] = []
    remaining_orders: list[str] = []
    reconciliation_required: list[str] = []
    order_numbers = sorted({
        _text(row.get("order_number"))
        for row in batch.get("lines") or []
        if _text(row.get("order_number"))
    })
    for order_number in order_numbers:
        try:
            complete, remaining = await _reconcile_order_stage(
                db,
                user_id=user_id,
                order_number=order_number,
                batch_id=_text(batch.get("id")),
                actor=actor,
            )
            if complete:
                transitioned.append(order_number)
            elif remaining > 0:
                remaining_orders.append(order_number)
        except Exception:
            reconciliation_required.append(order_number)
    return {
        "transitioned_order_numbers": transitioned,
        "remaining_review_order_numbers": remaining_orders,
        "reconciliation_required": reconciliation_required,
    }


def make_reviewed_preparation_batches_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/reviewed-preparation-batches-v1",
        tags=["Reviewed Preparation Batches"],
    )

    @router.post("/batches")
    async def create_batch(
        payload: CreatePreparationBatchRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        await ensure_preparation_batch_indexes(db)
        await _cleanup_stale_builds(db, user_id)

        existing = await db[BATCHES].find_one(
            {
                "user_id": user_id,
                "client_request_id": payload.client_request_id,
            },
            {"_id": 0},
        )
        if existing:
            if _text(existing.get("status")) == "ready":
                reconciliation = await _reconcile_batch_orders(
                    db,
                    user_id=user_id,
                    batch=existing,
                    actor=reviewer,
                )
                await db[BATCHES].update_one(
                    {"user_id": user_id, "id": existing.get("id")},
                    {"$set": reconciliation},
                )
                existing.update(reconciliation)
                return _batch_response(existing)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preparation_batch_build_in_progress",
                    "message": "يجري إنشاء هذا الملف بالفعل.",
                },
            )

        context = await load_reviewed_product_context(
            db,
            user_id=user_id,
            limit=MAX_REVIEWED_ORDERS,
        )
        if context.get("truncated"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "reviewed_catalog_truncated",
                    "message": "عدد الطلبات يتجاوز الحد التشغيلي؛ لا يمكن إنشاء ملف من بيانات ناقصة.",
                },
            )
        selection_rows = [value.model_dump() for value in payload.selections]
        try:
            planned = plan_preparation_allocations(
                context["catalog"].get("products") or [],
                selection_rows,
            )
        except ValueError as exc:
            code = str(exc)
            messages = {
                "reviewed_product_not_available": "المنتج لم يعد متاحًا في مرحلة تمت المراجعة. حدّث الصفحة.",
                "preparation_quantity_exceeds_remaining": "الكمية المختارة أكبر من الكمية المتبقية.",
                "reviewed_product_allocation_incomplete": "تعذّر توزيع الكمية على الطلبات المتاحة. حدّث الصفحة.",
                "duplicate_product_group": "لا يمكن تكرار المنتج نفسه داخل الملف.",
            }
            raise HTTPException(
                status_code=409,
                detail={"code": code, "message": messages.get(code, "اختيار المنتجات غير صالح.")},
            ) from exc

        # Resolve the gate at the selected product grain.  An order-wide stop
        # blocks every selected line, while a product stop blocks only that
        # product and does not freeze unrelated lines from the same order.
        for order_number, order_item_id in sorted({
            (_text(row.get("order_number")), _text(row.get("order_item_id")))
            for row in planned
            if _text(row.get("order_number"))
        }):
            await enforce_stage_instructions(
                db,
                user_id=user_id,
                order_number=order_number,
                order_item_id=order_item_id,
                stage="reviewed",
                actor_id=_text(reviewer.get("id")),
            )

        batch_id = uuid.uuid4().hex
        now = _now()
        riyadh_now = riyadh_now_aware()
        file_name = (
            f"ملف_تجهيز_{riyadh_now.strftime('%Y-%m-%d_%H-%M')}_"
            f"{batch_id[:8]}.pdf"
        )
        shell = {
            "id": batch_id,
            "user_id": user_id,
            "client_request_id": payload.client_request_id,
            "status": "building",
            "title": "تجهيز المنتجات",
            "file_name": file_name,
            "selections": selection_rows,
            "created_at": now,
            "created_by": _text(reviewer.get("id")),
            "created_by_name": _text(reviewer.get("name") or reviewer.get("email")),
            "expires_at": now + timedelta(minutes=BATCH_BUILD_TTL_MINUTES),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        try:
            await db[BATCHES].insert_one(shell)
        except DuplicateKeyError:
            duplicate = await db[BATCHES].find_one(
                {"user_id": user_id, "client_request_id": payload.client_request_id},
                {"_id": 0},
            )
            if duplicate and _text(duplicate.get("status")) == "ready":
                return _batch_response(duplicate)
            raise HTTPException(status_code=409, detail={"code": "preparation_batch_build_in_progress"})

        allocation_docs: list[dict[str, Any]] = []
        reservation_expiry = now + timedelta(minutes=BATCH_BUILD_TTL_MINUTES)
        for allocation in planned:
            for unit_index in allocation.get("unit_indices") or []:
                allocation_docs.append({
                    "id": uuid.uuid4().hex,
                    "user_id": user_id,
                    "batch_id": batch_id,
                    "status": "reserved",
                    "group_key": allocation["group_key"],
                    "order_number": allocation["order_number"],
                    "order_item_id": allocation["order_item_id"],
                    "unit_index": int(unit_index),
                    "reserved_at": now,
                    "expires_at": reservation_expiry,
                })
        try:
            await db[PREPARATION_UNIT_ALLOCATIONS].insert_many(
                allocation_docs,
                ordered=True,
            )
        except (BulkWriteError, DuplicateKeyError) as exc:
            await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({
                "user_id": user_id,
                "batch_id": batch_id,
            })
            await db[BATCHES].delete_one({"user_id": user_id, "id": batch_id})
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preparation_units_already_allocated",
                    "message": "حجز موظف آخر بعض القطع. حدّث الصفحة وأعد الاختيار.",
                },
            ) from exc

        try:
            batch_lines = await _build_batch_lines(context, planned)
            pdf_bytes = generate_preparation_pdf(
                [
                    _line_from_batch_storage(
                        row,
                        {"id": batch_id, "user_id": user_id},
                    )
                    for row in batch_lines
                ],
                serial_start=1,
                title="تجهيز المنتجات",
            )
            if not pdf_bytes.startswith(b"%PDF"):
                raise ValueError("invalid_preparation_pdf")
            order_numbers = sorted({row["order_number"] for row in batch_lines})
            selected_product_count = len({row["group_key"] for row in batch_lines})
            allocated_quantity = sum(int(row.get("quantity") or 0) for row in batch_lines)
            ready_at = _now()
            ready_patch = {
                "status": "ready",
                "ready_at": ready_at,
                "updated_at": ready_at,
                "lines": batch_lines,
                "card_count": len(batch_lines),
                "order_count": len(order_numbers),
                "order_numbers": order_numbers,
                "selected_product_count": selected_product_count,
                "allocated_quantity": allocated_quantity,
                "pdf_size_bytes": len(pdf_bytes),
                "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            }
            await db[BATCHES].update_one(
                {"user_id": user_id, "id": batch_id, "status": "building"},
                {"$set": ready_patch, "$unset": {"expires_at": ""}},
            )
            await db[PREPARATION_UNIT_ALLOCATIONS].update_many(
                {"user_id": user_id, "batch_id": batch_id, "status": "reserved"},
                {
                    "$set": {"status": "committed", "committed_at": ready_at},
                    "$unset": {"expires_at": ""},
                },
            )
        except HTTPException:
            await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({"user_id": user_id, "batch_id": batch_id})
            await db[BATCHES].delete_one({"user_id": user_id, "id": batch_id})
            raise
        except Exception as exc:
            await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({"user_id": user_id, "batch_id": batch_id})
            await db[BATCHES].delete_one({"user_id": user_id, "id": batch_id})
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "preparation_batch_generation_failed",
                    "message": "تعذّر إنشاء ملف التجهيز ولم تُخصم أي قطعة.",
                },
            ) from exc

        batch = await db[BATCHES].find_one(
            {"user_id": user_id, "id": batch_id},
            {"_id": 0},
        ) or {**shell, **ready_patch}
        reconciliation = await _reconcile_batch_orders(
            db,
            user_id=user_id,
            batch=batch,
            actor=reviewer,
        )
        await db[BATCHES].update_one(
            {"user_id": user_id, "id": batch_id},
            {"$set": reconciliation},
        )
        batch.update(reconciliation)
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "batch_id": batch_id,
            "event_type": "preparation_batch_created",
            "order_numbers": batch.get("order_numbers") or [],
            "allocated_quantity": batch.get("allocated_quantity"),
            "occurred_at": _now_iso(),
            "actor_id": _text(reviewer.get("id")),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
        return _batch_response(batch)

    @router.post("/batches/{batch_id}/repair-customer-options")
    async def repair_customer_options(
        batch_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        batch = await db[BATCHES].find_one(
            {"user_id": user_id, "id": batch_id, "status": "ready"},
            {"_id": 0},
        )
        if not batch:
            raise HTTPException(
                status_code=404,
                detail={"code": "preparation_batch_not_found"},
            )
        order_numbers = sorted({
            _text(row.get("order_number"))
            for row in batch.get("lines") or []
            if isinstance(row, dict) and _text(row.get("order_number"))
        })
        refresh_failures: list[dict[str, Any]] = []
        refresh_limit = asyncio.Semaphore(8)

        async def refresh_one(order_number: str) -> tuple[str, dict[str, Any]]:
            async with refresh_limit:
                result = await refresh_order_from_salla(
                    db,
                    user_id,
                    order_number,
                    force=True,
                    minimum_fresh_seconds=0,
                    allow_auto_fulfillment=False,
                )
                return order_number, result

        refresh_results = await asyncio.gather(*(
            refresh_one(order_number) for order_number in order_numbers
        ))
        for order_number, result in refresh_results:
            if not result.get("ok") or not result.get("found"):
                refresh_failures.append({
                    "order_number": order_number,
                    "code": _text(result.get("code")) or "salla_refresh_failed",
                })

        repository = MongoOrderRepository(db)
        identities_by_order: dict[str, list[Any]] = {}
        for order_number in order_numbers:
            try:
                order = await get_order(
                    repository,
                    user_id=user_id,
                    order_number=order_number,
                )
            except OrderNotFoundError:
                continue
            identities_by_order[order_number] = map_order_item_identities(order)
        workflow_rows = await db[WORKFLOWS].find(
            {"user_id": user_id, "order_number": {"$in": order_numbers}},
            {"_id": 0},
        ).to_list(len(order_numbers) or 1)
        workflows_by_order = {
            _text(row.get("order_number")): row for row in workflow_rows
        }
        repaired_lines, repaired_count, unresolved = repair_batch_line_customer_options(
            [dict(row) for row in batch.get("lines") or [] if isinstance(row, dict)],
            identities_by_order=identities_by_order,
            workflows_by_order=workflows_by_order,
        )
        repaired_batch = {**batch, "lines": repaired_lines}
        pdf_bytes = render_preparation_batch_pdf(repaired_batch)
        now = _now()
        patch = {
            "lines": repaired_lines,
            "pdf_size_bytes": len(pdf_bytes),
            "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "customer_options_repaired_at": now,
            "customer_options_repaired_by": _text(reviewer.get("id")),
            "customer_options_repaired_line_count": repaired_count,
            "customer_options_repair_unresolved": unresolved,
            "updated_at": now,
        }
        await db[BATCHES].update_one(
            {"user_id": user_id, "id": batch_id, "status": "ready"},
            {"$set": patch},
        )
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "batch_id": batch_id,
            "event_type": "preparation_batch_customer_options_repaired",
            "repaired_line_count": repaired_count,
            "unresolved": unresolved,
            "refresh_failures": refresh_failures,
            "occurred_at": now,
            "actor_id": _text(reviewer.get("id")),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
        return {
            "ok": not unresolved and not refresh_failures,
            "batch_id": batch_id,
            "repaired_line_count": repaired_count,
            "unresolved": unresolved,
            "refresh_failures": refresh_failures,
            "pdf_sha256": patch["pdf_sha256"],
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.get("/batches")
    async def list_batches(
        limit: int = Query(20, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        rows = await db[BATCHES].find(
            {"user_id": user_id, "status": "ready"},
            {"_id": 0, "lines": 0},
        ).sort("created_at", -1).limit(limit).to_list(limit)
        return {"items": [_batch_response(row) for row in rows]}

    @router.get("/batches/{batch_id}/pdf")
    async def download_batch_pdf(
        batch_id: str,
        user: dict = Depends(current_user),
    ) -> StreamingResponse:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        batch = await db[BATCHES].find_one(
            {"user_id": user_id, "id": batch_id, "status": "ready"},
            {"_id": 0},
        )
        if not batch:
            raise HTTPException(status_code=404, detail={"code": "preparation_batch_not_found"})
        try:
            pdf_bytes = render_preparation_batch_pdf(batch)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "preparation_batch_pdf_failed"},
            ) from exc
        file_name = _text(batch.get("file_name")) or f"preparation-{batch_id}.pdf"
        encoded = quote(file_name)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=preparation-{batch_id}.pdf; "
                    f"filename*=UTF-8''{encoded}"
                ),
                "X-Mezan-Batch-Id": batch_id,
            },
        )

    return router


__all__ = [
    "BATCHES",
    "CreatePreparationBatchRequest",
    "PreparationProductSelection",
    "ensure_preparation_batch_indexes",
    "make_reviewed_preparation_batches_router",
    "plan_preparation_allocations",
    "render_preparation_batch_pdf",
]
