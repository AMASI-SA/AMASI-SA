"""Deterministic Mezan-to-Salla branch inventory synchronization rules.

Mezan physical locations are authoritative. Salla quantities are a branch
mirror. The pure functions in this module never perform provider or database
I/O, which keeps previews, execution checks and audit replays identical.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from product_fulfillment_rules import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    INVENTORY_POLICY_BRANCH_STOCK,
    STOCKOUT_POLICY_CLOSE,
    STOCKOUT_POLICY_PREORDER,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _whole_available(value: Any) -> int:
    try:
        return max(0, math.floor(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def validate_branch_mappings(
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize one-to-one active Salla-branch/Mezan-warehouse mappings."""
    normalized = []
    salla_ids: set[str] = set()
    warehouse_ids: set[str] = set()
    for row in mappings:
        salla_branch_id = _text(row.get("salla_branch_id"))
        warehouse_id = _text(row.get("mezan_warehouse_id"))
        if not salla_branch_id or not warehouse_id:
            raise ValueError("inventory_branch_mapping_incomplete")
        if salla_branch_id in salla_ids:
            raise ValueError("inventory_salla_branch_mapped_twice")
        if warehouse_id in warehouse_ids:
            raise ValueError("inventory_mezan_warehouse_mapped_twice")
        salla_ids.add(salla_branch_id)
        warehouse_ids.add(warehouse_id)
        normalized.append({
            "salla_branch_id": salla_branch_id,
            "mezan_warehouse_id": warehouse_id,
        })
    return sorted(
        normalized,
        key=lambda row: (
            row["salla_branch_id"],
            row["mezan_warehouse_id"],
        ),
    )


def remote_quantity_key(row: dict[str, Any]) -> str:
    variant_id = _text(
        row.get("sku_id")
        or row.get("variant_id")
        or row.get("product_sku_id")
    )
    if variant_id:
        return f"variant:{variant_id}"
    product_id = _text(row.get("id") or row.get("product_id"))
    return f"product:{product_id}" if product_id else ""


def _product_identifiers(product: dict[str, Any]) -> set[str]:
    values = {
        _text(product.get("mezan_product_id")),
        _text(product.get("salla_product_id")),
        _text(product.get("sku")),
    }
    values.discard("")
    return values


def _matching_base_rows(
    *,
    product: dict[str, Any],
    stock_rows: list[dict[str, Any]],
    warehouse_id: str,
) -> list[dict[str, Any]]:
    identifiers = _product_identifiers(product)
    return [
        row
        for row in stock_rows
        if _text(row.get("warehouse_id")) == warehouse_id
        and not identifiers.isdisjoint(row.get("identifiers") or set())
    ]


