"""Employee web reads for advertising and customer workspaces. Writes retain their native guards."""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response


def _page(key: str, section: str, label: str, to: str, **options) -> dict:
    return {"id": key, "section": section, "label": label, "to": to,
            "review_component": "marketing_read", **options}


PROVIDERS = {"snapchat": "سناب شات", "tiktok": "تيك توك", "meta": "ميتا", "google": "إعلانات Google"}
MARKETING_WEB_PAGES = (
    _page("marketing.overview", "marketing", "جميع المنصات", "/ads-manager"),
    _page("marketing.reports", "marketing", "التقارير", "/ads-manager/reports"),
    _page("marketing.recommendations", "marketing", "توصيات الحملات", "/ads-manager/recommendations"),
    _page("marketing.cohorts", "marketing", "أفواج العملاء", "/ads-manager/customer-cohorts"),
    *(_page("marketing." + name, "marketing", label,
            "/snapchat-accounts" if name == "snapchat" else "/ads-manager?provider=" + name)
      for name, label in PROVIDERS.items()),
    _page("marketing.costs", "marketing", "العمولات وسعر الصرف", "/ads-manager/cost-settings", aliases=["/settings/ads-currencies"]),
    _page("apps.integrations", "apps", "كل التطبيقات والتكاملات", "/integrations-v2"),
    _page("apps.instagram", "apps", "حالة تكامل إنستغرام", "/integrations-v2/instagram"),
    _page("apps.advertising", "apps", "الحسابات الإعلانية", "/integrations-v2?workspace=accounts"),
    _page("intelligence.assistant", "intelligence", "مساعد ميزان", "/assistant"),
    _page("intelligence.inbox", "intelligence", "ذكاء العملاء", "/customer-intelligence", aliases=["/customer-intelligence?tab=conversations"]),
    _page("intelligence.delivery", "intelligence", "تعليمات توصيل الموصلين", "/customer-intelligence?workspace=store-delivery"),
    _page("intelligence.tracking", "intelligence", "متابعة الطلبات وتعليماتها", "/order-tracking-notes"),
)
_STAFF_READERS = frozenset({
    "marketing.recommendations", "marketing.costs", "apps.integrations", "apps.advertising",
    "intelligence.delivery", "intelligence.tracking", "intelligence.inbox", "intelligence.assistant", "apps.instagram",
})


def _denied():
    raise HTTPException(403, detail={"code": "web_page_permission_required"})


def _report_page(modes, query):
    if "marketing.reports" in modes:
        return "marketing.reports"
    provider = query.get("provider") if query else None
    if provider not in PROVIDERS or query.get("source", "platform") != "platform":
        _denied()
    return "marketing." + provider


def enforce_marketing_web_pages(modes, path, method, query=None):
    if modes is None:
        return
    path = path.rstrip("/")
    if path == "/api/integrations-v2/dashboard/ads-platform-spend" and method == "GET":
        # Its read-only scope and chart projection belong to the dashboard policy.
        return
    if path == "/api/marketing-reports":
        page = _report_page(modes, query)
    elif path == "/api/mezan-attribution-v1/customer-cohorts":
        page = "marketing.cohorts"
    elif path == "/api/ads-manager/overview":
        page = "marketing.overview"
    elif path.startswith("/api/web-page-access-v1/marketing-read/"):
        page = path.rsplit("/", 1)[1]
        if page not in _STAFF_READERS:
            _denied()
    elif any(path == prefix or path.startswith(prefix + "/") for prefix in (
            "/api/ads-manager", "/api/snapchat-v2", "/api/integrations-v2",
            "/api/customer-intelligence", "/api/store-delivery/customer-service")):
        # Never use a view-only grant to activate provisioning, sync, budget or reply APIs.
        _denied()
    else:
        return
    if method != "GET" or page not in modes:
        _denied()
    return True


async def marketing_read_owner(db, user, page):
    """A tenant id for one read. Never give the caller an owner-shaped principal."""
    from web_page_access import delegated_page_owner
    return await delegated_page_owner(db, user, page)


async def report_read_owner(db, user, provider, source):
    from web_page_access import configured_page_modes
    modes = configured_page_modes(user)
    if modes is None:
        return None
    page = _report_page(modes, {"provider": provider, "source": source})
    return await marketing_read_owner(db, user, page)


def _table(columns, rows, *, more=False):
    return {"columns": columns, "rows": [[cell if isinstance(cell, (str, float, int, date, datetime)) else None
                                            for cell in row] for row in rows], "has_more": more}


