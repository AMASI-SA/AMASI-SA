"""Stable Dashboard V2 facade with core/optional-source isolation.

The original implementation is preserved in ``dashboard_v2_routes_legacy``.
This facade keeps every auxiliary route from that module, replaces only the
root dashboard aggregator, and exposes advertising as a separately refreshable
last-good snapshot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, TypeVar

from fastapi import APIRouter, Depends, Query, Request, Response

import dashboard_v2_routes_legacy as legacy

T = TypeVar("T")
log = logging.getLogger("mezan.dashboard_v2")
OPTIONAL_CACHE = "dashboard_optional_source_cache_v1"
OPTIONAL_CACHE_VERSION = 1

# Public compatibility exports used by existing route factories and tests.
build_mezan_v2_ads = legacy.build_mezan_v2_ads
build_mezan_v2_product_cost = legacy.build_mezan_v2_product_cost
calculate_mezan_v2_line_cost = legacy.calculate_mezan_v2_line_cost
select_abandoned_carts_for_period = legacy.select_abandoned_carts_for_period


def _request_id(request: Request) -> str:
    existing = str(getattr(request.state, "request_id", "") or "").strip()
    if existing:
        return existing[:128]
    candidate = str(request.headers.get("x-request-id") or "").strip()
    value = candidate[:128] if candidate else uuid.uuid4().hex
    request.state.request_id = value
    return value


def _milliseconds(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


async def _measure(
    timings: dict[str, float],
    name: str,
    awaitable: Awaitable[T],
) -> T:
    started = time.perf_counter()
    try:
        return await awaitable
    finally:
        timings[name] = _milliseconds(started)


def _cache_key(
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
    payment_methods: str | None,
    shipping_companies: str | None,
) -> str:
    return "|".join([
        user_id,
        str(from_date or ""),
        str(to_date or ""),
        str(payment_methods or ""),
        str(shipping_companies or ""),
    ])


def _server_timing(timings: dict[str, float]) -> str:
    initial_db = max(
        timings.get("legacy_dashboard_db_ms", 0.0),
        timings.get("orders_db_ms", 0.0),
        timings.get("month_orders_db_ms", 0.0),
    )
    derived_db = max(
        timings.get("product_cost_db_ms", 0.0),
        timings.get("recurring_obligations_db_ms", 0.0),
        timings.get("optional_cache_db_ms", 0.0),
    )
    db_total = round(
        initial_db
        + derived_db
        + timings.get("optional_cache_write_db_ms", 0.0),
        2,
    )
    return ", ".join([
        f'auth;dur={timings.get("auth_ms", 0.0):.2f}',
        f'db;dur={db_total:.2f}',
        f'salla;dur={timings.get("salla_ms", 0.0):.2f}',
        f'ads;dur={timings.get("ads_ms", 0.0):.2f}',
        f'final;dur={timings.get("final_aggregation_ms", 0.0):.2f}',
        f'total;dur={timings.get("total_ms", 0.0):.2f}',
    ])


def _log_request(
    *,
    request_id: str,
    user_id: str,
    route: str,
    status: str,
    timings: dict[str, float],
    optional_source: str,
    error_type: str | None = None,
) -> None:
    record = {
        "event": "dashboard_v2_request",
        "request_id": request_id,
        "route": route,
        "user_id": user_id,
        "status": status,
        "optional_source": optional_source,
        "timings_ms": timings,
    }
    if error_type:
        record["error_type"] = error_type
    log.info(json.dumps(record, ensure_ascii=False, sort_keys=True))


def _remove_root_route(router: APIRouter) -> None:
    router.routes[:] = [
        route
        for route in router.routes
        if not (
            getattr(route, "path", None) == "/dashboard-v2"
            and "GET" in (getattr(route, "methods", None) or set())
        )
    ]


def _legacy_ads_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    totals = response.get("totals") or {}
    return {
        "available": False,
        "stale": True,
        "last_success_at": None,
        "total": legacy._float(totals.get("total_ads_cost")),
        "breakdown": dict(totals.get("ads_cost_breakdown_v2") or {}),
        "history": [],
        "providers": {},
        "source_contract": {
            "status": "optional_snapshot_not_warmed",
            "fallback": "legacy_dashboard_last_known_total",
        },
    }


def _apply_ads_snapshot(
    response: dict[str, Any],
    ads: dict[str, Any] | None,
    *,
    last_success_at: str | None,
) -> dict[str, Any]:
    totals = response["totals"]
    previous_ads = legacy._float(totals.get("total_ads_cost"))
    if not ads:
        response["ads_v2"] = _legacy_ads_snapshot(response)
        return response

    next_ads = {
        **ads,
        "available": True,
        "stale": True,
        "last_success_at": last_success_at,
    }
    ads_total = legacy._float(next_ads.get("total"))
    totals["net_profit"] = round(
        legacy._float(totals.get("net_profit")) + previous_ads - ads_total,
        2,
    )
    config = response.get("net_sales_config") or {}
    if config.get("deduct_ads", True):
        totals["net_sales"] = round(
            legacy._float(totals.get("net_sales")) + previous_ads - ads_total,
            2,
        )
    totals.update({
        "total_ads_cost": ads_total,
        "daily_ads_total": ads_total,
        "overall_roas": (
            round(legacy._float(totals.get("total_sales")) / ads_total, 2)
            if ads_total > 0
            else None
        ),
        "avg_cost_per_order": (
            round(ads_total / int(totals.get("total_orders") or 0), 2)
            if ads_total > 0 and int(totals.get("total_orders") or 0) > 0
            else None
        ),
    })
    providers = next_ads.get("providers") or {}
    for provider in ("tiktok", "meta"):
        metrics = providers.get(provider) or {}
        totals[f"{provider}_spend"] = metrics.get("spend")
        totals[f"{provider}_purchases"] = metrics.get("orders")
        totals[f"{provider}_revenue"] = metrics.get("revenue")
        totals[f"{provider}_roas"] = metrics.get("roas")
    legacy.merge_ad_bank_fees_into_dashboard(response, next_ads)
    response["ads_v2"] = next_ads
    return response


def make_dashboard_v2_router(
    db: Any,
    current_user: Callable[..., Any],
    legacy_dashboard: Callable[..., Any],
    require_owner: Callable[[dict[str, Any]], Any],
) -> APIRouter:
    router = legacy.make_dashboard_v2_router(
        db,
        current_user,
        legacy_dashboard,
        require_owner,
    )
    _remove_root_route(router)

    async def measured_current_user(request: Request) -> dict[str, Any]:
        started = time.perf_counter()
        request_id = _request_id(request)
        try:
            return await current_user(request)
        except Exception as exc:
            timings = {
                "auth_ms": _milliseconds(started),
                "salla_ms": 0.0,
                "ads_ms": 0.0,
                "total_ms": _milliseconds(started),
            }
            _log_request(
                request_id=request_id,
                user_id="unknown",
                route="/dashboard-v2",
                status="auth_failed",
                timings=timings,
                optional_source="not_reached",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            request.state.dashboard_auth_ms = _milliseconds(started)

    @router.get("/dashboard-v2")
    async def dashboard_v2(
        request: Request,
        response_header: Response,
        from_date: str | None = None,
        to_date: str | None = None,
        payment_methods: str | None = None,
        shipping_companies: str | None = None,
        user: dict = Depends(measured_current_user),
    ) -> dict[str, Any]:
        request_started = time.perf_counter()
        timings: dict[str, float] = {
            "auth_ms": float(getattr(request.state, "dashboard_auth_ms", 0.0) or 0.0),
            "salla_ms": 0.0,
            "ads_ms": 0.0,
        }
        request_id = _request_id(request)
        current = user
        require_owner(current)
        user_id = str(current["id"])
        optional_source = "cache_miss"
        try:
            today = legacy._today_riyadh()
            month_start = today.replace(day=1).isoformat()
            today_s = today.isoformat()
            selected_is_current_month = (
                from_date == month_start and to_date == today_s
            )

            legacy_task = _measure(
                timings,
                "legacy_dashboard_db_ms",
                legacy_dashboard(
                    user=current,
                    from_date=from_date,
                    to_date=to_date,
                    payment_methods=payment_methods,
                    shipping_companies=shipping_companies,
                    include_legacy_analyses=False,
                    allow_self_heal=False,
                ),
            )
            orders_task = _measure(
                timings,
                "orders_db_ms",
                legacy._filtered_orders(
                    db,
                    user_id,
                    from_date=from_date,
                    to_date=to_date,
                    payment_methods=payment_methods,
                    shipping_companies=shipping_companies,
                    include_marketing_attribution=True,
                ),
            )
            initial_reads: list[Awaitable[Any]] = [legacy_task, orders_task]
            if not selected_is_current_month:
                initial_reads.append(_measure(
                    timings,
                    "month_orders_db_ms",
                    legacy._filtered_orders(
                        db,
                        user_id,
                        from_date=month_start,
                        to_date=today_s,
                        payment_methods=payment_methods,
                        shipping_companies=shipping_companies,
                    ),
                ))
            initial_results = await asyncio.gather(*initial_reads)
            dashboard = initial_results[0]
            orders = initial_results[1]
            month_orders = orders if selected_is_current_month else initial_results[2]
            timings.setdefault("month_orders_db_ms", 0.0)

            month_kpis = {
                "from_date": month_start,
                "to_date": today_s,
                "total_orders": len(month_orders),
                "total_sales": round(
                    sum(legacy._float(order.get("total_amount")) for order in month_orders),
                    2,
                ),
            }
            totals = dashboard["totals"]
            authoritative_sales = round(
                sum(legacy._float(order.get("total_amount")) for order in orders),
                2,
            )
            previous_sales = legacy._float(totals.get("total_sales"))
            sales_delta = round(authoritative_sales - previous_sales, 2)
            totals["total_orders"] = len(orders)
            totals["total_sales"] = authoritative_sales
            previous_product = legacy._float(totals.get("total_product_cost"))
            previous_operating = legacy._float(totals.get("operating_expenses_total"))
            salary_total = legacy._float(totals.get("operating_salaries_total"))

            try:
                operating_from = (
                    date.fromisoformat(from_date)
                    if from_date
                    else today.replace(day=1)
                )
            except (TypeError, ValueError):
                operating_from = today.replace(day=1)
            try:
                operating_to = date.fromisoformat(to_date) if to_date else today
            except (TypeError, ValueError):
                operating_to = today
            if operating_to < operating_from:
                operating_from, operating_to = operating_to, operating_from

            key = _cache_key(
                user_id,
                from_date=from_date,
                to_date=to_date,
                payment_methods=payment_methods,
                shipping_companies=shipping_companies,
            )
            product_cost, recurring, cached = await asyncio.gather(
                _measure(
                    timings,
                    "product_cost_db_ms",
                    legacy.build_mezan_v2_product_cost(db, user_id, orders),
                ),
                _measure(
                    timings,
                    "recurring_obligations_db_ms",
                    legacy.compute_recurring_obligations_for_range(
                        db,
                        user_id,
                        operating_from,
                        operating_to,
                    ),
                ),
                _measure(
                    timings,
                    "optional_cache_db_ms",
                    db[OPTIONAL_CACHE].find_one(
                        {
                            "cache_version": OPTIONAL_CACHE_VERSION,
                            "cache_key": key,
                            "user_id": user_id,
                        },
                        {"_id": 0, "ads_v2": 1, "last_success_at": 1},
                    ),
                ),
            )

            final_started = time.perf_counter()
            recurring_total = legacy._float(recurring.get("total"))
            operating_total = salary_total + recurring_total
            product_total = product_cost["total"]
            totals["net_profit"] = round(
                legacy._float(totals.get("net_profit"))
                + sales_delta
                + previous_product - product_total
                + previous_operating - operating_total,
                2,
            )
            totals["net_sales"] = round(
                legacy._float(totals.get("net_sales")) + sales_delta,
                2,
            )
            config = dashboard.get("net_sales_config") or {}
            if config.get("deduct_product_costs", True):
                totals["net_sales"] = round(
                    legacy._float(totals.get("net_sales"))
                    + previous_product - product_total,
                    2,
                )
            if config.get("deduct_operating_expenses", True):
                totals["net_sales"] = round(
                    legacy._float(totals.get("net_sales"))
                    + previous_operating - operating_total,
                    2,
                )
            totals.update({
                "total_product_cost": product_total,
                "computed_product_cost": product_total,
                "manual_product_cost": 0.0,
                "missing_product_cost_count": product_cost["missing_products_count"],
                "incomplete_profit_orders_count": product_cost["incomplete_orders_count"],
                "no_products_orders_count": product_cost["no_products_orders_count"],
                "excel_no_products_count": 0,
                "daily_products_total": product_total,
                "operating_expenses_total": round(operating_total, 2),
                "operating_rentals_total": recurring["rentals_total"],
                "operating_utilities_total": recurring["utilities_total"],
                "operating_renewals_total": recurring["renewals_total"],
                "operating_recurring_total": recurring["total"],
                "operating_recurring_by_type": recurring["by_type"],
                "operating_prepaid_total": 0.0,
                "operating_prepaid_by_type": {},
                "operating_daily_other_total": 0.0,
                "legacy_analyses_count": 0,
                "analyses_count": 0,
            })
            cached_ads = (cached or {}).get("ads_v2")
            if cached_ads:
                optional_source = "last_good_cache"
            _apply_ads_snapshot(
                dashboard,
                cached_ads,
                last_success_at=(cached or {}).get("last_success_at"),
            )
            current_ads_total = legacy._float(totals.get("total_ads_cost"))
            totals["daily_ads_total"] = current_ads_total
            totals["daily_costs_total"] = round(
                product_total + current_ads_total,
                2,
            )
            totals["daily_expenses_total"] = product_total
            dashboard.update({
                "recent_analyses": [],
                "product_cost_v2": product_cost,
                "month_kpis": month_kpis,
                "dashboard_source": "mezan_v2",
                "source_contract": {
                    "orders_sales_payment_methods": "unified_orders:mezan_v2",
                    "product_cost": product_cost["source_contract"],
                    "advertising": (
                        "dashboard_optional_source_cache_v1:last_good"
                        if cached_ads
                        else "legacy_dashboard_last_known_total:optional_cache_pending"
                    ),
                    "employee_salaries": "mezan_employee_salary_contracts_v2",
                    "recurring_obligations": "operating_recurring_obligations_v2",
                    "shipping_partners": "legacy_shipping_cost_ssot",
                    "payment_gateway_fees": (
                        "legacy_payment_method_settings + "
                        "mezan_ad_account_cost_settings_v2"
                    ),
                },
                "optional_sources": {
                    "advertising": {
                        "status": optional_source,
                        "last_success_at": (cached or {}).get("last_success_at"),
                        "refresh_endpoint": "/api/dashboard-v2/optional-sources",
                    }
                },
                "source_only": True,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
            })
            dashboard["recurring_obligations_v2"] = recurring
            timings["final_aggregation_ms"] = _milliseconds(final_started)
            timings["total_ms"] = _milliseconds(request_started)
            dashboard["request_id"] = request_id
            dashboard["stage_timings_ms"] = timings
            response_header.headers["X-Request-ID"] = request_id
            response_header.headers["Server-Timing"] = _server_timing(timings)
            response_header.headers["Cache-Control"] = "no-store"
            _log_request(
                request_id=request_id,
                user_id=user_id,
                route="/dashboard-v2",
                status="ok",
                timings=timings,
                optional_source=optional_source,
            )
            return dashboard
        except Exception as exc:
            timings["total_ms"] = _milliseconds(request_started)
            _log_request(
                request_id=request_id,
                user_id=user_id,
                route="/dashboard-v2",
                status="failed",
                timings=timings,
                optional_source=optional_source,
                error_type=type(exc).__name__,
            )
            raise

    @router.get("/dashboard-v2/optional-sources")
    async def dashboard_v2_optional_sources(
        request: Request,
        response_header: Response,
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        payment_methods: str | None = Query(default=None),
        shipping_companies: str | None = Query(default=None),
        user: dict = Depends(measured_current_user),
    ) -> dict[str, Any]:
        request_started = time.perf_counter()
        request_id = _request_id(request)
        timings: dict[str, float] = {
            "auth_ms": float(getattr(request.state, "dashboard_auth_ms", 0.0) or 0.0),
            "salla_ms": 0.0,
        }
        require_owner(user)
        user_id = str(user["id"])
        key = _cache_key(
            user_id,
            from_date=from_date,
            to_date=to_date,
            payment_methods=payment_methods,
            shipping_companies=shipping_companies,
        )
        try:
            ads, orders = await asyncio.gather(
                _measure(
                    timings,
                    "ads_ms",
                    legacy.build_mezan_v2_ads(
                        db,
                        user_id,
                        from_date=from_date,
                        to_date=to_date,
                    ),
                ),
                _measure(
                    timings,
                    "orders_db_ms",
                    legacy._filtered_orders(
                        db,
                        user_id,
                        from_date=from_date,
                        to_date=to_date,
                        payment_methods=payment_methods,
                        shipping_companies=shipping_companies,
                        include_marketing_attribution=True,
                    ),
                ),
            )
            final_started = time.perf_counter()
            ads["executive_breakdown"] = (
                legacy.build_salla_ads_executive_breakdown(orders, ads)
            )
            last_success_at = datetime.now(timezone.utc).isoformat()
            snapshot = {
                **ads,
                "available": True,
                "stale": False,
                "last_success_at": last_success_at,
            }
            timings["final_aggregation_ms"] = _milliseconds(final_started)
            await _measure(
                timings,
                "optional_cache_write_db_ms",
                db[OPTIONAL_CACHE].update_one(
                    {
                        "cache_version": OPTIONAL_CACHE_VERSION,
                        "cache_key": key,
                        "user_id": user_id,
                    },
                    {
                        "$set": {
                            "ads_v2": snapshot,
                            "last_success_at": last_success_at,
                            "updated_at": last_success_at,
                        },
                        "$setOnInsert": {
                            "created_at": last_success_at,
                        },
                    },
                    upsert=True,
                ),
            )
            timings["total_ms"] = _milliseconds(request_started)
            payload = {
                "request_id": request_id,
                "ads_v2": snapshot,
                "last_success_at": last_success_at,
                "source_only": True,
                "accounting_write_reached": False,
                "qoyod_write_reached": False,
                "stage_timings_ms": timings,
            }
            response_header.headers["X-Request-ID"] = request_id
            response_header.headers["Server-Timing"] = _server_timing(timings)
            response_header.headers["Cache-Control"] = "no-store"
            _log_request(
                request_id=request_id,
                user_id=user_id,
                route="/dashboard-v2/optional-sources",
                status="ok",
                timings=timings,
                optional_source="fresh",
            )
            return payload
        except Exception as exc:
            timings["total_ms"] = _milliseconds(request_started)
            _log_request(
                request_id=request_id,
                user_id=user_id,
                route="/dashboard-v2/optional-sources",
                status="failed",
                timings=timings,
                optional_source="last_good_cache_preserved",
                error_type=type(exc).__name__,
            )
            raise

    return router


__all__ = [
    "build_mezan_v2_ads",
    "build_mezan_v2_product_cost",
    "calculate_mezan_v2_line_cost",
    "make_dashboard_v2_router",
    "select_abandoned_carts_for_period",
]
