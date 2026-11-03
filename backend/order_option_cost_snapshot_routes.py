"""Order cost snapshots using the customer's selected product options.

The product base cost is always applied. Conditional option costs are applied only
when the matching option value exists on the order item. Shared resource costs are
resolved at snapshot time so historical order profitability never changes when a
component's current cost is edited later.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ASCENDING

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS, _number

SNAPSHOTS = "mezan_order_item_cost_snapshots_v2"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


def _item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def selected_option_tokens(item: Any) -> set[tuple[str, str]]:
    """Use the same stable selections as bundle and component fulfillment.

    This covers nested/plural values, option-level work and filled fields, while
    preserving name-only matching for legacy orders without provider IDs.
    """
    from order_cost_authority import economic_item
    from stock_component_consumption_service import selection_tokens
    return {tuple(pair) for pair in selection_tokens(economic_item(item))}


def binding_matches(binding: dict[str, Any], tokens: set[tuple[str, str]]) -> bool:
    from stock_component_consumption_service import binding_selected
    return binding_selected(binding, [list(pair) for pair in tokens])


def resolve_base_unit_cost(
    item: Any,
    profile: dict[str, Any] | None,
    product: dict[str, Any] | None,
) -> tuple[float | None, str]:
    """Resolve a line's base cost with the Mezan V2 → Salla fallback rule.

    An explicit zero is a real Mezan cost and must never fall back to Salla.
    Variant costs take precedence over their corresponding product base cost.
    """
    profile = profile or {}
    product = product or {}
    variant_id = str(_item_value(item, "variant_id") or "").strip()
    sku = str(_item_value(item, "sku") or "").strip().casefold()

    variant_costs = profile.get("variant_costs")
    if isinstance(variant_costs, dict) and variant_id:
        if variant_id in variant_costs:
            parsed = _number(variant_costs.get(variant_id))
            if parsed is not None:
                return parsed, "mezan_v2_variant"

    if "base_cost" in profile and profile.get("base_cost") not in (None, ""):
        parsed = _number(profile.get("base_cost"))
        if parsed is not None:
            return parsed, "mezan_v2_base"

    for variant in product.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        same_variant = variant_id and str(variant.get("id") or "").strip() == variant_id
        same_sku = sku and str(variant.get("sku") or "").strip().casefold() == sku
        if not (same_variant or same_sku):
            continue
        for cost_key in ("cost_price_from_salla", "cost_price", "cost"):
            if variant.get(cost_key) in (None, ""):
                continue
            parsed = _number(variant.get(cost_key))
            if parsed is not None:
                return parsed, "salla_variant_fallback"

    for cost_key in ("cost_price_from_salla", "cost_price", "cost"):
        if product.get(cost_key) in (None, ""):
            continue
        parsed = _number(product.get(cost_key))
        if parsed is not None:
            return parsed, "salla_product_fallback"
    return None, "missing"


MEZAN_V2_COST_SOURCES = frozenset({"mezan_v2_variant", "mezan_v2_base"})
SALLA_FALLBACK_COST_SOURCES = frozenset({
    "salla_variant_fallback",
    "salla_product_fallback",
})
COST_SEMANTICS_VERSION = "mezan-cost-semantics-v1"


def classify_base_unit_cost(
    item: Any,
    profile: dict[str, Any] | None,
    product: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe both calculation availability and Mezan V2 completeness.

    Salla remains a valid temporary fallback for profitability calculations,
    but it never marks the product as having an explicit Mezan V2 cost.  This
    distinction drives the actionable dashboard warning and product filter.
    """
    unit_cost, source = resolve_base_unit_cost(item, profile, product)
    calculation_cost_available = unit_cost is not None
    mezan_cost_complete = source in MEZAN_V2_COST_SOURCES
    uses_salla_fallback = source in SALLA_FALLBACK_COST_SOURCES
    return {
        "semantics_version": COST_SEMANTICS_VERSION,
        "unit_cost": unit_cost,
        "source": source,
        # Product setup completeness is deliberately Mezan-only.  A Salla
        # fallback must never hide a product from "missing Mezan cost".
        "mezan_cost_complete": mezan_cost_complete,
        "mezan_cost_missing": not mezan_cost_complete,
        # Profitability/campaign calculations may use Salla when Mezan has no
        # explicit cost.  Keep this axis separate from setup completeness.
        "calculation_cost_available": calculation_cost_available,
        "calculation_cost_source": source,
        "calculation_uses_salla_fallback": uses_salla_fallback,
        # Compatibility aliases for existing dashboard and snapshot callers.
        "cost_available": calculation_cost_available,
        "uses_salla_fallback": uses_salla_fallback,
    }


