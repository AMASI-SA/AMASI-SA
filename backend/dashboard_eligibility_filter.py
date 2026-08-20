"""Dashboard-only eligibility rules and owner toggle.

This module intentionally changes only the Mezan V2 dashboard read path.
Operational orders, accounting, reports, Salla sync and stored order documents
remain untouched.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

import dashboard_v2_routes as _dashboard
from auth import ensure_user_settings

ORDER_MIN_TOTAL_SAR = 50.0
PRODUCT_MIN_UNIT_SALE_SAR = 25.0
SETTING_KEY = "dashboard_eligibility_filter_enabled"

_original_filtered_orders = _dashboard._filtered_orders
_original_product_cost = _dashboard.build_mezan_v2_product_cost
_installed = False


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed else 0.0


def order_is_eligible(order: dict[str, Any]) -> bool:
    return _number(order.get("total_amount")) >= ORDER_MIN_TOTAL_SAR


def line_unit_sale(item: dict[str, Any]) -> float:
    quantity = max(_number(item.get("quantity")), 1.0)
    line_total = item.get("total")
    if line_total is not None:
        return max(_number(line_total) / quantity, 0.0)
    return max(_number(item.get("price") or item.get("unit_price")), 0.0)


def qualifying_piece_counts(orders: list[dict[str, Any]]) -> tuple[float, float]:
    eligible_units = 0.0
    excluded_low_price_units = 0.0
    for order in orders:
        if not order_is_eligible(order):
            continue
        for item in order.get("products") or []:
            if not isinstance(item, dict):
                continue
            quantity = max(_number(item.get("quantity")), 1.0)
            if line_unit_sale(item) >= PRODUCT_MIN_UNIT_SALE_SAR:
                eligible_units += quantity
            else:
                excluded_low_price_units += quantity
    return round(eligible_units, 2), round(excluded_low_price_units, 2)


async def _enabled(db: Any, user_id: str) -> bool:
    settings = await ensure_user_settings(db, user_id)
    value = settings.get(SETTING_KEY)
    return True if value is None else bool(value)


async def _filtered_orders_with_dashboard_eligibility(
    db: Any,
    user_id: str,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    orders = await _original_filtered_orders(db, user_id, **kwargs)
    if not await _enabled(db, user_id):
        return orders
    return [order for order in orders if order_is_eligible(order)]


async def _product_cost_with_dashboard_piece_policy(
    db: Any,
    user_id: str,
    orders: list[dict[str, Any]],
) -> dict[str, Any]:
    result = await _original_product_cost(db, user_id, orders)
    if not await _enabled(db, user_id):
        return result
    eligible_units, excluded_units = qualifying_piece_counts(orders)
    summary = dict(result.get("product_profit_summary") or {})
    summary.update({
        "total_units": eligible_units,
        "eligible_total_units": eligible_units,
        "excluded_low_price_units": excluded_units,
        "piece_min_unit_sale_sar": PRODUCT_MIN_UNIT_SALE_SAR,
    })
    result["product_profit_summary"] = summary
    return result


def install_dashboard_eligibility_filter() -> None:
    global _installed
    if _installed:
        return
    _dashboard._filtered_orders = _filtered_orders_with_dashboard_eligibility
    _dashboard.build_mezan_v2_product_cost = _product_cost_with_dashboard_piece_policy
    _installed = True


class EligibilitySettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _require_owner(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "owner_only"})
    role = str(user.get("role") or "").strip().casefold()
    if role != "owner" and user.get("is_owner") is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "owner_only"})
    return user


def make_dashboard_eligibility_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/dashboard-v2", tags=["Mezan Dashboard V2"])

    @router.get("/eligibility-settings")
    async def get_settings(user: dict = Depends(current_user)) -> dict[str, Any]:
        current = _require_owner(user)
        enabled = await _enabled(db, str(current["id"]))
        return {
            "enabled": enabled,
            "order_min_total_sar": ORDER_MIN_TOTAL_SAR,
            "product_min_unit_sale_sar": PRODUCT_MIN_UNIT_SALE_SAR,
            "scope": "advanced_dashboard_only",
        }

    @router.put("/eligibility-settings")
    async def update_settings(
        payload: EligibilitySettingUpdate,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        current = _require_owner(user)
        user_id = str(current["id"])
        await db.user_settings.update_one(
            {"user_id": user_id},
            {"$set": {SETTING_KEY: bool(payload.enabled)}},
            upsert=True,
        )
        return {
            "enabled": bool(payload.enabled),
            "order_min_total_sar": ORDER_MIN_TOTAL_SAR,
            "product_min_unit_sale_sar": PRODUCT_MIN_UNIT_SALE_SAR,
            "scope": "advanced_dashboard_only",
        }

    @router.get("/eligibility-summary")
    async def eligibility_summary(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        payment_methods: str | None = Query(default=None),
        shipping_companies: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        current = _require_owner(user)
        user_id = str(current["id"])
        orders = await _original_filtered_orders(
            db,
            user_id,
            from_date=from_date,
            to_date=to_date,
            payment_methods=payment_methods,
            shipping_companies=shipping_companies,
            include_marketing_attribution=False,
        )
        eligible_orders = [order for order in orders if order_is_eligible(order)]
        excluded_orders = [order for order in orders if not order_is_eligible(order)]
        eligible_units, excluded_units = qualifying_piece_counts(eligible_orders)
        return {
            "enabled": await _enabled(db, user_id),
            "orders_total_before_filter": len(orders),
            "eligible_orders_count": len(eligible_orders),
            "excluded_orders_count": len(excluded_orders),
            "excluded_orders_sales": round(sum(_number(order.get("total_amount")) for order in excluded_orders), 2),
            "eligible_piece_count": eligible_units,
            "excluded_low_price_piece_count": excluded_units,
            "order_min_total_sar": ORDER_MIN_TOTAL_SAR,
            "product_min_unit_sale_sar": PRODUCT_MIN_UNIT_SALE_SAR,
            "scope": "advanced_dashboard_only",
        }

    return router
