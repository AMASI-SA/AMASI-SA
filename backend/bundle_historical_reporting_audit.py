"""Read-only, fail-closed evidence for historical bundle reporting corrections.

This does NOT price an order, backfill a lifecycle, or authorize fulfillment.
The original Salla sale-line payload must be independently present and agree
with the persisted canonical line. All customer text stays server-side.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Any

from order_cost_authority import item_dict, line_fingerprint, line_id


class BundleHistoricalEvidenceUnavailable(ValueError):
    """Safe, non-PII refusal suitable for an authenticated diagnostic."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _refuse(code: str) -> None:
    raise BundleHistoricalEvidenceUnavailable(code)


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str,
                             allow_nan=False).encode("utf-8")).hexdigest()


def _identity(value: Any) -> str | None:
    if value is None or isinstance(value, (bool, dict, list)):
        return None
    value = str(value).strip()
    return value if value and len(value) <= 256 else None


def _source_rows(item: dict) -> list[dict]:
    """Use the source field families already handled by the Salla mapper."""
    rows: list[dict] = []
    # Provider *item selections* only. Nested product/variant catalogs list
    # every available option, not necessarily what this buyer selected.
    # Those catalog arrays must never masquerade as historical choices.
    for candidate in (
        item.get("options"), item.get("choices"), item.get("attributes"),
        item.get("product_options"), item.get("selected_options"),
        item.get("customer_options"),
    ):
        if isinstance(candidate, list):
            rows.extend(candidate)
        elif isinstance(candidate, dict):
            rows.append(candidate)
    if len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        _refuse("historical_source_options_invalid")
    return rows


def _canonical_rows(item: dict) -> list[dict]:
    rows = item.get("options_raw") or item.get("selected_options") or item.get("options") or []
    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        _refuse("historical_canonical_options_invalid")
    return rows


def _selection(row: dict) -> dict:
    row = row.get("raw") if isinstance(row.get("raw"), dict) else row
    nested = row.get("option") if isinstance(row.get("option"), dict) else {}
    order_row_id = _identity(row.get("id"))
    declared_id = _identity(row.get("option_id"))
    nested_id = _identity(nested.get("id"))
    if declared_id and nested_id and declared_id != nested_id:
        _refuse("historical_option_identity_conflict")
    option_id = declared_id or nested_id or order_row_id
    if option_id is None:
        _refuse("historical_option_identity_missing")
    name = row.get("option_name") or row.get("name") or nested.get("name")
    if not isinstance(name, str) or not name.strip():
        _refuse("historical_option_name_missing")
    values = row.get("values") if "values" in row else row.get(
        "value", row.get("value_name", row.get("selected", row.get("answer", row.get("text")))))
    if isinstance(values, list):
        parts = values
    elif values in (None, "", []):
        parts = []
    else:
        parts = [values]
    if len(parts) > 12:
        _refuse("historical_option_value_limit")
    entries = []
    for value in parts:
        detail = value if isinstance(value, dict) else {}
        nested_value_id = _identity(detail.get("id"))
        selected_id = _identity(row.get("value_id"))
        if selected_id and nested_value_id and selected_id != nested_value_id:
            _refuse("historical_value_identity_conflict")
        label = (detail.get("name") or detail.get("label") or detail.get("value")
                 or detail.get("text")) if detail else value
        if not isinstance(label, (str, int, float)) or isinstance(label, bool):
            _refuse("historical_value_shape_invalid")
        text = str(label).strip()
        if not text or len(text) > 10000:
            _refuse("historical_value_empty_or_oversized")
        entries.append({"value_id": selected_id or nested_value_id,
                        "value_hash": _hash(text)})
    return {
        "order_row_id": order_row_id,
        "option_id": option_id,
        "name_hash": _hash(name.strip()),
        "values": sorted(entries, key=lambda v: (v["value_id"] or "", v["value_hash"])),
    }


def _ordered_evidence(rows: list[dict]) -> list[dict]:
    # Salla may return the same exact row in more than one selected-options
    # collection. A genuine conflicting duplicate identity is not evidence.
    entries = [_selection(row) for row in rows]
    ids: dict[str, dict] = {}
    for row in entries:
        key = row["option_id"]
        prior = ids.get(key)
        if prior is not None and prior != row:
            _refuse("historical_duplicate_option_conflict")
        ids[key] = row
    return sorted(ids.values(), key=lambda row: (
        row["option_id"], row["order_row_id"] or "", row["name_hash"]))


