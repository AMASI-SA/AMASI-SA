"""Single-order financial acceptance: real cost authority, no provider/DB writes."""
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from order_engine import _original_make_order_engine_router as make_order_engine_router
from order_profitability_summary import build_order_profitability_summary, single_order_costs, summarize_order_profitability
from .test_dashboard_bundle_cost_authority import OWNER, ReadOnlyDB, db, freeze, sale, sale_line


def summary(*, amount=100, complete=True, advertising=None, **fields):
    order = {"order_number": "synthetic", "currency": "SAR", "total_amount": amount, **fields}
    if advertising is not None:
        order["mezan_profit"] = {"known": True, "net_profit_sar": 1,
            "revenue_sar": amount, "allocated_ad_spend_sar": advertising}
    return summarize_order_profitability(order, [{"line_total": 24.2, "mezan_cost_complete": complete,
        "missing_cost_reasons": [] if complete else ["option_selection_unverified"]}],
        {"shipping_cost_sar": 17.25, "payment_fees_sar": 3.55})


def test_complete_without_ad_evidence_has_only_before_ad_profit():
    result = summary()
    assert result["direct_cost_sar"] == 45
    assert result["profit_before_advertising_sar"] == 55
    assert result["advertising_cost_sar"] is None
    assert result["profit_sar"] is None
    assert result["total_cost_sar"] is None


@pytest.mark.parametrize("advertising,expected", [(10, 45), (0, 55), (80, -25)])
def test_explicit_allocation_including_zero_and_loss(advertising, expected):
    result = summary(advertising=advertising)
    assert result["profit_sar"] == expected
    assert result["complete"]


def test_missing_choice_retains_known_amount_and_never_certifies_profit():
    result = summary(complete=False, advertising=10)
    assert result["known_total_cost_sar"] == 55
    assert result["product_cost_sar"] is None
    assert result["total_cost_sar"] is None and result["profit_sar"] is None
    assert "option_selection_unverified" in result["reasons"]


@pytest.mark.parametrize("fields", [{"actual_partial_refund_amount": 5}, {"order_status": "ملغي"}])
def test_refunds_need_reconciliation_instead_of_fabricated_margin(fields):
    result = summary(**fields)
    assert result["profit_before_advertising_sar"] is None
    assert "refund_cost_reconciliation_required" in result["reasons"]


def test_missing_fx_and_stale_allocation_are_unknown():
    result = summary(currency="XYZ", advertising=10)
    assert result["revenue_sar"] is None and result["profit_sar"] is None
    order = {"order_number": "stale", "currency": "SAR", "total_amount": 100,
        "mezan_profit": {"known": True, "net_profit_sar": 50, "revenue_sar": 200, "allocated_ad_spend_sar": 10}}
    result = summarize_order_profitability(order, [], {})
    assert result["advertising_cost_sar"] is None
    assert result["shipping_cost_sar"] is None and result["payment_fees_sar"] is None


def test_verified_foreign_shipping_fallback_converts_once_and_merchant_rate_stays_sar():
    order = {"order_number": "foreign", "currency": "KWD", "total_amount": 100,
        "accounting_currency": "SAR", "currency_conversion_status": "verified",
        "exchange_rate_to_sar": "12.2", "shipping_cost": 5, "shipping_company": "Carrier",
        "payment_fee_status": "actual", "actual_payment_fee": 0}
    costs = single_order_costs(order, {}, {})
    result = summarize_order_profitability(order, [{"line_total": 100, "mezan_cost_complete": True}], costs)
    assert result["shipping_cost_sar"] == 61
    assert result["profit_before_advertising_sar"] == 1059
    configured = single_order_costs(order, {}, {"Carrier": {"cost_per_order": 15, "vat_percent": 15}})
    assert configured["shipping_cost_sar"] == 17.25
    order.pop("exchange_rate_to_sar")
    assert single_order_costs(order, {}, {})["shipping_cost_sar"] is None


def test_explicit_free_merchant_shipping_is_not_replaced_by_customer_charge():
    costs = single_order_costs({"shipping_company": "Carrier", "shipping_cost": 25}, {},
        {"Carrier": {"cost_per_order": 0}})
    assert costs["shipping_cost_sar"] == 0
    assert costs["shipping_cost_source"] == "company_config"


@pytest.mark.asyncio
async def test_catalog_bundle_costs_and_shipping_vat_are_read_only(db):
    order = sale("card", sale_line("one", 4), shipping_company="Synthetic carrier",
        payment_fee_status="actual", actual_payment_fee=5)
    await freeze(db, order)
    await db.settings.insert_one({"user_id": OWNER, "shipping_companies": [
        {"name": "Synthetic carrier", "cost_per_order": 15, "vat_percent": 15}]})
    before = deepcopy(await db.unified_orders.find_one({"user_id": OWNER}))
    result = await build_order_profitability_summary(ReadOnlyDB(db), OWNER, "card")
    assert result["product_cost_sar"] == 220  # four shawls + selected bouquet
    assert result["shipping_cost_sar"] == 17.25
    assert result["direct_cost_sar"] == 242.25
    assert result["profit_before_advertising_sar"] == 157.75
    assert await db.unified_orders.find_one({"user_id": OWNER}) == before
    with pytest.raises(HTTPException) as exc:
        await build_order_profitability_summary(ReadOnlyDB(db), "another-owner", "card")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_http_owner_scope_and_employee_denial(db):
    await freeze(db, sale("http-card", sale_line("plain", product="plain")))
    app = FastAPI()
    user = {"id": OWNER, "role": "owner"}
    app.include_router(make_order_engine_router(ReadOnlyDB(db), lambda: user), prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/orders-v2/http-card/profitability")
        assert response.status_code == 200
        assert response.json()["product_cost_sar"] == 9
        user["id"] = "another-owner"
        assert (await client.get("/api/orders-v2/http-card/profitability")).status_code == 404
        user["role"] = "employee"
        assert (await client.get("/api/orders-v2/http-card/profitability")).status_code == 403
