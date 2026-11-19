"""Small, read-only projections of the original web modules for delegated staff.

Every collection and column is chosen on the server. Historical accounts are
never matched by a display name. Opening these pages cannot initialize, sync,
post, repair or recalculate any stored record.
"""
from datetime import date
from fastapi import HTTPException
from operational_balance_access import load_access

SIZE = 8

# Fields are deliberately projected at Mongo, before row materialization.
_DATA = {
    "alerts": ("settlement_alerts", "created_at", {"alert_type": "نوع التنبيه", "title": "التنبيه", "severity": "الأهمية", "status": "الحالة", "created_at": "تاريخ التنبيه"}),
    "daily": ("daily_costs", "date", {"date": "التاريخ", "product_costs": "تكاليف المنتجات", "snapchat_ads": "إعلانات سناب", "tiktok_ads": "إعلانات تيك توك", "instagram_ads": "إعلانات إنستغرام", "google_ads": "إعلانات Google"}),
    "orders": ("unified_orders", "order_date", {"order_number": "رقم الطلب", "order_status": "الحالة", "order_date": "تاريخ الطلب"}),
    "salla_events": ("salla_webhook_inbox", "last_received_at", {"event_name": "نوع الحدث", "state": "حالة المعالجة", "attempts": "محاولات المعالجة", "last_received_at": "آخر استلام"}),
    "imports": ("import_jobs", "created_at", {"filename": "الملف", "status": "الحالة", "processed_rows": "صفوف معالجة", "total_rows": "عدد الصفوف"}),
    "history": ("analyses", "created_at", {"name": "التحليل", "filename": "الملف", "created_at": "تاريخ الإنشاء"}),
    "products": ("products", "updated_at", {"name": "المنتج", "sku": "رمز المنتج", "product_id": "رقم المنتج"}),
    "costs": ("product_costs", "updated_at", {"product_name": "المنتج", "sku": "رمز المنتج", "cost_price": "التكلفة (ر.س)"}),
    "categories": ("expense_category_tree", "name", {"name": "التصنيف", "code": "الرمز", "status": "الحالة"}),
    "prep": ("exported_items", "exported_at", {"order_number": "رقم الطلب", "product_name": "المنتج", "exported_at": "تاريخ التصدير"}),
    "images": ("product_image_catalog", "updated_at", {"product_name": "المنتج", "sku": "رمز المنتج", "image_mime": "نوع الصورة", "updated_at": "آخر تعديل"}),
    "shipping": ("unified_orders", "order_date", {"order_number": "الطلب", "shipping_company": "شركة الشحن", "order_date": "التاريخ", "order_status": "حالة الطلب"}),
    "bank_review": ("bank_transfer_reviews", "transfer_date", {"transfer_date": "التاريخ", "expected_amount": "المبلغ المتوقع", "status": "الحالة", "currency": "العملة"}),
    "statements": ("settlement_files", "uploaded_at", {"provider": "المنصة", "uploaded_at": "تاريخ الرفع", "rows": "الصفوف", "matched": "مطابق", "unmatched": "غير مطابق"}),
    "purchases": ("purchase_invoices", "invoice_date", {"invoice_number": "رقم الفاتورة", "invoice_date": "تاريخ الفاتورة", "total": "المبلغ", "status": "الحالة"}),
    "movements": ("financial_movements", "doc_date", {"doc_number": "المستند", "doc_date": "التاريخ", "movement_type": "نوع الحركة", "total_amount": "المبلغ"}),
    "expenses": ("operating_daily_expenses", "date", {"expense_type": "نوع المصروف", "date": "التاريخ", "amount": "المبلغ"}),
    "transfers": ("courier_transfers", "transfer_date", {"transfer_date": "تاريخ التحويل", "direction": "الاتجاه", "amount": "المبلغ"}),
    "receivables": ("liabilities", "created_at", {"due_date": "تاريخ الاستحقاق", "expected_amount": "المبلغ المستحق", "status": "الحالة"}),
}
_LEDGER_COLUMNS = {"entry_no": "رقم القيد", "entity_type": "النوع", "entity_name": "الحساب أو الجهة", "sub_account": "الحساب الفرعي",
                   "side": "الجانب", "amount": "المبلغ", "currency": "العملة", "posted_at": "التاريخ"}
_BALANCE_COLUMNS = {"entity_type": "النوع", "entity_name": "الحساب أو الجهة", "sub_account": "الحساب الفرعي",
                    "currency": "العملة", "debit": "مدين", "credit": "دائن", "net": "صافي المدين ناقص الدائن"}
