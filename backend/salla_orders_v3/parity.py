"""Strict, read-only parity comparisons required before V3 cutover."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any, Iterable


def _canonical(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _fulfillment_signature(order: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in order.get("products") or []:
        if not isinstance(item, dict):
            continue
        result.append({
            "order_item_id": item.get("order_item_id"),
            "product_id": item.get("product_id"),
            "parent_product_id": item.get("parent_product_id"),
            "variant_id": item.get("variant_id"),
            "sku": item.get("sku"),
            "quantity": item.get("quantity"),
            "options": _canonical(item.get("options") or []),
            "custom_fields": _canonical(item.get("custom_fields") or []),
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
    passed = legacy == v3
    return {
        "passed": passed,
        "legacy_product_count": len(legacy),
        "v3_product_count": len(v3),
        "legacy": legacy,
        "v3": v3,
    }


def compare_qoyod_parity(
    legacy_dry_run: dict[str, Any],
    v3_dry_run: dict[str, Any],
) -> dict[str, Any]:
    legacy_eligible = bool(legacy_dry_run.get("eligible"))
    v3_eligible = bool(v3_dry_run.get("eligible"))
    payload_equal = _canonical(legacy_dry_run.get("payload")) == _canonical(
        v3_dry_run.get("payload")
    )
    idempotency_equal = (
        legacy_dry_run.get("idempotency_key")
        == v3_dry_run.get("idempotency_key")
    )
    return {
        "passed": (
            legacy_eligible == v3_eligible
            and payload_equal
            and idempotency_equal
        ),
        "eligibility_unchanged": legacy_eligible == v3_eligible,
        "payload_unchanged": payload_equal,
        "idempotency_key_unchanged": idempotency_equal,
        "legacy": deepcopy(legacy_dry_run),
        "v3": deepcopy(v3_dry_run),
        "provider_write_reached": False,
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
            "campaign_id": campaign_id,
            "utm": utm,
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
