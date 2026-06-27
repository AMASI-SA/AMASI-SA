"""Qoyod Product Resolution (Step 4b) — `CUSTOMER_RESOLVED → PRODUCT_RESOLVED`.

SSOT (Single Source Of Truth) for products at runtime
─────────────────────────────────────────────────────
The runtime pipeline uses **Mezan + Salla** as the SSOT for products.
It does NOT read from the migration snapshot collections
(`qoyod_external_products`, `qoyod_migration_products`) — those are
review-only artefacts populated by the «مرحلة الانتقال» page.

For each line item:
    1. Hit `qoyod_products_mapping` by `sku` (the runtime mapping table).
    2. On miss → SSOT trust gate (see below) → POST /products to Qoyod.
    3. Persist the new mapping for next time.

Failures route to FAILED_PRODUCT → DEAD_LETTER (no PARTIAL_FAILURE
at this stage — nothing has been written to Qoyod yet).

SSOT Trust Gate (2026-02-27)
────────────────────────────
Historical Qoyod tenants frequently contain dozens of legacy products
from old Salla syncs, manual data entry, or other connectors. The
resolver MUST NOT silently bind a new order to those historical rows.

Before creating a product, the gate queries Qoyod for the SKU:
  • Mezan mapping HIT                → use it (happy path).
  • Mezan mapping MISS + Qoyod NONE  → create fresh.
  • Mezan mapping MISS + Qoyod HIT
      AND settings.block_untrusted_existing_products is True (default)
                                     → fail with `qoyod_existing_untrusted`.
      AND settings.block_untrusted_existing_products is False
                                     → adopt + log audit trail.

To onboard a historical product into Mezan, the operator must call
`POST /api/integrations/qoyod/products/adopt` (manual review), which
inserts the row into `qoyod_products_mapping` with `adopted=True` so
the gate stops blocking it.
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
    # SSOT gate metadata — surfaces "yes this came from Mezan" vs
    # "we adopted a legacy Qoyod row by operator action".
    trust_source: Optional[str] = None    # "mezan" | "adopted" | "created"


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
                       "trust_source": i.trust_source,
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


def _untrusted_error(sku: str, qoyod_product: dict) -> dict:
    """Build the `qoyod_existing_untrusted` error payload. Includes
    enough detail for the operator to decide: adopt or archive."""
    return {
        "code":    "qoyod_existing_untrusted",
        "message": (f"SKU '{sku}' موجود في قيود (product_id="
                    f"{qoyod_product.get('id')}) لكنه غير مربوط محلياً "
                    "في ميزان. لمنع ربط فاتورة جديدة بمنتج تاريخي "
                    "مجهول المصدر، تم إيقاف المعالجة. "
                    "اعتمد المنتج عبر "
                    "POST /api/integrations/qoyod/products/adopt أو "
                    "أرشفه في قيود."),
        "qoyod_product_id":   str(qoyod_product.get("id")),
        "qoyod_product_name": (qoyod_product.get("name")
                               or qoyod_product.get("name_ar")
                               or qoyod_product.get("name_en")),
        "qoyod_product_sku":  qoyod_product.get("sku")
                              or qoyod_product.get("reference"),
        "remediation":        "adopt_or_archive",
    }


async def resolve_products(
    db, user_id: str, dto_items: list[dict], settings: dict,
    *, trace_id: str, api_client,
) -> ProductsResolutionResult:
    result = ProductsResolutionResult(success=True)
    # Default trust gate to ON — operator must explicitly opt out via
    # settings.block_untrusted_existing_products = False. This protects
    # tenants whose Qoyod account already holds historical products
    # (cod_item, custom_product, legacy Salla SKUs, etc.).
    trust_gate_on = settings.get("block_untrusted_existing_products", True)

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
            {"_id": 0, "qoyod_product_id": 1, "adopted": 1},
        )
        if existing and existing.get("qoyod_product_id"):
            result.items.append(ProductResolutionItem(
                sku=sku,
                qoyod_product_id=str(existing["qoyod_product_id"]),
                created_new=False,
                trust_source="adopted" if existing.get("adopted") else "mezan",
            ))
            continue

        # ─── SSOT Trust Gate ─────────────────────────────────────────
        # Before creating, ask Qoyod whether this SKU already exists.
        # If it does AND we have no local mapping AND the gate is on →
        # refuse to proceed. The operator must adopt the historical
        # product or archive it. This prevents a fresh order from
        # silently binding to a legacy Qoyod row of unknown origin.
        if trust_gate_on:
            try:
                qoyod_match = await api_client.find_product_by_sku(sku)
            except QoyodAPIError as exc:
                # Lookup failed → be strict and refuse. Treat as
                # transient: caller will retry / dead-letter as usual.
                err = exc.to_log_dict()
                err.setdefault(
                    "context",
                    "ssot_trust_gate_lookup_failed (cannot create safely)")
                result.success = False
                result.error = err
                result.items.append(ProductResolutionItem(sku=sku, error=err))
                return result
            if qoyod_match and isinstance(qoyod_match, dict):
                err = _untrusted_error(sku, qoyod_match)
                result.success = False
                result.error = err
                result.items.append(ProductResolutionItem(sku=sku, error=err))
                return result

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
                "adopted":            False,
                "resolved_via":       "global_setting",
                "source":             "mezan_created",
            },
             "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )
        result.items.append(ProductResolutionItem(
            sku=sku, qoyod_product_id=pid, created_new=True,
            trust_source="created"))
    return result


# ─────────────────────────────────────────────────────────────────────
# Manual adoption — operator explicitly onboards a historical product
# ─────────────────────────────────────────────────────────────────────
async def adopt_qoyod_product(
    db, *, user_id: str, sku: str, qoyod_product_id: str,
    qoyod_product_name: Optional[str] = None,
    note: Optional[str] = None,
    actor: str = "operator",
) -> dict:
    """Insert a row in `qoyod_products_mapping` flagged `adopted=True`.

    After adoption the resolver flows normally for this SKU. The full
    audit trail (`adopted_by`, `adopted_at`, `adoption_note`) is
    persisted so the operator can answer 'why is this SKU bound to a
    legacy Qoyod product?' months later.

    Idempotent: re-adopting the same SKU updates the note / actor
    without inserting a duplicate.
    """
    if not sku or not qoyod_product_id:
        return {"ok": False, "reason": "sku_and_qoyod_product_id_required"}
    sku = sku.strip()
    qoyod_product_id = str(qoyod_product_id).strip()

    now = _now()
    await db.qoyod_products_mapping.update_one(
        {"user_id": user_id, "sku": sku},
        {"$set": {
            "schema_version":     1,
            "user_id":            user_id,
            "sku":                sku,
            "qoyod_product_id":   qoyod_product_id,
            "qoyod_product_name": qoyod_product_name,
            "adopted":            True,
            "adopted_by":         actor,
            "adopted_at":         now,
            "adoption_note":      note,
            "source":             "operator_adopted",
            "auto_created":       False,
        },
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {
        "ok":                 True,
        "sku":                sku,
        "qoyod_product_id":   qoyod_product_id,
        "qoyod_product_name": qoyod_product_name,
        "adopted_by":         actor,
        "adopted_at":         now.isoformat(),
    }
