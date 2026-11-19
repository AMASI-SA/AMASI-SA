"""Independent read grants for the operational diagnostics and report routes."""
import asyncio
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from tz_utils import riyadh_today


def _page(key, title, url, section):
    return {"id": f"extra.{key}", "section": section, "label": title, "to": url, "review_component": "extra_read"}


EXTRA_WEB_PAGES = (
    _page("inventory_purchases", "فواتير شراء المخزون", "/inventory-v2?tab=purchases", "products"),
    _page("inventory_opening", "إضافات المخزون المسجلة", "/inventory-v2?tab=opening", "products"),
    _page("inventory_purchase", "شراء مخزون", "/inventory-v2?tab=purchase", "products"),
    _page("salla_recovery", "استعادة أحداث سلة المتوقفة", "/salla-webhook-recovery", "apps"),
    _page("ads_report", "تقرير أداء المنصات الإعلانية", "/reports/ads", "marketing"),
    _page("bnpl_diagnostics", "فحص معاملات تابي وتمارا", "/integrations/bnpl/diagnostics", "finance"),
    _page("financial_entry", "مدخل تسجيل الحركات المالية", "/financial-movement/new", "finance"),
    _page("snapchat_period", "تشخيص اتساق فترة طلبات سناب", "/diagnostics/snapchat-period", "marketing"),
)
PAGE_IDS = frozenset(page["id"] for page in EXTRA_WEB_PAGES)


def _result(labels, rows, description=""):
    return {"columns": [{"key": key, "label": label} for key, label in labels.items()],
            "rows": [{key: row.get(key) for key in labels} for row in rows], "description": description}


async def _recovery(db, owner):
    from salla_integration.webhook_incident_recovery import IncidentRecovery, RecoveryError, _binding, enabled

    try:
        await _binding(db, owner)
    except RecoveryError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    queue = await IncidentRecovery(db)._queue_metadata(owner)
    states = {"pending": "بانتظار المعالجة", "processing": "قيد المعالجة", "retry": "بانتظار إعادة المحاولة",
              "quarantined": "متوقفة", "done": "مكتملة"}
    return _result({"state": "حالة الحدث", "count": "الأحداث", "incident": "أحداث دفعة الاستعادة"},
                   [{"state": label, "count": queue["counts"][key], "incident": queue["grant_counts"][key]}
                    for key, label in states.items()],
                   "الاستعادة مفعلة في الخادم." if enabled() else "الاستعادة متوقفة في الخادم.")


async def _bnpl(db, owner, actor):
    from bnpl.config_store import BNPL_PROVIDERS
    from bnpl.diagnostics_routes import _provider_stats
    from operational_balance_access import load_access
    from web_legacy_page_readers import _report_providers

    grants = await load_access(db, actor, owner)
    grants.require_enabled()
    providers = _report_providers(grants)
    rows = []
    for provider in BNPL_PROVIDERS:
        if provider in providers:
            stats = await _provider_stats(db, owner, provider)
            rows.append({"provider": "تابي" if provider == "tabby" else "تمارا", **stats})
    return _result({"provider": "المنصة", "transactions_count": "معاملات الدفع", "refunds_count": "الاسترجاعات",
                    "latest_payment_at": "تاريخ آخر دفعة"}, rows,
                   "معاملات المنصات المحفوظة ضمن الحسابات المسموح لك بمراجعتها.")


async def _entry(db, owner, actor):
    from operational_balance_access import load_access
    from web_page_access import configured_page_modes

    grants = configured_page_modes(actor) or {}
    links = []
    if grants.get("balances.entry") == "write":
        policy = await load_access(db, actor, owner)
        if policy.enabled and any(not op.startswith(("customer.refund", "provider.statement_")) and op != "courier.review"
                                  for op in policy.operations) and any(item["use"] for item in policy.resources):
            links.append({"to": "/operational-balances?view=entry", "label": "تسجيل حركة مالية"})
    for key, url, label in (("balances.log", "/operational-balances/daily-movements", "سجل الحركات المالية"),
                            ("legacy.financial_movements", "/financial-movements", "قائمة الحركات القديمة")):
        if key in grants:
            links.append({"to": url, "label": label})
    return {**_result({}, [], "اختر العملية من الصفحات الممنوحة لحسابك." if links else
                      "تسجيل حركة أو عرض سجلها يحتاج إلى صلاحية الصفحة والعملية المرتبطة بها."), "links": links}


def _range(start, end):
    if (start is None) != (end is None):
        raise HTTPException(422, detail="incomplete_date_range")
    if not start:
        today = riyadh_today()
        return today.replace(day=1), today
    if start > end or end - start > timedelta(days=92):
        raise HTTPException(422, detail="invalid_date_range")
    return start, end


async def _snapchat(db, owner, start, end, split):
    from snapchat_v2.period_diagnostics import audit_period_partition
    from snapchat_v2.routes import _read_days, _selected_account_or_404

    _read_days(start, end)
    if not split or not start < split <= end:
        raise HTTPException(422, detail="invalid_period_split")
    account = await _selected_account_or_404(db, owner)
    try:
        data = await asyncio.wait_for(audit_period_partition(db, owner, date_from=start, date_to=end, split_on=split,
                            timezone_name=str(account["timezone"]), max_rows=10_000), timeout=20)
    except ValueError as exc:
        raise HTTPException(422, detail="period_diagnostic_incomplete") from exc
    except TimeoutError as exc:
        raise HTTPException(503, detail="period_diagnostic_timeout") from exc
    periods = {"whole": "الفترة الكاملة", "left": "الجزء الأول", "right": "الجزء الثاني", "gap": "فرق العدّ"}
    return _result({"period": "الفترة", "orders": "كل الطلبات", "snap": "مصدرها سناب صراحة"},
                   [{"period": label, "orders": data["all_orders"][key], "snap": data["explicit_snapchat_source"][key]}
                    for key, label in periods.items()], f"توقيت المقارنة {data['timezone']}")


