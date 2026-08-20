"""Lightweight shared role and permission contract for Mezan Store Operations.

This module deliberately has no database, product, Salla or HTTP imports so
employee, fulfillment and access-control tests can use the canonical catalogue
without loading unrelated integrations.
"""
from __future__ import annotations

from typing import Any


ROLE_ASSIGNMENTS = "mezan_role_assignments_v2"
AI_ACTION_LOG = "mezan_ai_action_log_v2"
ROLE_ASSIGNMENT_OWNER_FIELD = "owner_user_id"

PERMISSIONS = {
    "products.read",
    "products.create",
    "products.review",
    "products.publish",
    "products.cost.read",
    "products.cost.write",
    "products.media.read",
    "products.media.upload",
    "products.media.edit",
    "products.media.delete",
    "products.media.reorder",
    "products.media.publish",
    "products.media.ai_generate",
    "products.media.ai_edit",
    "products.ai.recommend",
    "products.ai.execute_low_risk",
    "products.ai.execute_high_risk",
    "employees.read",
    "employees.manage",
    "roles.manage",
    "audit.read",
    "preparation.assigned.read",
    "preparation.assigned.work",
    "preparation.assigned.stop",
    "fulfillment.stop.manage",
    "fulfillment.experiment.reset",
    "fulfillment.ready.read",
    "fulfillment.batch.claim",
    "fulfillment.labels.print",
    "fulfillment.labels.reprint",
    "fulfillment.pack.confirm",
    "fulfillment.carrier.handoff",
    "fulfillment.store_courier.assign",
    "fulfillment.store_courier.deliver",
    "inventory.receipts.read",
    "inventory.receipts.write",
    "inventory.preparation.read",
    "inventory.preparation.create",
    "inventory.preparation.work",
    "inventory.preparation.receive",
    "supplier_receiving.product_price.edit",
    "supplier_receiving.service_price.edit",
    "supplier_receiving.service.add",
    "suppliers.read",
    "suppliers.manage",
    "inventory.salla_sync.read",
    "inventory.salla_sync.manage_mappings",
    "inventory.salla_sync.publish",
    "customer_intelligence.inbox.read",
    "customer_intelligence.suggestions.review",
    "customer_intelligence.escalate",
}

ROLE_CATALOG = {
    "owner": sorted(PERMISSIONS),
    "product_manager": sorted({
        "products.read", "products.create", "products.review", "products.publish",
        "products.cost.read", "products.media.read", "products.media.upload",
        "products.media.edit", "products.media.delete", "products.media.reorder",
        "products.media.publish", "products.ai.recommend", "audit.read",
        "suppliers.read",
    }),
    "product_operator": sorted({
        "products.read", "products.create", "products.review", "products.cost.read",
        "products.media.read", "products.media.upload", "products.media.edit",
        "products.media.reorder", "products.ai.recommend",
    }),
    "preparation_operator": sorted({
        "preparation.assigned.read", "preparation.assigned.work",
        "preparation.assigned.stop",
    }),
    "customer_service": sorted({
        "products.read", "fulfillment.stop.manage",
        "customer_intelligence.inbox.read",
        "customer_intelligence.suggestions.review",
        "customer_intelligence.escalate",
    }),
    "cost_manager": sorted({
        "products.read", "products.cost.read", "products.cost.write", "audit.read",
        "inventory.receipts.read", "inventory.receipts.write",
        "inventory.preparation.read", "inventory.preparation.create",
        "inventory.preparation.receive", "inventory.salla_sync.read", "suppliers.read",
    }),
    "warehouse_operator": sorted({
        "products.read", "products.cost.read", "fulfillment.ready.read",
        "fulfillment.batch.claim", "fulfillment.pack.confirm",
        "inventory.receipts.read", "inventory.receipts.write",
        "inventory.preparation.read", "inventory.preparation.create",
        "inventory.preparation.work", "inventory.preparation.receive",
        "inventory.salla_sync.read", "suppliers.read",
    }),
    "shipping_operator": sorted({
        "products.read", "fulfillment.ready.read", "fulfillment.batch.claim",
        "fulfillment.labels.print", "fulfillment.pack.confirm",
        "fulfillment.carrier.handoff", "fulfillment.store_courier.assign",
    }),
    "store_courier": sorted({
        "fulfillment.store_courier.deliver",
    }),
    "marketing_manager": sorted({
        "products.read", "products.review", "products.media.read",
        "products.media.upload", "products.media.edit", "products.media.reorder",
        "products.ai.recommend", "audit.read",
    }),
    "ai_product_optimizer": sorted({
        "products.read", "products.cost.read", "products.media.read",
        "products.media.ai_generate", "products.media.ai_edit",
        "products.ai.recommend", "products.ai.execute_low_risk",
    }),
}

