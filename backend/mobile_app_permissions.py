"""Independent, fail-closed access contract for the native AMASI app.

These permissions intentionally do not belong to Mezan's operational
``PERMISSIONS`` catalogue. Granting native access never grants a browser page
or an operational Mezan permission. Native access remains explicit for staff;
the account owner receives full native-app access dynamically so newly added
app pages and actions are available without a separate permission migration.
"""
from __future__ import annotations

from typing import Any


MOBILE_APP_CLIENT = "amasi_mobile"
MOBILE_APP_ACCESS = "mezan_mobile_app_access_v1"
MOBILE_APP_ACCESS_OWNER_FIELD = "owner_user_id"
MOBILE_APP_MANAGER = "app.role.manager"
# Retained for backwards compatibility with older imports. Owner access is no
# longer limited to this legacy monitoring-only baseline.
OWNER_BASELINE_PERMISSIONS = {"app.page.operations_monitoring"}

MOBILE_APP_PERMISSION_GROUPS = [
    {
        "key": "app_management",
        "label": "إدارة التطبيق",
        "permissions": [
            {
                "key": MOBILE_APP_MANAGER,
                "label": "مدير تطبيق AMASI",
                "kind": "role",
                "grants_all_current_and_future_app_permissions": True,
            },
        ],
    },
    {
        "key": "preparation",
        "label": "إدارة التجهيز",
        "permissions": [
            {"key": "app.page.pending_review", "label": "انتظار المراجعة", "kind": "page"},
            {"key": "app.page.reviewed_preparation", "label": "تمت المراجعة", "kind": "page"},
            {"key": "app.page.my_products", "label": "إدارة منتجاتي", "kind": "page"},
            {"key": "app.page.preparation_receiving", "label": "الاستلام من الموظف", "kind": "page"},
            {"key": "app.page.assembly_shipping", "label": "التجميع والعنونة", "kind": "page"},
            {"key": "app.page.carrier_handoff", "label": "استلام موظف التسليم", "kind": "page"},
        ],
    },
    {
        "key": "catalogue",
        "label": "صفحات التطبيق",
        "permissions": [
            {"key": "app.page.products", "label": "المنتجات", "kind": "page"},
            {"key": "app.page.orders", "label": "الطلبات", "kind": "page"},
            {"key": "app.page.categories", "label": "التصنيف", "kind": "page"},
            {"key": "app.page.suppliers", "label": "إدارة الموردين", "kind": "page"},
            {"key": "app.page.couriers", "label": "إدارة الموصلين", "kind": "page"},
            {"key": "app.page.employees", "label": "إدارة الموظفين", "kind": "page"},
            {"key": "app.page.operations_monitoring", "label": "مراقبة العمليات", "kind": "page"},
        ],
    },
    {
        "key": "my_products_actions",
        "label": "إجراءات إدارة منتجاتي",
        "permissions": [
            {
                "key": "app.action.my_products.invoice_product_price.edit",
                "label": "إضافة أو تعديل سعر المنتج في الفاتورة",
                "kind": "action",
                "requires": "app.page.my_products",
            },
            {
                "key": "app.action.my_products.service_price.edit",
                "label": "تعديل سعر الخدمة",
                "kind": "action",
                "requires": "app.page.my_products",
            },
            {
                "key": "app.action.my_products.service.add",
                "label": "إضافة خدمة",
                "kind": "action",
                "requires": "app.page.my_products",
            },
        ],
    },
]

MOBILE_APP_PERMISSIONS = {
    item["key"]
    for group in MOBILE_APP_PERMISSION_GROUPS
    for item in group["permissions"]
}


def mobile_app_permission_catalog() -> list[dict[str, Any]]:
    """Return a response-safe copy of the fixed native-app catalogue."""
    return [
        {
            **group,
            "permissions": [dict(item) for item in group["permissions"]],
        }
        for group in MOBILE_APP_PERMISSION_GROUPS
    ]


def validate_mobile_app_permissions(values: Any) -> list[str]:
    if values is None:
        values = []
    if not isinstance(values, list):
        raise ValueError("mobile_app_permissions_invalid")
    normalized = sorted({
        str(value or "").strip()
        for value in values
        if str(value or "").strip()
    })
    unknown = [value for value in normalized if value not in MOBILE_APP_PERMISSIONS]
    if unknown:
        raise ValueError(f"unknown_mobile_app_permission:{unknown[0]}")
    selected = set(normalized)
    if MOBILE_APP_MANAGER in selected:
        return normalized
    for group in MOBILE_APP_PERMISSION_GROUPS:
        for item in group["permissions"]:
            required = str(item.get("requires") or "").strip()
            if item["key"] in selected and required and required not in selected:
                raise ValueError(f"mobile_app_permission_parent_required:{item['key']}")
    return normalized