def build_mezan_branch_targets(
    *,
    products: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    stock_rows: list[dict[str, Any]],
    warehouse_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Build branch-specific Salla targets from available Mezan quantities.

    Variant products require an explicit Salla variant id (or exact variant
    SKU on legacy stock). Unassigned variant stock is reported and excluded,
    never copied to the parent product or another option.
    """
    profiles_by_product = {
        _text(row.get("salla_product_id")): row
        for row in profiles
        if _text(row.get("salla_product_id"))
    }
    targets: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for product in products:
        salla_product_id = _text(product.get("salla_product_id"))
        if not salla_product_id:
            skipped.append({
                "mezan_product_id": product.get("mezan_product_id"),
                "name": product.get("name"),
                "code": "product_not_published_to_salla",
            })
            continue
        profile = profiles_by_product.get(salla_product_id) or {}
        if profile.get("inventory_policy") != INVENTORY_POLICY_BRANCH_STOCK:
            skipped.append({
                "mezan_product_id": product.get("mezan_product_id"),
                "salla_product_id": salla_product_id,
                "name": product.get("name"),
                "code": "finished_goods_inventory_not_tracked",
            })
            continue

        stockout_policy = (
            profile.get("stockout_policy")
            if profile.get("stockout_policy")
            in {STOCKOUT_POLICY_CLOSE, STOCKOUT_POLICY_PREORDER}
            else STOCKOUT_POLICY_CLOSE
        )
        try:
            low_stock_threshold = max(
                0,
                min(
                    100000,
                    int(
                        profile.get(
                            "low_stock_threshold",
                            DEFAULT_LOW_STOCK_THRESHOLD,
                        )
                    ),
                ),
            )
        except (TypeError, ValueError, OverflowError):
            low_stock_threshold = DEFAULT_LOW_STOCK_THRESHOLD

        base_rows = _matching_base_rows(
            product=product,
            stock_rows=stock_rows,
            warehouse_id=warehouse_id,
        )
        variants = [
            row
            for row in product.get("variants") or []
            if isinstance(row, dict) and _text(row.get("id"))
        ]
        variants_count = _whole_available(product.get("variants_count"))
        if variants_count and not variants:
            issues.append({
                "mezan_product_id": product.get("mezan_product_id"),
                "salla_product_id": salla_product_id,
                "name": product.get("name"),
                "code": "product_variants_not_loaded",
                "blocking": True,
            })
            continue

        if variants:
            assigned_row_keys: set[str] = set()
            for variant in variants:
                variant_id = _text(variant.get("id"))
                variant_sku = _text(variant.get("sku")).upper()
                matching = []
                for row in base_rows:
                    explicit_variant_id = _text(
                        row.get("salla_variant_id")
                    )
                    row_skus = {
                        _text(value).upper()
                        for value in row.get("identifiers") or set()
                        if _text(value)
                    }
                    if (
                        explicit_variant_id == variant_id
                        or (
                            not explicit_variant_id
                            and variant_sku
                            and variant_sku in row_skus
                        )
                    ):
                        matching.append(row)
                        assigned_row_keys.add(_text(row.get("key")))
                available = sum(
                    _whole_available(row.get("remaining"))
                    for row in matching
                )
                desired_unlimited = (
                    stockout_policy == STOCKOUT_POLICY_PREORDER
                    and available <= 0
                )
                targets.append({
                    "target_key": f"variant:{variant_id}",
                    "identifier_type": "variant_id",
                    "identifier": variant_id,
                    "mezan_product_id": product.get("mezan_product_id"),
                    "salla_product_id": salla_product_id,
                    "salla_variant_id": variant_id,
                    "name": product.get("name"),
                    "variant_name": (
                        variant.get("display_name")
                        or variant.get("name")
                    ),
                    "sku": variant.get("sku") or product.get("sku"),
                    "warehouse_id": warehouse_id,
                    "desired_quantity": available,
                    "desired_unlimited": desired_unlimited,
                    "stockout_policy": stockout_policy,
                    "low_stock_threshold": low_stock_threshold,
                })
            unassigned = [
                row
                for row in base_rows
                if _text(row.get("key")) not in assigned_row_keys
                and _whole_available(row.get("remaining")) > 0
            ]
            if unassigned:
                issues.append({
                    "mezan_product_id": product.get("mezan_product_id"),
                    "salla_product_id": salla_product_id,
                    "name": product.get("name"),
                    "code": "variant_stock_not_linked",
                    "blocking": True,
                    "quantity": sum(
                        _whole_available(row.get("remaining"))
                        for row in unassigned
                    ),
                })
            continue

        available = sum(
            _whole_available(row.get("remaining"))
            for row in base_rows
        )
        desired_unlimited = (
            stockout_policy == STOCKOUT_POLICY_PREORDER
            and available <= 0
        )
        targets.append({
            "target_key": f"product:{salla_product_id}",
            "identifier_type": "id",
            "identifier": salla_product_id,
            "mezan_product_id": product.get("mezan_product_id"),
            "salla_product_id": salla_product_id,
            "salla_variant_id": None,
            "name": product.get("name"),
            "variant_name": None,
            "sku": product.get("sku"),
            "warehouse_id": warehouse_id,
            "desired_quantity": available,
            "desired_unlimited": desired_unlimited,
            "stockout_policy": stockout_policy,
            "low_stock_threshold": low_stock_threshold,
        })

    return {
        "targets": sorted(
            targets,
            key=lambda row: (
                _text(row.get("name")).casefold(),
                _text(row.get("variant_name")).casefold(),
                row["target_key"],
            ),
        ),
        "issues": issues,
        "skipped": skipped,
    }


def build_branch_sync_plan(
    *,
    salla_branch_id: str,
    warehouse_id: str,
    targets: list[dict[str, Any]],
    remote_quantities: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    remote_by_key = {
        key: row
        for row in remote_quantities
        if (key := remote_quantity_key(row))
    }
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for target in targets:
        key = target["target_key"]
        remote = remote_by_key.get(key)
        if not remote:
            issues.append({
                **target,
                "code": "salla_quantity_target_not_found",
                "blocking": True,
            })
            continue
        remote_quantity = _whole_available(remote.get("quantity"))
        remote_unlimited = bool(remote.get("unlimited_quantity"))
        desired_quantity = _whole_available(target.get("desired_quantity"))
        desired_unlimited = bool(target.get("desired_unlimited"))
        if remote_unlimited != desired_unlimited:
            operation = "overwrite"
            operation_quantity = desired_quantity
        elif desired_unlimited:
            operation = "noop"
            operation_quantity = 0
        else:
            delta = desired_quantity - remote_quantity
            operation = (
                "increment"
                if delta > 0
                else "decrement"
                if delta < 0
                else "noop"
            )
            operation_quantity = abs(delta)

        if desired_quantity <= 0:
            health_status = (
                "preorder"
                if target.get("stockout_policy") == STOCKOUT_POLICY_PREORDER
                else "out_of_stock"
            )
        elif desired_quantity <= int(
            target.get("low_stock_threshold") or 0
        ):
            health_status = "low_stock"
        else:
            health_status = "healthy"
        rows.append({
            **target,
            "salla_branch_id": _text(salla_branch_id),
            "warehouse_id": _text(warehouse_id),
            "remote_quantity": remote_quantity,
            "remote_unlimited": remote_unlimited,
            "operation": operation,
            "operation_quantity": operation_quantity,
            "health_status": health_status,
        })
    return {"rows": rows, "issues": issues}


def executable_payload_row(
    row: dict[str, Any],
    *,
    reason_id: str,
) -> dict[str, Any] | None:
    operation = row.get("operation")
    if operation == "noop":
        return None
    return {
        "identifer_type": row["identifier_type"],
        "identifer": row["identifier"],
        "quantity": int(row["operation_quantity"]),
        "mode": operation,
        "branch": row["salla_branch_id"],
        "reason_id": (
            int(reason_id)
            if str(reason_id).isdigit()
            else reason_id
        ),
        "unlimited_quantity": bool(row["desired_unlimited"]),
    }


def plan_signature(rows: list[dict[str, Any]]) -> str:
    material = [
        {
            "target_key": row.get("target_key"),
            "salla_branch_id": row.get("salla_branch_id"),
            "warehouse_id": row.get("warehouse_id"),
            "desired_quantity": int(row.get("desired_quantity") or 0),
            "desired_unlimited": bool(row.get("desired_unlimited")),
            "remote_quantity": int(row.get("remote_quantity") or 0),
            "remote_unlimited": bool(row.get("remote_unlimited")),
            "operation": row.get("operation"),
            "operation_quantity": int(row.get("operation_quantity") or 0),
        }
        for row in sorted(
            rows,
            key=lambda value: (
                _text(value.get("salla_branch_id")),
                _text(value.get("target_key")),
            ),
        )
    ]
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "build_branch_sync_plan",
    "build_mezan_branch_targets",
    "executable_payload_row",
    "plan_signature",
    "remote_quantity_key",
    "validate_branch_mappings",
]
