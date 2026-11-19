"""Tenant-bound read models for the original operating forms and diagnostic pages."""
from fastapi import HTTPException

from operational_balance_access import OPERATION_LABELS, load_access
from web_legacy_page_readers import (
    SIZE, _documents, _label_entities, _ledger_scope, _report_providers, _result,
)


async def _rows(collection, query, columns, sort, offset, extra=()):
    projection = {"_id": 0, **dict.fromkeys((*columns, *extra), 1)}
    return await collection.find(query, projection).sort([(sort, -1), ("_id", -1)]).skip(offset).limit(SIZE + 1).to_list(SIZE + 1)


async def _order_diagnostics(db, owner, path, day, offset):
    query = {"user_id": owner, "order_date": day}
    if path == "/diagnostics":
        columns = {"source": "مصدر الطلبات", "count": "عدد الطلبات", "date": "التاريخ"}
        if offset:
            return _result(columns, [])
        count = await db.unified_orders.count_documents(query)
        analyses = await db.analyses.aggregate([
            {"$match": {"user_id": owner, "date": day, "orders_imported": {"$in": [None, 0]}}},
            {"$group": {"_id": None, "count": {"$sum": "$report.summary.total_orders"}}},
        ]).to_list(1)
        return _result(columns, [{"source": "الطلبات الموحدة", "count": count, "date": day},
                                {"source": "التحليلات القديمة غير المستوردة", "count": analyses[0]["count"] if analyses else 0, "date": day}],
                       "عدد الطلبات من كل مصدر يُستخدم في تقرير اليوم. راجع التحليلات القديمة عند ظهور فرق في العدد.")
    if path == "/ai/control-center":
        columns = {"status": "حالة الطلب", "count": "الطلبات", "date": "التاريخ"}
        data = await db.unified_orders.aggregate([
            {"$match": query}, {"$group": {"_id": {"$ifNull": ["$order_status", "غير محدد"]}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1, "_id": 1}}, {"$skip": offset}, {"$limit": SIZE + 1},
        ]).to_list(SIZE + 1)
        return _result(columns, [{"status": r["_id"], "count": r["count"], "date": day} for r in data],
                       "توزيع طلبات اليوم حسب حالتها المسجلة في ميزان.")
    from cod_diagnostic_routes import _is_cod_robust
    # Group on the date index before normalizing Arabic and legacy method aliases.
    # Only a scalar method name and its source leave Mongo; no order payloads.
    grouped = db.unified_orders.aggregate([
        {"$match": {**query, "is_pre_accounting": {"$ne": True}}},
        {"$group": {"_id": {"actual": "$actual_payment_method", "recorded": "$payment_method"}, "count": {"$sum": 1}}},
    ])
    methods = {}
    async for entry in grouped:
        actual = entry["_id"].get("actual")
        method = actual or entry["_id"].get("recorded")
        if isinstance(method, str) and _is_cod_robust(method):
            key = (method, "طريقة الدفع الفعلية" if actual else "طريقة الدفع المسجلة")
            methods[key] = methods.get(key, 0) + entry["count"]
    rows = [{"method": method, "source": source, "count": count} for (method, source), count in
            sorted(methods.items(), key=lambda item: (-item[1], item[0]))]
    return _result({"method": "طريقة الدفع", "source": "مصدر التعرف على الطلب", "count": "طلبات الدفع عند الاستلام"},
                   rows[offset:offset + SIZE + 1], "طلبات اليوم المصنفة دفعًا عند الاستلام بعد تطبيق سياسة بدء المحاسبة.")


async def _migration(db, owner, offset):
    columns = {"status": "حالة الترحيل", "cutoff_date": "تاريخ القطع", "applied_at": "تاريخ التنفيذ", "applied_count": "عدد القيود المطبّقة"}
    if offset:
        return _result(columns, [])
    doc = await db.migration_cutoffs.find_one({"user_id": owner}, {"_id": 0, **dict.fromkeys(columns, 1)})
    return _result(columns, [doc or {"status": "لم يُنفّذ الترحيل"}], "آخر حالة محفوظة لترحيل الأرصدة الافتتاحية.")


