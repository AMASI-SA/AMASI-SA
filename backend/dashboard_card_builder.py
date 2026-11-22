"""Canonical dashboard card builders independent of HTTP and queue execution."""
from datetime import date
import asyncio
from fastapi import HTTPException

import dashboard_v2_routes as dashboard

CARD_TTLS = {"executive": 60, "top-products": 1800, "period-summary": 18000}


async def prepare_dashboard_card(db, *, owner_id, section, from_date=None, to_date=None,
                                 payment_methods=None, shipping_companies=None, report_statuses=None):
    if section not in CARD_TTLS:
        raise HTTPException(422, detail="invalid_dashboard_card")
    user_id = str(owner_id)
    if report_statuses is not None:
        from sold_financial_report import validate_report_statuses
        report_statuses = validate_report_statuses(report_statuses)
    try:
        start = date.fromisoformat(from_date) if from_date else None
        end = date.fromisoformat(to_date) if to_date else None
        if start and end and end < start:
            raise ValueError("reversed_date_range")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid_date_range") from exc
    from_date = start.isoformat() if start else None
    to_date = end.isoformat() if end else None
    today = dashboard._today_riyadh()
    from supplier_invoice_reporting_costs import latest_report_invoice
    revision, invoice_revision, settings, policy = await asyncio.gather(
        dashboard.get_product_cost_revision(db, user_id),
        latest_report_invoice(db, user_id),
        db.settings.find_one(
            {"user_id": user_id},
            {"_id": 0, "hide_inferred_date_orders": 1, "report_included_statuses": 1,
             **({"payment_methods": 1} if section == "executive" else {})},
        ),
        dashboard.get_policy_map(db, user_id),
    )
    settings = settings or {}
    fee_context = await dashboard.load_fee_setup_context(db, user_id) if section == "executive" else None

    def filter_key(value: str | None) -> tuple[str, ...]:
        return tuple(sorted({part.strip().casefold() for part in (value or "").split(",") if part.strip()}))

    key = (
        dashboard.FINANCIAL_COHORT_READER_VERSION,
        user_id, section, today.isoformat(), from_date, to_date,
        filter_key(payment_methods), filter_key(shipping_companies),
        None if report_statuses is None else filter_key(report_statuses),
        revision, invoice_revision, bool(settings.get("hide_inferred_date_orders")),
        tuple(sorted(str(value) for value in (settings.get("report_included_statuses") or []))),
        tuple(sorted(policy.items())),
        dashboard.payment_settings_revision(settings) if section == "executive" else None,
        dashboard.fee_setup_revision(fee_context) if section == "executive" else None,
    )
    filters = {
        "from_date": from_date, "to_date": to_date,
        "payment_methods": payment_methods, "shipping_companies": shipping_companies,
        **({"report_statuses": report_statuses} if report_statuses is not None else {}),
    }

    return {"key": key, "filters": filters, "settings": settings,
            "fee_context": fee_context, "today": today}


async def build_dashboard_card(db, legacy_dashboard, *, owner_id, section, binding, read_only=True):
    """Return the existing financial response; callers own authorization and scheduling."""
    user_id = str(owner_id)
    filters, settings, fee_context, today = (binding[name] for name in ("filters", "settings", "fee_context", "today"))
    from_date, to_date = filters.get("from_date"), filters.get("to_date")
    settings_options = {"persist_settings": False} if read_only else {}
    if section == "executive":
        result, method_setup = await dashboard._gather_dashboard_reads(
            dashboard._compute_dashboard_v2(
                db, legacy_dashboard, user={"id": user_id}, **filters,
                include_month_kpis=False, include_product_rows=False,
                include_account_results=True,
                fee_context=fee_context, persist_settings=not read_only,
            ),
            # Setup is owner-wide, so a date or payment filter cannot
            # hide an unresolved method. This reads names only; it does
            # not initialize fees, create accounts or run a sync.
            dashboard.load_payment_method_setup(db, user_id, settings=settings,
                                      fee_context=fee_context, current_day=today),
        )
        return {**result, "payment_method_setup": method_setup}
    source_orders = await dashboard._filtered_orders(
        db, user_id, **filters,
        include_marketing_attribution=True,
        **settings_options,
    )
    orders, financial_cohort = dashboard.split_financial_cohort(source_orders)
    if section == "top-products":
        return {"product_cost_v2": await dashboard.build_mezan_v2_product_cost_cooperative(db, user_id, orders),
                "financial_cohort": financial_cohort}

    month_from = today.replace(day=1).isoformat()
    month_to = today.isoformat()
    if from_date == month_from and to_date == month_to:
        month_orders, month_cohort = orders, financial_cohort
    else:
        month_source_orders = await dashboard._filtered_orders(
            db, user_id, **{**filters, "from_date": month_from, "to_date": month_to},
            **settings_options,
        )
        month_orders, month_cohort = dashboard.split_financial_cohort(month_source_orders)
    product_cost, ads, month_sales = await dashboard._gather_dashboard_reads(
        dashboard.build_mezan_v2_product_cost_cooperative(db, user_id, orders, include_product_rows=False),
        dashboard.build_mezan_v2_ads(db, user_id, from_date=from_date, to_date=to_date),
        dashboard.summarize_orders_sar_cooperative(month_orders, checkpoint=dashboard.CPUWorkBudget(dashboard.PARSER_BUDGET_MS).checkpoint),
    )
    executive = await dashboard.build_salla_ads_executive_breakdown_cooperative(orders, ads)
    return {
        "month_kpis": {
            "from_date": month_from, "to_date": month_to,
            "total_orders": month_cohort["total_orders"], "total_sales": month_sales["total_sar"],
            **{key: month_cohort[key] for key in ("financial_eligible_orders", "excluded_unpaid_orders", "review_orders")},
            "accounting_currency": "SAR", "currency_conversion": month_sales,
        },
        "product_cost_v2": product_cost,
        "ads_v2": {"executive_breakdown": executive},
        "financial_cohort": financial_cohort,
    }
