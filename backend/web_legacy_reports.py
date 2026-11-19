"""Read projections for specifically assigned original Mezan reporting pages."""
from fastapi import HTTPException
from web_legacy_page_readers import SIZE, _documents, _legacy_id, _result


async def shop_report(db, owner, source, offset, day, app_state):
    if source in {"dashboard", "sales_report"}:
        columns = {"date": "تاريخ الطلب", "total_orders": "عدد الطلبات", "total_sales": "المبيعات (ر.س)",
                   "total_shipping_cost": "تكاليف الشحن (ر.س)", "daily_ads_total": "تكاليف الإعلان (ر.س)", "net_profit": "صافي الربح (ر.س)"}
        if offset:
            return _result(columns, [])
        reader = getattr(app_state, "legacy_dashboard_reader", None)
        if not reader:
            raise HTTPException(503, detail={"code": "legacy_dashboard_unavailable"})
        # Internal data reader with a verified tenant argument, never a fabricated
        # owner identity. Suppress settings initialization and order self-healing.
        report = await reader(db, owner, from_date=day, to_date=day, include_legacy_analyses=False,
                              allow_self_heal=False, persist_settings=False)
        totals = report["totals"]
        return _result(columns, [{"date": day, **{k: totals.get(k) for k in columns if k != "date"}}],
                       "إجماليات المتجر لليوم المختار من لوحة التحكم المحاسبية القديمة.")
    if source == "salla_sources":
        from salla_integration.sync import compute_sources_comparison
        columns = {"source": "المصدر", "orders": "عدد الطلبات"}
        if offset:
            return _result(columns, [])
        result = await compute_sources_comparison(db, owner, from_date=day, to_date=day)
        labels = {"salla_direct": "سلة", "excel": "Excel", "make": "Make"}
        return _result(columns, [{"source": labels[key], "orders": row["orders"]}
                                 for key, row in result["per_source_totals"].items() if key in labels],
                       "عدد الطلبات لكل مصدر في اليوم المختار؛ قد يكون الطلب موجودًا في أكثر من مصدر.")
    if source == "ads_report":
        from ads_v2.data_layer.reports import get_spend_by_day
        columns = {"date": "التاريخ", "spend_sar": "الصرف الإعلاني (ر.س)", "bank_fee_sar": "الرسوم (ر.س)", "gross_sar": "الإجمالي (ر.س)"}
        result = await get_spend_by_day(db, owner, day, day) if not offset else {"data": []}
        return _result(columns, result["data"], "إجمالي الصرف المسجل لمنصات المتجر، مجمّعًا حسب اليوم.")
    if source == "shipping_settings":
        rows = await db.settings.find({"user_id": owner}, {"_id": 0, "shipping_companies.name": 1,
                                      "shipping_companies.cost_per_order": 1, "shipping_companies.vat_percent": 1}).limit(2).to_list(2)
        companies = rows[0].get("shipping_companies", []) if len(rows) == 1 else []
        return _result({"name": "شركة الشحن", "cost_per_order": "تكلفة الشحنة", "vat_percent": "نسبة الضريبة"},
                       [r for r in companies if isinstance(r, dict)][offset:offset+SIZE+1])
    from legacy_usage_report_routes import _audit_collection
    columns = {"label": "المصدر", "total": "عدد السجلات", "last_activity": "آخر نشاط", "last_7d": "آخر ٧ أيام"}
    if offset:
        return _result(columns, [])
    rows = []
    for table, label in (("purchase_invoices", "فواتير المشتريات"), ("daily_costs", "التكاليف اليومية"),
                         ("operating_salaries", "الرواتب القديمة"), ("operating_rentals", "الإيجارات القديمة"),
                         ("operating_prepaid_expenses", "المصاريف المقدمة"), ("liabilities", "الالتزامات القديمة")):
        info = await _audit_collection(db, owner, table, ["updated_at", "created_at", "date", "invoice_date", "month", "doc_date"])
        rows.append({"label": label, **info})
    return _result(columns, rows)


