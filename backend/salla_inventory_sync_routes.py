"""Governed Mezan inventory mirroring into Salla branch quantities."""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING

from fulfillment_v2_routes import (
    INVENTORY_RESERVATIONS,
    _actor_context,
    _apply_inventory_reservations,
    _inventory_rows,
    _require_permission,
)
from product_fulfillment_rules import PRODUCT_OPERATION_PROFILES
from product_v2_routes import PRODUCTS
from salla_integration.service import SallaError, call_salla
from salla_inventory_sync_rules import (
    build_branch_sync_plan,
    build_mezan_branch_targets,
    executable_payload_row,
    plan_signature,
    validate_branch_mappings,
)
from warehouse_location_routes import LOCATIONS, WAREHOUSES


SALLA_BRANCH_MAPPINGS = "mezan_salla_branch_mappings_v2"
SALLA_INVENTORY_SYNC_RUNS = "mezan_salla_inventory_sync_runs_v2"
PREVIEW_TTL_MINUTES = 15
MAX_PROVIDER_PAGES = 500
MAX_BULK_ROWS = 100
MAX_SYNC_ROWS = 5000
MAX_RECORDED_ISSUES = 2000
MAX_RECORDED_SKIPPED = 2000
SALLA_BRANCH_SYNC_FEATURE_FLAG = (
    "MEZAN_SALLA_BRANCH_INVENTORY_SYNC_ENABLED"
)


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def salla_branch_inventory_sync_enabled() -> bool:
    """Keep branch sync dormant until Salla approves the required scope."""
    return _text(os.environ.get(SALLA_BRANCH_SYNC_FEATURE_FLAG)).casefold() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def _require_branch_sync_enabled() -> None:
    if salla_branch_inventory_sync_enabled():
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "salla_branch_inventory_sync_frozen",
            "message": (
                "مزامنة فروع سلة مجمّدة حتى توافق سلة على "
                "صلاحية branches.read."
            ),
            "feature_flag": SALLA_BRANCH_SYNC_FEATURE_FLAG,
        },
    )


async def _scope_state(db: Any, merchant_id: str) -> dict[str, Any]:
    integration = await db.salla_integrations.find_one(
        {"user_id": merchant_id},
        {"_id": 0, "status": 1, "scope": 1},
    )
    scopes = set(_text((integration or {}).get("scope")).split())
    products_read = bool(
        {"products.read", "products.read_write"} & scopes
    )
    return {
        "connected": bool(
            integration
            and integration.get("status") in {"connected", "active"}
        ),
        "branches_read": "branches.read" in scopes,
        "products_read": products_read,
        "products_read_write": "products.read_write" in scopes,
        "required_scopes": [
            "branches.read",
            "products.read_write",
        ],
        "missing_scopes": [
            scope
            for scope, present in (
                ("branches.read", "branches.read" in scopes),
                ("products.read_write", "products.read_write" in scopes),
            )
            if not present
        ],
    }


async def _require_provider_scopes(
    db: Any,
    *,
    merchant_id: str,
    branches: bool = False,
    write: bool = False,
) -> dict[str, Any]:
    state = await _scope_state(db, merchant_id)
    missing = []
    if not state["connected"]:
        missing.append("salla_connection")
    if branches and not state["branches_read"]:
        missing.append("branches.read")
    if write and not state["products_read_write"]:
        missing.append("products.read_write")
    if not write and not state["products_read"]:
        missing.append("products.read")
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "salla_inventory_scopes_required",
                "missing_scopes": missing,
                "message": (
                    "فعّل صلاحيات الفروع والمنتجات في تطبيق سلة "
                    "ثم أعد ربط المتجر."
                ),
            },
        )
    return state


def _public_run(run: dict[str, Any], *, include_rows: bool = True) -> dict[str, Any]:
    excluded = {
        "_id",
        "user_id",
        "confirm_token_hash",
        "provider_responses",
    }
    if not include_rows:
        excluded |= {"rows", "issues", "skipped"}
    return {
        key: value
        for key, value in run.items()
        if key not in excluded
    }


def _pagination_total_pages(response: dict[str, Any]) -> int:
    pagination = response.get("pagination") or {}
    try:
        return int(
            pagination.get("totalPages")
            or pagination.get("total_pages")
            or pagination.get("last_page")
            or 0
        )
    except (TypeError, ValueError, OverflowError):
        return 0


