"""Stored configuration visible to staff with an explicit grant for the matching page."""
import re

from fastapi import HTTPException

from accounting_cutoffs import DEFAULT_CUTOFFS
from web_legacy_page_readers import SIZE, _result


_QOYOD = "/integrations-v2/qoyod"
_QOYOD_TABS = {"overview", "exceptions", "invoices", "settings", "reconciliation"}
CONFIG_PAGES = frozenset({_QOYOD + "?tab=" + tab for tab in _QOYOD_TABS} | {
    "/make-webhook", "/integrations/custom-app", "/integrations/bnpl", "/settings/salla",
    "/integrations-v2/qoyod", "/ads-v2/settings", "/settings/accounting-cutoffs",
    "/settings/operation-account-bindings", "/settings", "/team", "/diagnostics/api-permissions",
})
_PLATFORMS = {"tabby": "تابي", "tamara": "تمارا", "salla": "سلة", "cod": "الدفع عند الاستلام",
              "bank_transfer": "تحويل بنكي", "snapchat_ads": "سناب شات", "meta_ads": "ميتا"}
_OPS = {
    "advance_grant": "سلفة موظف", "salary_settle": "صرف راتب", "custody_grant": "تسليم عهدة",
    "custody_return": "إرجاع عهدة", "supplier_pay": "سداد مورد", "external_grant": "سلفة لجهة",
    "external_collect": "تحصيل من جهة", "expense_record": "مصروف عام", "bank_transfer": "تحويل بين الحسابات",
    "supplier_invoice": "فاتورة مورد", "general_expense": "مصروف موحّد", "fixed_asset": "شراء أصل ثابت",
}


def _switch(value):
    return {True: "مفعّل", False: "متوقف"}.get(value) if isinstance(value, bool) else None


def _known_list(value):
    return "، ".join(v for v in value if isinstance(v, str)) if isinstance(value, list) else None


def _field_rows(labels, data, offset, description=None):
    rows = [{"field": label, "value": _switch(value) if isinstance(value, bool) else value}
            for key, label in labels.items() if (value := data.get(key)) is not None]
    return _result({"field": "الإعداد", "value": "القيمة"}, rows[offset:offset + SIZE + 1], description)


async def _doc(db, collection, owner, keys):
    return await db[collection].find_one({"user_id": owner}, {"_id": 0, **dict.fromkeys(keys, 1)}) or {}


async def _list(db, collection, selector, keys, order, offset):
    return await db[collection].find(selector, {"_id": 0, **dict.fromkeys(keys, 1)}).sort(
        [*order, ("_id", 1)]).skip(offset).limit(SIZE + 1).to_list(SIZE + 1)


