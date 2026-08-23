"""Permission, tenant, and navigation contract for MZ2-FIN-CUTOVER-001."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

OPERATION_ID = "MZ2-FIN-CUTOVER-001"

ACCOUNTING_PAGES: tuple[dict[str, str], ...] = (
    {"id": "home", "label": "الرئيسية المحاسبية", "permission": "accounting.home.view"},
    {"id": "settlements", "label": "التسويات", "permission": "accounting.settlements.view"},
    {"id": "shipping-cod", "label": "الشحن والتحصيل", "permission": "accounting.shipping.view"},
    {"id": "inventory-purchases", "label": "المخزون والمشتريات", "permission": "accounting.inventory.view"},
    {"id": "financial-movements", "label": "الحركات المالية", "permission": "accounting.movements.view"},
    {"id": "payroll-obligations", "label": "الرواتب والالتزامات", "permission": "accounting.payroll.view"},
    {"id": "opening-balances", "label": "الأرصدة الافتتاحية", "permission": "accounting.opening_balances.view"},
    {"id": "journals-reports", "label": "القيود والتقارير", "permission": "accounting.journals_reports.view"},
)

ACCOUNTING_ACTIONS: tuple[dict[str, str], ...] = (
    {"id": "draft-create", "label": "إنشاء وحفظ مسودة مالية", "permission": "accounting.drafts.create"},
    {"id": "settlement-post", "label": "اعتماد وترحيل تسوية", "permission": "accounting.settlements.post"},
    {"id": "rules-manage", "label": "تعديل قواعد العمولات والحسابات", "permission": "accounting.rules.manage"},
    {"id": "purchase-post", "label": "ترحيل فاتورة شراء وتحديث المخزون", "permission": "accounting.purchases.post"},
    {"id": "payroll-post", "label": "اعتماد وترحيل الرواتب والالتزامات", "permission": "accounting.payroll.post"},
    {"id": "opening-approve", "label": "اعتماد القيد الافتتاحي", "permission": "accounting.opening_balances.approve"},
    {"id": "manual-journal", "label": "إنشاء قيد يدوي", "permission": "accounting.journals.manual_create"},
    {"id": "journal-reverse", "label": "عكس قيد مرحّل", "permission": "accounting.journals.reverse"},
)

ACCOUNTING_PAGE_PERMISSION_KEYS = frozenset(row["permission"] for row in ACCOUNTING_PAGES)
ACCOUNTING_PERMISSION_KEYS = frozenset(
    row["permission"] for row in (*ACCOUNTING_PAGES, *ACCOUNTING_ACTIONS)
)

EVIDENCE_SECTIONS: tuple[dict[str, str], ...] = (
    {"id": "banks_cash", "label": "البنوك والصندوق"},
    {"id": "providers", "label": "سلة وطرق الدفع والتمويل"},
    {"id": "couriers_cod", "label": "شركات الشحن والتحصيل وذمم الموصلين"},
    {"id": "inventory", "label": "المخزون وتكلفته المعتمدة"},
    {"id": "suppliers", "label": "الموردون وفواتير الشراء غير المسددة"},
    {"id": "payroll_obligations", "label": "الرواتب والالتزامات المستحقة"},
    {"id": "equity", "label": "رأس المال وحقوق الملكية والتسوية"},
)


class AccountingPermissionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permissions: list[str] = Field(
        default_factory=list,
        max_length=len(ACCOUNTING_PERMISSION_KEYS),
    )


def role_name(user: dict[str, Any]) -> str:
    return str(user.get("role") or "").strip().lower()


def is_owner(user: dict[str, Any]) -> bool:
    # The persisted role is the only owner authority. Never trust a stored or
    # client-provided boolean marker for financial privilege.
    return role_name(user) == "owner"


def accounting_owner_id(user: dict[str, Any]) -> str | None:
    """Resolve the merchant data owner for a browser team user.

    Team users are created with ``created_by=<owner id>``.  We use that
    explicit relationship instead of silently reading the employee's empty
    personal tenant.  Unknown/unlinked users fail closed.
    """
    if is_owner(user):
        return str(user.get("id") or "").strip() or None
    return str(user.get("created_by") or "").strip() or None


def accounting_permissions_for_user(user: dict[str, Any]) -> list[str]:
    """Owner gets all; every other role gets only dedicated assignments."""
    if is_owner(user):
        return sorted(ACCOUNTING_PERMISSION_KEYS)
    assigned = set(user.get("accounting_permissions") or [])
    return sorted(assigned & ACCOUNTING_PERMISSION_KEYS)


def require_owner(user: dict[str, Any]) -> None:
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="هذه العملية متاحة لمالك ميزان فقط")


def require_accounting_permission(user: dict[str, Any], permission: str) -> None:
    if permission not in accounting_permissions_for_user(user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "accounting_permission_required",
                "permission": permission,
                "message": "لا تملك الصلاحية المحاسبية المطلوبة",
            },
        )
    if not accounting_owner_id(user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "accounting_owner_scope_missing",
                "message": "لا يوجد ربط موثق بين المستخدم ومالك بيانات المحاسبة",
            },
        )


def require_any_accounting_page(user: dict[str, Any]) -> None:
    if not (set(accounting_permissions_for_user(user)) & ACCOUNTING_PAGE_PERMISSION_KEYS):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "accounting_page_permission_required",
                "message": "لا تملك صلاحية أي صفحة محاسبية",
            },
        )
    if not accounting_owner_id(user):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "accounting_owner_scope_missing",
                "message": "لا يوجد ربط موثق بين المستخدم ومالك بيانات المحاسبة",
            },
        )


def accounting_user_view(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user.get("id"),
        "name": user.get("name") or "",
        "email": user.get("email") or "",
        "role": user.get("role") or "viewer",
        "is_owner": is_owner(user),
        "accounting_permissions": accounting_permissions_for_user(user),
    }