async def _fetch_salla_branches(
    db: Any,
    *,
    merchant_id: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page in range(1, 21):
        response = await call_salla(
            db,
            merchant_id,
            "GET",
            "/branches",
            params={"page": page, "per_page": 100},
        )
        rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict) or not _text(row.get("id")):
                continue
            result.append({
                "id": _text(row.get("id")),
                "name": _text(row.get("name")) or _text(row.get("id")),
                "status": row.get("status"),
                "is_default": bool(row.get("is_default")),
                "is_open": row.get("is_open"),
                "short_address": row.get("short_address"),
                "address_description": row.get("address_description"),
                "branch_code": row.get("branch_code") or row.get("code"),
            })
        total_pages = _pagination_total_pages(response)
        if total_pages > 20:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "inventory_sync_branch_catalog_too_large",
                    "total_pages": total_pages,
                },
            )
        if total_pages and page >= total_pages:
            break
    return result


async def _fetch_salla_quantity_reasons(
    db: Any,
    *,
    merchant_id: str,
) -> list[dict[str, Any]]:
    response = await call_salla(
        db,
        merchant_id,
        "GET",
        "/products/quantities/quantity-change-reason",
    )
    rows = response.get("data") if isinstance(response, dict) else None
    return [
        {
            "id": _text(row.get("id")),
            "name": _text(row.get("name")) or _text(row.get("id")),
        }
        for row in rows or []
        if isinstance(row, dict) and _text(row.get("id"))
    ]


async def _fetch_salla_quantities(
    db: Any,
    *,
    merchant_id: str,
    salla_branch_id: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for page in range(1, MAX_PROVIDER_PAGES + 1):
        response = await call_salla(
            db,
            merchant_id,
            "GET",
            "/products/quantities",
            params={
                "branch": salla_branch_id,
                "page": page,
                "per_page": 100,
            },
        )
        rows = response.get("data") if isinstance(response, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        result.extend(row for row in rows if isinstance(row, dict))
        total_pages = _pagination_total_pages(response)
        if total_pages > MAX_PROVIDER_PAGES:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "inventory_sync_provider_catalog_too_large",
                    "total_pages": total_pages,
                },
            )
        if total_pages and page >= total_pages:
            break
    return result


def _raise_salla(exc: Exception) -> None:
    if isinstance(exc, SallaError):
        raise HTTPException(
            status_code=exc.status_code if exc.status_code != 200 else 400,
            detail={
                "code": "salla_inventory_api_error",
                "message": str(exc),
                "needs_reauth": exc.needs_reauth,
            },
        ) from exc
    raise HTTPException(
        status_code=503,
        detail={
            "code": "salla_inventory_temporarily_unavailable",
            "message": str(exc),
        },
    ) from exc


class BranchMappingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    salla_branch_id: str = Field(min_length=1, max_length=120)
    mezan_warehouse_id: str = Field(min_length=1, max_length=120)


class SaveBranchMappingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    mappings: list[BranchMappingItem] = Field(
        default_factory=list,
        max_length=100,
    )


class InventorySyncPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_id: str = Field(min_length=1, max_length=120)


class InventorySyncPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(min_length=1, max_length=120)
    confirm_token: str = Field(min_length=16, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=160)
    acknowledge_issues: bool = False


