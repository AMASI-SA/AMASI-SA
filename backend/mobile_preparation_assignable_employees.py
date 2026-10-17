"""Resolve preparation assignees from AMASI native-app permissions.

The reviewed-preparation mobile screen must not depend on Mezan web RBAC.
Employees are assignable when their linked native account is active and has
``app.page.my_products`` (or the live AMASI manager role, which expands to all
native permissions). The merchant owner remains assignable automatically.
"""
from __future__ import annotations

from typing import Any

from mobile_app_permissions import (
    MOBILE_APP_ACCESS,
    MOBILE_APP_ACCESS_OWNER_FIELD,
    effective_mobile_app_permissions,
)
from order_review_export_controls import assignable_employee_view


REQUIRED_ASSIGNMENT_PERMISSION = "app.page.my_products"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _account_active(row: dict[str, Any]) -> bool:
    return not (
        row.get("disabled") is True
        or row.get("is_active") is False
        or row.get("deleted_at")
    )


async def native_preparation_assignable_employees(
    db: Any,
    *,
    user_id: str,
    reviewer: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return owner + active native employees allowed to work in My Products."""
    owner_id = _text(user_id)
    if not owner_id:
        return []

    # Team accounts created under the merchant remain the canonical account IDs
    # used by preparation piece custody. Employees V2 is also consulted because
    # older linked accounts can legitimately lack ``created_by``.
    direct_users = await db.users.find(
        {"created_by": owner_id},
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "email": 1,
            "role": 1,
            "disabled": 1,
            "is_active": 1,
            "deleted_at": 1,
        },
    ).to_list(1000)

    employee_links = await db["mezan_employees_v2"].find(
        {
            "user_id": owner_id,
            "account_user_id": {"$nin": [None, ""]},
        },
        {
            "_id": 0,
            "account_user_id": 1,
            "name": 1,
            "email": 1,
            "status": 1,
            "is_active": 1,
            "deleted_at": 1,
        },
    ).to_list(1000)
    linked_ids = sorted({
        _text(row.get("account_user_id"))
        for row in employee_links
        if _text(row.get("account_user_id"))
    })
    linked_users = (
        await db.users.find(
            {"id": {"$in": linked_ids}},
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "email": 1,
                "role": 1,
                "disabled": 1,
                "is_active": 1,
                "deleted_at": 1,
            },
        ).to_list(max(len(linked_ids), 1))
        if linked_ids
        else []
    )

    candidates_by_id: dict[str, dict[str, Any]] = {}
    owner = dict(reviewer or {})
    if _text(owner.get("id")) == owner_id and _account_active(owner):
        candidates_by_id[owner_id] = owner
    for row in [*direct_users, *linked_users]:
        account_id = _text(row.get("id"))
        if account_id and _account_active(row):
            candidates_by_id[account_id] = row

    subject_ids = [value for value in candidates_by_id if value != owner_id]
    access_rows = (
        await db[MOBILE_APP_ACCESS].find(
            {
                MOBILE_APP_ACCESS_OWNER_FIELD: owner_id,
                "user_id": {"$in": subject_ids},
            },
            {"_id": 0},
        ).to_list(max(len(subject_ids), 1))
        if subject_ids
        else []
    )
    access_by_user = {
        _text(row.get("user_id")): row
        for row in access_rows
        if _text(row.get("user_id"))
    }

    result: list[dict[str, Any]] = []
    for account_id, row in candidates_by_id.items():
        if account_id == owner_id:
            result.append(assignable_employee_view(row))
            continue
        permissions = set(effective_mobile_app_permissions(
            access_by_user.get(account_id),
            account_active=True,
        ))
        if REQUIRED_ASSIGNMENT_PERMISSION not in permissions:
            continue
        result.append(assignable_employee_view(row))

    # Prefer the Employees V2 display name/email when the account profile is
    # sparse, without changing the stable account ID used for custody.
    link_by_account = {
        _text(row.get("account_user_id")): row
        for row in employee_links
        if _text(row.get("account_user_id"))
    }
    for item in result:
        link = link_by_account.get(_text(item.get("id"))) or {}
        if not _text(item.get("name")):
            item["name"] = _text(link.get("name")) or _text(item.get("email")) or "موظف التجهيز"
        if not _text(item.get("email")):
            item["email"] = _text(link.get("email"))

    result.sort(key=lambda row: (_text(row.get("name")).casefold(), _text(row.get("id"))))
    return result


def install_native_preparation_assignable_employees() -> None:
    """Patch the reviewed-file registry resolver used by the native app."""
    import preparation_file_registry as registry

    registry._assignable_employees = native_preparation_assignable_employees


__all__ = [
    "REQUIRED_ASSIGNMENT_PERMISSION",
    "install_native_preparation_assignable_employees",
    "native_preparation_assignable_employees",
]