def inspect_historical_bundle_order(
    *, order_number: str, raw_order: dict,
    canonical_items: list[Any], target_line_id: str,
) -> dict:
    """Establish original source-option identity WITHOUT deriving any costs.

    Source and canonical line must agree. An absence or mismatch is a refusal,
    not a reason to consult today's catalog or to update stock/suppliers.
    """
    number = _identity(order_number)
    if not number or not isinstance(raw_order, dict):
        _refuse("historical_source_unavailable")
    provider_number = _identity(raw_order.get("reference_id") or raw_order.get("order_number"))
    if provider_number != number:
        _refuse("historical_source_order_mismatch")
    from bundle_reporting_cost import canonical_sale_created_at
    created_at = canonical_sale_created_at({"raw_by_source": {"salla_direct": raw_order}})
    if not created_at:
        _refuse("historical_source_date_missing")
    try:
        created = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        _refuse("historical_source_date_invalid")
    if created.tzinfo is None:
        _refuse("historical_source_date_invalid")

    if not isinstance(canonical_items, list) or not 0 < len(canonical_items) <= 100:
        _refuse("historical_lines_unavailable")
    target = [item_dict(item) for item in canonical_items if line_id(item) == target_line_id]
    if len(target) != 1:
        _refuse("historical_target_line_ambiguous")
    line = target[0]
    source_id = _identity(line.get("source_item_id"))
    if not source_id:
        _refuse("historical_source_line_id_missing")
    source_items = raw_order.get("items") or raw_order.get("products") or []
    if not isinstance(source_items, list) or len(source_items) > 100:
        _refuse("historical_source_lines_invalid")
    originals = [item for item in source_items
                 if isinstance(item, dict) and
                 _identity(item.get("id") or item.get("item_id")) == source_id]
    if len(originals) != 1:
        _refuse("historical_source_line_not_unique")

    source = originals[0]
    provider_product = source.get("product") if isinstance(source.get("product"), dict) else {}
    provider_id = _identity(source.get("product_id") or provider_product.get("id"))
    expected_ids = {_identity(line.get("product_id")), _identity(line.get("parent_product_id"))}
    expected_ids.discard(None)
    if provider_id and provider_id not in expected_ids:
        _refuse("historical_source_product_mismatch")
    if _identity(source.get("quantity")) != _identity(line.get("quantity")):
        # Canonical quantity can be 1.0 and original Salla is 1.
        from decimal import Decimal, InvalidOperation
        try:
            if Decimal(str(source.get("quantity"))) != Decimal(str(line.get("quantity"))):
                _refuse("historical_source_quantity_mismatch")
        except (InvalidOperation, TypeError, ValueError):
            _refuse("historical_source_quantity_mismatch")

    original_evidence = _ordered_evidence(_source_rows(source))
    canonical_evidence = _ordered_evidence(_canonical_rows(line))
    if not original_evidence or original_evidence != canonical_evidence:
        _refuse("historical_options_not_proven")
    fp = line_fingerprint(line)
    public_rows = [{
        "source_option_id": row["option_id"],
        "source_row_id_present": row["order_row_id"] is not None,
        "row_id_differs_from_option_id": bool(
            row["order_row_id"] and row["order_row_id"] != row["option_id"]),
        "selected_value_id_present": all(v["value_id"] is not None for v in row["values"]),
        "value_count": len(row["values"]),
    } for row in original_evidence]
    return {
        "order_number": number,
        "source_created_at": created_at,
        "line_fingerprint": fp,
        "source_choice_evidence_hash": _hash({
            "order": number, "line": source_id, "created_at": created_at,
            "fingerprint": fp, "options": original_evidence,
        }),
        "option_rows": public_rows,
        "original_source_verified": True,
        "historical_cost_verified": False,
        "ready_to_apply_cost": False,
        "next_gate": "sale_time_unit_cost_attestation_and_reviewed_manifest",
    }