async def read_config_page(db, owner, actor, path, offset):
    if path not in CONFIG_PAGES:
        raise HTTPException(403, detail={"code": "web_page_permission_required"})
    tenant = {"user_id": owner}
    if path == "/make-webhook":
        labels = {"created_at": "تاريخ إنشاء الربط", "last_sync_at": "آخر استقبال", "total_received": "إجمالي الوارد"}
        data = await _doc(db, "webhook_tokens", owner, labels)
        return _field_rows(labels, data, offset)
    if path == "/integrations/custom-app":
        labels = {"enabled": "تشغيل الربط", "created_at": "تاريخ الإعداد"}
        data = await _doc(db, "settings", owner, ["custom_app." + key for key in labels])
        return _field_rows(labels, data.get("custom_app") or {}, offset)
    if path == "/integrations/bnpl":
        columns = {"provider": "المنصة", "enabled": "الربط", "environment": "البيئة",
                   "activation_date": "تاريخ التفعيل", "last_sync_at": "آخر مزامنة"}
        rows = await _list(db, "bnpl_settings", {**tenant, "provider": {"$in": ["tabby", "tamara"]}},
                           columns, [("provider", 1)], offset)
        for row in rows:
            row["provider"] = _PLATFORMS.get(row.get("provider"))
            row["enabled"] = _switch(row.get("enabled"))
        return _result(columns, rows)
    if path == "/settings/salla":
        labels = {"store_name": "المتجر", "status": "حالة الربط", "created_at": "تاريخ الربط",
                  "last_refreshed_at": "آخر تجديد للربط", "store_status": "حالة المتجر"}
        return _field_rows(labels, await _doc(db, "salla_integrations", owner, labels), offset)
    if path.partition("?")[0] == _QOYOD:
        return await _qoyod(db, owner, path.partition("?tab=")[2] or "overview", offset)
    if path == "/ads-v2/settings":
        columns = {"display_name": "الحساب", "provider": "المنصة", "currency_native": "العملة",
                   "timezone": "المنطقة الزمنية", "sync_enabled": "المزامنة", "sync_status": "حالة المزامنة"}
        rows = await _list(db, "ads_accounts", {**tenant, "soft_deleted": False}, columns,
                           [("provider", 1), ("display_name", 1)], offset)
        for row in rows:
            row["sync_enabled"] = _switch(row.get("sync_enabled"))
        return _result(columns, rows)
    if path == "/settings/accounting-cutoffs":
        # The original get_all_cutoffs writes missing defaults. Compose the same effective dates read-only.
        saved = await db.accounting_cutoffs.find(
            {**tenant, "provider": {"$in": list(DEFAULT_CUTOFFS)}},
            {"_id": 0, "provider": 1, "accounting_start_date": 1}).to_list(len(DEFAULT_CUTOFFS))
        dates = {r["provider"]: r.get("accounting_start_date") for r in saved}
        rows = [{"provider": _PLATFORMS.get(p, p), "date": dates.get(p) or default,
                 "source": "إعداد المتجر" if dates.get(p) else "افتراضي"} for p, default in DEFAULT_CUTOFFS.items()]
        return _result({"provider": "الجهة", "date": "بداية المحاسبة", "source": "المصدر"}, rows[offset:])
    if path == "/settings/operation-account-bindings":
        settings = await _doc(db, "settings", owner, ["operation_account_bindings", "operation_withdrawal_methods"])
        bindings, methods = settings.get("operation_account_bindings") or {}, settings.get("operation_withdrawal_methods") or {}
        method_labels = {"cash": "نقدي", "transfer": "تحويل", "pos": "شبكة"}
        rows = []
        for op, label in _OPS.items():
            ids = bindings.get(op) or []
            allowed = methods.get(op) or []
            rows.append({"operation": label, "accounts": str(len(set(v for v in ids if isinstance(v, str) and v != "__none__"))) if ids else "كل الحسابات",
                         "methods": "، ".join(method_labels[m] for m in allowed if m in method_labels)
                         if allowed else "كل الطرق"})
        return _result({"operation": "العملية", "accounts": "عدد الحسابات المرتبطة", "methods": "طرق السحب"},
                       rows[offset:], "إعدادات المتجر العامة. الوصول إلى أرصدة الحسابات يخضع لصلاحيات الموظف.")
    if path == "/settings":
        labels = {"payment_methods": "طرق الدفع", "shipping_companies": "شركات الشحن",
                  "report_included_statuses": "حالات الطلب في التقارير", "updated_at": "آخر تعديل"}
        data = await _doc(db, "settings", owner, ["payment_methods.name", "shipping_companies.name",
                                                "report_included_statuses", "updated_at"])
        for key in ("payment_methods", "shipping_companies"):
            if isinstance(data.get(key), list):
                data[key] = _known_list([row.get("name") for row in data[key] if isinstance(row, dict)])
        data["report_included_statuses"] = _known_list(data.get("report_included_statuses"))
        return _field_rows(labels, data, offset)
    if path == "/team":
        columns = {"name": "الموظف", "role": "الدور", "status": "حالة الحساب", "created_at": "تاريخ الإضافة"}
        rows = await _list(db, "users", {"created_by": owner}, ["name", "role", "created_at", "disabled", "is_active", "deleted_at"],
                           [("created_at", -1)], offset)
        for row in rows:
            row["status"] = "متوقف" if row.get("disabled") or row.get("is_active") is False or row.get("deleted_at") else "نشط"
        return _result(columns, rows)
    columns = {"provider": "المنصة", "permission_key": "الصلاحية", "permission_status": "الحالة المحفوظة",
               "observed_at": "تاريخ الفحص"}
    rows = await _list(db, "mezan_integration_permissions_v2", {**tenant, "provider": {"$in": ["snapchat_ads", "meta_ads"]}},
                       columns, [("provider", 1), ("permission_key", 1)], offset)
    for row in rows:
        row["provider"] = _PLATFORMS[row["provider"]]
        row["permission_status"] = {"current": "متاحة", "missing": "ناقصة"}.get(row.get("permission_status"), "غير مؤكدة")
    return _result(columns, rows, "صلاحيات سناب وميتا المسجّلة في آخر فحوص الربط.")