async def ensure_indexes(db: Any) -> None:
    await db[SNAPSHOTS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING), ("order_item_id", ASCENDING)],
        unique=True,
        name="uq_order_item_cost_snapshot_v2",
    )


def _cost_projection(cost: dict[str, Any]) -> dict[str, Any]:
    """Stable response fields shared by persisted and read-only snapshots."""
    from copy import deepcopy
    q = cost["quantity"]
    return {
        "quantity": q, "base_unit_cost": cost["base_total"] / q if q else 0,
        "product_resource_unit_cost": cost["product_components_total"] / q if q else 0,
        "option_unit_cost": cost["selected_options_total"] / q if q else 0,
        "unit_cost": cost["line_total"] / q if q and cost["calculation_cost_available"] else None,
        "line_cost": cost["line_total"] if cost["calculation_cost_available"] else None,
        "cost_complete": cost["calculation_cost_available"],
        "mezan_cost_complete": cost["mezan_cost_complete"], "mezan_cost_missing": cost["mezan_cost_missing"],
        "calculation_cost_available": cost["calculation_cost_available"],
        "uses_salla_fallback": cost["uses_salla_fallback"],
        "base_cost_source": cost["base_cost_source"], "currency": "SAR",
        "cost_authority": cost["cost_authority"], "cost_stage": cost["cost_stage"],
        "cost_semantics_version": cost["cost_semantics_version"], "cost": deepcopy(cost),
    }


