"""Strict, read-only parity comparisons required before V3 cutover."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any, Iterable


def _canonical(value: Any) -> Any:
    """Canonicalize without Python's bool/int or coercion equality leaks."""
    if value is None:
        return {"type": "null"}
    if type(value) is bool:
        return {"type": "bool", "value": value}
    if type(value) is int:
        return {"type": "int", "value": value}
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("parity evidence contains a non-finite number")
        return {"type": "float", "value": value}
    if type(value) is str:
        return {"type": "str", "value": value}
    if isinstance(value, list):
        return {"type": "list", "value": [_canonical(item) for item in value]}
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise TypeError("parity evidence object keys must be strings")
        entries = []
        for key in sorted(value):
            entries.append({"key": key, "value": _canonical(value[key])})
        return {"type": "object", "value": entries}
    raise TypeError(
        f"unsupported parity evidence value type: {type(value).__name__}"
    )


def _semantic_named_values(value: Any) -> list[dict[str, Any]]:
    """Compare customer choices by meaning, never by provider provenance."""
    rows: list[dict[str, Any]] = []
    if value is None:
        return rows
    if isinstance(value, dict):
        source = [{"name": key, "value": entry} for key, entry in value.items()]
    elif isinstance(value, list):
        source = value
    else:
        raise ValueError("malformed semantic option container")
    for entry in source:
        if (
            not isinstance(entry, dict)
            or "name" not in entry
            or "value" not in entry
            or type(entry.get("name")) is not str
            or not entry["name"].strip()
        ):
            raise ValueError("malformed semantic option row")
        rows.append({
            "name": _canonical(entry["name"]),
            "value": _canonical(entry["value"]),
        })
    return sorted(
        rows,
        key=lambda row: json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    )


