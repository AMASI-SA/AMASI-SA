"""Deterministic rules for configuration-aware Mezan inventory.

Inventory quantities belong to physical branch locations. A receipt line may
represent either a base unit that still needs preparation or a fully prepared
unit whose exact order specifications are already complete.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Iterable


PREPARATION_STATE_REQUIRES_PREPARATION = "requires_preparation"
PREPARATION_STATE_READY_COMPLETE = "ready_complete"
PREPARATION_STATES = {
    PREPARATION_STATE_REQUIRES_PREPARATION,
    PREPARATION_STATE_READY_COMPLETE,
}


def normalize_specification_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.strip().casefold().split())


def normalize_specification_name(value: Any) -> str:
    key = normalize_specification_text(value)
    compact = key.replace("_", " ").replace("-", " ")
    if "لون" in compact or compact in {"color", "colour"}:
        return "اللون"
    if "مقاس" in compact or "حجم" in compact or compact == "size":
        return "المقاس"
    if "خامة" in compact or "مادة" in compact or compact == "material":
        return "الخامة"
    if "الاسم" in compact or compact in {"اسم", "name", "customer name"}:
        return "الاسم"
    return key


def canonical_specifications(value: Any) -> dict[str, str]:
    """Return stable, non-empty specification pairs sorted by key."""
    pairs: Iterable[tuple[Any, Any]]
    if isinstance(value, dict):
        pairs = value.items()
    elif isinstance(value, list):
        pairs = (
            (
                row.get("name") or row.get("key") or row.get("label"),
                row.get("value") or row.get("text"),
            )
            for row in value
            if isinstance(row, dict)
        )
    else:
        pairs = []

    result: dict[str, str] = {}
    for raw_key, raw_value in pairs:
        key = normalize_specification_name(raw_key)
        spec_value = normalize_specification_text(raw_value)
        if not key or not spec_value:
            continue
        result[key] = spec_value
    return dict(sorted(result.items()))


def build_inventory_configuration_key(
    *,
    sku: Any,
    preparation_state: str,
    specifications: Any,
) -> str:
    if preparation_state not in PREPARATION_STATES:
        raise ValueError("invalid_preparation_state")
    canonical = canonical_specifications(specifications)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    normalized_sku = str(sku or "").strip().upper()
    return f"{normalized_sku}|state={preparation_state}|specs={digest}"


def _display_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("value", "name", "label", "text", "title"):
            candidate = value.get(key)
            if candidate not in (None, "", [], {}):
                return _display_value(candidate)
        return None
    if isinstance(value, list):
        values = [
            str(extracted)
            for entry in value
            if (extracted := _display_value(entry)) not in (None, "")
        ]
        return " / ".join(values) if values else None
    return value


def order_item_specifications(item: Any) -> dict[str, str]:
    """Extract an auditable specification signature from a canonical item."""
    values: dict[str, Any] = {}
    normalized = getattr(item, "options_normalized", None) or {}
    if isinstance(normalized, dict):
        values.update({
            str(name): _display_value(value)
            for name, value in normalized.items()
        })

    for row in getattr(item, "custom_fields", None) or []:
        if not isinstance(row, dict):
            continue
        name = (
            row.get("name")
            or row.get("label")
            or row.get("key")
            or row.get("title")
        )
        value = _display_value(
            row.get("value")
            or row.get("text")
            or row.get("selected")
            or row.get("answer")
        )
        if name not in (None, "") and value not in (None, "", [], {}):
            values[str(name)] = value

    for name, value in (
        ("اللون", getattr(item, "color", None)),
        ("المقاس", getattr(item, "size", None)),
        ("الخامة", getattr(item, "material", None)),
    ):
        if value not in (None, ""):
            values.setdefault(name, value)
    return canonical_specifications(values)


def specifications_are_exact(
    inventory_specifications: Any,
    order_specifications: Any,
) -> bool:
    return canonical_specifications(
        inventory_specifications
    ) == canonical_specifications(order_specifications)


def specifications_are_compatible_base(
    inventory_specifications: Any,
    order_specifications: Any,
) -> bool:
    """Base stock may constrain color/size while leaving a name unfinished."""
    inventory = canonical_specifications(inventory_specifications)
    order = canonical_specifications(order_specifications)
    return all(order.get(key) == value for key, value in inventory.items())


def choose_inventory_rows(
    *,
    rows: list[dict[str, Any]],
    identifiers: set[str],
    quantity: float,
    order_specifications: Any,
    preparation_required: bool,
) -> dict[str, Any]:
    """Choose ready-complete stock first, then compatible base stock.

    The function mutates ``remaining`` only after a candidate group can cover
    the complete requested quantity.
    """
    matching_product_rows = [
        row
        for row in rows
        if float(row.get("remaining") or 0) > 0
        and row.get("warehouse_id")
        and not identifiers.isdisjoint(row.get("identifiers") or set())
    ]
    ready_rows = [
        row
        for row in matching_product_rows
        if row.get("preparation_state") == PREPARATION_STATE_READY_COMPLETE
        and specifications_are_exact(
            row.get("specifications"),
            order_specifications,
        )
    ]
    base_rows = [
        row
        for row in matching_product_rows
        if row.get("preparation_state")
        in {None, "", PREPARATION_STATE_REQUIRES_PREPARATION}
        and specifications_are_compatible_base(
            row.get("specifications"),
            order_specifications,
        )
    ]

    candidate_groups: list[tuple[str, list[dict[str, Any]]]] = []
    if preparation_required:
        candidate_groups.append(("ready_complete", ready_rows))
        candidate_groups.append(("requires_preparation", base_rows))
        candidate_groups.append(("mixed", [*ready_rows, *base_rows]))
    else:
        candidate_groups.append(("ready_complete", ready_rows))
        candidate_groups.append(("standard", base_rows))
        candidate_groups.append(("mixed", [*ready_rows, *base_rows]))

    available_total = sum(
        float(row.get("remaining") or 0)
        for row in matching_product_rows
    )
    compatible_total = sum(
        float(row.get("remaining") or 0)
        for row in [*ready_rows, *base_rows]
    )
    for match_type, candidates in candidate_groups:
        candidate_available = sum(
            float(row.get("remaining") or 0)
            for row in candidates
        )
        if candidate_available < quantity:
            continue
        needed = quantity
        warehouse_ids: set[str] = set()
        configuration_keys: set[str] = set()
        allocations: list[dict[str, Any]] = []
        for row in candidates:
            take = min(float(row.get("remaining") or 0), needed)
            row["remaining"] = float(row.get("remaining") or 0) - take
            needed -= take
            if take > 0:
                warehouse_ids.add(str(row["warehouse_id"]))
                if row.get("configuration_key"):
                    configuration_keys.add(str(row["configuration_key"]))
                allocations.append({
                    "inventory_row_key": row.get("key"),
                    "location_id": row.get("location_id"),
                    "item_index": row.get("item_index"),
                    "warehouse_id": row.get("warehouse_id"),
                    "receipt_id": row.get("receipt_id"),
                    "lot_id": row.get("lot_id"),
                    "configuration_key": row.get("configuration_key"),
                    "quantity": take,
                })
            if needed <= 0:
                break
        return {
            "available": True,
            "available_quantity": candidate_available,
            "total_product_quantity": available_total,
            "warehouse_ids": sorted(warehouse_ids),
            "configuration_keys": sorted(configuration_keys),
            "allocations": allocations,
            "match_type": match_type,
            "preparation_satisfied_by_ready_stock": (
                preparation_required and match_type == "ready_complete"
            ),
        }
    return {
        "available": False,
        "available_quantity": compatible_total,
        "total_product_quantity": available_total,
        "warehouse_ids": [],
        "configuration_keys": [],
        "allocations": [],
        "match_type": None,
        "preparation_satisfied_by_ready_stock": False,
    }


__all__ = [
    "PREPARATION_STATE_READY_COMPLETE",
    "PREPARATION_STATE_REQUIRES_PREPARATION",
    "PREPARATION_STATES",
    "build_inventory_configuration_key",
    "canonical_specifications",
    "choose_inventory_rows",
    "normalize_specification_text",
    "normalize_specification_name",
    "order_item_specifications",
    "specifications_are_compatible_base",
    "specifications_are_exact",
]
