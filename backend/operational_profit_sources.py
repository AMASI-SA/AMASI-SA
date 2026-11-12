"""Bounded refresh-only profit reader, reusing Mezan's existing cost authorities.

The caller may save this presentation snapshot alongside a successful refresh.
This module has no financial/database writer and is never called by a report GET.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import time, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from operational_forecast_projection import money, number
from operational_payment_evidence import _instant
from operational_profit_projection import OPERATING, SOURCE, VERSION
from operational_read_runtime import SourceReadError, load_collections, source_read_budget

RIYADH = ZoneInfo("Asia/Riyadh")
MAX_ORDERS = 100_000


def whole_days(start, end):
    """Only whole local days are attributable from a source with daily resolution."""
    first, last = start.astimezone(RIYADH), end.astimezone(RIYADH)
    day_from = first.date() + timedelta(days=first.time() != time.min)
    day_to = last.date() - timedelta(days=1)
    return ((day_from, day_to) if day_from <= day_to else None,
            first.time() == time.min and last.time() == time.min)


def _cohort_steps(orders, projected_rows, settings, owner, start, end):
    """Use native creation instants, unique identities and source versions before hydration."""
    from dashboard_v2_routes import _matches_any

    projected = defaultdict(list)
    counts = Counter(str(order.get("order_number") or "") for order in orders)
    for row in projected_rows:
        projected[str(row.get("order_number") or "")].append(row)
    selected, pending, identities = [], [], []
    for original in orders:
        yield
        num = str(original.get("order_number") or "")
        if not _matches_any(original.get("order_status", ""), settings.get("report_included_statuses") or []):
            continue
        if settings.get("hide_inferred_date_orders") and original.get("order_date_inferred"):
            continue
        rows = projected[num]
        raw = ((rows[0].get("raw_by_source") or {}).get("salla_direct") or {}) if len(rows) == 1 else {}
        oid = str(original.get("salla_order_id") or raw.get("id") or original.get("order_id") or num)
        reason = None
        if not num or counts[num] != 1 or len(rows) != 1 or original.get("user_id") != owner or rows[0].get("user_id") != owner:
            reason = "profit_order_identity_incomplete"
        if not reason and raw and (str(raw.get("id") or "") != oid or str(raw.get("reference_id") or raw.get("order_number") or "") != num):
            reason = "profit_order_identity_incomplete"
        created = raw.get("date") or raw.get("created_at") or raw.get("order_date")
        created = created or original.get("created_at_utc") or original.get("order_created_at") or original.get("source_created_at")
        try:
            created = _instant(created)
            if not start < created <= end:
                continue
        except (ValueError, TypeError):
            reason = reason or "profit_order_time_unknown"
        version = raw.get("updated_at") or raw.get("date_updated")
        watermark = original.get("g47_salla_snapshot") or {}
        try:
            if version is not None and _instant(version) > end:
                reason = reason or "profit_order_version_after_cutoff"
            if watermark.get("requires_authoritative_refresh") or (
                    watermark.get("source_updated_at") and (not version or _instant(watermark["source_updated_at"]) != _instant(version))):
                reason = reason or "profit_order_version_incomplete"
        except (ValueError, TypeError, AttributeError):
            reason = reason or "profit_order_version_incomplete"
        if reason:
            pending.append({"order_id": oid, "order_number": num, "code": reason})
            continue
        selected.append(deepcopy(original))
        identities.append([oid, num, created.isoformat(), _instant(version).isoformat() if version else None])
    digest = sha256(json.dumps(sorted(identities), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return selected, {"orders": len(selected), "pending_orders": len(pending), "pending_order_details": pending,
                      "complete": not pending, "identity_digest": digest, "boundaries": "started_at_exclusive_as_of_inclusive"}


def select_order_cohort(orders, projected_rows, settings, owner, start, end):
    steps = _cohort_steps(orders, projected_rows, settings, owner, start, end)
    while True:
        try:
            next(steps)
        except StopIteration as finished:
            return finished.value


async def _cohort_cooperative(orders, raw_rows, settings, owner, start, end, budget):
    steps = _cohort_steps(orders, raw_rows, settings, owner, start, end)
    while True:
        try:
            next(steps)
        except StopIteration as finished:
            return finished.value
        await budget.checkpoint()


async def _load_context(db, owner, start, end):
    from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
    from order_currency import SALLA_RAW_CURRENCY_PROJECTION, hydrate_order_currency_fields_cooperative
    from report_option_recovery import SALLA_REPORT_OPTION_PROJECTION, attach_report_option_evidence_cooperative

    settings_rows = await db["settings"].find({"user_id": owner}, {"_id": 0, "user_id": 1,
        "report_included_statuses": 1, "hide_inferred_date_orders": 1, "payment_methods": 1, "shipping_companies": 1}).to_list(2)
    if len(settings_rows) > 1 or any(row.get("user_id") != owner for row in settings_rows):
        raise ValueError("profit_settings_ambiguous")
    settings = settings_rows[0] if settings_rows else {}
    # A one-day guard band covers historical date-only compatibility fields.
    # Exact inclusion is decided from the original instant, not this index.
    query = {"user_id": owner, "order_date": {
        "$gte": (start.astimezone(RIYADH).date() - timedelta(days=1)).isoformat(),
        "$lte": (end.astimezone(RIYADH).date() + timedelta(days=1)).isoformat()}}
    orders = await db["unified_orders"].find(query, {"_id": 0, "raw_by_source": 0}).to_list(MAX_ORDERS + 1)
    if len(orders) > MAX_ORDERS:
        raise ValueError("profit_order_scope_too_large")
    projection = {**SALLA_RAW_CURRENCY_PROJECTION, **SALLA_REPORT_OPTION_PROJECTION, "user_id": 1,
        **{"raw_by_source.salla_direct." + field: 1 for field in ("id", "order_number", "date", "created_at", "order_date", "updated_at", "date_updated")}}
    raw_rows = await db["unified_orders"].find(query, projection).to_list(MAX_ORDERS + 1) if orders else []
    if len(raw_rows) > MAX_ORDERS:
        raise ValueError("profit_order_scope_too_large")
    budget = CPUWorkBudget(PARSER_BUDGET_MS)
    selected, coverage = await _cohort_cooperative(orders, raw_rows, settings, owner, start, end, budget)
    await attach_report_option_evidence_cooperative(selected, raw_rows, checkpoint=budget.checkpoint)
    await hydrate_order_currency_fields_cooperative(selected, raw_rows, checkpoint=budget.checkpoint)
    return {"orders": selected, "settings": settings, "coverage": coverage}


async def _order_cost_totals(orders, settings):
    from dashboard_cpu_budget import CPUWorkBudget, COST_PROFIT_BUDGET_MS
    from order_profitability_summary import single_order_costs
    from order_status_policy import default_category_for

    configs = {}
    for row in settings.get("shipping_companies") or []:
        name = str(row.get("name") or "").strip().strip("'\"")
        if name:
            configs[name] = configs[name.lower()] = row
    result = {key: {"known": Decimal(0), "known_orders": 0, "pending_orders": 0} for key in ("shipping", "payment_fees")}
    adjusted = 0
    budget = CPUWorkBudget(COST_PROFIT_BUDGET_MS)
    for order in orders:
        await budget.checkpoint()
        changed = default_category_for(order.get("order_status")) in {"cancelled", "refunded"} or any(
            (number(order.get(field)) or Decimal(0)) > 0 for field in ("actual_refund_amount", "actual_partial_refund_amount"))
        adjusted += int(changed)
        costs = single_order_costs(order, settings, configs)
        for key, field in (("shipping", "shipping_cost_sar"), ("payment_fees", "payment_fees_sar")):
            value = number(costs.get(field))
            if value is None or value < 0 or changed:
                result[key]["pending_orders"] += 1
            else:
                result[key]["known"] += value
                result[key]["known_orders"] += 1
    return {**result, "adjusted_orders": adjusted}


def _component_readers(db, owner, context, days):
    from dashboard_v2_routes import build_mezan_v2_ads, build_mezan_v2_product_cost_cooperative
    from expenses_routes import compute_operating_expenses_for_range
    from order_currency import summarize_orders_sar_cooperative
    from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
    from recurring_obligations_routes import compute_recurring_obligations_for_range

    orders = context["orders"]
    readers = {
        "sales": lambda: summarize_orders_sar_cooperative(orders, checkpoint=CPUWorkBudget(PARSER_BUDGET_MS).checkpoint),
        "products": lambda: build_mezan_v2_product_cost_cooperative(db, owner, orders, include_product_rows=False),
        "order_costs": lambda: _order_cost_totals(orders, context["settings"]),
    }
    if days:
        first, last = days
        readers.update({
            "ads": lambda: build_mezan_v2_ads(db, owner, from_date=first.isoformat(), to_date=last.isoformat()),
            "payroll": lambda: compute_operating_expenses_for_range(db, owner, first, last),
            "recurring": lambda: compute_recurring_obligations_for_range(db, owner, first, last),
        })
    return readers


def _component(value, *, complete, source, known=None, reason=None, coverage=None):
    value, known = number(value), number(known)
    complete = complete is True and value is not None and value >= 0
    if complete:
        known = value
    return {"amount": money(value) if complete else None, "known_subtotal": money(known), "complete": complete,
            "source": source, "reason": reason, "coverage": coverage or {}}


def assemble_snapshot(owner, start, end, context, values, errors):
    """Keep each source's own coverage; one unavailable source cannot erase another."""
    days, daily_complete = whole_days(start, end)
    coverage = context.get("coverage") or {"orders": 0, "complete": False, "pending_orders": None}
    cohort_complete = coverage.get("complete") is True
    sales, products, costs = (values.get(key) or {} for key in ("sales", "products", "order_costs"))
    sales_complete = cohort_complete and sales.get("conversion_complete") is True
    sales_known = number(sales.get("known_total_sar"))
    if sales_known == 0 and not (sales_complete or (number(sales.get("converted_orders_count")) or 0) > 0):
        sales_known = None
    products_complete = (cohort_complete and products.get("missing_products_count") == 0
                         and products.get("incomplete_orders_count") == 0 and costs.get("adjusted_orders") == 0)
    products_known = number(products.get("total"))
    known_product_lines = sum((number(count) or 0) for source, count in (products.get("source_lines") or {}).items()
                              if source != "missing")
    if products_known == 0 and not (products_complete or known_product_lines > 0
            or (number(products.get("linked_products_count")) or 0) > 0
            or (number(products.get("salla_fallback_products_count")) or 0) > 0):
        products_known = None
    source_period = {"from": start.isoformat(), "to": end.isoformat(), "timezone": "Asia/Riyadh"}
    daily_coverage = {"whole_days_from": days[0].isoformat() if days else None,
        "whole_days_to": days[1].isoformat() if days else None, "partial_day_boundaries": not daily_complete}
    components = {
        "net_sales": _component(sales.get("total_sar"), complete=sales_complete,
            known=sales_known, source="unified_orders.total_amount_sar:salla_order_fx",
            reason="أساس إجمالي الطلبات في المصدر بعد تعديلاتها؛ لا يُعاد وصفه بأنه مبلغ محصل أو مبيعات دون ضريبة القيمة المضافة.", coverage=coverage),
        "cost_of_goods": _component(products.get("total"), known=products_known, complete=products_complete,
            source="mezan_v2_product_cost:order_cost_authority", coverage={
                "missing_products": products.get("missing_products_count"), "incomplete_orders": products.get("incomplete_orders_count"),
                "refund_review_orders": costs.get("adjusted_orders")}),
    }
    for name in ("shipping", "payment_fees"):
        cost = costs.get(name) or {}
        known = cost.get("known") if cost.get("known_orders") or coverage.get("orders") == 0 and cohort_complete else None
        components[name] = _component(cost.get("known"), known=known,
            complete=cohort_complete and cost.get("pending_orders") == 0,
            source="shipping_cost_ssot" if name == "shipping" else "merchant_payment_settings:actual_fee_precedence",
            coverage={key: cost.get(key) for key in ("known_orders", "pending_orders")})
    ads = values.get("ads") or {}
    quality = ads.get("spend_quality") or {}
    ad_total = number(ads.get("total"))
    ad_known = ad_total if quality.get("amount_complete") is True else number(ads.get("known_subtotal_sar"))
    components["advertising"] = _component(ad_total, known=ad_known,
        complete=daily_complete and quality.get("amount_complete") is True, source="mezan_v2_ads:stored_provider_spend", coverage=daily_coverage)
    for name, key, field, source in (
            ("salaries", "payroll", "salaries_total", "mezan_employee_salary_contracts_v2:operating_expenses"),
            ("operating_expenses", "recurring", "total", "operating_recurring_obligations_v2")):
        value = (values.get(key) or {}).get(field)
        components[name] = _component(value, known=value, complete=daily_complete and key in values,
            source=source, coverage=daily_coverage)
    # The existing consolidated profit authority includes ad-bank commissions
    # once alongside payment fees. Unknown commission coverage stays unknown.
    bank = ads.get("bank_commissions") or {}
    bank_fee = number(bank.get("total_fee_sar"))
    bank_known = bank_fee if quality.get("amount_complete") is True else None
    order_fees = components["payment_fees"]
    order_known = number(order_fees["known_subtotal"])
    total_known = (order_known or Decimal(0)) + (bank_known or Decimal(0)) if order_known is not None or bank_known is not None else None
    complete_fees = order_fees["complete"] and daily_complete and bank_known is not None
    components["payment_fees"] = _component(total_known, known=total_known, complete=complete_fees,
        source="merchant_payment_settings + mezan_ad_account_cost_settings_v2",
        coverage={**order_fees["coverage"], "ad_bank_fee_complete": daily_complete and bank_known is not None},
        reason="رسوم الطلبات وعمولات الإعلانات البنكية مرة واحدة؛ التحويلات والتمويل لا تصبح مصروفًا إضافيًا.")
    for name in ("financing", "tax"):
        components[name] = _component(None, complete=False, source=None,
            reason="لا توجد في عقد الربح الحالي تكلفة معتمدة لهذا البند عن الفترة؛ لا يفترض صفرًا.")
    if not daily_complete:
        for name in ("advertising", "salaries", "operating_expenses"):
            components[name]["reason"] = "المجموع المعلوم يغطي الأيام الكاملة داخل الفترة؛ المصدر اليومي لا يثبت توزيع جزء يوم البداية أو النهاية."
    return {"version": VERSION, "source": SOURCE, "owner_id": owner, "currency": "SAR", "period": source_period,
        "as_of": end.isoformat(), "cohort": coverage, "components": components,
        "quality": {"operating_complete": all(components[key]["complete"] for key in OPERATING), "complete": False,
            "unknown_is_zero": False, "reason_codes": sorted(set(errors.values())), "source_errors": errors,
            "daily_coverage": daily_coverage}, "read_only": True}


async def load_profit_snapshot(db, owner, state, *, as_of, budget_seconds=25):
    """Return a bounded source snapshot; persist only through the caller's normal refresh."""
    if not owner or state.get("owner_id") != owner:
        raise ValueError("operational_profit_owner_mismatch")
    start, end = _instant(state["started_at"]), _instant(as_of)
    if end < start:
        raise ValueError("operational_profit_period_invalid")
    values, errors = {}, {}

    async def batch(readers):
        async def read(_db, _owner, name):
            try:
                value = await readers[name]()
                values[name] = value
                return value
            except (ValueError, TypeError, KeyError, HTTPException, PyMongoError):
                errors[name] = "operational_profit_source_incomplete"
                return None
        try:
            await load_collections(db, owner, readers, reader=read, per_read_timeout=20)
        except SourceReadError as error:
            errors[error.collection] = error.code
        for name in readers:
            if name not in values and name not in errors:
                errors[name] = "operational_profit_source_unavailable"

    with source_read_budget(budget_seconds):
        await batch({"context": lambda: _load_context(db, owner, start, end)})
        context = values.get("context") or {}
        if context:
            await batch(_component_readers(db, owner, context, whole_days(start, end)[0]))
    return assemble_snapshot(owner, start, end, context, values, errors)