# Constrain the journal legs themselves. A grant on one participant does not
# reveal names, amounts or notes stored in metadata about other participants.
_JOURNALS = {
    "/employee-corrections": ({"entity_type": "employee", "entry_type": "correction", "status": "posted"},
                              "قيود التصحيح التي رُحّلت إلى حسابات الموظفين الممنوحة."),
    "/salary-reversals": ({"entry_type": "reversal", "status": "posted", "metadata.original_operation": "salary_payment",
                           "reversal_of_txn_group_id": {"$ne": None}}, "قيود عكس صرف الرواتب ضمن الحسابات الممنوحة."),
    "/expense-reversals": ({"entry_type": "reversal", "status": "posted", "metadata.original_operation": "expense_record",
                            "reversal_of_txn_group_id": {"$ne": None}}, "قيود عكس المصروفات ضمن الحسابات الممنوحة."),
    "/audit/employee-orphans": ({"entity_type": "employee", "metadata.legacy_orphan": True},
                                "قيود الموظفين المعلّمة كقيود قديمة يتيمة ضمن الحسابات الممنوحة."),
    "/audit/ad-account-forensic": ({"entity_type": "ad_account", "metadata.source": "ad_account_cron"},
                                   "قيود المزامنة التلقائية للحسابات الإعلانية الممنوحة."),
}


async def _journal_history(db, owner, policy, path, offset):
    fields = {"posted_at": "التاريخ", "entity_name": "الحساب", "sub_account": "الحساب الفرعي",
              "entry_no": "رقم القيد", "side": "الجانب", "amount": "المبلغ", "currency": "العملة"}
    match, description = _JOURNALS[path]
    scope = _ledger_scope(policy, None, balance=False, report=path.startswith("/audit/"))
    docs = []
    if scope:
        docs = await _rows(db.general_ledger, {"user_id": owner, **match, **scope}, fields, "posted_at", offset,
                           extra=("entity_type", "entity_id"))
        await _label_entities(db, owner, docs[:SIZE])
    return _result(fields, docs, description)


async def _post_migration(db, owner, policy, offset):
    fields = {"entity_name": "الحساب", "entity_type": "نوع الحساب", "sub_account": "الحساب الفرعي", "count": "عدد القيود الافتتاحية"}
    scope = _ledger_scope(policy, None, balance=True, report=True)
    rows = []
    if scope:
        async for entry in db.general_ledger.aggregate([
            {"$match": {"user_id": owner, "entry_type": "opening_balance", **scope}},
            {"$group": {"_id": {key: "$" + key for key in ("entity_type", "entity_id", "sub_account")}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}}, {"$sort": {"count": -1, "_id": 1}}, {"$skip": offset}, {"$limit": SIZE + 1},
        ]):
            rows.append({**entry["_id"], "count": entry["count"]})
        await _label_entities(db, owner, rows[:SIZE])
    return _result(fields, rows, "الحسابات الممنوحة التي سُجّل لها أكثر من قيد افتتاحي في نفس الحساب الفرعي.")


async def _ad_debt(db, owner, policy, offset):
    from ledger_core import compute_balances_bulk
    fields = {"name": "الحساب الإعلاني", "old": "آخر مديونية مسجلة", "live": "مديونية الدفتر", "difference": "الفرق"}
    ids = sorted(r["id"] for r in policy.resources if r["kind"] == "ad_account" and r["balance"] and r["report"])
    accounts = await _rows(db.counterparties, {"user_id": owner, "kind": "ad_account", "id": {"$in": ids}},
                           ["id", "name"], "name", offset) if ids else []
    if accounts:
        balances = await compute_balances_bulk(db, user_id=owner, entity_type="ad_account",
                                               entity_ids=[a["id"] for a in accounts[:SIZE]], sub_account="debt")
        for record in accounts[:SIZE]:
            latest = await db.ad_account_ledger.find_one(
                {"user_id": owner, "counterparty_id": record["id"], "debt_after": {"$ne": None}},
                {"_id": 0, "debt_after": 1}, sort=[("date", -1), ("created_at", -1), ("_id", -1)])
            record["old"] = latest.get("debt_after") if latest else None
            record["live"] = -balances.get(record["id"], {}).get("net_balance", 0)
            if isinstance(record["old"], (float, int)):
                record["difference"] = round(record["live"] - record["old"], 2)
    return _result(fields, accounts, "مقارنة آخر مديونية محفوظة في سجل الإعلان مع رصيد الدائن ناقص المدين في دفتر المديونية.")


