export const ACCOUNTING_OPERATION_ID = "MZ2-FIN-CUTOVER-001";

export const ACCOUNTING_PAGES = [
    {
        id: "home",
        label: "الرئيسية المحاسبية",
        permission: "accounting.home.view",
        to: "/integrations-v2?workspace=financial&page=home",
        implementationStatus: "implemented",
    },
    {
        id: "settlements",
        label: "التسويات",
        permission: "accounting.settlements.view",
        to: "/integrations-v2?workspace=financial&page=settlements",
        implementationStatus: "partial_existing_workflows",
    },
    {
        id: "shipping-cod",
        label: "الشحن والتحصيل",
        permission: "accounting.shipping.view",
        to: "/integrations-v2?workspace=financial&page=shipping-cod",
        implementationStatus: "partial_existing_workflows",
    },
    {
        id: "inventory-purchases",
        label: "المخزون والمشتريات",
        permission: "accounting.inventory.view",
        to: "/integrations-v2?workspace=financial&page=inventory-purchases",
        implementationStatus: "partial_existing_workflows",
    },
    {
        id: "financial-movements",
        label: "الحركات المالية",
        permission: "accounting.movements.view",
        to: "/integrations-v2?workspace=financial&page=financial-movements",
        implementationStatus: "partial_existing_workflows",
    },
    {
        id: "payroll-obligations",
        label: "الرواتب والالتزامات",
        permission: "accounting.payroll.view",
        to: "/integrations-v2?workspace=financial&page=payroll-obligations",
        implementationStatus: "partial_existing_workflows",
    },
    {
        id: "opening-balances",
        label: "الأرصدة الافتتاحية",
        permission: "accounting.opening_balances.view",
        to: "/integrations-v2?workspace=financial&page=opening-balances",
        implementationStatus: "blocked_not_implemented",
    },
    {
        id: "journals-reports",
        label: "القيود والتقارير",
        permission: "accounting.journals_reports.view",
        to: "/integrations-v2?workspace=financial&page=journals-reports",
        implementationStatus: "partial_existing_workflows",
    },
];

export const ACCOUNTING_ACTIONS = [
    { id: "draft-create", label: "إنشاء وحفظ مسودة مالية", permission: "accounting.drafts.create" },
    { id: "settlement-post", label: "اعتماد وترحيل تسوية", permission: "accounting.settlements.post" },
    { id: "rules-manage", label: "تعديل قواعد العمولات والحسابات", permission: "accounting.rules.manage" },
    { id: "purchase-post", label: "ترحيل فاتورة شراء وتحديث المخزون", permission: "accounting.purchases.post" },
    { id: "payroll-post", label: "اعتماد وترحيل الرواتب والالتزامات", permission: "accounting.payroll.post" },
    { id: "opening-approve", label: "اعتماد القيد الافتتاحي", permission: "accounting.opening_balances.approve" },
    { id: "manual-journal", label: "إنشاء قيد يدوي", permission: "accounting.journals.manual_create" },
    { id: "journal-reverse", label: "عكس قيد مرحّل", permission: "accounting.journals.reverse" },
];

export function accountingPageById(pageId) {
    return ACCOUNTING_PAGES.find((page) => page.id === pageId) || ACCOUNTING_PAGES[0];
}

export function accountingPageFromSearchParams(searchParams) {
    return accountingPageById(searchParams?.get?.("page") || "home");
}

export function userCanAccessAccounting(user, permission, assignedPermissions = []) {
    if (!user || !permission) return false;
    return user.is_owner === true
        || String(user.role || "").toLowerCase() === "owner"
        || (Array.isArray(assignedPermissions) && assignedPermissions.includes(permission));
}

export function accountingNavItems() {
    return ACCOUNTING_PAGES.map(({ to, label, permission }) => ({
        to,
        label,
        permission,
    }));
}
