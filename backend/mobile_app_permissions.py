"""Independent, fail-closed access contract for the native AMASI app.

These permissions intentionally do not belong to Mezan's operational
``PERMISSIONS`` catalogue.  Granting a mobile page never grants a browser page
or an operational Mezan permission, and a team account may therefore have zero
Mezan permissions while retaining explicitly selected native-app pages.
"""
from __future__ import annotations

from typing import Any


MOBILE_APP_CLIENT = "amasi_mobile"
MOBILE_APP_ACCESS = "mezan_mobile_app_access_v1"
MOBILE_APP_ACCESS_OWNER_FIELD = "owner_user_id"

MOBILE_APP_PERMISSION_GROUPS = [
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
    return sorted({
        str(value).strip()
        for value in access.get("permissions") or []
        if str(value).strip() in MOBILE_APP_PERMISSIONS
    })


async def mobile_app_access_for_user(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().casefold()
    is_owner = role == "owner" or user.get("is_owner") is True
    if is_owner:
        return {
            "configured": True,
            "enabled": True,
            "owner_override": True,
            "permissions": sorted(MOBILE_APP_PERMISSIONS),
        }

    owner_id = str(user.get("created_by") or "").strip()
    user_id = str(user.get("id") or "").strip()
    access = await find_mobile_app_access(
        db,
        owner_user_id=owner_id,
        user_id=user_id,
    )
    account_active = not (
        user.get("disabled") is True
        or user.get("is_active") is False
        or user.get("deleted_at")
    )
    return {
        "configured": access is not None,
        "enabled": bool(access and access.get("enabled", True) and account_active),
        "owner_override": False,
        "permissions": effective_mobile_app_permissions(
            access,
            account_active=account_active,
        ),
    }


__all__ = [
    "MOBILE_APP_ACCESS",
    "MOBILE_APP_ACCESS_OWNER_FIELD",
    "MOBILE_APP_CLIENT",
    "MOBILE_APP_PERMISSION_GROUPS",
    "MOBILE_APP_PERMISSIONS",
    "effective_mobile_app_permissions",
    "find_mobile_app_access",
    "mobile_app_access_for_user",
    "mobile_app_permission_catalog",
    "validate_mobile_app_permissions",
]
