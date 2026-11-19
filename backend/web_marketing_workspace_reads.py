"""Read-only tab projections. Account and conversation scope come from the native readers."""
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from web_marketing_page_policy import _table

PAGE_SIZE = 20
READINESS = {
    "report_ready": "اكتمال بيانات التقرير", "campaign_identity_ready": "هوية الحملات",
    "spend_ready": "الصرف", "orders_ready": "الطلبات المنسوبة", "sales_ready": "المبيعات المنسوبة",
    "ratios_ready": "مؤشرات العائد والتكلفة",
}


async def provider_workspace(db, owner, provider, tab, *, offset):
    if tab == "content":
        from integrations_control_center.tiktok_content_publishing import PROPOSALS
        fields = ("account_label", "kind", "delivery", "status", "created_at")
        records = await db[PROPOSALS].find({"user_id": owner}, {"_id": 0, **dict.fromkeys(fields, 1)}).sort(
            [("created_at", -1), ("_id", 1)]).skip(offset).limit(PAGE_SIZE + 1).to_list(PAGE_SIZE + 1)
        return _table(["حساب النشر", "النوع", "طريقة النشر", "حالة النشر", "تاريخ التسجيل"],
                      [[row.get(key) for key in fields] for row in records[:PAGE_SIZE]], more=len(records) > PAGE_SIZE)
    if tab == "accounts":
        from integrations_control_center.service import IntegrationsControlCenterService
        provider_id = provider + "_ads"
        result = await IntegrationsControlCenterService(db).overview(owner, provider_ids={provider_id})
        accounts = [a for p in result["providers"] if p["provider"] == provider_id for a in p.get("accounts", [])]
        return _table(["الحساب", "العملة", "حالة الربط", "آخر مزامنة"],
                      [[a.get("display_name") or a.get("external_account_id"), a.get("currency"),
                        a.get("connection_status"), a.get("last_sync_at")] for a in accounts[offset:offset + PAGE_SIZE]],
                      more=offset + PAGE_SIZE < len(accounts))
    page = offset // PAGE_SIZE + 1
    today = datetime.now(ZoneInfo("Asia/Riyadh")).date().isoformat()
    if provider == "snapchat":
        from integrations_control_center.snapchat_campaign_report_routes import read_snapchat_campaign_report
        from integrations_control_center.snapchat_native_data_common import SnapchatNativeSyncError
        try:
            data = await read_snapchat_campaign_report(db, owner, from_date=today, to_date=today,
                                                       campaign_query=None, page=page, limit=PAGE_SIZE)
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_accounts_not_selected":
                return {**_table([], []), "notice": "لم يُحدد حساب سناب شات لقراءة تقاريره."}
            raise HTTPException(exc.status_code, detail=exc.message) from exc
    elif provider == "tiktok":
        from integrations_control_center.tiktok_native_hierarchy import tiktok_workspace
        from integrations_control_center.tiktok_native_reporting import TikTokReportingError
        try:
            data = await tiktok_workspace(db, owner, entity_type="campaign" if tab == "campaigns" else "overview",
                                          page=page, limit=PAGE_SIZE)
        except TikTokReportingError as exc:
            if exc.code == "tiktok_reporting_accounts_missing":
                return {**_table([], []), "notice": "لا يوجد حساب تيك توك إعلاني مرتبط بالمتجر."}
            raise HTTPException(exc.status_code, detail=exc.message) from exc
    else:
        if provider == "google":
            # The shared aggregate would also load every other platform before discarding them.
            from ads_manager.google_native_overview import augment_google_overview
            data = await augment_google_overview(db, owner, {"range": {"date_from": today, "date_to": today},
                "daily_spend": [], "sources": []}, provider=provider, page=page, limit=PAGE_SIZE)
        else:
            from ads_manager.service import AdsManagerService
            data = await AdsManagerService(db).overview(owner, date_from=today, date_to=today,
                                                       provider=provider, page=page, limit=PAGE_SIZE)
        summary = next((p for p in data.get("providers", []) if p.get("provider") == provider), {})
        coverage = summary.get("performance_coverage") or {}
        metrics = summary.get("metrics") or {}
        data["ai_readiness"] = {
            "report_ready": coverage.get("status") == "complete",
            "campaign_identity_ready": (summary.get("campaign_coverage") or {}).get("status") == "available",
            "spend_ready": metrics.get("provider_reported_spend_sar") is not None,
            "orders_ready": metrics.get("platform_reported_purchases") is not None,
            "sales_ready": metrics.get("platform_attributed_revenue_sar") is not None,
            "ratios_ready": coverage.get("eligible_for_ratios") is True,
        }
    if tab == "ai":
        readiness = data.get("ai_readiness") or {}
        result = _table(["العنصر", "جاهزية البيانات"], [[label, "متاح" if readiness.get(key) else "غير مكتمل"]
                                                       for key, label in READINESS.items()])
    else:
        campaigns = data.get("entities") if provider == "tiktok" else data.get("campaigns")
        rows = [[r.get("campaign_name") or r.get("entity_name") or r.get("campaign_id"),
                 r.get("account_name") or r.get("account_id") or r.get("ad_account_id"),
                 r.get("status"), r.get("spend_sar", r.get("spend_sar_equivalent"))]
                for r in campaigns or [] if provider in {"tiktok", "snapchat"} or r.get("provider") == provider]
        more = page < (data.get("campaign_pagination") or {}).get("pages", 0)
        result = _table(["الحملة", "الحساب", "الحالة", "الصرف بالريال"], rows, more=more)
    period = data.get("range") or data
    if period.get("date_from") and period.get("date_to"):
        result["notice"] = f"بيانات المنصة للفترة من {period['date_from']} إلى {period['date_to']}."
    return result


