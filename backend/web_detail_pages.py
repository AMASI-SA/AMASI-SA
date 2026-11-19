"""Grantable drill-downs. Every lookup binds a single merchant and a reviewed data shape."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from operational_balance_access import load_access
from web_legacy_page_readers import SIZE, _label_entities, _ledger, _report_providers, _result


_DETAIL = {
    "detail.order": ("orders", "تفاصيل الطلب", "/orders-v2/:orderNumber"),
    "detail.analysis": ("legacy", "نتيجة تحليل الملف", "/analyses/:id"),
    "detail.account": ("legacy", "كشف الحساب المالي", "/accounts/:id"),
    "detail.supplier": ("legacy", "كشف المورد التفصيلي", "/suppliers/:id/ledger-detail"),
    "detail.entity": ("legacy", "كشف حركات الجهة", "/entity-ledger/:type/:id"),
    "detail.reconciliation": ("legacy", "مطابقة حساب منصة الدفع", "/reconciliation/:accountId"),
}
DETAIL_WEB_PAGES = tuple({
    "id": key, "section": section, "label": label, "to": "/employee-detail/" + key,
    "detail_route": route, "review_component": "detail_read",
} for key, (section, label, route) in _DETAIL.items())
_ENDPOINT = "/api/web-page-access-v1/details/"
_ID_PATTERN = r"^[\w][\w:.~-]{0,199}$"


def _forbidden():
    raise HTTPException(403, detail={"code": "web_page_permission_required"})


def _missing():
    raise HTTPException(404, detail={"code": "detail_unavailable"})


def enforce_web_detail_pages(modes, path, method, query=None):
    if modes is None or not path.startswith(_ENDPOINT):
        return False
    page_id = path[len(_ENDPOINT):].rstrip("/")
    if method != "GET" or page_id not in _DETAIL or page_id not in modes:
        _forbidden()
    return True


def _fields(names):
    return {"_id": 0, **dict.fromkeys(names, 1)}


def _values(labels, row):
    # Do not serialize a malformed nested object as a scalar field.
    return [{"label": labels[key], "value": value} for key, value in
            _result(labels, [row])["rows"][0].items() if value is not None]


def _table(title, labels, rows):
    return {"title": title, **_result(labels, rows)}


def _response(title, facts, tables=()):
    return {"title": title, "facts": facts, "tables": list(tables)}


async def _analysis(db, owner, identity):
    summary = {"total_orders": "عدد الطلبات", "total_sales": "إجمالي المبيعات (ر.س)",
               "total_payment_fees": "رسوم الدفع (ر.س)", "total_shipping_cost": "الشحن (ر.س)",
               "total_ads_cost": "الإعلانات (ر.س)", "total_product_cost": "تكلفة المنتجات (ر.س)",
               "net_revenue_after_fees": "الصافي بعد العمولات (ر.س)", "net_profit": "صافي الربح (ر.س)"}
    fields = ["name", "date", "filename", *["report.summary." + key for key in summary]]
    item = await db.analyses.find_one({"user_id": owner, "id": identity}, _fields(fields))
    if not item:
        _missing()
    facts = _values({"date": "تاريخ التحليل", "filename": "الملف"}, item)
    facts += _values(summary, (item.get("report") or {}).get("summary") or {})
    return _response(item.get("name") or identity, facts)


async def _order(db, owner, identity, offset):
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import OrderNotFoundError, get_order

    try:
        order = await get_order(MongoOrderRepository(db), user_id=owner, order_number=identity)
    except OrderNotFoundError:
        _missing()
    facts = _values({"status": "حالة الطلب", "created_at": "تاريخ الطلب", "method": "طريقة الدفع",
                     "company": "شركة الشحن", "shipping_status": "حالة الشحنة",
                     "total": "إجمالي الطلب", "currency": "العملة"},
                    {"status": order.status_native or order.status, "created_at": order.created_at,
                     "method": order.payment.method_native or order.payment.method, "company": order.shipping.company,
                     "shipping_status": order.shipping.status, "total": order.totals.total, "currency": order.totals.currency})
    items = [item.model_dump(include={"name", "sku", "quantity", "total"}) for item in order.items[offset:offset + SIZE + 1]]
    return _response("طلب " + order.order_number, facts, [
        _table("منتجات الطلب", {"name": "المنتج", "sku": "رمز المنتج", "quantity": "الكمية", "total": "الإجمالي"}, items),
    ])


async def _supplier_statement(db, owner, identity, access, actor_id, offset, start, end):
    # The active accounting book determines the only admissible source. The
    # canonical V2 read validates journals and cutover evidence before projection.
    from accounting_report_dates import accounting_instant, report_cutoff
    from accounting_mz2_reports import read_mz2_ledger
    from supplier_payment_service import money

    scope = await read_mz2_ledger(db, owner=owner, required_accounts=[("supplier", identity, "payable")])
    if scope.get("status") != "available" or scope.get("ledger_backend") != "v2":
        raise HTTPException(409, detail={"code": "supplier_statement_not_ready"})
    rows = [row for row in scope["items"] if (row["entity_type"], row["entity_id"], row.get("sub_account"))
            == ("supplier", identity, "payable")]
    facts = []
    if access["balance"]:
        # The V2 book accepts posted SAR legs. Supplier credit means money owed.
        balance = sum((money(row["amount"]) * (1 if row["side"] == "credit" else -1) for row in rows), Decimal(0))
        facts = _values({"net": "رصيد دائن ناقص مدين (ر.س)"}, {"net": float(balance)})
    tables = []
    mode = access["movements"]
    if mode != "none":
        if mode == "own":
            rows = [row for row in rows if row.get("posted_by") == actor_id]
        lower = accounting_instant({"metadata": {"accounting_at": start.isoformat()}}) if start else None
        upper = report_cutoff(end.isoformat()) if end else None
        if lower or upper:
            def within(row):
                at = accounting_instant({"metadata": {"accounting_at": row["effective_at"]}})
                return (not lower or lower <= at) and (not upper or at < upper)
            rows = [row for row in rows if within(row)]
        rows.reverse()
        columns = {"entry_no": "رقم القيد", "effective_at": "التاريخ", "entry_type": "نوع الحركة",
                   "side": "الجانب", "amount": "المبلغ", "currency": "العملة"}
        tables.append(_table("حركات المورد", columns, rows[offset:offset + SIZE + 1]))
    ref = [{"entity_type": "supplier", "entity_id": identity}]
    await _label_entities(db, owner, ref)
    return _response(ref[0]["entity_name"], facts, tables)


async def _party(db, owner, policy, identity, kind, offset, start, end):
    access = policy.resource(kind, identity)
    if not access["balance"] and access["movements"] == "none":
        _missing()
    if kind == "supplier":
        control = await db.mz2_atomic_owners.find_one({"_id": owner}, {"ledger_backend_state": 1})
        if (control or {}).get("ledger_backend_state") == "v2_active":
            return await _supplier_statement(db, owner, identity, access, policy.actor["id"], offset, start, end)
    # Intersect the legacy reader's existing movement-owner, subaccount and currency
    # rules with the single requested identity before either query runs.
    pinned = replace(policy, resources=[{"kind": kind, "id": identity, **access}])
    source = {"supplier": "supplier", "external_person": "external", "courier": "courier"}[kind]
    tables = []
    if access["balance"]:
        tables.append({"title": "الرصيد حسب العملة والحساب الفرعي", **await _ledger(db, owner, pinned, source, offset)})
    if access["movements"] != "none":
        if kind == "supplier":
            fields = {"entry_no": "رقم القيد", "created_at": "التاريخ", "side": "الجانب", "amount": "المبلغ", "currency": "العملة"}
            query = {"user_id": owner, "entity_type": kind, "entity_id": identity, "sub_account": "payable", "status": "posted"}
            if access["movements"] == "own":
                query["posted_by"] = policy.actor["id"]
            if start or end:
                query["created_at"] = {**({"$gte": start.isoformat()} if start else {}),
                                       **({"$lt": (end + timedelta(days=1)).isoformat()} if end else {})}
            docs = await db.general_ledger.find(query, _fields(fields)).sort([("created_at", -1), ("entry_no", -1)]).skip(
                offset).limit(SIZE + 1).to_list(SIZE + 1)
            tables.append(_table("حركات المورد", fields, docs))
        else:
            tables.append({"title": "حركات الجهة", **await _ledger(db, owner, pinned, "ledger", offset)})
    name = next((row["entity_name"] for table in tables for row in table["rows"] if row.get("entity_name")), None)
    if not name:
        reference = [{"entity_type": kind, "entity_id": identity}]
        await _label_entities(db, owner, reference)
        name = reference[0]["entity_name"]
    return _response(name, [], tables)


def _account_types(policy, page_id, identity=None):
    types = []
    if page_id == "detail.account":
        bank_ids = [row["id"][8:] for row in policy.resources if row["kind"] == "bank" and
                    row["id"].startswith("account:") and (row["balance"] or row["movements"] != "none")]
        if identity is not None:
            bank_ids = [identity] if identity in bank_ids else []
        if bank_ids:
            types.append({"account_type": "bank", "id": {"$in": bank_ids}})
    provider_ids = _report_providers(policy)
    if provider_ids:
        # The canonical provider binding is persisted; a display name cannot grant access.
        types.append({"account_type": "payment_platform", "normalized_payment_method": {"$in": list(provider_ids)}})
    return types


async def _account(db, owner, policy, identity, page_id, offset):
    query = {"user_id": owner, "id": identity}
    types = _account_types(policy, page_id, identity)
    if not types:
        _missing()
    fields = ("id", "name", "currency", "account_type", "normalized_payment_method", "opening_balance",
              "current_balance", "expected_orders_balance", "orders_count", "last_synced_at")
    acc = await db.accounts.find_one({**query, "$or": types}, _fields(fields))
    if not acc:
        _missing()
    if acc["account_type"] == "payment_platform":
        if page_id == "detail.reconciliation":
            fields = {"transfer_date": "التاريخ", "amount": "المبلغ"}
            rows = await db.transfers.find({"user_id": owner, "from_account_id": identity}, _fields(fields)).sort(
                [("transfer_date", -1), ("_id", -1)]).skip(offset).limit(SIZE + 1).to_list(SIZE + 1)
            facts = _values({"currency": "العملة", "expected_orders_balance": "قيمة التحصيل المتوقعة المحفوظة",
                             "orders_count": "عدد الطلبات المحفوظ", "last_synced_at": "تاريخ المزامنة"}, acc)
            return _response(acc.get("name") or identity, facts, [_table("التحويلات الصادرة من حساب المنصة", fields, rows)])
        access = policy.resource("provider", acc["normalized_payment_method"])
    else:
        access = policy.resource("bank", "account:" + identity)

    facts = _values({"currency": "العملة"}, acc)
    if access["balance"]:
        from financial_position_ssot import account_balance_ssot
        value = await account_balance_ssot(db, user_id=owner, account=acc)
        facts += _values({"balance": "رصيد الحساب"}, {"balance": value})
    tables = []
    if access["movements"] == "all":
        # Historical account_transactions has no reliable actor field. An own-only
        # grant can use attributed GL rows below, never all legacy transactions.
        columns = {"transaction_date": "التاريخ", "transaction_type": "نوع الحركة", "direction": "الاتجاه", "amount": "المبلغ"}
        rows = await db.account_transactions.find({"user_id": owner, "account_id": identity}, _fields(columns)).sort(
            [("transaction_date", -1), ("_id", -1)]).skip(offset).limit(SIZE + 1).to_list(SIZE + 1)
        tables.append(_table("الحركات المسجلة على الحساب", columns, rows))
    if acc["account_type"] == "bank" and access["movements"] != "none":
        pinned = replace(policy, resources=[{"kind": "bank", "id": "account:" + identity, **access}])
        tables.append({"title": "قيود الحساب", **await _ledger(db, owner, pinned, "ledger", offset)})
    return _response(acc.get("name") or identity, facts, tables)


async def _choices(db, owner, policy, page_id, kind, offset):
    if page_id in {"detail.entity", "detail.supplier"}:
        allowed = sorted(row["id"] for row in policy.resources if row["kind"] == kind and
                         (row["balance"] or row["movements"] != "none") and re.fullmatch(_ID_PATTERN, row["id"]))
        selected = allowed[offset:offset + SIZE]
        rows = [{"entity_type": kind, "entity_id": identity} for identity in selected]
        await _label_entities(db, owner, rows)
        return {"choices": [{"id": row["entity_id"], "label": row["entity_name"]} for row in rows],
                "has_more": offset + SIZE < len(allowed)}
    if page_id == "detail.analysis":
        docs = db.analyses.find({"user_id": owner}, _fields(("id", "name"))).sort([("created_at", -1), ("id", 1)])
    elif page_id in {"detail.account", "detail.reconciliation"}:
        types = _account_types(policy, page_id)
        if not types:
            return {"choices": [], "has_more": False}
        docs = db.accounts.find({"user_id": owner, "$or": types}, _fields(("id", "name"))).sort([("name", 1), ("id", 1)])
    else:
        return {"choices": [], "has_more": False}
    rows = await docs.skip(offset).limit(SIZE + 1).to_list(SIZE + 1)
    return {"choices": [{"id": row["id"], "label": row.get("name") or row["id"]} for row in rows[:SIZE]
                        if isinstance(row.get("id"), str) and re.fullmatch(_ID_PATTERN, row["id"])],
            "has_more": len(rows) > SIZE}


def make_web_detail_router(db, current_user):
    from web_page_access import delegated_page_owner

    router = APIRouter()

    @router.get("/details/{page_id}")
    async def details(page_id: str, request: Request, response: Response, identity: str | None = Query(None, pattern=_ID_PATTERN),
                      kind: str | None = None, start: date | None = None, end: date | None = None,
                      page: int = Query(1, ge=1, le=12500), user=Depends(current_user)):
        if page_id not in _DETAIL:
            _forbidden()
        owner = await delegated_page_owner(db, user, page_id)
        if not owner:
            _forbidden()
        params = request.query_params
        if any(key not in {"identity", "kind", "page", "start", "end"} or len(params.getlist(key)) != 1 for key in params):
            raise HTTPException(422, detail={"code": "invalid_detail_selector"})
        if (start or end) and (page_id != "detail.supplier" or not identity or end == date.max or (start and end and start > end)):
            raise HTTPException(422, detail={"code": "invalid_date_range"})
        if page_id == "detail.entity":
            if kind not in {"external", "external_person", "courier"}:
                raise HTTPException(422, detail={"code": "invalid_entity_type"})
        elif kind is not None:
            raise HTTPException(422, detail={"code": "invalid_entity_type"})
        response.headers["Cache-Control"] = "no-store, private"
        offset = (page - 1) * SIZE
        policy = None
        if page_id not in {"detail.analysis", "detail.order"}:
            policy = await load_access(db, user, owner)
            policy.require_enabled()
        kind = {"external": "external_person", None: "supplier"}.get(kind, kind)
        if identity is None:
            return await _choices(db, owner, policy, page_id, kind, offset)
        if page_id == "detail.analysis":
            return await _analysis(db, owner, identity)
        if page_id == "detail.order":
            return await _order(db, owner, identity, offset)
        if page_id in {"detail.supplier", "detail.entity"}:
            return await _party(db, owner, policy, identity, kind, offset, start, end)
        return await _account(db, owner, policy, identity, page_id, offset)

    return router
