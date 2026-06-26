"""Qoyod Product Resolution (Step 4b) — `CUSTOMER_RESOLVED → PRODUCT_RESOLVED`.

For each line item:
    1. Hit `qoyod_products_mapping` by `sku`.
    2. On miss → POST /products to Qoyod (or stub in dry-run).
    3. Persist the mapping for next time.

Failures route to FAILED_PRODUCT → DEAD_LETTER (no PARTIAL_FAILURE
at this stage — nothing has been written to Qoyod yet).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIError


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ProductResolutionItem:
    sku: str
    qoyod_product_id: Optional[str] = None
    created_new: bool = False
    error: Optional[dict] = None


@dataclass
class ProductsResolutionResult:
    success: bool
    items: list[ProductResolutionItem] = field(default_factory=list)
    error: Optional[dict] = None     # first failure that flipped success=False

    def to_log_dict(self) -> dict:
        return {
            "success": self.success,
            "items": [{"sku": i.sku,
                       "qoyod_product_id": i.qoyod_product_id,
                       "created_new": i.created_new,
                       "error": i.error}
                      for i in self.items],
            "error": self.error,
        }


def _build_product_payload(item: dict, settings: dict) -> dict:
    """Map a DTO LineItem (as dict) → Qoyod /products POST body."""
    return {"product": {
        "name":              item.get("name") or item.get("sku") or "منتج",
        "sku":               item.get("sku"),
        "type":              (settings.get("default_product_type")
                              or "service"),
        "is_non_stock":      (settings.get("default_product_type") or "service") == "service",
        "selling_price":     item.get("unit_price"),
    }}


def _extract_product_id(resp: Any) -> Optional[str]:
    if not isinstance(resp, dict):
        return None
    if "product" in resp and isinstance(resp["product"], dict):
        pid = resp["product"].get("id")
        if pid is not None:
            return str(pid)
    pid = resp.get("id") or resp.get("product_id")
    return str(pid) if pid is not None else None


async def resolve_products(
    db, user_id: str, dto_items: list[dict], settings: dict,
    *, trace_id: str, api_client,
) -> ProductsResolutionResult:
    result = ProductsResolutionResult(success=True)
    for it in dto_items:
        sku = (it.get("sku") or "").strip()
        if not sku:
            result.success = False
            result.error = {"code": "missing_sku",
                            "message": "line item has no sku"}
            result.items.append(ProductResolutionItem(
                sku="", error=result.error))
            return result

        existing = await db.qoyod_products_mapping.find_one(
            {"user_id": user_id, "sku": sku},
            {"_id": 0, "qoyod_product_id": 1},
        )
        if existing and existing.get("qoyod_product_id"):
            result.items.append(ProductResolutionItem(
                sku=sku,
                qoyod_product_id=str(existing["qoyod_product_id"]),
                created_new=False,
            ))
            continue

        # Need to create in Qoyod.
        idem = f"mzn-{trace_id}-product-{sku}"
        try:
            resp = await api_client.create_product(
                _build_product_payload(it, settings), idem=idem)
        except QoyodAPIError as exc:
            err = exc.to_log_dict()
            result.success = False
            result.error = err
            result.items.append(ProductResolutionItem(sku=sku, error=err))
            return result

        pid = _extract_product_id(resp)
        if not pid:
            err = {"code": "qoyod_response_missing_id",
                   "message": f"create_product for sku={sku} returned no id",
                   "qoyod_response_excerpt": str(resp)[:200]}
            result.success = False
            result.error = err
            result.items.append(ProductResolutionItem(sku=sku, error=err))
            return result

        await db.qoyod_products_mapping.update_one(
            {"user_id": user_id, "sku": sku},
            {"$set": {
                "schema_version":     1,
                "user_id":            user_id,
                "sku":                sku,
                "qoyod_product_id":   pid,
                "qoyod_product_name": it.get("name"),
                "product_type":       settings.get("default_product_type") or "service",
                "is_non_stock":       (settings.get("default_product_type") or "service") == "service",
                "auto_created":       True,
                "resolved_via":       "global_setting",
            },
             "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )
        result.items.append(ProductResolutionItem(
            sku=sku, qoyod_product_id=pid, created_new=True))
    return result
