"""Permission-scoped merchant context for native AMASI requests.

The native app authenticates the employee account, while Mezan's historical
read models are tenant-scoped by the merchant owner id.  This adapter bridges
those identities only for an explicit allow-list of native routes and only
when the linked employee owns the matching app-page permission.
"""
from __future__ import annotations

from typing import Any, Iterable

from fastapi import HTTPException

from mobile_app_permissions import MOBILE_APP_CLIENT, mobile_app_access_for_user
from preparation_route_history import install_supplier_dispatch_route_guard
from supplier_receipt_employee_custody import install_supplier_receipt_employee_custody


# Install the two native-operation guards once per process. They preserve
# physical-piece history while enforcing current route visibility and transfer
# employee custody only when supplier receipt is finally approved.
install_supplier_dispatch_route_guard()
install_supplier_receipt_employee_custody()


def _permissions(*values: str) -> frozenset[str]:
    return frozenset(values)


# Longest/specific prefixes must appear before their broader parent prefixes.
MOBILE_ROUTE_PERMISSIONS: tuple[tuple[str, frozenset[str]], ...] = (
    ("/api/mobile/operations-monitoring", _permissions("app.page.operations_monitoring")),
    ("/api/order-reviews-v1/reviewed", _permissions("app.page.reviewed_preparation")),
    ("/api/order-reviews-v1", _permissions("app.page.pending_review", "app.page.reviewed_preparation")),
    ("/api/order-review-customer-waiting-v1", _permissions("app.page.pending_review")),
    ("/api/order-review-export-controls-v1", _permissions("app.page.pending_review")),
    ("/api/mobile-reviewed-preparation-v1", _permissions("app.page.reviewed_preparation")),
    ("/api/reviewed-preparation-batches-v1", _permissions("app.page.reviewed_preparation")),
    ("/api/reviewed-products-v1", _permissions("app.page.reviewed_preparation")),
    ("/api/reviewed-product-sorting-v1", _permissions("app.page.reviewed_preparation")),
    ("/api/mobile-preparation-execution-v1", _permissions("app.page.my_products")),
    ("/api/mobile-product-file-v1", _permissions("app.page.my_products")),
    ("/api/preparation-file-registry-v1", _permissions("app.page.my_products")),
    ("/api/preparation-file-safety-v1", _permissions("app.page.my_products")),
    ("/api/supplier-dispatch-pdf-v1", _permissions("app.page.my_products")),
    ("/api/supplier-dispatch-share-v1", _permissions("app.page.my_products")),
    ("/api/supplier-dispatch-v1", _permissions("app.page.my_products")),
    ("/api/supplier-receiving-v1", _permissions("app.page.my_products")),
    ("/api/preparation-work-v1/receiving", _permissions("app.page.preparation_receiving")),
    ("/api/preparation-work-v1/assembly", _permissions("app.page.assembly_shipping")),
    ("/api/preparation-work-v1/files", _permissions("app.page.my_products")),
    ("/api/fulfillment-v2/carrier-handoff", _permissions("app.page.carrier_handoff")),
    ("/api/fulfillment-v2/completed", _permissions("app.page.assembly_shipping", "app.page.carrier_handoff")),
    ("/api/orders-v2", _permissions("app.page.orders")),
    ("/api/order-items-v2", _permissions("app.page.orders")),
    ("/api/product-costs", _permissions("app.page.products", "app.page.my_products")),
    ("/api/products-v2", _permissions("app.page.products", "app.page.my_products")),
    ("/api/service-candidates", _permissions("app.page.products", "app.page.my_products")),
    ("/api/components-v2", _permissions("app.page.categories")),
    ("/api/reports/suppliers/mobile", _permissions("app.page.suppliers")),
    ("/api/mobile", _permissions("app.page.suppliers")),
    ("/api/store-delivery", _permissions("app.page.couriers", "app.page.carrier_handoff")),
    ("/api/employees-v2/mobile-directory", _permissions("app.page.employees")),
    ("/api/employees-v2/management", _permissions("app.page.employees")),
    ("/api/team/users", _permissions("app.page.employees")),
)


def required_mobile_permissions(path: str) -> frozenset[str] | None:
    normalized = str(path or "").rstrip("/") or "/"
    for prefix, permissions in MOBILE_ROUTE_PERMISSIONS:
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return permissions
    return None


async def _linked_owner_id(db: Any, user: dict[str, Any]) -> str:
    owner_id = str(user.get("created_by") or "").strip()
    if owner_id:
        return owner_id
    actor_id = str(user.get("id") or "").strip()
    employee = await db["mezan_employees_v2"].find_one(
        {"account_user_id": actor_id},
        {"_id": 0, "user_id": 1},
    )
    return str((employee or {}).get("user_id") or "").strip()


def _has_any(granted: Iterable[str], required: frozenset[str]) -> bool:
    return bool(set(granted).intersection(required))


async def mobile_app_request_user(
    db: Any,
    user: dict[str, Any],
    *,
    path: str,
    method: str,
) -> dict[str, Any]:
    """Return a merchant-scoped principal for an authorized native route.

    Browser sessions, owners, and auth/profile endpoints are untouched. Native
    employee routes fail closed when they are not in the explicit route map.
    """
    if str(user.get("_session_client") or "") != MOBILE_APP_CLIENT:
        return user
    role = str(user.get("role") or "").strip().casefold()
    if role == "owner" or user.get("is_owner") is True:
        return user
    if str(path or "").startswith("/api/auth/"):
        return user

    required = required_mobile_permissions(path)
    if not required:
        raise HTTPException(
            status_code=403,
            detail={"code": "mobile_app_route_not_allowed"},
        )

    access = await mobile_app_access_for_user(db, user)
    granted = access.get("permissions") or []
    if not access.get("enabled") or not _has_any(granted, required):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "mobile_app_page_permission_required",
                "required_any": sorted(required),
            },
        )

    owner_id = await _linked_owner_id(db, user)
    if not owner_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "employee_store_not_linked"},
        )
    owner = await db.users.find_one({"id": owner_id}, {"_id": 0})
    if not owner:
        raise HTTPException(
            status_code=409,
            detail={"code": "employee_store_owner_unavailable"},
        )

    principal = dict(owner)
    principal["_session_client"] = MOBILE_APP_CLIENT
    principal["_mobile_actor_id"] = str(user.get("id") or "")
    principal["_mobile_actor_name"] = str(user.get("name") or user.get("display_name") or "")
    principal["_mobile_actor_email"] = str(user.get("email") or "")
    principal["_mobile_app_permissions"] = sorted(set(granted))
    principal["_mobile_request_method"] = str(method or "").upper()
    principal["_mobile_owner_id"] = owner_id
    return principal


__all__ = [
    "MOBILE_ROUTE_PERMISSIONS",
    "mobile_app_request_user",
    "required_mobile_permissions",
]