_LEDGER_KINDS = {"employee", "employee_custody", "external_person", "supplier", "courier", "bank", "ad_account"}
_ONE_KIND = {"supplier_report": "supplier", "supplier": "supplier", "employee": "employee", "custody": "employee_custody",
             "external": "external_person", "courier": "courier", "advertising": "ad_account", "ad_expense": "ad_account"}
_STATUS = {"debit": "مدين", "credit": "دائن", "queued": "في الانتظار", "processing": "قيد المعالجة",
           "completed": "مكتمل", "failed": "تعذّر", "draft": "مسودة", "posted": "مرحّل",
           "courier_to_bank": "من شركة الشحن إلى البنك", "bank_to_courier": "من البنك إلى شركة الشحن"}
_KINDS = {"bank": "بنك", "employee": "موظف", "supplier": "مورد", "courier": "شركة شحن",
          "external_person": "جهة خارجية", "ad_account": "حساب إعلاني"}


def _result(columns, rows, description=None):
    def clean(key, value):
        if isinstance(value, date):
            return value.isoformat()
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return None
        if key in {"status", "side", "direction"}:
            return _STATUS.get(value, value)
        return _KINDS.get(value, value) if key == "entity_type" else value

    return {"columns": [{"key": key, "label": label} for key, label in columns.items()],
            "rows": [{k: clean(k, row.get(k)) for k in columns} for row in rows[:SIZE]],
            "has_more": len(rows) > SIZE, "description": description}


async def _documents(db, owner, source, offset, scope=None, description=None):
    collection, sort, fields = _DATA[source]
    if scope == {}:
        return _result(fields, [], description)
    query = {"user_id": owner, **(scope or {})}
    projection = {"_id": 0, **dict.fromkeys(fields, 1)}
    docs = await db[collection].find(query, projection).sort([(sort, -1), ("_id", -1)]).skip(offset).limit(SIZE + 1).to_list(SIZE + 1)
    return _result(fields, docs, description)


def _legacy_id(kind, identity):
    if kind == "bank":
        # Only this namespace is backed by the historical accounts collection.
        return identity.removeprefix("account:") if identity.startswith("account:") else None
    return identity


def _document_ids(policy, kinds, capability):
    return [identity for r in policy.resources if r["kind"] in kinds and r.get(capability) is True
            for identity in [_legacy_id(r["kind"], r["id"])] if identity]


def _movement_scope(policy, bindings, by=None):
    # Group matching IDs to keep the query small even with many grants.
    grouped = {}
    for grant in policy.resources:
        field = bindings.get(grant["kind"])
        identity = _legacy_id(grant["kind"], grant["id"])
        mode = grant["movements"]
        if not field or not identity or mode == "none" or (mode == "own" and not by):
            continue
        grouped.setdefault((field, mode), []).append(identity)
    branches = [{field: {"$in": ids}, **({by: policy.actor["id"]} if mode == "own" else {})}
                for (field, mode), ids in grouped.items()]
    return {"$or": branches} if branches else {}


def _ledger_scope(policy, kind, *, balance, report):
    grouped = {}
    for row in policy.resources:
        resource = row["kind"]
        if resource not in _LEDGER_KINDS or (kind and resource != kind):
            continue
        identity = _legacy_id(resource, row["id"])
        allowed = row["balance"] if balance else row["movements"] != "none"
        if not identity or not allowed or (report and not row["report"]):
            continue
        own = not balance and row["movements"] == "own"
        grouped.setdefault((resource, own), []).append(identity)
    branches = []
    for (resource, own), ids in grouped.items():
        condition = {"entity_type": resource, "entity_id": {"$in": ids}}
        if resource == "employee_custody":
            condition.update(entity_type="employee", sub_account="custody")
        if resource == "employee":
            condition["sub_account"] = {"$ne": "custody"}
        if own:
            condition["posted_by"] = policy.actor["id"]
        branches.append(condition)
    return {"$or": branches} if branches else {}