async def calculate_order_cost_snapshot(db: Any, *, user_id: str, order: Any) -> dict[str, Any]:
    """Freeze once; only a proven expected-to-actual transition may supersede it.

    Catalog edits and repeated POSTs cannot change an existing historical cost.
    Approved receipts may improve a bundle estimate; its previous evidence is
    retained. Historical repairs use the separately audited correction workflow.
    """
    from pymongo.errors import DuplicateKeyError
    from order_cost_authority import (
        VERSION, capture_reporting_costs, item_dict, line_aliases, line_id, line_fingerprint,
        load_order_cost_authority, resolve_order_line_cost, _unknown,
    )
    await ensure_indexes(db)
    number = str(order.order_number)
    items = [item_dict(item) for item in order.items]
    source = {"order_number": number, "products": items}
    authority = await load_order_cost_authority(db, user_id, [source])
    existing_rows = await db[SNAPSHOTS].find({"user_id": user_id, "order_number": number}, {"_id": 0}).to_list(length=10001)
    if len(existing_rows) > 10000:
        raise HTTPException(409, detail={"code": "order_cost_snapshot_limit"})
    existing = {str(row["order_item_id"]): row for row in existing_rows}
    ordinary = await capture_reporting_costs(db, user_id,
        [item for item in items if (number, line_id(item)) not in authority["lines"]], {})
    now = _now()
    rows = []
    for item in items:
        key = line_id(item)
        if not key:
            raise HTTPException(409, detail={"code": "order_cost_line_identity_required"})
        catalog = (ordinary.get(key) or {}).get("cost") or _unknown(item.get("quantity"), "missing_historical_cost")
        cost = resolve_order_line_cost(item, order_number=number, catalog_cost=catalog,
            authority=authority, product_id=item.get("parent_product_id") or item.get("product_id"))
        matches = [existing[alias] for alias in line_aliases(item) if alias in existing]
        if len(matches) > 1:
            raise HTTPException(409, detail={"code": "order_cost_snapshot_identity_ambiguous", "order_item_id": key})
        previous = matches[0] if matches else None
        # Retain the first source identity instead of inserting another row
        # merely because a later projection uses the canonical alias.
        selector = {"user_id": user_id, "order_number": number,
                    "order_item_id": previous["order_item_id"] if previous else key}
        if previous:
            previous_cost = resolve_order_line_cost(item, order_number=number, catalog_cost=catalog,
                authority={"lines": {(number, key): previous}},
                product_id=item.get("parent_product_id") or item.get("product_id"))
            if previous_cost["cost_authority"] == "unresolved_snapshot":
                raise HTTPException(409, detail={"code": "order_cost_snapshot_reconciliation_required", "order_item_id": key})
        snapshot = {
            "id": (previous or {}).get("id") or uuid.uuid5(uuid.NAMESPACE_URL, f"mezan-cost:{user_id}:{number}:{key}").hex,
            **selector, "order_id": getattr(order, "order_id", None),
            "source_item_id": item.get("source_item_id"),
            "salla_product_id": item.get("parent_product_id") or item.get("product_id"),
            "product_name": item.get("name"), "sku": item.get("sku"), **_cost_projection(cost),
            "selected_options": [list(token) for token in sorted(selected_option_tokens(item))],
            "line_fingerprint": line_fingerprint(item), "cost_semantics_version": VERSION,
            "calculated_at": now, "updated_at": now,
        }
        if previous:
            if previous.get("line_fingerprint") and previous["line_fingerprint"] != snapshot["line_fingerprint"]:
                raise HTTPException(409, detail={"code": "order_cost_snapshot_reconciliation_required", "order_item_id": key})
            if cost["cost_authority"] == "bundle_snapshot" and cost["cost_stage"] == "actual" and previous.get("cost_stage") != "actual":
                await db[SNAPSHOTS].update_one({**selector, "cost_stage": {"$ne": "actual"}},
                    {"$set": snapshot, "$push": {"history": {"reason": "verified_bundle_consumption",
                        "line_cost": previous.get("line_cost"), "cost_stage": previous.get("cost_stage", "expected"),
                        "cost": previous.get("cost"), "superseded_at": now}}})
            # Otherwise the first written record is authoritative, including ID
            # and timestamp. Replay is a read, not a fresh catalog valuation.
        else:
            try:
                await db[SNAPSHOTS].update_one(selector,
                    {"$setOnInsert": {**snapshot, "created_at": now}}, upsert=True)
            except DuplicateKeyError:
                pass  # A concurrent first capture won the unique identity.
        saved = await db[SNAPSHOTS].find_one(selector, {"_id": 0})
        # A legacy manual snapshot can predate the frozen bundle authority or
        # an audited reporting correction. Return the same reporting projection
        # as every other reader while retaining that original database record.
        # Missing/reconciled evidence must likewise not expose stale completeness.
        if (cost["cost_authority"] in {"bundle_snapshot", "unresolved_snapshot"}
                and (saved.get("line_cost") != snapshot["line_cost"]
                     or saved.get("cost_complete") != snapshot["cost_complete"]
                     or saved.get("cost_authority") != snapshot["cost_authority"])):
            stored_cost = saved.get("line_cost")
            saved = {**saved, **{field: snapshot[field] for field in (
                "quantity", "base_unit_cost", "product_resource_unit_cost", "option_unit_cost",
                "unit_cost", "line_cost", "cost_complete", "mezan_cost_complete", "mezan_cost_missing",
                "calculation_cost_available", "uses_salla_fallback", "base_cost_source", "currency",
                "cost_authority", "cost_stage", "cost_semantics_version", "cost",
            )}, "persisted_snapshot_line_cost": stored_cost, "reporting_projection": True}
        rows.append(saved)
    complete = all(row.get("cost_complete") for row in rows)
    known = round(sum((_number(row.get("line_cost")) or 0.0) for row in rows), 4)
    return {"order_number": number, "currency": "SAR", "total_cost": known if complete else None,
            "known_total_cost": known, "cost_complete": complete, "items": rows, "calculated_at": now.isoformat()}