async def ensure_salla_inventory_sync_indexes(db: Any) -> None:
    await db[SALLA_BRANCH_MAPPINGS].create_index(
        [("user_id", ASCENDING)],
        unique=True,
        name="uq_salla_branch_mapping_merchant_v2",
    )
    await db[SALLA_INVENTORY_SYNC_RUNS].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)],
        unique=True,
        name="uq_salla_inventory_sync_run_v2",
    )
    await db[SALLA_INVENTORY_SYNC_RUNS].create_index(
        [("user_id", ASCENDING), ("publish_idempotency_key", ASCENDING)],
        unique=True,
        sparse=True,
        name="uq_salla_inventory_publish_idempotency_v2",
    )
    await db[SALLA_INVENTORY_SYNC_RUNS].create_index(
        [("user_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_salla_inventory_sync_runs_v2",
    )


async def _mapping_document(db: Any, merchant_id: str) -> dict[str, Any]:
    return (
        await db[SALLA_BRANCH_MAPPINGS].find_one(
            {"user_id": merchant_id},
            {"_id": 0},
        )
        or {
            "user_id": merchant_id,
            "revision": 0,
            "mappings": [],
        }
    )


async def _inventory_facts(
    db: Any,
    *,
    merchant_id: str,
    warehouse_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    products = await db[PRODUCTS].find(
        {
            "user_id": merchant_id,
            "archived": {"$ne": True},
        },
        {
            "_id": 0,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "name": 1,
            "sku": 1,
            "variants": 1,
            "variants_count": 1,
        },
    ).to_list(length=30000)
    profiles = await db[PRODUCT_OPERATION_PROFILES].find(
        {"user_id": merchant_id},
        {
            "_id": 0,
            "salla_product_id": 1,
            "inventory_policy": 1,
            "stockout_policy": 1,
            "low_stock_threshold": 1,
        },
    ).to_list(length=30000)
    locations = await db[LOCATIONS].find(
        {
            "user_id": merchant_id,
            "warehouse_id": {"$in": warehouse_ids},
            "state": {"$ne": "disabled"},
            "occupancy": {"$ne": None},
        },
        {
            "_id": 0,
            "id": 1,
            "warehouse_id": 1,
            "state": 1,
            "occupancy": 1,
        },
    ).to_list(length=50000)
    stock_rows = _inventory_rows(locations)
    reservations = await db[INVENTORY_RESERVATIONS].find(
        {
            "user_id": merchant_id,
            "status": "active",
        },
        {"_id": 0},
    ).to_list(length=100000)
    _apply_inventory_reservations(
        stock_rows,
        reservations,
        current_order_number="",
    )
    return products, profiles, stock_rows


async def _build_live_plan(
    db: Any,
    *,
    merchant_id: str,
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    warehouse_ids = sorted({
        _text(row.get("mezan_warehouse_id"))
        for row in mappings
        if _text(row.get("mezan_warehouse_id"))
    })
    products, profiles, stock_rows = await _inventory_facts(
        db,
        merchant_id=merchant_id,
        warehouse_ids=warehouse_ids,
    )
    all_rows: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    all_skipped: list[dict[str, Any]] = []
    branch_summaries = []
    for mapping in mappings:
        salla_branch_id = _text(mapping.get("salla_branch_id"))
        warehouse_id = _text(mapping.get("mezan_warehouse_id"))
        targets_result = build_mezan_branch_targets(
            products=products,
            profiles=profiles,
            stock_rows=stock_rows,
            warehouse_id=warehouse_id,
        )
        remote_quantities = await _fetch_salla_quantities(
            db,
            merchant_id=merchant_id,
            salla_branch_id=salla_branch_id,
        )
        plan = build_branch_sync_plan(
            salla_branch_id=salla_branch_id,
            warehouse_id=warehouse_id,
            targets=targets_result["targets"],
            remote_quantities=remote_quantities,
        )
        plan["rows"] = [
            {
                **row,
                "salla_branch": mapping.get("salla_branch"),
                "mezan_warehouse": mapping.get("mezan_warehouse"),
            }
            for row in plan["rows"]
        ]
        all_rows.extend(plan["rows"])
        if len(all_rows) > MAX_SYNC_ROWS:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "inventory_sync_plan_too_large",
                    "max_rows": MAX_SYNC_ROWS,
                },
            )
        all_issues.extend([
            {
                **row,
                "salla_branch_id": salla_branch_id,
                "warehouse_id": warehouse_id,
            }
            for row in [
                *targets_result["issues"],
                *plan["issues"],
            ]
        ])
        all_skipped.extend(targets_result["skipped"])
        branch_summaries.append({
            "salla_branch_id": salla_branch_id,
            "warehouse_id": warehouse_id,
            "remote_rows": len(remote_quantities),
            "sync_rows": len(plan["rows"]),
            "changes": len([
                row
                for row in plan["rows"]
                if row.get("operation") != "noop"
            ]),
        })
    if len(all_issues) > MAX_RECORDED_ISSUES:
        omitted = len(all_issues) - MAX_RECORDED_ISSUES
        all_issues = all_issues[:MAX_RECORDED_ISSUES]
        all_issues.append({
            "code": "additional_inventory_sync_issues_omitted",
            "blocking": True,
            "omitted_count": omitted,
        })
    deduped_skipped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in all_skipped:
        key = (
            _text(row.get("salla_product_id") or row.get("mezan_product_id")),
            _text(row.get("code")),
        )
        deduped_skipped.setdefault(key, row)
    all_skipped = list(deduped_skipped.values())[:MAX_RECORDED_SKIPPED]
    return {
        "rows": all_rows,
        "issues": all_issues,
        "skipped": all_skipped,
        "branch_summaries": branch_summaries,
        "plan_signature": plan_signature(all_rows),
    }


async def _verify_run(
    db: Any,
    *,
    merchant_id: str,
    run: dict[str, Any],
) -> dict[str, Any]:
    current = await _build_live_plan(
        db,
        merchant_id=merchant_id,
        mappings=run.get("mappings") or [],
    )
    intended = {
        (
            _text(row.get("salla_branch_id")),
            _text(row.get("target_key")),
        ): (
            int(row.get("desired_quantity") or 0),
            bool(row.get("desired_unlimited")),
        )
        for row in run.get("rows") or []
    }
    current_by_key = {
        (
            _text(row.get("salla_branch_id")),
            _text(row.get("target_key")),
        ): row
        for row in current["rows"]
    }
    mismatches = []
    for key, expected in intended.items():
        row = current_by_key.get(key)
        if not row:
            mismatches.append({
                "salla_branch_id": key[0],
                "target_key": key[1],
                "code": "verification_target_missing",
            })
            continue
        current_desired = (
            int(row.get("desired_quantity") or 0),
            bool(row.get("desired_unlimited")),
        )
        if current_desired != expected:
            mismatches.append({
                "salla_branch_id": key[0],
                "target_key": key[1],
                "code": "mezan_quantity_changed_after_publish",
                "published_quantity": expected[0],
                "published_unlimited": expected[1],
                "current_mezan_quantity": current_desired[0],
                "current_mezan_unlimited": current_desired[1],
            })
            continue
        remote = (
            int(row.get("remote_quantity") or 0),
            bool(row.get("remote_unlimited")),
        )
        if expected[1]:
            matched = remote[1] is True
        else:
            matched = remote == expected
        if not matched:
            mismatches.append({
                "salla_branch_id": key[0],
                "target_key": key[1],
                "expected_quantity": expected[0],
                "expected_unlimited": expected[1],
                "actual_quantity": remote[0],
                "actual_unlimited": remote[1],
            })
    verified = not mismatches
    return {
        "verified": verified,
        "verification_status": (
            "verified" if verified else "pending_provider_queue"
        ),
        "verification_mismatches": mismatches[:500],
        "verified_at": _now(),
    }


def make_salla_inventory_sync_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/inventory-v2/salla-sync",
        tags=["Mezan Salla Inventory Sync V2"],
    )

    @router.get("/catalog")
    async def catalog(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.salla_sync.read")
        await ensure_salla_inventory_sync_indexes(db)
        merchant_id = context["merchant_id"]
        feature_enabled = salla_branch_inventory_sync_enabled()
        if not feature_enabled:
            mapping = await _mapping_document(db, merchant_id)
            return {
                "ok": True,
                "source_of_truth": "mezan",
                "external_role": "sales_channel_mirror_frozen",
                "feature": {
                    "enabled": False,
                    "status": "frozen_pending_salla_approval",
                    "required_scope": "branches.read",
                    "activation_flag": SALLA_BRANCH_SYNC_FEATURE_FLAG,
                    "external_calls_made": False,
                    "external_writes_made": False,
                },
                "salla_connection": {
                    "connected": None,
                    "missing_scopes": ["branches.read"],
                    "scope_check_performed": False,
                },
                "salla_branches": [],
                "mezan_warehouses": [],
                "quantity_reasons": [],
                "mapping": {
                    "revision": int(mapping.get("revision") or 0),
                    "mappings": mapping.get("mappings") or [],
                },
                "recent_runs": [],
                "permissions": {
                    "can_manage_mappings": False,
                    "can_publish": False,
                },
                "safety": {
                    "products_are_global": True,
                    "quantities_are_branch_scoped": True,
                    "live_write_requires_explicit_confirmation": True,
                },
            }
        scope_state = await _scope_state(db, merchant_id)
        branches = []
        reasons = []
        if (
            scope_state["connected"]
            and scope_state["branches_read"]
            and scope_state["products_read"]
        ):
            try:
                branches = await _fetch_salla_branches(
                    db,
                    merchant_id=merchant_id,
                )
                reasons = await _fetch_salla_quantity_reasons(
                    db,
                    merchant_id=merchant_id,
                )
            except (
                SallaError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:
                _raise_salla(exc)
        warehouses = await db[WAREHOUSES].find(
            {
                "user_id": merchant_id,
                "status": {"$ne": "disabled"},
            },
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "code": 1,
                "city": 1,
                "is_primary": 1,
            },
        ).sort([("is_primary", -1), ("name", 1)]).to_list(500)
        mapping = await _mapping_document(db, merchant_id)
        recent_runs = await db[SALLA_INVENTORY_SYNC_RUNS].find(
            {"user_id": merchant_id},
            {
                "_id": 0,
                "user_id": 0,
                "rows": 0,
                "issues": 0,
                "skipped": 0,
                "confirm_token_hash": 0,
                "provider_responses": 0,
            },
        ).sort("created_at", -1).limit(20).to_list(length=20)
        return {
            "ok": True,
            "source_of_truth": "mezan",
            "external_role": "sales_channel_mirror",
            "feature": {
                "enabled": True,
                "status": "active",
                "activation_flag": SALLA_BRANCH_SYNC_FEATURE_FLAG,
            },
            "salla_connection": scope_state,
            "salla_branches": branches,
            "mezan_warehouses": warehouses,
            "quantity_reasons": reasons,
            "mapping": {
                "revision": int(mapping.get("revision") or 0),
                "mappings": mapping.get("mappings") or [],
            },
            "recent_runs": recent_runs,
            "permissions": {
                "can_manage_mappings": (
                    "inventory.salla_sync.manage_mappings"
                    in context["permissions"]
                ),
                "can_publish": (
                    "inventory.salla_sync.publish"
                    in context["permissions"]
                ),
            },
            "safety": {
                "flow": [
                    "map_branches",
                    "preview",
                    "confirm",
                    "publish",
                    "verify",
                    "audit",
                ],
                "products_are_global": True,
                "quantities_are_branch_scoped": True,
                "live_write_requires_explicit_confirmation": True,
            },
        }

    @router.put("/mappings")
    async def save_mappings(
        payload: SaveBranchMappingsRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "inventory.salla_sync.manage_mappings",
        )
        await ensure_salla_inventory_sync_indexes(db)
        merchant_id = context["merchant_id"]
        _require_branch_sync_enabled()
        await _require_provider_scopes(
            db,
            merchant_id=merchant_id,
            branches=True,
        )
        try:
            normalized = validate_branch_mappings([
                row.model_dump()
                for row in payload.mappings
            ])
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc)},
            ) from exc
        current = await _mapping_document(db, merchant_id)
        if int(current.get("revision") or 0) != payload.expected_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "inventory_branch_mapping_revision_conflict",
                    "current_revision": int(current.get("revision") or 0),
                },
            )
        try:
            branches = await _fetch_salla_branches(
                db,
                merchant_id=merchant_id,
            )
        except (
            SallaError,
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:
            _raise_salla(exc)
        branch_by_id = {row["id"]: row for row in branches}
        requested_warehouse_ids = [
            row["mezan_warehouse_id"] for row in normalized
        ]
        warehouses = await db[WAREHOUSES].find(
            {
                "user_id": merchant_id,
                "id": {"$in": requested_warehouse_ids},
                "status": {"$ne": "disabled"},
            },
            {"_id": 0, "id": 1, "name": 1, "code": 1, "city": 1},
        ).to_list(length=500)
        warehouse_by_id = {row["id"]: row for row in warehouses}
        for row in normalized:
            if row["salla_branch_id"] not in branch_by_id:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "inventory_salla_branch_not_found",
                        "salla_branch_id": row["salla_branch_id"],
                    },
                )
            if row["mezan_warehouse_id"] not in warehouse_by_id:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "inventory_mezan_warehouse_not_found",
                        "mezan_warehouse_id": row["mezan_warehouse_id"],
                    },
                )
        now = _now()
        revision = payload.expected_revision + 1
        mappings = [
            {
                **row,
                "salla_branch": branch_by_id[row["salla_branch_id"]],
                "mezan_warehouse": warehouse_by_id[
                    row["mezan_warehouse_id"]
                ],
            }
            for row in normalized
        ]
        document = {
            "user_id": merchant_id,
            "revision": revision,
            "mappings": mappings,
            "updated_at": now,
            "updated_by": context["actor_id"],
        }
        if not current.get("created_at"):
            document["created_at"] = now
        result = await db[SALLA_BRANCH_MAPPINGS].update_one(
            {
                "user_id": merchant_id,
                "revision": payload.expected_revision,
            }
            if current.get("created_at")
            else {"user_id": merchant_id},
            {"$set": document},
            upsert=not bool(current.get("created_at")),
        )
        if current.get("created_at") and result.modified_count != 1:
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_branch_mapping_revision_conflict"},
            )
        return {
            "ok": True,
            "mapping": {
                "revision": revision,
                "mappings": mappings,
            },
        }

    @router.post("/previews", status_code=201)
    async def create_preview(
        payload: InventorySyncPreviewRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.salla_sync.read")
        await ensure_salla_inventory_sync_indexes(db)
        merchant_id = context["merchant_id"]
        _require_branch_sync_enabled()
        await _require_provider_scopes(
            db,
            merchant_id=merchant_id,
        )
        mapping = await _mapping_document(db, merchant_id)
        mappings = mapping.get("mappings") or []
        if not mappings:
            raise HTTPException(
                status_code=422,
                detail={"code": "inventory_branch_mapping_required"},
            )
        try:
            reasons = await _fetch_salla_quantity_reasons(
                db,
                merchant_id=merchant_id,
            )
            if _text(payload.reason_id) not in {
                row["id"] for row in reasons
            }:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "inventory_quantity_reason_invalid"},
                )
            plan = await _build_live_plan(
                db,
                merchant_id=merchant_id,
                mappings=mappings,
            )
        except HTTPException:
            raise
        except (
            SallaError,
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:
            _raise_salla(exc)
        now = _now_dt()
        run_id = f"sallainv_{uuid.uuid4().hex}"
        confirm_token = secrets.token_urlsafe(32)
        run = {
            "id": run_id,
            "user_id": merchant_id,
            "status": "previewed",
            "mapping_revision": int(mapping.get("revision") or 0),
            "mappings": mappings,
            "reason_id": _text(payload.reason_id),
            "rows": plan["rows"],
            "issues": plan["issues"],
            "skipped": plan["skipped"],
            "branch_summaries": plan["branch_summaries"],
            "plan_signature": plan["plan_signature"],
            "confirm_token_hash": hashlib.sha256(
                confirm_token.encode("utf-8")
            ).hexdigest(),
            "changes_count": len([
                row
                for row in plan["rows"]
                if row.get("operation") != "noop"
            ]),
            "unchanged_count": len([
                row
                for row in plan["rows"]
                if row.get("operation") == "noop"
            ]),
            "issues_count": len(plan["issues"]),
            "created_at": now.isoformat(),
            "created_by": context["actor_id"],
            "expires_at": (
                now + timedelta(minutes=PREVIEW_TTL_MINUTES)
            ).isoformat(),
        }
        await db[SALLA_INVENTORY_SYNC_RUNS].insert_one(dict(run))
        return {
            "ok": True,
            "confirm_token": confirm_token,
            "run": _public_run(run),
        }

    @router.post("/publish")
    async def publish(
        payload: InventorySyncPublishRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.salla_sync.publish")
        await ensure_salla_inventory_sync_indexes(db)
        merchant_id = context["merchant_id"]
        _require_branch_sync_enabled()
        await _require_provider_scopes(
            db,
            merchant_id=merchant_id,
            write=True,
        )
        duplicate = await db[SALLA_INVENTORY_SYNC_RUNS].find_one(
            {
                "user_id": merchant_id,
                "publish_idempotency_key": payload.idempotency_key,
            },
            {"_id": 0},
        )
        if duplicate:
            return {
                "ok": True,
                "duplicate": True,
                "run": _public_run(duplicate),
            }
        run = await db[SALLA_INVENTORY_SYNC_RUNS].find_one(
            {
                "user_id": merchant_id,
                "id": payload.preview_id,
            },
            {"_id": 0},
        )
        if not run:
            raise HTTPException(
                status_code=404,
                detail={"code": "inventory_sync_preview_not_found"},
            )
        if run.get("status") != "previewed":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "inventory_sync_preview_not_publishable",
                    "status": run.get("status"),
                },
            )
        if _text(run.get("expires_at")) <= _now():
            await db[SALLA_INVENTORY_SYNC_RUNS].update_one(
                {"user_id": merchant_id, "id": payload.preview_id},
                {"$set": {"status": "expired", "updated_at": _now()}},
            )
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_sync_preview_expired"},
            )
        token_hash = hashlib.sha256(
            payload.confirm_token.encode("utf-8")
        ).hexdigest()
        if not secrets.compare_digest(
            token_hash,
            _text(run.get("confirm_token_hash")),
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "inventory_sync_confirmation_invalid"},
            )
        if run.get("issues") and not payload.acknowledge_issues:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "inventory_sync_issues_acknowledgement_required",
                    "issues_count": len(run.get("issues") or []),
                },
            )
        mapping = await _mapping_document(db, merchant_id)
        if int(mapping.get("revision") or 0) != int(
            run.get("mapping_revision") or 0
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_sync_mapping_changed"},
            )
        try:
            live_plan = await _build_live_plan(
                db,
                merchant_id=merchant_id,
                mappings=run.get("mappings") or [],
            )
        except (
            SallaError,
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:
            _raise_salla(exc)
        if live_plan["plan_signature"] != run.get("plan_signature"):
            await db[SALLA_INVENTORY_SYNC_RUNS].update_one(
                {"user_id": merchant_id, "id": payload.preview_id},
                {
                    "$set": {
                        "status": "stale",
                        "updated_at": _now(),
                        "stale_reason": (
                            "mezan_or_salla_quantity_changed"
                        ),
                    },
                },
            )
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_sync_preview_stale"},
            )
        locked = await db[SALLA_INVENTORY_SYNC_RUNS].update_one(
            {
                "user_id": merchant_id,
                "id": payload.preview_id,
                "status": "previewed",
            },
            {
                "$set": {
                    "status": "publishing",
                    "publish_idempotency_key": payload.idempotency_key,
                    "publish_started_at": _now(),
                    "published_by": context["actor_id"],
                    "issues_acknowledged": payload.acknowledge_issues,
                },
            },
        )
        if locked.modified_count != 1:
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_sync_publish_conflict"},
            )
        operations = [
            payload_row
            for row in live_plan["rows"]
            if (
                payload_row := executable_payload_row(
                    row,
                    reason_id=_text(run.get("reason_id")),
                )
            )
        ]
        responses = []
        applied_rows = 0
        try:
            for start in range(0, len(operations), MAX_BULK_ROWS):
                batch = operations[start:start + MAX_BULK_ROWS]
                response = await call_salla(
                    db,
                    merchant_id,
                    "POST",
                    "/products/quantities/bulk",
                    json={"products": batch},
                )
                responses.append({
                    "batch_number": len(responses) + 1,
                    "rows_count": len(batch),
                    "accepted": bool(
                        isinstance(response, dict)
                        and response.get("success", True)
                    ),
                })
                applied_rows += len(batch)
        except (
            SallaError,
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:
            failed_at = _now()
            await db[SALLA_INVENTORY_SYNC_RUNS].update_one(
                {"user_id": merchant_id, "id": payload.preview_id},
                {
                    "$set": {
                        "status": "publish_uncertain",
                        "applied_rows_before_error": applied_rows,
                        "provider_responses": responses,
                        "publish_error": str(exc)[:500],
                        "publish_ended_at": failed_at,
                        "updated_at": failed_at,
                    },
                },
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "inventory_sync_publish_uncertain",
                    "safe_to_retry": False,
                    "reconciliation_required": True,
                    "applied_rows_before_error": applied_rows,
                },
            ) from exc
        accepted_at = _now()
        await db[SALLA_INVENTORY_SYNC_RUNS].update_one(
            {"user_id": merchant_id, "id": payload.preview_id},
            {
                "$set": {
                    "status": "accepted_pending_verification",
                    "provider_responses": responses,
                    "published_rows": applied_rows,
                    "publish_ended_at": accepted_at,
                    "updated_at": accepted_at,
                },
            },
        )
        refreshed = await db[SALLA_INVENTORY_SYNC_RUNS].find_one(
            {"user_id": merchant_id, "id": payload.preview_id},
            {"_id": 0},
        )
        if not operations:
            verification = {
                "verified": True,
                "verification_status": "verified",
                "verification_mismatches": [],
                "verified_at": _now(),
            }
        else:
            try:
                verification = await _verify_run(
                    db,
                    merchant_id=merchant_id,
                    run=refreshed or run,
                )
            except (
                SallaError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as exc:
                verification = {
                    "verified": False,
                    "verification_status": (
                        "provider_read_temporarily_unavailable"
                    ),
                    "verification_mismatches": [],
                    "verification_error": str(exc)[:500],
                    "verified_at": _now(),
                }
        final_status = (
            "verified"
            if verification["verified"]
            else "accepted_pending_verification"
        )
        await db[SALLA_INVENTORY_SYNC_RUNS].update_one(
            {"user_id": merchant_id, "id": payload.preview_id},
            {
                "$set": {
                    "status": final_status,
                    **verification,
                    "updated_at": _now(),
                },
            },
        )
        final_run = await db[SALLA_INVENTORY_SYNC_RUNS].find_one(
            {"user_id": merchant_id, "id": payload.preview_id},
            {"_id": 0},
        )
        return {
            "ok": True,
            "duplicate": False,
            "run": _public_run(final_run or run),
        }

    @router.post("/runs/{run_id}/verify")
    async def verify(
        run_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.salla_sync.read")
        _require_branch_sync_enabled()
        merchant_id = context["merchant_id"]
        run = await db[SALLA_INVENTORY_SYNC_RUNS].find_one(
            {"user_id": merchant_id, "id": run_id},
            {"_id": 0},
        )
        if not run:
            raise HTTPException(
                status_code=404,
                detail={"code": "inventory_sync_run_not_found"},
            )
        if run.get("status") not in {
            "accepted_pending_verification",
            "verified",
            "publish_uncertain",
        }:
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_sync_run_not_verifiable"},
            )
        try:
            verification = await _verify_run(
                db,
                merchant_id=merchant_id,
                run=run,
            )
        except (
            SallaError,
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:
            _raise_salla(exc)
        final_status = (
            "verified"
            if verification["verified"]
            else "accepted_pending_verification"
        )
        await db[SALLA_INVENTORY_SYNC_RUNS].update_one(
            {"user_id": merchant_id, "id": run_id},
            {
                "$set": {
                    "status": final_status,
                    **verification,
                    "updated_at": _now(),
                },
            },
        )
        updated = await db[SALLA_INVENTORY_SYNC_RUNS].find_one(
            {"user_id": merchant_id, "id": run_id},
            {"_id": 0},
        )
        return {"ok": True, "run": _public_run(updated or run)}

    @router.get("/runs/{run_id}")
    async def get_run(
        run_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.salla_sync.read")
        run = await db[SALLA_INVENTORY_SYNC_RUNS].find_one(
            {
                "user_id": context["merchant_id"],
                "id": run_id,
            },
            {"_id": 0},
        )
        if not run:
            raise HTTPException(
                status_code=404,
                detail={"code": "inventory_sync_run_not_found"},
            )
        return {"ok": True, "run": _public_run(run)}

    @router.get("/runs")
    async def list_runs(
        limit: int = Query(20, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.salla_sync.read")
        rows = await db[SALLA_INVENTORY_SYNC_RUNS].find(
            {"user_id": context["merchant_id"]},
            {
                "_id": 0,
                "user_id": 0,
                "rows": 0,
                "issues": 0,
                "skipped": 0,
                "confirm_token_hash": 0,
                "provider_responses": 0,
            },
        ).sort("created_at", DESCENDING).limit(limit).to_list(length=limit)
        return {"ok": True, "items": rows, "total": len(rows)}

    return router


__all__ = [
    "SALLA_BRANCH_MAPPINGS",
    "SALLA_BRANCH_SYNC_FEATURE_FLAG",
    "SALLA_INVENTORY_SYNC_RUNS",
    "make_salla_inventory_sync_router",
    "salla_branch_inventory_sync_enabled",
]