async def _reconciliation(db, owner, policy, offset):
    from ledger_core import compute_balances_bulk
    ids = [r["id"][8:] for r in policy.resources if r["kind"] == "bank" and r["id"].startswith("account:")
           and r["balance"] and r["report"]]
    columns = {"name": "البنك", "currency": "العملة", "balance": "رصيد الحساب القديم", "ledger": "رصيد الدفتر", "difference": "الفرق"}
    if not ids:
        return _result(columns, [])
    docs = await db.accounts.find({"user_id": owner, "account_type": "bank", "id": {"$in": ids}},
                                  {"id": 1, "name": 1, "currency": 1, "current_balance": 1}).sort(
        [("name", 1), ("id", 1)]).skip(offset).limit(SIZE+1).to_list(SIZE+1)
    balances = await compute_balances_bulk(db, user_id=owner, entity_type="bank",
                                          entity_ids=[d["id"] for d in docs[:SIZE]], sub_account="main")
    result = []
    for doc in docs[:SIZE]:
        current = doc.get("current_balance")
        net = balances.get(doc["id"], {}).get("net_balance", 0)
        difference = round(current - net, 2) if isinstance(current, (int, float)) else None
        result.append({**doc, "balance": current, "ledger": net, "difference": difference})
    return _result(columns, result + ([{}] if len(docs) > SIZE else []), "مطابقة أرصدة البنوك الممنوحة مع دفتر الحسابات التاريخي.")


async def scoped_report(db, owner, policy, source, offset, day):
    if source == "reconciliation":
        return await _reconciliation(db, owner, policy, offset)
    permitted = {}
    for r in policy.resources:
        if r["balance"] and r["report"]:
            identity = _legacy_id(r["kind"], r["id"])
            if identity:
                permitted.setdefault(r["kind"], []).append(identity)
    if source == "alerts":
        types = {"courier": "shipping_company", "ad_account": "ad_account", "provider": "provider", "bank": "bank"}
        branches = [{"related_entity_type": types[k], "related_entity_id": {"$in": ids}}
                    for k, ids in permitted.items() if k in types]
        return await _documents(db, owner, "alerts", offset, {"$or": branches} if branches else {},
                                "التنبيهات المحفوظة المرتبطة بالحسابات والجهات الممنوحة.")
    fields = {"kind": "نوع الالتزام", "count": "عدد الالتزامات", "expected": "المستحق (ر.س)", "paid": "المدفوع (ر.س)"}
    mappings = (("employee", "employee_salary_id", ["salary", "salary_advance"]),
                ("supplier", "supplier_entity_id", ["supplier"]), ("supplier", "counterparty_id", ["supplier"]),
                ("external_person", "counterparty_id", ["receivable"]))
    branches = [{"kind": {"$in": kinds}, field: {"$in": permitted[resource]}}
                for resource, field, kinds in mappings if resource in permitted]
    description = ("الالتزامات المستحقة في اليوم المختار للجهات الممنوحة." if source == "operations_report"
                   else "الالتزامات المفتوحة لدى الجهات الممنوحة.")
    if not branches:
        return _result(fields, [], description)
    match = {"user_id": owner, "$or": branches, "currency": {"$in": ["SAR", None]}}
    if source == "operations_report":
        match["due_date"] = day
    else:
        match["status"] = {"$in": ["unpaid", "partial"]}
    rows = await db.liabilities.aggregate([
        {"$match": match}, {"$group": {"_id": "$kind", "count": {"$sum": 1},
         "expected": {"$sum": "$expected_amount"}, "paid": {"$sum": "$paid_amount"}}},
        {"$sort": {"_id": 1}}, {"$skip": offset}, {"$limit": SIZE+1},
    ]).to_list(SIZE+1)
    labels = {"supplier": "دفعات الموردين", "salary": "رواتب", "salary_advance": "سلف", "receivable": "ذمم مدينة"}
    return _result(fields, [{**row, "kind": labels.get(row["_id"], row["_id"])} for row in rows], description)