def _fulfillment_signature(order: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    products = order.get("products")
    if products is None:
        products = []
    if not isinstance(products, list):
        raise ValueError("malformed fulfillment products container")
    for item in products:
        if not isinstance(item, dict):
            raise ValueError("malformed fulfillment product row")
        result.append({
            "order_item_id": _canonical(item.get("order_item_id")),
            "product_id": _canonical(item.get("product_id")),
            "parent_product_id": _canonical(item.get("parent_product_id")),
            "variant_id": _canonical(item.get("variant_id")),
            "sku": _canonical(item.get("sku")),
            "quantity": _canonical(item.get("quantity")),
            "options": _semantic_named_values(item.get("options")),
            "custom_fields": _semantic_named_values(item.get("custom_fields")),
        })
    return sorted(
        result,
        key=lambda row: json.dumps(
            _canonical(row), ensure_ascii=False, sort_keys=True, default=str
        ),
    )


def compare_fulfillment_parity(
    legacy_order: dict[str, Any],
    v3_order: dict[str, Any],
) -> dict[str, Any]:
    legacy = _fulfillment_signature(legacy_order)
    v3 = _fulfillment_signature(v3_order)
    v3_items_authoritative = v3_order.get("items_authoritative") is True
    v3_items_payload_valid = v3_order.get("items_payload_valid") is True
    v3_needs_enrichment = v3_order.get("needs_items_enrichment") is True
    v3_enrichment_complete = v3_order.get("needs_items_enrichment") is False
    signal_revision = v3_order.get("signal_revision")
    success_revision = v3_order.get("items_success_signal_revision")
    latest_success_matches_signal = bool(
        isinstance(signal_revision, int)
        and not isinstance(signal_revision, bool)
        and signal_revision > 0
        and isinstance(success_revision, int)
        and not isinstance(success_revision, bool)
        and success_revision > 0
        and success_revision == signal_revision
    )
    passed = bool(
        legacy == v3
        and v3_items_authoritative
        and v3_items_payload_valid
        and v3_enrichment_complete
        and latest_success_matches_signal
    )
    return {
        "passed": passed,
        "v3_items_authoritative": v3_items_authoritative,
        "v3_items_payload_valid": v3_items_payload_valid,
        "v3_needs_items_enrichment": v3_needs_enrichment,
        "v3_enrichment_complete": v3_enrichment_complete,
        "latest_success_matches_signal": latest_success_matches_signal,
        "signal_revision": signal_revision,
        "items_success_signal_revision": success_revision,
        "legacy_product_count": len(legacy),
        "v3_product_count": len(v3),
        "legacy": legacy,
        "v3": v3,
    }


def compare_qoyod_parity(
    legacy_dry_run: dict[str, Any],
    v3_dry_run: dict[str, Any],
) -> dict[str, Any]:
    def valid_shape(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        eligible = value.get("eligible")
        payload = value.get("payload")
        idempotency_key = value.get("idempotency_key")
        reason = value.get("ineligible_reason")
        return bool(
            type(eligible) is bool
            and isinstance(payload, dict)
            and (
                bool(payload)
                if eligible
                else type(reason) is str and bool(reason.strip())
            )
            and type(idempotency_key) is str
            and bool(idempotency_key.strip())
            and type(value.get("provider_write_reached")) is bool
        )

    legacy_valid = valid_shape(legacy_dry_run)
    v3_valid = valid_shape(v3_dry_run)
    legacy_eligible = legacy_dry_run.get("eligible")
    v3_eligible = v3_dry_run.get("eligible")
    payload_equal = _canonical(legacy_dry_run.get("payload")) == _canonical(
        v3_dry_run.get("payload")
    )
    idempotency_equal = (
        legacy_dry_run.get("idempotency_key")
        == v3_dry_run.get("idempotency_key")
    )
    ineligible_reason_equal = (
        legacy_dry_run.get("ineligible_reason")
        == v3_dry_run.get("ineligible_reason")
    )
    provider_write_reached = (
        legacy_dry_run.get("provider_write_reached") is True
        or v3_dry_run.get("provider_write_reached") is True
    )
    return {
        "passed": (
            legacy_valid
            and v3_valid
            and legacy_eligible == v3_eligible
            and payload_equal
            and idempotency_equal
            and ineligible_reason_equal
            and not provider_write_reached
        ),
        "legacy_evidence_valid": legacy_valid,
        "v3_evidence_valid": v3_valid,
        "eligibility_unchanged": legacy_eligible == v3_eligible,
        "payload_unchanged": payload_equal,
        "idempotency_key_unchanged": idempotency_equal,
        "ineligible_reason_unchanged": ineligible_reason_equal,
        "legacy": deepcopy(legacy_dry_run),
        "v3": deepcopy(v3_dry_run),
        "provider_write_reached": provider_write_reached,
    }


def _duplicates(rows: Iterable[dict[str, Any]]) -> list[str]:
    counts = Counter(
        str(row.get("order_number") or "").strip()
        for row in rows
        if str(row.get("order_number") or "").strip()
    )
    return sorted(key for key, count in counts.items() if count > 1)


def _attribution_signature(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_order = {}
    revenue_by_campaign = defaultdict(float)
    attributed = 0
    unattributed = 0
    for row in rows:
        order_number = str(row.get("order_number") or "").strip()
        if not order_number:
            continue
        campaign_id = row.get("campaign_id")
        utm = {
            key: row.get(key)
            for key in sorted(row)
            if str(key).lower().startswith("utm_")
        }
        revenue = float(row.get("revenue") or row.get("total_amount") or 0)
        attributed_flag = bool(campaign_id or any(utm.values()))
        attributed += int(attributed_flag)
        unattributed += int(not attributed_flag)
        if campaign_id:
            revenue_by_campaign[str(campaign_id)] += revenue
        by_order[order_number] = {
            "campaign_id": _canonical(campaign_id),
            "utm": _canonical(utm),
            "revenue": revenue,
        }
    return {
        "by_order": by_order,
        "attributed": attributed,
        "unattributed": unattributed,
        "revenue_by_campaign": dict(sorted(revenue_by_campaign.items())),
    }


def compare_attribution_parity(
    legacy_rows: list[dict[str, Any]],
    v3_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    duplicates = {
        "legacy": _duplicates(legacy_rows),
        "v3": _duplicates(v3_rows),
    }
    legacy = _attribution_signature(legacy_rows)
    v3 = _attribution_signature(v3_rows)
    return {
        "passed": legacy == v3 and not duplicates["legacy"] and not duplicates["v3"],
        "duplicate_orders": duplicates,
        "legacy": legacy,
        "v3": v3,
    }