ROLE_LABELS = {
    "owner": "مالك النظام",
    "product_manager": "مدير المنتجات",
    "product_operator": "موظف المنتجات",
    "preparation_operator": "موظف التجهيز",
    "customer_service": "خدمة العملاء",
    "cost_manager": "مسؤول التكاليف والمشتريات",
    "warehouse_operator": "موظف المخزن",
    "shipping_operator": "موظف الشحن والعنونة",
    "store_courier": "مندوب توصيل المتجر",
    "marketing_manager": "مسؤول التسويق",
    "ai_product_optimizer": "وكيل تحسين المنتجات بالذكاء الاصطناعي",
}

RESPONSIBILITY_TYPES = {
    "instant_ready",
    "packing",
    "shipping_labeling",
    "carrier_handoff",
    "store_courier_dispatch",
    "store_courier_delivery",
    "stock_preparation",
}


def validate_assignment(payload: dict[str, Any]) -> dict[str, Any]:
    role_key = str(payload.get("role_key") or "").strip()
    if role_key not in ROLE_CATALOG:
        raise ValueError("invalid_role_key")
    extra = sorted({str(value).strip() for value in payload.get("extra_permissions") or [] if str(value).strip()})
    denied = sorted({str(value).strip() for value in payload.get("denied_permissions") or [] if str(value).strip()})
    unknown = [value for value in extra + denied if value not in PERMISSIONS]
    if unknown:
        raise ValueError(f"unknown_permission:{unknown[0]}")
    overlap = set(extra) & set(denied)
    if overlap:
        raise ValueError(f"permission_conflict:{sorted(overlap)[0]}")
    warehouse_ids = sorted({
        str(value).strip()
        for value in payload.get("warehouse_ids") or []
        if str(value).strip()
    })
    workplace_warehouse_id = str(payload.get("workplace_warehouse_id") or "").strip()
    if workplace_warehouse_id and workplace_warehouse_id not in warehouse_ids:
        raise ValueError("workplace_warehouse_not_assigned")
    responsibilities = sorted({
        str(value).strip()
        for value in payload.get("fulfillment_responsibilities") or []
        if str(value).strip()
    })
    unknown_responsibilities = [value for value in responsibilities if value not in RESPONSIBILITY_TYPES]
    if unknown_responsibilities:
        raise ValueError(f"unknown_fulfillment_responsibility:{unknown_responsibilities[0]}")
    return {
        "role_key": role_key,
        "extra_permissions": extra,
        "denied_permissions": denied,
        "enabled": bool(payload.get("enabled", True)),
        "warehouse_ids": warehouse_ids,
        "workplace_warehouse_id": workplace_warehouse_id or None,
        "fulfillment_responsibilities": responsibilities,
    }


def effective_permissions(assignment: dict[str, Any] | None) -> list[str]:
    if not assignment or not assignment.get("enabled", True):
        return []
    role_key = str(assignment.get("role_key") or "")
    base = set(ROLE_CATALOG.get(role_key, []))
    base |= set(assignment.get("extra_permissions") or [])
    base -= set(assignment.get("denied_permissions") or [])
    return sorted(base)


def _role_assignments_collection(db: Any) -> Any:
    """Support Motor's item/attribute access without coupling test doubles."""
    try:
        return db[ROLE_ASSIGNMENTS]
    except (AttributeError, TypeError):
        return getattr(db, ROLE_ASSIGNMENTS)


