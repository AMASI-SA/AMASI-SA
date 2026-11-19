"""Employee access to the original Mezan menus and historical sources."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from datetime import date


_MENU = (
    ("/legacy-dashboard", "لوحة التحكم القديمة", "dashboard"),
    ("/orders", "الطلبات", "orders"),
    ("/accounts", "الأصول والحسابات", "accounts"),
    ("/new-transaction", "حركة مالية جديدة (موحدة)", "/new-transaction"),
    ("/transactions", "سجل الحركات المالية", "ledger"),
    ("/employees-ledger", "الموظفون (Ledger)", "employee"),
    ("/employee-corrections", "تصحيح عملية موظف", "/employee-corrections"),
    ("/salary-reversals", "عكس صرف راتب", "/salary-reversals"),
    ("/expense-reversals", "عكس مصروف", "/expense-reversals"),
    ("/employees/custody-balances", "أرصدة العهد المفتوحة", "custody"),
    ("/suppliers-ledger", "الموردون (Ledger)", "supplier"),
    ("/externals-ledger", "خارجيون (Ledger)", "external"),
    ("/couriers-ledger", "شركات شحن (Ledger)", "courier"),
    ("/financial-position-ledger", "المركز المالي (Ledger)", "position"),
    ("/accounting/reconciliation", "تقرير المطابقة", "reconciliation"),
    ("/payment-settlements", "فواتير وتسويات بوابات الدفع", "statements"),
    ("/bank-transfer-review", "مراجعة التحويلات البنكية", "bank_review"),
    ("/ai/control-center", "مركز الذكاء والتحقق", "/ai/control-center"),
    ("/settlement-engine", "محرّك التسويات (Dry Run)", "/settlement-engine"),
    ("/bnpl-settlements", "تسويات Tabby و Tamara", "bnpl"),
    ("/bnpl-settlements/register", "تسجيل تسويات Tabby و Tamara", "/bnpl-settlements/register"),
    ("/audit/employee-orphans", "تشخيص قيود الموظفين اليتيمة", "/audit/employee-orphans"),
    ("/audit/ad-debt", "تشخيص فرق المديونيات الإعلانية", "/audit/ad-debt"),
    ("/audit/ad-account-forensic", "Forensic للحسابات الإعلانية", "/audit/ad-account-forensic"),
    ("/audit/balance-drift", "انحراف الأرصدة (Iter-250b P1.5)", "/audit/balance-drift"),
    ("/audit/ledger-health", "صحة الـ Ledger (Iter-240)", "/audit/ledger-health"),
    ("/salla-settlements", "تسويات سلة 🟧", "salla"),
    ("/settlements-overview", "جميع التسويات", "statements"),
    ("/bnpl-balances", "أرصدة BNPL (المصدر الموحَّد)", "bnpl"),
    ("/diagnostics/cod-source", "تشخيص مصدر COD", "/diagnostics/cod-source"),
    ("/audit/post-migration", "فحص ما بعد الترحيل", "/audit/post-migration"),
    ("/diagnostics", "تشخيص فروقات الطلبات", "/diagnostics"),
    ("/refund-audit", "تدقيق المسترجعات (BNPL)", "refund_audit"),
    ("/alerts", "التنبيهات الذكية", "alerts"),
    ("/accounting/migration", "ترحيل الأرصدة (Phase 2)", "/accounting/migration"),
    ("/diagnostics/api-permissions", "فحص صلاحيات API", "/diagnostics/api-permissions"),
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
    ("/upload", "رفع ملف Excel", "/upload"),
    ("/import-jobs", "حالة الاستيراد", "imports"),
    ("/make-webhook", "Webhooks", "/make-webhook"),
    ("/integrations/custom-app", "ربط تطبيقي الخاص", "/integrations/custom-app"),
    ("/integrations/bnpl", "ربط تمارا وتابي", "/integrations/bnpl"),
    ("/history", "سجل التحليلات", "history"),
    ("/settings/salla", "إعدادات سلة", "/settings/salla"),
    ("/integrations/salla/orders", "مراقبة الطلبات", "salla_order_monitor"),
    ("/integrations/salla/events", "سجل الأحداث", "salla_events"),
    ("/salla-sources", "مقارنة مصادر البيانات", "salla_sources"),
    ("/integrations-v2/qoyod", "قيود — التشغيل التلقائي", "/integrations-v2/qoyod"),
    ("/integrations-v2/qoyod?tab=exceptions", "استثناءات الإرسال إلى قيود", "/integrations-v2/qoyod?tab=exceptions"),
    ("/integrations-v2/qoyod?tab=invoices", "فواتير قيود", "/integrations-v2/qoyod?tab=invoices"),
    ("/integrations-v2/qoyod?tab=reconciliation", "مطابقة فواتير قيود", "/integrations-v2/qoyod?tab=reconciliation"),
    ("/integrations-v2/qoyod?tab=settings", "إعدادات قيود", "/integrations-v2/qoyod?tab=settings"),
    ("/operational-reports", "التقارير التشغيلية", "operations_report"),
    ("/reports", "التقارير", "sales_report"),
    ("/ads-v2/settings", "إعدادات الإعلانات V2", "/ads-v2/settings"),
    ("/ads-v2/report", "تقرير الإعلانات V2", "ads_report"),
    ("/products", "المنتجات", "products"),
    ("/product-costs", "تكاليف المنتجات", "costs"),
    ("/product-preparation", "تجهيز المنتجات", "prep"),
    ("/image-catalog", "إدارة صور المنتجات", "images"),
    ("/profile", "حسابي", "profile"),
    ("/settings/accounting-cutoffs", "تواريخ بدء المحاسبة", "/settings/accounting-cutoffs"),
    ("/settings/operation-account-bindings", "ربط العمليات بالحسابات", "/settings/operation-account-bindings"),
    ("/settings", "الإعدادات", "/settings"),
    ("/legacy-usage-report", "تقرير الاستخدام", "usage"),
    ("/purchase-invoices", "فواتير المشتريات 🕰️ Legacy", "purchases"),
    ("/operating-expenses", "المصروفات التشغيلية 🕰️ Legacy", "expenses"),
    ("/daily-costs", "التكاليف اليومية 🕰️ Legacy", "daily"),
    ("/team", "إدارة الفريق", "/team"),
)

LEGACY_WEB_PAGES = tuple({
    "id": "legacy." + url.strip("/").replace("?tab=", ".").replace("/", ".").replace("-", "_"),
    "section": "legacy", "section_label": "صفحات ميزان القديمة", "label": label, "to": url,
    **({"aliases": ["/integrations-v2/qoyod?tab=overview", "/integrations-v2/qoyod?tab=status"]} if url == "/integrations-v2/qoyod" else {}),
    **({} if source == "profile" else {"review_component": "legacy_read"}),
    **({"date_filter": True} if source in {"dashboard", "sales_report", "operations_report", "ads_report", "salla_sources",
                                          "/ai/control-center", "/diagnostics/cod-source", "/diagnostics"} else {}),
} for url, label, source in _MENU)
_SOURCES = {page["id"]: item[2] for page, item in zip(LEGACY_WEB_PAGES, _MENU)}
_ENDPOINT = "/api/web-page-access-v1/legacy-read/"


def _deny():
    raise HTTPException(403, detail={"code": "web_page_permission_required"})


def enforce_legacy_web_pages(modes, path, method, query=None):
    if modes is None or not path.startswith(_ENDPOINT):
        return
    page_id = path[len(_ENDPOINT):]
    if method != "GET" or page_id not in modes or page_id not in _SOURCES:
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
        if not source:
            _deny()
        owner = await delegated_page_owner(db, user, page_id)
        if not owner:
            _deny()
        response.headers["Cache-Control"] = "no-store, private"
        return await read_legacy_page(db, owner, user, source, page, day=day, app_state=request.app.state)

    return router