def _original_preview(tab):
    from customer_intelligence.routes import _feature_enabled
    from customer_intelligence.service import CustomerIntelligencePreviewService
    if not _feature_enabled():
        raise HTTPException(404, detail={"code": "customer_preview_disabled"})
    model = CustomerIntelligencePreviewService().workspace()
    if tab == "drafts":
        cart = model["conversation_cart"]
        result = _table(["الصنف", "الكمية", "سعر الوحدة التجريبي"],
                        [[r["title"], r["quantity"], r["unit_price_sar"]] for r in cart["items"]])
    elif tab == "campaigns":
        result = _table(["الحملة", "المحادثات", "محادثات مؤهلة", "الطلبات المدفوعة"],
                        [[r["campaign_name"], r["conversations"], r["qualified"], r["paid_orders"]]
                         for r in model["workspace"]["campaign_impact"]])
    else:
        result = _table(["مقالات مقترحة في المعاينة"], [[r] for r in model["knowledge"]["suggested_articles"]])
    result["notice"] = "معاينة تعليمية ببيانات تجريبية. هذا التبويب لم يُربط بعد بسجلات المتجر الفعلية."
    return result


SIGNAL_VIEWS = {
    "sales": {"commercial_impact": "sales_opportunity", "source_role": "customer"},
    "market": {"signal_type": "product_request", "source_role": "customer"},
    "objections": {"signal_type": {"$in": ["objection", "complaint"]}, "source_role": "customer"},
    "quality": {"signal_type": "response_quality", "source_role": "employee"},
}
SIGNAL_COLUMNS = ["الإشارة", "القناة", "الأولوية", "حالة المراجعة", "تاريخ الرصد"]


async def _customer_events(db, owner, actor, tab, scopes, offset):
    from customer_intelligence.foundation import CONVERSATIONS_COLLECTION
    from customer_intelligence.learning_contract import CUSTOMER_SIGNALS_COLLECTION
    from customer_intelligence.reply_suggestions import REPLY_SUGGESTIONS_COLLECTION
    audit = tab == "audit"
    fields = ("review_decision", "status", "reviewed_at") if audit else (
        "signal_type", "provider", "urgency", "review_status", "created_at")
    columns = ["إجراء المراجعة", "النتيجة", "التاريخ"] if audit else SIGNAL_COLUMNS
    if not scopes:
        return _table(columns, [])
    # Join evidence back to the currently assigned thread. Former assignees lose visibility.
    match = {"user_id": owner, "$or": scopes, **({"reviewed_by": actor} if audit else SIGNAL_VIEWS[tab])}
    pipeline = [
        {"$match": match}, {"$sort": {fields[-1]: -1, "_id": 1}},
        {"$lookup": {"from": CONVERSATIONS_COLLECTION, "localField": "conversation_id",
                     "foreignField": "conversation_id", "as": "thread"}},
        {"$unwind": "$thread"},
        {"$match": {"thread.assigned_employee_id": {"$in": [actor, None]}, "$expr": {"$and": [
            {"$eq": ["$" + field, "$thread." + field]} for field in ("user_id", "merchant_id", "channel_id")]}}},
        {"$skip": offset}, {"$limit": PAGE_SIZE + 1}, {"$project": {"_id": 0, **dict.fromkeys(fields, 1)}},
    ]
    collection = REPLY_SUGGESTIONS_COLLECTION if audit else CUSTOMER_SIGNALS_COLLECTION
    records = await db[collection].aggregate(pipeline, maxTimeMS=3000).to_list(PAGE_SIZE + 1)
    return _table(columns, [[r.get(field) for field in fields] for r in records[:PAGE_SIZE]],
                  more=len(records) > PAGE_SIZE)