async def _ledger_health(db, owner, policy, offset):
    fields = {"transaction_date": "تاريخ الحركة", "transaction_type": "العملية", "status": "ترحيل الحركة إلى الدفتر"}
    ids = sorted(r["id"][8:] for r in policy.resources if r["kind"] == "bank" and r["id"].startswith("account:")
                 and r["report"] and r["movements"] == "all")
    rows = await _rows(db.account_transactions, {"user_id": owner, "account_id": {"$in": ids}},
                       fields, "created_at", offset, extra=("id",)) if ids else []
    refs = [r["id"] for r in rows[:SIZE] if r.get("id")]
    mirrors = set()
    if refs:
        keys = ["account_transaction_id", "paired_account_transaction_id"]
        async for leg in db.general_ledger.find(
            {"user_id": owner, "metadata.source": "account_transaction_double_write",
             "$or": [{"metadata." + k: {"$in": refs}} for k in keys]},
            {"_id": 0, **{"metadata." + k: 1 for k in keys}}):
            mirrors.update(leg["metadata"].get(k) for k in keys)
    for row in rows[:SIZE]:
        row["status"] = "موجود في الدفتر" if row.get("id") in refs and row["id"] in mirrors else "لم يُعثر على قيد مقابل"
    return _result(fields, rows, "حالة القيد المقابل لكل حركة بنكية من الحسابات الممنوحة.")


async def _settlements(db, owner, policy, path, offset):
    providers = _report_providers(policy)
    if path == "/settlement-engine":
        fields = {"provider_name": "المنصة", "invoice_no": "رقم الفاتورة", "period_from": "بداية الفترة",
                  "period_to": "نهاية الفترة", "expected_transfer_amount": "صافي التحويل المتوقع", "status": "الحالة"}
        if "emkan" in providers:
            providers.add("imkan")
        rows = await _rows(db.settlement_invoices, {"user_id": owner, "provider_name": {"$in": sorted(providers)}},
                           fields, "period_from", offset) if providers else []
        return _result(fields, rows, "الفواتير التي سبق أن أنشأها محرّك التسويات للمنصات الممنوحة.")
    fields = {"entity_id": "المنصة", "entry_no": "رقم القيد", "amount": "مبلغ التسوية المسجّل", "currency": "العملة", "posted_at": "تاريخ التسجيل"}
    providers &= {"tabby", "tamara"}
    rows = await _rows(db.general_ledger, {"user_id": owner, "entity_type": "payment_gateway", "entity_id": {"$in": sorted(providers)},
                       "entry_type": "bnpl_settlement", "status": "posted", "side": "credit"}, fields, "posted_at", offset) if providers else []
    return _result(fields, rows, "تسويات تابي وتمارا المسجلة في دفتر المنصات الممنوحة.")


async def read_operating(db, owner, actor, path, offset, day):
    if path in {"/ai/control-center", "/diagnostics/cod-source", "/diagnostics"}:
        return await _order_diagnostics(db, owner, path, day, offset)
    if path == "/upload":
        return await _documents(db, owner, "imports", offset,
                                description="ملفات استيراد الطلبات السابقة وحالة معالجة كل ملف.")
    if path == "/accounting/migration":
        return await _migration(db, owner, offset)

    policy = await load_access(db, actor, owner)
    if path == "/new-transaction":
        rows = [{"operation": label} for key, label in OPERATION_LABELS.items() if policy.can_operation(key)]
        return _result({"operation": "العمليات المالية الممنوحة لك"}, rows[offset:offset + SIZE + 1],
                       "تخضع كل عملية عند تسجيلها لصلاحية الحساب أو الجهة المعنية.")
    if path in _JOURNALS:
        return await _journal_history(db, owner, policy, path, offset)
    if path in {"/settlement-engine", "/bnpl-settlements/register"}:
        return await _settlements(db, owner, policy, path, offset)
    if path == "/audit/ad-debt":
        return await _ad_debt(db, owner, policy, offset)
    if path == "/audit/ledger-health":
        return await _ledger_health(db, owner, policy, offset)
    if path == "/audit/post-migration":
        return await _post_migration(db, owner, policy, offset)
    if path == "/audit/balance-drift":
        from web_legacy_reports import _reconciliation
        return await _reconciliation(db, owner, policy, offset)
    raise HTTPException(403, detail={"code": "web_page_permission_required"})
