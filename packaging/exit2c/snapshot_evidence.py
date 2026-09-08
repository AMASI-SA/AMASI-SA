"""Bounded, value-free diagnostics for strict preparation snapshot equality.

This module does not normalize, mutate, or decide acceptance of snapshots.
"""
from collections import Counter
from datetime import datetime
import math
import re

COLLECTIONS = {
    "REGISTRY": "mezan_preparation_file_registry_v2",
    "BATCHES": "mezan_preparation_batches_v2",
    "ALLOCATIONS": "mezan_preparation_unit_allocations_v2",
    "PIECES": "mezan_preparation_pieces_v1",
    "EVENTS": "mezan_preparation_piece_events_v1",
    "WORKFLOWS": "order_review_workflows",
}
IDENTITY_FIELDS = frozenset(("id", "user_id", "batch_id", "piece_id", "allocation_id", "order_number", "order_item_id", "unit_index", "client_request_id", "file_number", "product_id", "supplier_id", "responsible_employee_id"))
QUANTITY_FIELDS = frozenset(("quantity", "allocated_quantity", "expected_quantity", "selected_product_count", "order_count", "received_quantity", "remaining_quantity", "total_quantity"))
STATUS_FIELDS = frozenset(("status", "registry_status", "piece_registry_status", "preparation_assignment_status", "review_status", "assembly_status", "receipt_status", "salla_sync_status", "stage", "execution_status", "carrier_label_ready", "carrier_label_status", "salla_status_sync", "salla_status_sync_state", "preparation_receipt_status", "preparation_status", "branch_handoff_status", "preparation_employee_custody_status", "service_plan_status"))
FIELD_NAMES = tuple(sorted(IDENTITY_FIELDS | QUANTITY_FIELDS | STATUS_FIELDS | {
    "revision", "operational_items", "started_at", "in_progress_at", "preparation_fully_allocated_at", "piece_registry_materialized_at", "preparation_received_at", "preparation_receipt_updated_at", "preparation_completed_at", "assembly_ready_at", "assembly_updated_at", "assembly_completed_at", "branch_handoff_at", "ready_to_ship_at", "salla_status_synced_at", "claimed_at", "estimated_due_at", "required_due_at", "schedule_updated_at",
    "created_at", "updated_at", "registered_at", "occurred_at", "expires_at", "received_at", "completed_at", "assigned_at", "reviewed_at", "finalized_at", "assembled_at", "last_synced_at", "file_date", "preparation_progress", "event_type", "carrier_label", "product_options_snapshot", "product_image_snapshot", "image_url", "images", "history", "lines", "items", "salla_updated", "qoyod_updated", "mezan_only",
}))
FLAGS = ("IDENTITIES_CHANGED", "QUANTITIES_CHANGED", "STATUSES_CHANGED", "ORDER_CHANGED", "UNKNOWN_FIELDS_CHANGED")
MAX_COUNT = 10000
MAX_LINES = 128
_MISSING = ("missing",)


def _freeze(value, budget, depth=0):
    budget[0] += 1
    if depth > 32 or budget[0] > 200000:
        raise ValueError("snapshot bounds")
    kind = type(value)
    if value is None:
        return ("none",)
    if kind in (str, bytes):
        budget[1] += len(value)
        if budget[1] > 2000000:
            raise ValueError("snapshot bounds")
        return (kind.__name__, value)
    if kind in (bool, int):
        return (kind.__name__, value)
    if kind is float and math.isfinite(value):
        return ("float", value)
    if kind is datetime:
        return ("datetime", value)
    if kind in (tuple, list):
        return (kind.__name__, tuple(_freeze(item, budget, depth + 1) for item in value))
    if kind is dict and all(type(key) is str for key in value):
        return ("dict", tuple(sorted((_freeze(key, budget, depth + 1), _freeze(item, budget, depth + 1)) for key, item in value.items())))
    raise ValueError("snapshot type")


def _validate(snapshot):
    if type(snapshot) is not dict or set(snapshot) != {"identity", "documents"}:
        raise ValueError("snapshot shape")
    documents = snapshot["documents"]
    if type(documents) is not dict or set(documents) != set(COLLECTIONS.values()):
        raise ValueError("snapshot groups")
    if any(type(rows) is not list or len(rows) > MAX_COUNT or any(type(row) is not dict for row in rows) for rows in documents.values()):
        raise ValueError("snapshot rows")
    if any(len(set().union(*(set(row) for row in rows))) > 256 for rows in documents.values()):
        raise ValueError("snapshot fields bounds")
    _freeze(snapshot, [0, 0])


def _values(rows, fields):
    return Counter(tuple((field, _freeze(row[field], [0, 0]) if field in row else _MISSING) for field in sorted(fields)) for row in rows)


def snapshot_lines(expected, actual):
    """Explain unequal original snapshots without emitting any document values."""
    try:
        _validate(expected)
        _validate(actual)
        if expected == actual:
            return []
        lines = ["SNAPSHOT IDENTITY_CHANGED " + str(expected["identity"] != actual["identity"]).lower()]
        for alias, collection in COLLECTIONS.items():
            before, after = expected["documents"][collection], actual["documents"][collection]
            if before == after:
                continue
            lines.append(f"SNAPSHOT {alias} COUNT {len(before)} {len(after)}")
            changed = {field for field in set().union(*(set(row) for row in before + after)) if [_freeze(row[field], [0, 0]) if field in row else _MISSING for row in before] != [_freeze(row[field], [0, 0]) if field in row else _MISSING for row in after]}
            whole_before = Counter(_freeze(row, [0, 0]) for row in before)
            whole_after = Counter(_freeze(row, [0, 0]) for row in after)
            flags = {
                "IDENTITIES_CHANGED": _values(before, IDENTITY_FIELDS) != _values(after, IDENTITY_FIELDS),
                "QUANTITIES_CHANGED": _values(before, IDENTITY_FIELDS | QUANTITY_FIELDS) != _values(after, IDENTITY_FIELDS | QUANTITY_FIELDS),
                "STATUSES_CHANGED": _values(before, IDENTITY_FIELDS | STATUS_FIELDS) != _values(after, IDENTITY_FIELDS | STATUS_FIELDS),
                "ORDER_CHANGED": whole_before == whole_after and before != after,
                "UNKNOWN_FIELDS_CHANGED": bool(changed - set(FIELD_NAMES)),
            }
            lines.extend(f"SNAPSHOT {alias} {flag} {str(flags[flag]).lower()}" for flag in FLAGS)
            lines.extend(f"SNAPSHOT {alias} FIELD {field}" for field in FIELD_NAMES if field in changed)
        return lines if len(lines) <= MAX_LINES and all(validate_snapshot_line(line) for line in lines) else ["SNAPSHOT unavailable"]
    except Exception:
        return ["SNAPSHOT unavailable"]


def validate_snapshot_line(line):
    if type(line) is not str or len(line) > 160:
        return False
    if line == "SNAPSHOT unavailable":
        return True
    if line in ("SNAPSHOT IDENTITY_CHANGED true", "SNAPSHOT IDENTITY_CHANGED false"):
        return True
    parts = line.split(" ")
    if len(parts) not in (4, 5) or parts[0] != "SNAPSHOT" or parts[1] not in COLLECTIONS:
        return False
    if len(parts) == 4:
        return (parts[2] in FLAGS and parts[3] in ("true", "false")) or (parts[2] == "FIELD" and parts[3] in FIELD_NAMES)
    return parts[2] == "COUNT" and all(re.fullmatch(r"0|[1-9][0-9]{0,4}", part) is not None and int(part) <= MAX_COUNT for part in parts[3:])
