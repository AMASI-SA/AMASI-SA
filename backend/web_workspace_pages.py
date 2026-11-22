"""Mezan navigation entries and request boundaries for explicit web grants."""
from __future__ import annotations

import re

from fastapi import HTTPException


def page(key, section, title, to, **options):
    return {"id": key, "section": section, "label": title, "to": to, **options}


CORE_WEB_PAGES = (
    page("home.dashboard", "home", "الرئيسية", "/dashboard-advanced"),
    page("home.summary", "home", "لوحة ميزان ٢", "/dashboard-v2"),
    page("home.charts", "home", "رسوم لوحة التحكم", "/dashboard-charts"),
    page("products.sold", "products", "المنتجات المباعة", "/products-v2/sold"),
    page("employees.staff", "employees", "إدارة الموظفين", "/employees-v2"),
    page("employees.drivers", "employees", "موصلو المتجر", "/employees-v2?workspace=drivers"),
    page("employees.migration", "employees", "تقرير الترحيل والرواتب", "/employees-v2?workspace=migration"),
    page("employees.permissions", "employees", "إدارة الصلاحيات", "/employees-v2?workspace=permissions"),
    page("suppliers.directory", "suppliers", "الموردون والفواتير", "/suppliers-v2"),
    page("finance.obligations", "finance", "الالتزامات والمصاريف الدورية", "/recurring-obligations"),
    page("finance.collections", "finance", "مراجعة تحصيلات الموصلين", "/bank-transfer-review?workspace=store-delivery"),
    page("finance.settlements", "finance", "تسويات موصلي المتجر", "/settlements-overview?workspace=store-delivery"),
    page("shipping.settings", "shipping", "إدارة شركات الشحن والأسعار", "/shipping-settings-v2"),
    page("fulfillment.overview", "fulfillment", "إدارة التجهيز · بانتظار المراجعة", "/fulfillment-v2",
         aliases=["/order-review", "/fulfillment-v2?stage=pending_review"]),
    page("fulfillment.mine", "fulfillment", "إدارة منتجاتي", "/fulfillment-v2?workspace=my-products"),
    page("fulfillment.reviewed", "fulfillment", "تم المراجعة", "/fulfillment-v2?stage=reviewed&view=products",
         aliases=["/fulfillment-v2?stage=reviewed"]),
    page("fulfillment.reviewed_files", "fulfillment", "سجل ملفات التجهيز", "/fulfillment-v2?stage=reviewed&view=files"),
    page("fulfillment.progress", "fulfillment", "قيد التجهيز", "/fulfillment-v2?stage=in_progress"),
    page("fulfillment.supplier_receipt", "fulfillment", "استلام المورد", "/fulfillment-v2?stage=preparation"),
    page("fulfillment.receiving", "fulfillment", "الاستلام من التجهيز", "/fulfillment-v2?stage=assembly"),
    page("fulfillment.assembly", "fulfillment", "التجميع والعنونة", "/fulfillment-v2?stage=ready_to_ship"),
    page("fulfillment.completed", "fulfillment", "تم التنفيذ", "/fulfillment-v2?stage=completed"),
    page("fulfillment.handover", "fulfillment", "تسليم الشحنات للموصلين", "/fulfillment-v2?stage=courier_dispatch",
         aliases=["/fulfillment-v2?workspace=store-driver-handover"]),
    page("fulfillment.delivering", "fulfillment", "جاري التوصيل", "/fulfillment-v2?stage=delivering"),
    page("fulfillment.delivered", "fulfillment", "تم التوصيل", "/fulfillment-v2?stage=delivered"),
)