def make_order_option_cost_snapshot_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/orders-v2", tags=["Mezan Order Cost Snapshots"])

    @router.post("/{order_number}/cost-snapshot")
    async def rebuild_cost_snapshot(order_number: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        try:
            order = await get_order(MongoOrderRepository(db), user_id=user_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        return await calculate_order_cost_snapshot(db, user_id=user_id, order=order)

    @router.get("/{order_number}/cost-snapshot")
    async def get_cost_snapshot(order_number: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        from order_cost_authority import (
            item_dict, line_aliases, line_id, load_order_cost_authority,
            resolve_order_line_cost, _unknown,
        )
        user_id = str(user["id"])
        number = str(order_number)
        try:
            order = await get_order(MongoOrderRepository(db), user_id=user_id, order_number=number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        items = [item_dict(item) for item in order.items]
        authority = await load_order_cost_authority(db, user_id, [{"order_number": number, "products": items}])
        stored_rows = await db[SNAPSHOTS].find(
            {"user_id": user_id, "order_number": str(order_number)}, {"_id": 0}
        ).sort("order_item_id", 1).to_list(length=10001)
        if len(stored_rows) > 10000:
            raise HTTPException(409, detail={"code": "order_cost_snapshot_limit"})
        stored = {str(row["order_item_id"]): row for row in stored_rows}
        rows = []
        for item in items:
            key = line_id(item)
            cost = resolve_order_line_cost(item, order_number=number,
                catalog_cost=_unknown(item.get("quantity"), "missing_historical_cost"), authority=authority,
                product_id=item.get("parent_product_id") or item.get("product_id"))
            matches = [stored[alias] for alias in line_aliases(item) if alias in stored]
            original = matches[0] if len(matches) == 1 else {}
            rows.append({**original, "order_number": number, "order_item_id": key,
                "salla_product_id": item.get("parent_product_id") or item.get("product_id"),
                **_cost_projection(cost), "persisted_snapshot_line_cost": original.get("line_cost"),
                "reporting_projection": True})
        for row in rows:
            for key in ("created_at", "updated_at", "calculated_at"):
                if hasattr(row.get(key), "isoformat"):
                    row[key] = row[key].isoformat()
        complete = bool(rows) and all(row["cost_complete"] for row in rows)
        known = round(sum((_number(row.get("line_cost")) or 0.0) for row in rows), 4)
        return {"order_number": number, "currency": "SAR", "items": rows,
                "total_cost": known if complete else None, "known_total_cost": known,
                "cost_complete": complete, "snapshot_exists": bool(authority["lines"])}

    async def _bundle_source_evidence(
        *, order_number: str, order_item_id: str | None, user: dict,
    ) -> dict[str, Any]:
        from bundle_historical_reporting_audit import (
            BundleHistoricalEvidenceUnavailable, inspect_historical_bundle_order,
        )
        if not isinstance(user, dict) or not (
            str(user.get("role") or "").strip().lower() == "owner"
            or user.get("is_owner") is True
        ):
            raise HTTPException(status_code=403, detail={"code": "owner_required"})
        owner_id = str(user["id"])
        number = str(order_number).strip()
        if not number or len(number) > 64:
            raise HTTPException(status_code=422, detail={"code": "order_number_invalid"})
        try:
            order = await get_order(MongoOrderRepository(db), user_id=owner_id, order_number=number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        if not order_item_id:
            if len(order.items) != 1:
                raise HTTPException(status_code=422, detail={"code": "explicit_order_item_id_required"})
            order_item_id = order.items[0].order_item_id
        item = next((row for row in order.items if row.order_item_id == order_item_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "order_item_not_found"})
        product_ids = {str(v) for v in (item.product_id, item.parent_product_id) if v}
        if not product_ids or not await db["mezan_bundle_definitions_v2"].find_one(
            {"user_id": owner_id, "product_type": "composite",
             "product_id": {"$in": sorted(product_ids)}}, {"_id": 1}
        ):
            raise HTTPException(status_code=409, detail={"code": "not_an_opted_in_composite_bundle"})
        source_rows = await db["unified_orders"].find(
            {"user_id": owner_id, "order_number": number},
            {"_id": 0, "raw_by_source.salla_direct": 1},
        ).limit(2).to_list(length=2)
        if len(source_rows) != 1:
            raise HTTPException(status_code=409, detail={"code": "historical_source_not_unique"})
        original = ((source_rows[0].get("raw_by_source") or {}).get("salla_direct"))
        try:
            return inspect_historical_bundle_order(
                order_number=number, raw_order=original,
                canonical_items=list(order.items), target_line_id=order_item_id,
            )
        except BundleHistoricalEvidenceUnavailable as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from exc

    @router.get("/{order_number}/bundle-financial-source-audit")
    async def bundle_financial_source_audit(
        order_number: str, order_item_id: str | None = None,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Owner-only original source audit; no pricing or database writes."""
        return await _bundle_source_evidence(
            order_number=order_number, order_item_id=order_item_id, user=user,
        )

    @router.post("/{order_number}/bundle-financial-cost-dry-run")
    async def bundle_financial_cost_dry_run(
        order_number: str, attestation: dict = Body(...),
        order_item_id: str | None = None, user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Review-only proposal: never applies a correction or touches stock."""
        from bundle_historical_reporting_audit import (
            BundleHistoricalEvidenceUnavailable, dry_run_attested_bundle_cost,
        )
        evidence = await _bundle_source_evidence(
            order_number=order_number, order_item_id=order_item_id, user=user,
        )
        try:
            return dry_run_attested_bundle_cost(evidence, attestation)
        except BundleHistoricalEvidenceUnavailable as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from exc

    @router.post("/{order_number}/bundle-financial-cost-repair-plan")
    async def bundle_financial_cost_repair_plan(
        order_number: str, payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Transactional-compatible no-write plan, no cost conclusion yet."""
        from bundle_historical_reporting_audit import BundleHistoricalEvidenceUnavailable
        from bundle_historical_reporting_repair import build_missing_bundle_repair_plan
        if not isinstance(user, dict) or not (
            str(user.get("role") or "").strip().lower() == "owner"
            or user.get("is_owner") is True
        ):
            raise HTTPException(status_code=403, detail={"code": "owner_required"})
        if set(payload) != {"order_item_id", "attestation"} or not isinstance(payload["attestation"], dict):
            raise HTTPException(status_code=422, detail={"code": "historical_repair_payload_invalid"})
        try:
            plan = await build_missing_bundle_repair_plan(
                db, str(user["id"]), str(order_number),
                payload["order_item_id"], payload["attestation"])
        except BundleHistoricalEvidenceUnavailable as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
        # Raw internal correction includes owner data. It is returned only to
        # the private transaction function, never in a public API response.
        return {k: v for k, v in plan.items() if k != "correction"}

    @router.post("/{order_number}/bundle-financial-cost-repair-apply")
    async def bundle_financial_cost_repair_apply(
        order_number: str, payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Explicit user-reviewed manifest; disabled on Production by default."""
        from bundle_historical_reporting_audit import BundleHistoricalEvidenceUnavailable
        from bundle_historical_reporting_repair import apply_missing_bundle_repair
        if not isinstance(user, dict) or not (
            str(user.get("role") or "").strip().lower() == "owner"
            or user.get("is_owner") is True
        ):
            raise HTTPException(status_code=403, detail={"code": "owner_required"})
        required = {"order_item_id", "attestation", "manifest_sha", "approval_phrase"}
        if set(payload) != required or not isinstance(payload["attestation"], dict):
            raise HTTPException(status_code=422, detail={"code": "historical_repair_payload_invalid"})
        try:
            return await apply_missing_bundle_repair(
                db, str(user["id"]), str(order_number),
                payload["order_item_id"], payload["attestation"],
                expected_manifest_sha=payload["manifest_sha"],
                approval_phrase=payload["approval_phrase"])
        except BundleHistoricalEvidenceUnavailable as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from exc

    return router