async def _ledger(db, owner, policy, source, offset):
    balance = source != "ledger" and source != "ad_expense"
    columns = _BALANCE_COLUMNS if balance else _LEDGER_COLUMNS
    scope = _ledger_scope(policy, _ONE_KIND.get(source), balance=balance,
                          report=source in {"position", "supplier_report", "ad_expense"})
    desc = "أرصدة القيود التاريخية للحسابات الممنوحة، بحسب الحساب الفرعي والعملة." if balance else "قيود السجل التاريخي ضمن الحسابات الممنوحة."
    if not scope:
        return _result(columns, [], desc)
    query = {"user_id": owner, **scope}
    if not balance:
        if source == "ad_expense":
            query.update(entry_type="spend", status="posted", **{"metadata.legacy_orphan": {"$ne": True}})
        rows = await db.general_ledger.find(query, {"_id": 0, **dict.fromkeys(columns, 1), "entity_id": 1}).sort(
            [("entry_no", -1), ("_id", -1)]).skip(offset).limit(SIZE + 1).to_list(SIZE + 1)
    else:
        # Same posted/non-reversed/orphan rules as ledger_core.compute_balance,
        # keeping each original currency and sub-ledger separate.
        query.update(status="posted", entry_type={"$ne": "reversal"}, **{"metadata.legacy_orphan": {"$ne": True}})
        group = {k: "$" + k for k in ("entity_type", "entity_id", "sub_account", "currency")}
        rows = []
        async for row in db.general_ledger.aggregate([
            {"$match": query}, {"$group": {"_id": group, **{side: {"$sum": {"$cond": [{"$eq": ["$side", side]}, "$amount", 0]}}
                                                           for side in ("debit", "credit")}}},
            {"$sort": {"_id.entity_type": 1, "_id.entity_id": 1, "_id.sub_account": 1, "_id.currency": 1}},
            {"$skip": offset}, {"$limit": SIZE + 1},
        ]):
            rows.append({**row["_id"], "debit": round(row["debit"], 2), "credit": round(row["credit"], 2),
                         "net": round(row["debit"] - row["credit"], 2)})
    await _label_entities(db, owner, rows[:SIZE])
    return _result(columns, rows, desc)


async def _label_entities(db, owner, rows):
    # Names are fetched only for the at most eight already-authorized rows.
    # Some older parties now exist only in the original counterparty register.
    registries = {"employee": ("mezan_employees_v2",), "bank": ("accounts",),
                  "supplier": ("mezan_suppliers_v2", "counterparties"), "external_person": ("mz2_external_persons_v2", "counterparties"),
                  "ad_account": ("counterparties",), "courier": ("counterparties",)}
    names = {}
    for kind in {row["entity_type"] for row in rows}:
        ids = {row["entity_id"] for row in rows if row["entity_type"] == kind and row.get("entity_id")}
        for registry in registries.get(kind, ()):
            if not ids:
                break
            query = {"user_id": owner, "id": {"$in": sorted(ids)}}
            if registry == "counterparties":
                query["kind"] = {"$nin": ["supplier", "ad_account", "courier"]} if kind == "external_person" else kind
            async for record in db[registry].find(query, {"_id": 0, "id": 1, "name": 1}):
                if isinstance(record.get("name"), str) and record["name"].strip():
                    names[(kind, record["id"])] = record["name"]
                    ids.discard(record["id"])
    for row in rows:
        row["entity_name"] = names.get((row["entity_type"], row["entity_id"]), row["entity_id"])


async def _accounts(db, owner, policy, offset):
    from financial_position_ssot import account_balance_ssot
    columns = {"name": "الحساب", "currency": "العملة", "balance": "الرصيد التاريخي"}
    ids = _document_ids(policy, {"bank"}, "balance")
    if not ids:
        return _result(columns, [])
    docs = await db.accounts.find({"user_id": owner, "id": {"$in": ids}, "account_type": "bank"},
                                  {"_id": 0, "id": 1, "name": 1, "currency": 1, "account_type": 1,
                                   "current_balance": 1, "opening_balance": 1}).sort(
        [("name", 1), ("id", 1)]).skip(offset).limit(SIZE + 1).to_list(SIZE + 1)
    for doc in docs[:SIZE]:
        doc["balance"] = await account_balance_ssot(db, user_id=owner, account=doc)
    return _result(columns, docs)


def _report_providers(policy):
    return {r["id"] for r in policy.resources if r["kind"] == "provider" and r["report"] and
            r["balance"] and r["movements"] == "all" and r["id"] in {"salla", "tabby", "tamara", "emkan"}}


async def _bnpl(db, owner, policy, audit, offset):
    from bnpl.balance_service import get_bnpl_provider_balance
    from bnpl.refund_audit_routes import _audit_provider
    columns = ({"provider": "المنصة", "delta": "فرق مبلغ الاسترجاع (ر.س)", "message": "نتيجة المراجعة"}
               if audit else {"provider": "المنصة", "balance": "الرصيد (ر.س)"})
    rows = []
    providers = _report_providers(policy) & ({"tabby", "tamara"} if audit else {"tabby", "tamara", "emkan"})
    for provider in sorted(providers)[offset:offset + SIZE]:
        if audit:
            report = await _audit_provider(db, owner, provider)
            rows.append({"provider": provider, "delta": report["deltas"]["amount_unified_vs_ptx"], "message": report["message"]})
        else:
            rows.append(await get_bnpl_provider_balance(db, owner, provider))
    return _result(columns, rows)