SECTION_LABELS = {
    "home": "الرئيسية", "orders": "الطلبات", "products": "المنتجات", "fulfillment": "إدارة التجهيز",
    "employees": "الموظفون", "suppliers": "الموردون والفواتير", "finance": "الإدارة المالية",
    "accounting": "المحاسبة", "balances": "الأرصدة التشغيلية", "marketing": "التسويق",
    "apps": "التطبيقات", "intelligence": "الذكاء الاصطناعي", "shipping": "شركات الشحن",
    "legacy": "صفحات ميزان القديم",
}
_DASHBOARD = ("home.dashboard", "home.summary")
_FULFILLMENT_READS = (
    (r"order-reviews-v1(?:/pages|/[0-9]+(?:/preparation-status)?)?", ("fulfillment.overview",)),
    (r"order-review-customer-waiting-v1", ("fulfillment.overview",)),
    (r"order-reviews-v1/reviewed|reviewed-products-v1/(?:groups|group-page|catalog)|reviewed-product-sorting-v1", ("fulfillment.reviewed",)),
    (r"reviewed-preparation-batches-v1/batches(?:/[^/]+/pdf)?", ("fulfillment.reviewed", "fulfillment.reviewed_files")),
    (r"preparation-file-registry-v1/(?:files|employees)", ("fulfillment.reviewed_files",)),
    (r"preparation-work-v1/(?:my-work|manager/summary|files/[^/]+)", ("fulfillment.mine", "fulfillment.progress")),
    (r"preparation-work-v1/receiving/(?:search|custody)", ("fulfillment.receiving",)),
    (r"preparation-work-v1/assembly/(?:search|orders/[^/]+)", ("fulfillment.assembly",)),
    (r"supplier-receiving-v1/(?:catalog|sessions/[^/]+(?:/invoice)?|display-groups)", ("fulfillment.supplier_receipt", "fulfillment.mine")),
    (r"fulfillment-v2/(?:ready-to-ship|batches)", ("fulfillment.assembly",)),
    (r"fulfillment-v2/completed", ("fulfillment.completed",)),
    (r"fulfillment-v2/carrier-handoff", ("fulfillment.handover",)),
)
_FULFILLMENT_NAMESPACES = frozenset(
    "order-reviews-v1 order-review-customer-waiting-v1 reviewed-products-v1 reviewed-product-sorting-v1 "
    "reviewed-preparation-batches-v1 preparation-file-registry-v1 preparation-work-v1 supplier-receiving-v1 "
    "fulfillment-v2".split()
)
_DASHBOARD_PARTS = {"", "cards/top-products", "cards/period-summary", "cards/executive",
    "product-cost-summary", "product-promotion-estimates", "abandoned-carts/recent", "ads-cost-breakdown",
    "meta-summary", "tiktok-summary", "google-summary", "snapchat-summary", "snapchat-accounts-summary",
    "unified-marketing-shadow"}


def deny():
    raise HTTPException(403, detail={"code": "web_page_permission_required"})


def workspace_read_pages(path, query):
    """None = other module. Empty tuple = unreviewed endpoint in this namespace."""
    name = path.removeprefix("/api/").rstrip("/")
    prefix, _, part = name.partition("/")
    if name == "integrations-v2/dashboard/ads-platform-spend":
        return (*_DASHBOARD, "home.charts")
    if prefix == "dashboard-v2":
        if not part:
            return (*_DASHBOARD, "home.charts")
        if part == "sold-products":
            return ("products.sold",)
        if part == "chart-orders":
            return ("home.charts", *_DASHBOARD)
        return _DASHBOARD if part in _DASHBOARD_PARTS else ()
    if prefix == "recurring-obligations":
        return ("finance.obligations",) if not part or part == "options" or re.fullmatch(r"[^/]+/invoices", part) else ()
    if prefix == "suppliers-v2":
        return ("suppliers.directory",) if part in {"workspace", "payment-workspace", "financials"} else ()
    courier_prefix = "ai-store-operations/access/store-courier-dispatch/"
    if name.startswith(courier_prefix):
        suffix = name[len(courier_prefix):]
        if suffix == "my-shipments":
            stage = (query or {}).get("stage", "waiting")
            return {"waiting": ("fulfillment.handover",), "delivering": ("fulfillment.delivering",),
                    "delivered": ("fulfillment.delivered",)}.get(stage, ())
        return ("fulfillment.handover",) if suffix in {"couriers", "assignments"} else ()
    if prefix in _FULFILLMENT_NAMESPACES:
        if name == "fulfillment-v2/delivery-tracking":
            stage = (query or {}).get("stage")
            return {"delivering": ("fulfillment.delivering",), "delivered": ("fulfillment.delivered",)}.get(stage, ())
        return next((pages for pattern, pages in _FULFILLMENT_READS if re.fullmatch(pattern, name)), ())
    return None


def enforce_workspace_read(modes, path, method, query):
    release = re.fullmatch(r"/api/(dashboard-v2|preparation-work-v1/assembly)/read-demand/[a-f0-9]{64}", path)
    if release:
        pages = _DASHBOARD if release[1] == "dashboard-v2" else ("fulfillment.assembly", "fulfillment.completed")
        # Release is interest in an actor/tenant-bound display job, never a
        # delegated financial, fulfillment or provider write.
        if method != "DELETE" or not any(page_id in modes for page_id in pages):
            deny()
        return
    pages = workspace_read_pages(path, query)
    if pages is None:
        return
    if method != "GET" or not any(page_id in modes for page_id in pages):
        deny()
    if path.startswith("/api/dashboard-v2") and any(
        str((query or {}).get(key, "")).lower() not in {"", "false", "0"}
        for key in ("refresh", "refresh_costs")
    ):
        deny()
