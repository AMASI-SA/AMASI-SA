export const ACCOUNTING_OPERATION_ID = "MZ2-FIN-CUTOVER-001";

export const ACCOUNTING_PAGES = [
    {
        id: "home",
        label: "المحاسبة اليومية",
        permission: "accounting.home.view",
        to: "/integrations-v2?workspace=financial&page=home",
        implementationStatus: "implemented",
    },
    {
        id: "financial-accounts",
        label: "الصناديق والحسابات المالية",
        permission: "accounting.financial_accounts.view",
        to: "/integrations-v2?workspace=financial&page=financial-accounts",
        implementationStatus: "implemented",
    },
    {
        id: "settlements",
        label: "التسويات",
        permission: "accounting.settlements.view",
        to: "/integrations-v2?workspace=financial&page=settlements",
        implementationStatus: "implemented",
    },
    {
        id: "shipping-cod",
        label: "الشحن والتحصيل",
        permission: "accounting.shipping.view",
        to: "/integrations-v2?workspace=financial&page=shipping-cod",
        implementationStatus: "implemented",
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
        implementationStatus: "implemented",
    },
    {
        id: "payroll-obligations",
        label: "الرواتب والالتزامات",
        permission: "accounting.payroll.view",
        to: "/integrations-v2?workspace=financial&page=payroll-obligations",
        implementationStatus: "implemented",
    },
    {
        id: "opening-balances",
        label: "الأرصدة الافتتاحية",
        permission: "accounting.opening_balances.view",
        to: "/integrations-v2?workspace=financial&page=opening-balances",
        implementationStatus: "implemented",
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
    { id: "refund-recognize", label: "اعتماد استحقاق العميل وإثبات التزام الاسترداد", permission: "accounting.refunds.recognize" },
    { id: "receipt-create", label: "تسجيل مبلغ واصل من منصة", permission: "accounting.receipts.create" },
    { id: "movement-import", label: "رفع كشف البنك والحركات اليومية", permission: "accounting.movements.import" },
    { id: "receivable-post", label: "إثبات ذمم الطلبات المؤهلة", permission: "accounting.receivables.post" },
    { id: "draft-create", label: "إنشاء وحفظ مسودة مالية", permission: "accounting.drafts.create" },
    { id: "settlement-post", label: "اعتماد وترحيل تسوية", permission: "accounting.settlements.post" },
    { id: "rules-manage", label: "تعديل قواعد العمولات والحسابات", permission: "accounting.rules.manage" },
    { id: "financial-accounts-manage", label: "إدارة الصناديق والحسابات المالية", permission: "accounting.financial_accounts.manage" },
    { id: "opening-drafts-manage", label: "إدارة مسودات الأرصدة الافتتاحية", permission: "accounting.opening_balances.drafts.manage" },
    { id: "opening-review", label: "مراجعة الأرصدة الافتتاحية", permission: "accounting.opening_balances.review" },
    { id: "opening-post", label: "ترحيل الأرصدة الافتتاحية", permission: "accounting.opening_balances.post" },
    { id: "ledger-transition-manage", label: "إدارة انتقال الدفتر المحاسبي", permission: "accounting.ledger_transition.manage" },
    { id: "purchase-post", label: "ترحيل فاتورة شراء وتحديث المخزون", permission: "accounting.purchases.post" },
    { id: "payroll-post", label: "اعتماد وترحيل الرواتب والالتزامات", permission: "accounting.payroll.post" },
    { id: "opening-approve", label: "اعتماد القيد الافتتاحي", permission: "accounting.opening_balances.approve" },
    { id: "manual-journal", label: "إنشاء قيد يدوي", permission: "accounting.journals.manual_create" },
    { id: "journal-reverse", label: "عكس قيد مرحّل", permission: "accounting.journals.reverse" },
];

export const ACCOUNTING_EXPLICIT_GRANT_PERMISSIONS = new Set([
    "accounting.financial_accounts.view",
    "accounting.financial_accounts.manage",
    "accounting.opening_balances.view",
    "accounting.opening_balances.drafts.manage",
    "accounting.opening_balances.review",
    "accounting.opening_balances.post",
    "accounting.journals.reverse",
    "accounting.ledger_transition.manage",
]);

export function accountingPageById(pageId) {
    return ACCOUNTING_PAGES.find((page) => page.id === pageId) || ACCOUNTING_PAGES[0];
}

export function accountingPageFromSearchParams(searchParams) {
    return accountingPageById(searchParams?.get?.("page") || "home");
}

export function userCanAccessAccounting(user, permission, assignedPermissions = []) {
    if (!user || !permission) return false;
    if (Array.isArray(assignedPermissions) && assignedPermissions.includes(permission)) return true;
    if (ACCOUNTING_EXPLICIT_GRANT_PERMISSIONS.has(permission)) return false;
    return user.is_owner === true || String(user.role || "").toLowerCase() === "owner";
}

export function accountingNavItems() {
    return ACCOUNTING_PAGES.map(({ to, label, permission }) => ({
        to,
        label,
        permission,
    }));
}
