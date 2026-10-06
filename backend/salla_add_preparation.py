"""Build an isolated preparation file for an accepted Salla addition.

The caller validates authority, employee eligibility and component reservations,
then inserts these records in its owner transaction. This module only reads
service bindings and renders a real PDF in memory; it never persists records.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from zoneinfo import ZoneInfo

from preparation_file_registry import preparation_file_name
from preparation_piece_operations import (
    _service_context_for_batch,
    build_piece_documents,
    validate_materialized_piece_count,
)
from reviewed_preparation_batches import (
    MAX_BATCH_UNITS,
    _card_field_projection,
    render_preparation_batch_pdf,
)


SOURCE_LABEL = "منتج مضاف إلى الطلب"


def _identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _display(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def build_add_preparation(
    db, *, user_id, order_number, change_id, line, employee, actor, now
):
    """Return new ready batch/registry, assigned pieces and committed allocations."""
    quantity = line.get("quantity")
    if type(quantity) is not int or not 1 <= quantity <= MAX_BATCH_UNITS:
        raise ValueError("salla_add_preparation_invalid_quantity")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("salla_add_preparation_timezone_required")
    item_id = str(line.get("order_item_id") or "").strip()
    if not all((user_id, order_number, change_id, item_id, employee.get("id"))):
        raise ValueError("salla_add_preparation_identity_required")
    options = line.get("options")
    if not isinstance(options, dict):
        raise ValueError("salla_add_preparation_options_required")

    identity = _identity(user_id, order_number, change_id, item_id)
    batch_id = "salla-add-" + identity
    request_id = "salla-add-" + identity
    file_number = "PF-ADD-" + identity
    local_now = now.astimezone(ZoneInfo("Asia/Riyadh"))
    file_date = local_now.strftime("%Y-%m-%d")
    # Keep the full trusted source separately; the projection is display-only.
    fields = []
    for name, value in options.items():
        values = value if isinstance(value, list) else [value]
        fields.extend({"name": str(name), "value": _display(v)} for v in values)
    custom = line.get("custom_fields")
    if isinstance(custom, dict):
        fields.extend({"name": str(k), "value": _display(v)} for k, v in custom.items())
    elif custom is not None:
        fields.append({"name": "custom_fields", "value": _display(custom)})
    projected = _card_field_projection(fields, SOURCE_LABEL)
    unit_indices = list(range(1, quantity + 1))
    batch_line = {
        **deepcopy(line), **projected,
        "order_number": order_number, "order_item_id": item_id,
        "unit_indices": unit_indices, "group_key": identity,
        "line_number": 1, "line_index": 0,
        "product_name": line.get("product_name") or str(line.get("product_id") or item_id),
        "total_products_in_order": quantity,
        "file_spec_fields": fields, "preparation_note": SOURCE_LABEL,
        "source_line_snapshot": deepcopy(line),
    }
    common = {
        "user_id": user_id, "client_request_id": request_id,
        "status": "ready", "execution_status": "assigned",
        "file_number": file_number, "file_title": SOURCE_LABEL,
        "file_name": preparation_file_name(SOURCE_LABEL, file_date, quantity),
        "responsible_employee_id": employee["id"],
        "responsible_employee_name": employee.get("name") or employee.get("email") or employee["id"],
        "responsible_employee_email": employee.get("email"),
        "assignment_status": "assigned_not_started",
        "allocated_quantity": quantity, "selected_product_count": 1,
        "order_count": 1, "created_at": now, "updated_at": now,
        "created_by": actor.get("id"),
        "created_by_name": actor.get("name") or actor.get("email"),
        "change_id": change_id, "source": "salla_order_add_product",
        "source_label": SOURCE_LABEL,
        "piece_registry_status": "ready", "piece_count": quantity,
        "pieces_materialized_at": now,
        "mezan_only": True, "salla_updated": False, "qoyod_updated": False,
    }
    batch = {
        **deepcopy(common), "id": batch_id, "title": SOURCE_LABEL,
        "lines": [batch_line], "card_count": 1,
        "order_numbers": [order_number], "ready_at": now,
        "moved_to_in_progress": False,
    }
    registry = {
        **deepcopy(common), "id": "registry-" + identity,
        "batch_id": batch_id, "file_date": file_date,
        "file_date_display": local_now.strftime("%Y/%m/%d"),
        "expected_quantity": quantity, "registered_at": now,
    }
    services = await _service_context_for_batch(db, user_id=user_id, batch=batch)
    pieces = build_piece_documents(
        user_id=user_id, registry=registry, batch=batch,
        services_by_product=services, assigned_at=now,
    )
    for piece in pieces:
        piece.update({
            "generation": 0, "revision": 0, "current": True, "active": True,
            "change_id": change_id, "source": "salla_order_add_product",
            "source_label": SOURCE_LABEL, "reviewed_at": now,
            "lifecycle_milestones": [{"stage": "reviewed", "at": now, "actor_id": actor.get("id")}],
            "variant_id": deepcopy(line.get("variant_id")),
            "source_options_snapshot": deepcopy(options),
            "source_custom_fields_snapshot": deepcopy(custom),
            "source_line_snapshot": deepcopy(line),
        })
    validate_materialized_piece_count(batch=batch, registry=registry, pieces=pieces)
    pdf = render_preparation_batch_pdf(batch)
    if not pdf.startswith(b"%PDF"):
        raise ValueError("invalid_preparation_pdf")
    batch.update(pdf_size_bytes=len(pdf), pdf_sha256=hashlib.sha256(pdf).hexdigest())
    allocations = [{
        "id": "salla-add-unit-" + _identity(identity, unit_index),
        "user_id": user_id, "batch_id": batch_id, "status": "committed",
        "group_key": identity, "order_number": order_number,
        "order_item_id": item_id, "unit_index": unit_index,
        "reserved_at": now, "committed_at": now, "change_id": change_id,
    } for unit_index in unit_indices]
    return {"batch": batch, "registry": registry, "pieces": pieces, "allocations": allocations}