async def customer_workspace(db, owner, actor, tab, offset):
    from ai_store_access_contract import find_role_assignment
    from customer_intelligence.foundation import CONVERSATIONS_COLLECTION
    from customer_intelligence.inbox import CustomerIntelligenceInboxService, _customer_name
    from customer_intelligence.routes import _live_inbox_enabled
    from customer_identity import CUSTOMER_IDENTITY_COLLECTION
    assignment = await find_role_assignment(db, owner_user_id=owner, user_id=actor)
    if "customer_intelligence.inbox.read" in ((assignment or {}).get("denied_permissions") or []):
        raise HTTPException(403, detail={"code": "customer_inbox_denied"})
    if tab in {"drafts", "campaigns", "knowledge"}:
        return _original_preview(tab)
    if not _live_inbox_enabled():
        raise HTTPException(404, detail={"code": "customer_inbox_disabled"})
    connections, _, channels, thread_query = await CustomerIntelligenceInboxService(db).conversation_scope(owner, actor)
    if tab == "integrations":
        return _table(["القناة", "حالة الربط", "قنوات تستقبل الرسائل"],
                      [[c.provider, c.status, c.receiving_channels] for c in connections])
    scopes = thread_query["$or"]
    if tab in SIGNAL_VIEWS or tab == "audit":
        return await _customer_events(db, owner, actor, tab, scopes, offset)
    collection = db[CONVERSATIONS_COLLECTION]
    if tab == "overview":
        counts = await collection.aggregate([{"$match": thread_query},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}],
            maxTimeMS=2500).to_list(8) if scopes else []
        return _table(["حالة المحادثات المتاحة لك", "العدد"], [[r["_id"], r["count"]] for r in counts])
    columns = ["العميل", "القناة", "حالة المحادثة", "آخر تواصل"]
    if not scopes:
        return _table(columns, [])
    if tab == "followups":
        thread_query["status"] = {"$in": ["follow_up_due", "needs_human"]}
    fields = ("merchant_id", "channel_id", "customer_id", "status", "last_message_at")
    pipeline = [{"$match": thread_query}, {"$sort": {"last_message_at": -1, "conversation_id": 1}}]
    if tab == "customers":
        pipeline += [{"$group": {"_id": {"merchant": "$merchant_id", "customer": "$customer_id"},
                                 "latest": {"$first": "$$ROOT"}}}, {"$replaceRoot": {"newRoot": "$latest"}},
                     {"$sort": {"last_message_at": -1, "conversation_id": 1}}]
    pipeline.extend([{"$skip": offset}, {"$limit": PAGE_SIZE + 1},
                     {"$project": {"_id": 0, **dict.fromkeys(fields, 1)}}])
    records = await collection.aggregate(pipeline, maxTimeMS=3000).to_list(PAGE_SIZE + 1)
    visible = records[:PAGE_SIZE]
    ids = [{"merchant_id": r["merchant_id"], "customer_identity_id": r["customer_id"]} for r in visible]
    identities = await db[CUSTOMER_IDENTITY_COLLECTION].find({"user_id": owner, "$or": ids},
        {"_id": 0, "merchant_id": 1, "customer_identity_id": 1, "private_profile_ciphertext": 1}).limit(
            PAGE_SIZE).to_list(PAGE_SIZE) if ids else []
    names = {(r["merchant_id"], r["customer_identity_id"]): r for r in identities}
    providers = {(c["merchant_id"], c["channel_id"]): c["provider"] for c in channels}
    rows = []
    for r in visible:
        provider = providers[(r["merchant_id"], r["channel_id"])]
        rows.append([_customer_name(names.get((r["merchant_id"], r["customer_id"])), provider=provider),
                     provider, r.get("status"), r.get("last_message_at")])
    return _table(columns, rows, more=len(records) > PAGE_SIZE)
