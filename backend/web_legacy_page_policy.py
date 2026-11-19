"""Employee access to the original Mezan menus and historical sources."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from datetime import date


_MENU = (
    ("/legacy-dashboard", "لوحة التحكم القديمة", "dashboard"),
    ("/orders", "الطلبات", "orders"),
    ("/accounts", "الأصول والحسابات", "accounts"),
    ("/new-transaction", "حركة مالية جديدة (موحدة)", "action"),
    ("/transactions", "سجل الحركات المالية", "ledger"),
    ("/employees-ledger", "الموظفون (Ledger)", "employee"),
    ("/employee-corrections", "تصحيح عملية موظف", "action"),
    ("/salary-reversals", "عكس صرف راتب", "action"),
    ("/expense-reversals", "عكس مصروف", "action"),
    ("/employees/custody-balances", "أرصدة العهد المفتوحة", "custody"),
    ("/suppliers-ledger", "الموردون (Ledger)", "supplier"),
    ("/externals-ledger", "خارجيون (Ledger)", "external"),
    ("/couriers-ledger", "شركات شحن (Ledger)", "courier"),
    ("/financial-position-ledger", "المركز المالي (Ledger)", "position"),
    ("/accounting/reconciliation", "تقرير المطابقة", "reconciliation"),
    ("/payment-settlements", "فواتير وتسويات بوابات الدفع", "statements"),
    ("/bank-transfer-review", "مراجعة التحويلات البنكية", "bank_review"),
    ("/ai/control-center", "مركز الذكاء والتحقق", "diagnostic"),
    ("/settlement-engine", "محرّك التسويات (Dry Run)", "diagnostic"),
    ("/bnpl-settlements", "تسويات Tabby و Tamara", "bnpl"),
    ("/bnpl-settlements/register", "تسجيل تسويات Tabby و Tamara", "action"),
    ("/audit/employee-orphans", "تشخيص قيود الموظفين اليتيمة", "diagnostic"),
    ("/audit/ad-debt", "تشخيص فرق المديونيات الإعلانية", "diagnostic"),
    ("/audit/ad-account-forensic", "Forensic للحسابات الإعلانية", "diagnostic"),
    ("/audit/balance-drift", "انحراف الأرصدة (Iter-250b P1.5)", "diagnostic"),
    ("/audit/ledger-health", "صحة الـ Ledger (Iter-240)", "diagnostic"),
    ("/salla-settlements", "تسويات سلة 🟧", "salla"),
    ("/settlements-overview", "جميع التسويات", "statements"),
    ("/bnpl-balances", "أرصدة BNPL (المصدر الموحَّد)", "bnpl"),
    ("/diagnostics/cod-source", "تشخيص مصدر COD", "diagnostic"),
    ("/audit/post-migration", "فحص ما بعد الترحيل", "diagnostic"),
    ("/diagnostics", "تشخيص فروقات الطلبات", "diagnostic"),
    ("/refund-audit", "تدقيق المسترجعات (BNPL)", "refund_audit"),
    ("/alerts", "التنبيهات الذكية", "alerts"),
    ("/accounting/migration", "ترحيل الأرصدة (Phase 2)", "action"),
    ("/diagnostics/api-permissions", "فحص صلاحيات API", "diagnostic"),
    ("/financial-movements", "قائمة الحركات المالية", "movements"),
    ("/suppliers-new", "الموردون", "supplier"),
    ("/expense-categories-tree", "شجرة التصنيفات", "categories"),
    ("/reports/suppliers", "تقرير الموردين", "supplier_report"),
    ("/operations-dashboard", "لوحة العمليات", "operations_dashboard"),
    ("/receivables", "الذمم والتحصيلات", "receivables"),
    ("/ad-accounts", "الحسابات الإعلانية والمديونية", "advertising"),
    ("/reports/advertising-expenses", "تقرير المصروفات الإعلانية", "ad_expense"),
    ("/shipping/orders-ledger", "دفتر الشحن التفصيلي", "shipping"),
    ("/shipping/transfers", "تحويلات شركات الشحن", "transfers"),
    ("/shipping/settings", "إعدادات شركات الشحن", "shipping_settings"),
    ("/upload", "رفع ملف Excel", "action"),
    ("/import-jobs", "حالة الاستيراد", "imports"),
    ("/make-webhook", "Webhooks", "admin"),
    ("/integrations/custom-app", "ربط تطبيقي الخاص", "admin"),
    ("/integrations/bnpl", "ربط تمارا وتابي", "admin"),
    ("/history", "سجل التحليلات", "history"),
    ("/settings/salla", "إعدادات سلة", "admin"),
    ("/integrations/salla/orders", "مراقبة الطلبات", "placeholder"),
    ("/integrations/salla/events", "سجل الأحداث", "placeholder"),
    ("/salla-sources", "مقارنة مصادر البيانات", "salla_sources"),
    ("/integrations-v2/qoyod", "قيود — التشغيل التلقائي", "admin"),
    ("/operational-reports", "التقارير التشغيلية", "operations_report"),
    ("/reports", "التقارير", "sales_report"),
    ("/ads-v2/settings", "إعدادات الإعلانات V2", "admin"),
    ("/ads-v2/report", "تقرير الإعلانات V2", "ads_report"),
    ("/products", "المنتجات", "products"),
    ("/product-costs", "تكاليف المنتجات", "costs"),
    ("/product-preparation", "تجهيز المنتجات", "prep"),
    ("/image-catalog", "إدارة صور المنتجات", "images"),
    ("/profile", "حسابي", "profile"),
    ("/settings/accounting-cutoffs", "تواريخ بدء المحاسبة", "admin"),
    ("/settings/operation-account-bindings", "ربط العمليات بالحسابات", "admin"),
    ("/settings", "الإعدادات", "admin"),
    ("/legacy-usage-report", "تقرير الاستخدام", "usage"),
    ("/purchase-invoices", "فواتير المشتريات 🕰️ Legacy", "purchases"),
    ("/operating-expenses", "المصروفات التشغيلية 🕰️ Legacy", "expenses"),
    ("/daily-costs", "التكاليف اليومية 🕰️ Legacy", "daily"),
    ("/team", "إدارة الفريق", "admin"),
)

_RESTRICTIONS = {
    "admin": "إدارة إعدادات المتجر والربط",
    "diagnostic": "فحص إداري لبيانات المتجر الكاملة",
    "action": "عملية قديمة تُنفّذ بصلاحية المالك",
    "aggregate": "تقرير قديم يجمع بيانات المتجر كاملة",
}
LEGACY_WEB_PAGES = tuple({
    "id": "legacy." + url.strip("/").replace("/", ".").replace("-", "_"),
    "section": "legacy", "section_label": "صفحات ميزان القديمة", "label": label, "to": url,
    **({"owner_only": True, "restriction_reason": _RESTRICTIONS[source]} if source in _RESTRICTIONS
       else {} if source in {"profile", "placeholder"} else {"review_component": "legacy_read"}),
    **({"date_filter": True} if source in {"dashboard", "sales_report", "operations_report", "ads_report", "salla_sources"} else {}),
} for url, label, source in _MENU)
_SOURCES = {page["id"]: item[2] for page, item in zip(LEGACY_WEB_PAGES, _MENU)}
_ENDPOINT = "/api/web-page-access-v1/legacy-read/"


def _deny():
    raise HTTPException(403, detail={"code": "web_page_permission_required"})


def enforce_legacy_web_pages(modes, path, method, query=None):
    if modes is None or not path.startswith(_ENDPOINT):
        return
    page_id = path[len(_ENDPOINT):]
    if method != "GET" or page_id not in modes or _SOURCES.get(page_id, "admin") in (*_RESTRICTIONS, "placeholder"):
        _deny()


def make_legacy_web_router(db, current_user):
    from web_page_access import delegated_page_owner

    router = APIRouter()

    @router.get("/legacy-read/{page_id}")
    async def legacy_read(page_id: str, request: Request, response: Response, page: int = Query(1, ge=1, le=12500),
                          day: date | None = None,
                          user=Depends(current_user)):
        from web_legacy_page_readers import read_legacy_page

        source = _SOURCES.get(page_id)
        if not source or source in (*_RESTRICTIONS, "placeholder"):
            _deny()
        owner = await delegated_page_owner(db, user, page_id)
        if not owner:
            _deny()
        response.headers["Cache-Control"] = "no-store, private"
        return await read_legacy_page(db, owner, user, source, page, day=day, app_state=request.app.state)

    return router