async def _read_page(db, owner, actor, page, q, offset):
    if page in {"apps.integrations", "apps.advertising", "apps.instagram"}:
        from integrations_control_center.catalog import ADVERTISING_PROVIDERS
        from integrations_control_center.service import IntegrationsControlCenterService
        provider_ids = {"instagram"} if page == "apps.instagram" else (ADVERTISING_PROVIDERS if page == "apps.advertising" else None)
        data = await IntegrationsControlCenterService(db).overview(owner, provider_ids=provider_ids)
        if page in {"apps.integrations", "apps.instagram"}:
            rows = [[p.get("name_ar") or p["provider"], p.get("connection_status"), p.get("last_sync_at")]
                    for p in data["providers"]]
            return _table(["التطبيق", "حالة الربط", "آخر مزامنة"], rows)
        rows = [[p.get("name_ar") or p["provider"], a.get("display_name") or a.get("external_account_id"),
                 a.get("currency"), a.get("connection_status")]
                for p in data["providers"] if p["provider"] in ADVERTISING_PROVIDERS
                for a in p.get("accounts", [])]
        return _table(["المنصة", "الحساب", "العملة", "حالة الربط"], rows)
    if page == "marketing.costs":
        from ads_manager.account_cost_settings import list_account_cost_settings
        data = await list_account_cost_settings(db, owner, ensure_indexes=False)
        return _table(["الحساب", "العملة", "سعر الصرف إلى الريال", "عمولة البنك ٪"],
                      [[r["display_name"], r["native_currency"], r["exchange_rate_to_sar"],
                        r["bank_commission_pct"] if r["apply_bank_commission"] else 0] for r in data["items"]])
    if page == "marketing.recommendations":
        from campaign_ai_public_guard import latest_public_recommendations
        data = await latest_public_recommendations(db, owner)
        return _table(["التوصية", "السبب", "الحالة"],
                      [[r.get("title") or r.get("recommended_action") or r.get("proposed_action") or r.get("action"),
                        r.get("why") or r.get("rationale") or r.get("diagnosis"), r.get("execution_status")]
                       for r in data.get("recommendations", [])])
    if page == "intelligence.assistant":
        # Present matching readiness counts only; conversation and order records are never returned.
        checks = {
            "رقم الطلب": ("order_number", "reference_id", "order_id"),
            "تاريخ الطلب": ("order_date", "created_at_utc", "created_at"),
            "حالة الطلب": ("order_status", "order_status_native", "status_native"),
            "المبلغ": ("total_amount_sar", "total_amount", "total"),
            "طريقة الدفع": ("payment_method", "payment_method_code"),
        }
        projection = {field: 1 for keys in checks.values() for field in keys}
        records = await db.unified_orders.find({"user_id": owner}, {"_id": 0, **projection}).sort(
            [("order_date", -1), ("order_number", -1)]).limit(100).to_list(100)
        if not records:
            return _table(["فحص جاهزية البيانات", "طلبات مكتملة الحقل", "حجم العينة"], [])
        return _table(["فحص جاهزية البيانات", "طلبات مكتملة الحقل", "أحدث الطلبات المفحوصة"],
                      [[label, sum(any(row.get(field) is not None and row[field] != "" for field in fields)
                                   for row in records), len(records)] for label, fields in checks.items()])
    if page == "intelligence.inbox":
        from ai_store_access_contract import find_role_assignment
        assignment = await find_role_assignment(db, owner_user_id=owner, user_id=actor)
        if "customer_intelligence.inbox.read" in ((assignment or {}).get("denied_permissions") or []):
            _denied()
        from customer_intelligence.routes import _live_inbox_enabled
        from customer_intelligence.inbox import CustomerIntelligenceInboxService
        if not _live_inbox_enabled():
            raise HTTPException(404, detail={"code": "customer_inbox_disabled"})
        data = await CustomerIntelligenceInboxService(db).inbox(
            owner_user_id=owner, actor_id=actor, is_owner=False, limit=20, messages_limit=1, offset=offset)
        return _table(["العميل", "القناة", "آخر رسالة", "تاريخ الرسالة"],
                      [[r.customer_name, r.channel, r.last_message, r.last_message_at]
                       for r in data.conversations], more=data.has_more)
    if page == "intelligence.delivery":
        from store_delivery_customer_instruction_routes import STORE_DELIVERY_INSTRUCTIONS
        collection = STORE_DELIVERY_INSTRUCTIONS
        match = {"user_id": owner}
        fields = ("order_number", "order_id", "note", "delivery_date", "status")
        columns = ["رقم الطلب", "التعليمات", "موعد التوصيل", "الحالة"]
    else:
        from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
        collection = ORDER_TRACKING_INSTRUCTIONS
        # Tracking history is personal. Independently assigned tasks retain their native inbox.
        match = {"user_id": owner, "created_by": actor}
        fields = ("order_number", "note", "created_at", "status")
        columns = ["رقم الطلب", "التعليمات", "تاريخ التسجيل", "الحالة"]
    if q:
        match["$or"] = [{name: q} for name in fields if name in ("order_number", "order_id")]
    results = await db[collection].find(match, {"_id": 0, **dict.fromkeys(fields, 1)}).sort(
        [("created_at", -1), ("id", 1)]).skip(offset).limit(21).to_list(21)
    return _table(columns, [[r.get("order_number") or r.get("order_id"), r.get("note"),
                             r.get("delivery_date") if page == "intelligence.delivery" else r.get("created_at"),
                             r.get("status")] for r in results[:20]], more=len(results) > 20)


def make_web_marketing_router(db, current_user):
    router = APIRouter()

    @router.get("/marketing-read/{page_id}")
    async def read_page(page_id: str, response: Response, q: str = Query(default="", max_length=100),
                        offset: int = Query(default=0, ge=0, le=10000), user: dict = Depends(current_user)):
        if page_id not in _STAFF_READERS:
            _denied()
        owner = await marketing_read_owner(db, user, page_id)
        if owner is None:
            _denied()
        if page_id == "intelligence.inbox" and "customer_intelligence.inbox.read" in (user.get("denied_permissions") or []):
            _denied()
        response.headers["Cache-Control"] = "private, no-store"
        return await _read_page(db, owner, user["id"], page_id, q.strip(), offset)

    return router