def role_assignment_owner_user_id(user: dict[str, Any] | None) -> str:
    """Resolve the existing team-account owner boundary for role assignments.

    Owners own their own store scope.  Every other authenticated team user must
    carry the existing ``created_by`` relationship; missing ownership evidence
    intentionally resolves to an empty value so permission reads fail closed.
    """
    value = user or {}
    actor_id = str(value.get("id") or "").strip()
    role = str(value.get("role") or "").strip().casefold()
    if role == "owner" or value.get("is_owner") is True:
        return actor_id
    return str(value.get("created_by") or "").strip()


def role_assignment_write_query(
    *,
    owner_user_id: str,
    user_id: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a tenant-bound selector, including safe legacy promotion.

    An unscoped legacy row is writable only when its immutable creator proves
    the same owner boundary.  Setting ``owner_user_id`` on that write promotes
    the row lazily without a global migration or a user-id-only update.
    """
    owner_id = str(owner_user_id or "").strip()
    subject_id = str(user_id or "").strip()
    if not owner_id or not subject_id:
        raise ValueError("role_assignment_tenant_required")
    if existing and not str(existing.get(ROLE_ASSIGNMENT_OWNER_FIELD) or "").strip():
        if str(existing.get("created_by") or "").strip() != owner_id:
            raise ValueError("legacy_role_assignment_owner_unproven")
        return {
            ROLE_ASSIGNMENT_OWNER_FIELD: None,
            "created_by": owner_id,
            "user_id": subject_id,
        }
    return {
        ROLE_ASSIGNMENT_OWNER_FIELD: owner_id,
        "user_id": subject_id,
    }


async def write_role_assignment(
    db: Any,
    *,
    owner_user_id: str,
    user_id: str,
    existing: dict[str, Any] | None,
    values: dict[str, Any],
    upsert: bool = False,
) -> Any:
    """Write only within one proven tenant and promote safe legacy rows."""
    owner_id = str(owner_user_id or "").strip()
    subject_id = str(user_id or "").strip()
    scoped_values = {
        **values,
        ROLE_ASSIGNMENT_OWNER_FIELD: owner_id,
        "user_id": subject_id,
    }
    collection = _role_assignments_collection(db)
    existing_owner = str(
        (existing or {}).get(ROLE_ASSIGNMENT_OWNER_FIELD) or ""
    ).strip()
    selector = role_assignment_write_query(
        owner_user_id=owner_id,
        user_id=subject_id,
        existing=existing,
    )
    if existing is None or existing_owner:
        return await collection.update_one(
            selector,
            {"$set": scoped_values},
            upsert=upsert,
        )
    result = await collection.update_one(
        selector,
        {"$set": scoped_values},
        upsert=False,
    )
    matched_count = int(getattr(result, "matched_count", 0) or 0)
    if not matched_count:
        # Another authorized request may have promoted the same legacy row.
        # Retry only against the canonical tenant selector; never upsert from a
        # legacy selector that could race into a duplicate scoped assignment.
        return await collection.update_one(
            role_assignment_write_query(
                owner_user_id=owner_id,
                user_id=subject_id,
            ),
            {"$set": scoped_values},
            upsert=upsert,
        )
    return result


async def find_role_assignment(
    db: Any,
    *,
    owner_user_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Read one assignment within a proven owner scope.

    Scoped rows are authoritative.  Backward compatibility is bounded to an
    unscoped row whose ``created_by`` equals the same proven owner; ambiguous
    or foreign legacy rows are ignored.
    """
    owner_id = str(owner_user_id or "").strip()
    subject_id = str(user_id or "").strip()
    if not owner_id or not subject_id:
        return None
    collection = _role_assignments_collection(db)
    scoped = await collection.find_one(
        {
            ROLE_ASSIGNMENT_OWNER_FIELD: owner_id,
            "user_id": subject_id,
        },
        {"_id": 0},
    )
    if (
        scoped
        and str(scoped.get(ROLE_ASSIGNMENT_OWNER_FIELD) or "").strip()
        == owner_id
        and str(scoped.get("user_id") or "").strip() == subject_id
    ):
        return scoped
    legacy = await collection.find_one(
        {
            ROLE_ASSIGNMENT_OWNER_FIELD: None,
            "created_by": owner_id,
            "user_id": subject_id,
        },
        {"_id": 0},
    )
    if (
        legacy
        and not str(legacy.get(ROLE_ASSIGNMENT_OWNER_FIELD) or "").strip()
        and str(legacy.get("created_by") or "").strip() == owner_id
        and str(legacy.get("user_id") or "").strip() == subject_id
    ):
        return legacy
    return None


async def find_role_assignments(
    db: Any,
    *,
    owner_user_id: str,
    user_ids: list[str] | set[str] | tuple[str, ...],
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Read tenant-scoped assignments for a bounded set of team identities."""
    owner_id = str(owner_user_id or "").strip()
    subject_ids = sorted(
        {
            str(value or "").strip()
            for value in user_ids
            if str(value or "").strip()
        }
    )
    if not owner_id or not subject_ids:
        return []
    collection = _role_assignments_collection(db)
    scoped_rows = await collection.find(
        {
            ROLE_ASSIGNMENT_OWNER_FIELD: owner_id,
            "user_id": {"$in": subject_ids},
        },
        {"_id": 0},
    ).to_list(length=limit)
    legacy_rows = await collection.find(
        {
            ROLE_ASSIGNMENT_OWNER_FIELD: None,
            "created_by": owner_id,
            "user_id": {"$in": subject_ids},
        },
        {"_id": 0},
    ).to_list(length=limit)
    by_user: dict[str, dict[str, Any]] = {}
    for row in legacy_rows:
        subject_id = str(row.get("user_id") or "").strip()
        if (
            subject_id in subject_ids
            and not str(row.get(ROLE_ASSIGNMENT_OWNER_FIELD) or "").strip()
            and str(row.get("created_by") or "").strip() == owner_id
        ):
            by_user[subject_id] = row
    for row in scoped_rows:
        subject_id = str(row.get("user_id") or "").strip()
        if (
            subject_id in subject_ids
            and str(row.get(ROLE_ASSIGNMENT_OWNER_FIELD) or "").strip() == owner_id
        ):
            by_user[subject_id] = row
    return list(by_user.values())


async def merged_session_permissions(
    db: Any,
    user: dict[str, Any],
    legacy_permissions: set[str] | list[str],
) -> list[str]:
    """Merge Employee OS permissions into `/auth/me` without role leakage.

    Only the account Owner automatically receives every operational
    permission. Legacy Admin and other legacy roles receive Customer
    Intelligence capabilities only through an enabled V2 role assignment.
    """
    merged = {str(value) for value in legacy_permissions if str(value)}
    role = str(user.get("role") or "").strip().casefold()
    if role == "owner" or user.get("is_owner") is True:
        merged |= PERMISSIONS
    elif role == "meta_reviewer":
        from meta_reviewer_access import (
            META_REVIEWER_CI_PERMISSIONS,
            require_review_scope,
        )

        require_review_scope(user, "customer_intelligence")
        merged = set(META_REVIEWER_CI_PERMISSIONS)
    else:
        assignment = await find_role_assignment(
            db,
            owner_user_id=role_assignment_owner_user_id(user),
            user_id=str(user.get("id") or ""),
        )
        merged |= set(effective_permissions(assignment))
    return sorted(merged)


__all__ = [
    "AI_ACTION_LOG",
    "PERMISSIONS",
    "RESPONSIBILITY_TYPES",
    "ROLE_ASSIGNMENTS",
    "ROLE_ASSIGNMENT_OWNER_FIELD",
    "ROLE_CATALOG",
    "ROLE_LABELS",
    "effective_permissions",
    "find_role_assignment",
    "find_role_assignments",
    "merged_session_permissions",
    "role_assignment_owner_user_id",
    "role_assignment_write_query",
    "write_role_assignment",
    "validate_assignment",
]