def _collection(db: Any) -> Any:
    try:
        return db[MOBILE_APP_ACCESS]
    except (AttributeError, TypeError):
        return getattr(db, MOBILE_APP_ACCESS)


async def find_mobile_app_access(
    db: Any,
    *,
    owner_user_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    owner_id = str(owner_user_id or "").strip()
    subject_id = str(user_id or "").strip()
    if not owner_id or not subject_id:
        return None
    return await _collection(db).find_one(
        {
            MOBILE_APP_ACCESS_OWNER_FIELD: owner_id,
            "user_id": subject_id,
        },
        {"_id": 0},
    )


def effective_mobile_app_permissions(
    access: dict[str, Any] | None,
    *,
    account_active: bool = True,
) -> list[str]:
    if not access or access.get("enabled", True) is False or not account_active:
        return []
    stored = {
        str(value).strip()
        for value in access.get("permissions") or []
        if str(value).strip() in MOBILE_APP_PERMISSIONS
    }
    if MOBILE_APP_MANAGER in stored:
        # Manager is a live role, not a copied static page list. New app pages
        # added to the catalogue therefore become available automatically.
        return sorted(MOBILE_APP_PERMISSIONS)
    return sorted(stored)


async def _mobile_app_access_for_linked_employee(
    db: Any,
    *,
    user_id: str,
) -> dict[str, Any] | None:
    """Resolve legacy linked accounts without trusting a tenant supplied by the client.

    Older team accounts may not carry the modern ``created_by`` field even
    though Employees V2 links the account to exactly one merchant. Resolve the
    owner through that server-owned link, then perform the normal
    owner-and-user scoped access lookup.
    """
    if not user_id:
        return None
    employee = await db["mezan_employees_v2"].find_one(
        {"account_user_id": user_id},
        {"_id": 0, "user_id": 1},
    )
    owner_id = str((employee or {}).get("user_id") or "").strip()
    if not owner_id:
        return None
    return await find_mobile_app_access(
        db,
        owner_user_id=owner_id,
        user_id=user_id,
    )


async def mobile_app_access_for_user(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().casefold()
    is_owner = role == "owner" or user.get("is_owner") is True
    owner_id = (
        str(user.get("id") or "").strip()
        if is_owner
        else str(user.get("created_by") or "").strip()
    )
    user_id = str(user.get("id") or "").strip()
    # Owner access is derived from the live permission catalogue. A stored row
    # cannot narrow that super-admin contract, so reading it adds latency and a
    # second Mongo failure point to every /auth/me bootstrap with no authority.
    access = None if is_owner else await find_mobile_app_access(
        db,
        owner_user_id=owner_id,
        user_id=user_id,
    )
    if access is None and not is_owner:
        access = await _mobile_app_access_for_linked_employee(
            db,
            user_id=user_id,
        )
    account_active = not (
        user.get("disabled") is True
        or user.get("is_active") is False
        or user.get("deleted_at")
    )
    permissions = set(effective_mobile_app_permissions(
        access,
        account_active=account_active,
    ))
    if is_owner and account_active:
        # The account owner is the native-app super-admin. Use the live
        # catalogue rather than a copied list so every current and future app
        # page/action becomes available automatically while Mezan web RBAC
        # remains completely separate.
        permissions.update(MOBILE_APP_PERMISSIONS)
    permissions_list = sorted(permissions)
    return {
        "configured": access is not None or is_owner,
        "enabled": bool(account_active and (access is not None or is_owner)),
        "owner_override": bool(is_owner and account_active),
        "owner_baseline": bool(is_owner),
        "manager": MOBILE_APP_MANAGER in permissions,
        "permissions": permissions_list,
    }


__all__ = [
    "MOBILE_APP_ACCESS",
    "MOBILE_APP_ACCESS_OWNER_FIELD",
    "MOBILE_APP_CLIENT",
    "MOBILE_APP_MANAGER",
    "MOBILE_APP_PERMISSION_GROUPS",
    "MOBILE_APP_PERMISSIONS",
    "OWNER_BASELINE_PERMISSIONS",
    "effective_mobile_app_permissions",
    "find_mobile_app_access",
    "mobile_app_access_for_user",
    "mobile_app_permission_catalog",
    "validate_mobile_app_permissions",
]