async def _qoyod(db, owner, tab, offset):
    columns = {
        "invoices": {"invoice_number": "رقم الفاتورة", "reference": "مرجع الطلب", "issue_date": "تاريخ الإصدار",
                     "total": "الإجمالي", "currency": "العملة", "status": "الحالة"},
        "reconciliation": {"order_number": "رقم الطلب", "invoice_number": "رقم الفاتورة", "match": "نتيجة المطابقة",
                           "salla_total": "إجمالي سلة", "qoyod_total": "إجمالي قيود"},
        "exceptions": {"order_number": "رقم الطلب", "salla_status": "حالة الطلب", "status": "حالة الإرسال", "reason": "السبب"},
    }
    settings_labels = {"enabled": "تشغيل التكامل", "auto_send": "الإرسال الآلي", "auto_receipt": "سند القبض الآلي",
                       "invoice_date_source": "تاريخ الفاتورة", "invoice_trigger_statuses": "حالات إرسال الفاتورة",
                       "updated_at": "آخر تعديل"}
    # Native Qoyod uses a historical singleton. Only an explicitly persisted owner binding
    # can authorize reading it on behalf of an employee, with an independent grant for this tab.
    shared = await db.qoyod_settings.find_one({"user_id": "main", "security_owner_id": owner},
                                               {"_id": 0, **dict.fromkeys(settings_labels, 1), "security_owner_id": 1})
    if not shared:
        return _result(columns.get(tab, {"field": "الإعداد", "value": "القيمة"}), [], "لا يوجد ربط قيود معتمد لهذا المتجر.")
    if tab == "settings":
        shared["invoice_trigger_statuses"] = _known_list(shared.get("invoice_trigger_statuses"))
        return _field_rows(settings_labels, shared, offset)
    if tab == "invoices":
        # Same presence contract as native invoice_review._real_local_invoice.
        query = {"user_id": "main", "qoyod_invoice_id": {"$exists": True, "$nin": [None, "", 0, False],
                                                         "$not": re.compile(r"^(?:DRY|PREVIEW):", re.I)}}
        rows = await _list(db, "qoyod_invoices", query, [*columns[tab], "qoyod_invoice_id"],
                           [("issue_date", -1), ("qoyod_invoice_id", -1)], offset)
        for row in rows:
            row["invoice_number"] = row.get("invoice_number") or row.get("qoyod_invoice_id")
        return _result(columns[tab], rows)
    match = {"_id": "main", "last_success.orders_user_id": owner}
    if tab == "reconciliation":
        # Mongo sends only the visible part of the committed snapshot.
        record = await db.qoyod_full_scans.aggregate([
            {"$match": match},
            {"$project": {"_id": 0, "rows": {"$slice": [
                {"$ifNull": ["$last_success.report.rows", []]}, offset, SIZE + 1]}}},
        ]).to_list(1)
        return _result(columns[tab], record[0]["rows"] if record else [],
                       "المطابقة المحفوظة في آخر فحص." if record else "لا توجد مطابقة محفوظة لهذا المتجر.")
    scan = await db.qoyod_full_scans.find_one(match, {"_id": 0, "last_success.generation": 1,
        "last_success.updated_at": 1, "last_success.report.matched_row_count": 1}) or {}
    saved = scan.get("last_success") or {}
    if tab == "overview":
        labels = {"enabled": "تشغيل التكامل", "auto_send": "الإرسال الآلي", "checked_at": "تاريخ آخر فحص",
                  "checked": "عدد الصفوف المشمولة بالفحص"}
        return _field_rows(labels, {**shared, "checked_at": saved.get("updated_at"),
                                   "checked": (saved.get("report") or {}).get("matched_row_count")}, offset)
    generation = saved.get("generation")
    if not generation:
        return _result(columns[tab], [], "لا توجد نتيجة فحص منشورة لهذا المتجر.")
    # Each committed generation contains chunks of exactly 100 rows except the last.
    chunk_start = offset // 100 * 100
    cursor = db.qoyod_scan_views.find({"tenant": "main", "generation": generation,
        "offset": {"$in": [chunk_start, chunk_start + 100]}},
        {"_id": 0, "offset": 1, **{"rows." + field: 1 for field in columns[tab]}}).sort("offset", 1).limit(2)
    rows = []
    async for chunk in cursor:
        start = max(0, offset - chunk["offset"])
        rows.extend((chunk.get("rows") or [])[start:start + SIZE + 1 - len(rows)])
        if len(rows) == SIZE + 1:
            break
    return _result(columns[tab], rows, "نتائج الإرسال وأسبابه من آخر فحص محفوظ.")