async def _shipping(db, owner, policy, offset):
    from operational_courier_tariffs import saved_identity
    # Old shipping rows store a carrier label, not an operational UUID.
    # Resolve only the merchant's exact configured labels against their
    # canonical carrier. No substring or inferred order-name join.
    granted = {r["id"] for r in policy.resources if r["kind"] == "courier" and
               r["report"] and r["movements"] == "all"}
    if not granted:
        return _result(_DATA["shipping"][2], [])
    docs = await db.settings.find({"user_id": owner}, {"_id": 0, "shipping_companies.name": 1,
                                  "shipping_companies.salla_carrier_key": 1}).limit(2).to_list(2)
    if len(docs) != 1:
        return _result(_DATA["shipping"][2], [])
    names = []
    for row in docs[0].get("shipping_companies") or []:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            parsed = saved_identity(row)
            if parsed and not parsed.get("identity_error") and parsed["id"] in granted:
                names.append(row["name"])
    return await _documents(db, owner, "shipping", offset, {"shipping_company": {"$in": names}} if names else {},
                            "شحنات الشركات الممنوحة المرتبطة بإعدادات المتجر.")


async def read_legacy_page(db, owner, user, source, page, *, day=None, app_state=None):
    offset = (page - 1) * SIZE
    from tz_utils import riyadh_today
    day = (day or riyadh_today()).isoformat()
    if source.startswith("/"):
        from web_legacy_config_read import CONFIG_PAGES, read_config_page
        if source in CONFIG_PAGES:
            return await read_config_page(db, owner, user, source, offset)
        from web_legacy_operating_read import read_operating
        return await read_operating(db, owner, user, source, offset, day)
    if source == "salla_order_monitor":
        return await _documents(db, owner, "orders", offset, {"$or": [
            {"data_sources.source": "salla_direct"}, {"raw_by_source.salla_direct": {"$exists": True}},
        ]}, "الطلبات التي وصلتها بيانات من ربط سلة المباشر وحالتها في ميزان.")
    if source == "salla_events":
        link = await db.salla_integrations.find_one({"user_id": owner}, {"store_id": 1})
        merchant = str((link or {}).get("store_id") or "").strip()
        return await _documents(db, owner, source, offset, {"merchant_id": merchant} if merchant else {},
                                "أحداث سلة المستلمة في قائمة المعالجة وحالة كل حدث.")
    if source in {"dashboard", "sales_report", "ads_report", "usage", "shipping_settings", "salla_sources"}:
        from web_legacy_reports import shop_report
        return await shop_report(db, owner, source, offset, day, app_state)
    if source == "daily":
        return await _documents(db, owner, source, offset, description="إجمالي التكاليف اليومية المسجلة على مستوى المتجر (ر.س).")
    if source == "profile":
        fields = {"name": "الاسم", "email": "البريد", "role": "الدور"}
        return _result(fields, [] if offset else [user])
    if source in {"orders", "imports", "history", "products", "costs", "categories", "prep", "images"}:
        scope = {"is_active": True} if source == "products" else None
        return await _documents(db, owner, source, offset, scope)
    policy = await load_access(db, user, owner)
    policy.require_enabled()
    if source in {"reconciliation", "operations_report", "operations_dashboard", "alerts"}:
        from web_legacy_reports import scoped_report
        return await scoped_report(db, owner, policy, source, offset, day)
    if source in _ONE_KIND or source in {"ledger", "position"}:
        return await _ledger(db, owner, policy, source, offset)
    if source == "accounts":
        return await _accounts(db, owner, policy, offset)
    if source in {"bnpl", "refund_audit"}:
        return await _bnpl(db, owner, policy, source == "refund_audit", offset)
    if source in {"statements", "salla"}:
        providers = _report_providers(policy)
        if source == "salla":
            providers &= {"salla"}
        return await _documents(db, owner, "statements", offset, {"provider": {"$in": sorted(providers)}} if providers else {})
    if source == "shipping":
        return await _shipping(db, owner, policy, offset)
    bindings = {
        "purchases": ({"supplier": "supplier_counterparty_id"}, "created_by"),
        "movements": ({"supplier": "supplier_id", "bank": "paid_from_account_id", "employee_custody": "custody_employee_id"}, None),
        "expenses": ({"bank": "paid_from_account_id"}, None),
        "transfers": ({"bank": "bank_account_id"}, None),
        "bank_review": ({"bank": "target_bank_id"}, "created_by"),
        "receivables": ({"external_person": "counterparty_id"}, None),
    }
    if source not in bindings:
        raise HTTPException(403, detail={"code": "web_page_permission_required"})
    fields, actor_field = bindings[source]
    scope = _movement_scope(policy, fields, actor_field)
    if scope and source == "receivables":
        scope["kind"] = "receivable"
    return await _documents(db, owner, source, offset, scope)