async def _stock_rows(db, owner, actor, page_id, page):
    from operational_balance_access import load_access
    from operational_balance_store import STATES
    from standalone_inventory_entry import ENTRIES

    offset = (page - 1) * 8
    stages = [{"$skip": offset}, {"$limit": 9}]
    tenant = {"user_id": owner}
    if page_id == "extra.inventory_opening":
        # Opening receipts can have several units and locations. Leave line
        # quantities separate and omit costs, approval fingerprints and notes.
        labels = {"recorded_at": "تاريخ الإدخال", "name": "الصنف", "quantity": "الكمية", "unit": "الوحدة", "location": "الموقع"}
        rows = await db[ENTRIES].aggregate([
            {"$match": tenant}, {"$sort": {"recorded_at": -1, "_id": -1}},
            {"$unwind": "$result.lines"}, *stages,
            {"$project": {"_id": 0, "recorded_at": 1, "name": "$result.lines.name",
                          "quantity": "$result.lines.quantity", "unit": "$result.lines.unit", "location": "$result.lines.location_name"}},
        ]).to_list(9)
    else:
        policy = await load_access(db, actor, owner)
        policy.require_enabled()
        suppliers = [r for r in policy.resources if r["kind"] == "supplier"]
        if page_id == "extra.inventory_purchases":
            labels = {"invoice_number": "رقم الفاتورة", "supplier_name": "المورد", "invoice_date": "تاريخ الفاتورة",
                      "gross": "إجمالي الشراء", "currency": "العملة"}
            clauses = []
            for mode in ("all", "own"):
                ids = [r["id"] for r in suppliers if r["report"] and r["movements"] == mode]
                if ids:
                    clauses.append({"inventory_purchases.supplier_id": {"$in": ids},
                                    **({"inventory_purchases.actor_id": actor["id"]} if mode == "own" else {})})
            rows = [] if not clauses else await db[STATES].aggregate([
                {"$match": {"_id": owner, "owner_id": owner}}, {"$unwind": "$inventory_purchases"},
                {"$match": {"$or": clauses}}, {"$sort": {"inventory_purchases.recorded_at": -1, "inventory_purchases.id": -1}},
                *stages, {"$project": {"_id": 0, **{key: f"$inventory_purchases.{key}" for key in labels}}},
            ]).to_list(9)
        else:
            labels = {"name": "المورد المتاح للشراء", "currency": "العملة"}
            ids = [r["id"] for r in suppliers if r["use"]]
            rows = [] if not ids else await db.mezan_suppliers_v2.aggregate([
                {"$match": {**tenant, "id": {"$in": ids}, "status": {"$nin": ["inactive", "archived", "deleted", "hidden", "suspended"]},
                            "active": {"$ne": False}, "is_active": {"$ne": False}, "ready": {"$ne": False},
                            "currency": {"$in": [None, "", "SAR"]},
                            **{key: {"$nin": [True, 1]} for key in ("archived", "deleted", "is_archived", "is_deleted")}}},
                {"$project": {"_id": 0, "id": 1, "name": {"$ifNull": ["$display_name", {"$ifNull": ["$name", "$company_name"]}]},
                              "currency": {"$ifNull": ["$currency", "SAR"]}}},
                {"$sort": {"name": 1, "id": 1}}, *stages,
            ]).to_list(9)
    return {**_result(labels, rows[:8]), "has_more": len(rows) > 8}


def make_extra_web_router(db, current_user):
    router = APIRouter()

    @router.get("/extra-read/{page_id}")
    async def read(page_id: str, request: Request, response: Response, date_from: date | None = None,
                   date_to: date | None = None, split_on: date | None = None, page: int = Query(1, ge=1, le=12500),
                   actor=Depends(current_user)):
        from web_page_access import delegated_page_owner

        if page_id not in PAGE_IDS:
            raise HTTPException(404)
        owner = await delegated_page_owner(db, actor, page_id)
        if not owner:
            raise HTTPException(403, detail="web_page_permission_required")
        response.headers["Cache-Control"] = "private, no-store"
        if page_id.startswith("extra.inventory_"):
            return await _stock_rows(db, owner, actor, page_id, page)
        if page_id == "extra.salla_recovery":
            return await _recovery(db, owner)
        if page_id == "extra.bnpl_diagnostics":
            return await _bnpl(db, owner, actor)
        if page_id == "extra.financial_entry":
            return await _entry(db, owner, actor)
        start, end = _range(date_from, date_to)
        if page_id == "extra.snapchat_period":
            return await _snapchat(db, owner, start, end, split_on)
        report = await request.app.state.unified_ads_report_for_tenant(db, owner, start.isoformat(), end.isoformat())
        return _result({"label": "المنصة", "spend": "الصرف (ر.س)", "purchases": "المشتريات",
                        "revenue": "الإيراد (ر.س)", "roas": "العائد الإعلاني"},
                       [*report["platforms"], report["combined"]])

    return router
