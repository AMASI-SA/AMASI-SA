from datetime import date
from unittest.mock import AsyncMock
from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import marketing_reports as reports
from marketing_report_costs import allocate, order_costs, finalize_report_costs


def test_order_allocation_reconciles_in_halalas_including_negative_and_zero_weights():
    assert allocate(1, [1, 1, 1]) == [.34, .33, .33]
    assert allocate(-1, [0, 0, 0]) == [-.34, -.33, -.33]
    assert allocate(None, [1, 2]) == [None, None]
    assert sum(allocate(1670.06, [186.92, 163.66, 494.64, 990.45])) == pytest.approx(1670.06)


def test_configured_shipping_tax_and_salla_payment_fee_are_used():
    result = order_costs({"total_amount": 100, "payment_method": "مدى", "shipping_company": "سمسا", "shipping_cost": 90},
        {"payment_methods": [{"name": "مدى", "commission_percent": 1, "fixed_fee": 1, "vat_percent": 15}]},
        {"سمسا": {"cost_per_order": 20, "vat_percent": 15}})
    assert result["shipping_cost_sar"] == 23
    assert result["payment_fees_sar"] == 2.3


def test_missing_costs_do_not_become_zero_and_actual_zero_is_preserved():
    missing = order_costs({"total_amount": 100}, {}, {})
    assert missing["shipping_cost_sar"] is None
    assert missing["payment_fees_sar"] is None
    actual = order_costs({"total_amount": 100, "shipping_company": "استلام", "payment_fee_status": "actual", "actual_payment_fee": 0},
        {}, {"استلام": {"cost_per_order": 0}})
    assert actual["shipping_cost_sar"] == actual["payment_fees_sar"] == 0


def test_campaign_net_subtracts_shipping_payment_and_ad_spend():
    result = finalize_report_costs({"sales_sar": 1835.67, "product_cost_sar": 608.4, "products": []},
        {"orders": 8, "shipping_cost_sar": 172.5, "payment_fees_sar": 55.07}, 1670.06)
    assert result["net_profit_sar"] == -670.36
    assert result["net_margin_pct"] == -36.52
    missing = finalize_report_costs({"sales_sar": 100, "product_cost_sar": 20, "products": []}, {"orders": 1}, 10)
    assert missing["net_profit_sar"] is None


def test_sources_do_not_blend_49_salla_orders_into_68_provider_conversions():
    row = {"spend_sar": 6402.41, "platform": {"orders": 68, "sales_sar": 18418.88},
           "salla": {"orders": 49, "sales_sar": 14520.26},
           "profitability": {"product_cost_sar": 5125.4, "shipping_cost_sar": 100, "payment_fees_sar": 50, "net_profit_sar": 2842.45}}
    assert reports.selected_metrics(row, "platform")["orders"] == 68
    assert reports.selected_metrics(row, "salla")["orders"] == 49
    assert reports.selected_metrics(row, "platform")["product_cost_sar"] is None
    assert reports.selected_metrics(row, "platform")["net_profit_sar"] is None
    assert reports.selected_metrics(row, "salla")["net_profit_sar"] == 2842.45


def test_totals_recompute_ratios_and_fail_closed_for_unavailable_platform():
    result = reports.summarize_totals([{"orders": 2, "sales_sar": 100, "spend_sar": 10}, {"orders": 1, "sales_sar": 20, "spend_sar": 30}])
    assert result["roas"] == 3
    assert result["cpa_sar"] == pytest.approx(40 / 3)
    assert reports.summarize_totals([result, {}])["sales_sar"] is None


def app_for(user):
    app = FastAPI()
    app.include_router(reports.make_marketing_reports_router(object(), lambda: user))
    return TestClient(app)


def test_reports_reject_non_owner_and_invalid_period_before_read(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(reports, "other_report", read)
    assert app_for({"id": "staff", "role": "staff"}).get("/marketing-reports?provider=meta").status_code == 403
    assert app_for({"id": "owner", "role": "owner"}).get("/marketing-reports?provider=meta&date_from=2026-01-03&date_to=2026-01-01").status_code == 422
    read.assert_not_called()


def test_snapchat_route_uses_canonical_account_totals_not_visible_rows(monkeypatch):
    import snapchat_v2.accounts as accounts
    monkeypatch.setattr(accounts, "resolve_account", AsyncMock(return_value={"timezone": "America/Los_Angeles"}))
    read = AsyncMock(return_value={"provider": "snapchat", "rows": [
        {"spend_sar": 100, "platform": {"orders": 8, "sales_sar": 300}, "salla": {"orders": 8}}],
        "status": "available", "source_totals": [{"spend_sar": 6402.41, "purchases": 68, "sales_sar": 18418.88}]})
    monkeypatch.setattr(reports, "snapchat_report", read)
    response = app_for({"id": "owner", "role": "owner"}).get("/marketing-reports?provider=snapchat&source=platform&date_from=2026-10-01&date_to=2026-10-01&account_id=owned")
    assert response.status_code == 200
    assert response.json()["totals"]["orders"] == 68
    assert response.json()["totals"]["spend_sar"] == 6402.41
    assert read.await_count == 1
    assert read.await_args.args[1:] == ("owner", date(2026,10,1), date(2026,10,1), "owned")


@pytest.mark.asyncio
async def test_snapchat_report_reuses_v2_readers_timezone_and_fx(monkeypatch):
    import snapchat_v2.accounts as accounts
    import snapchat_v2.routes as routes
    import snapchat_v2.salla_outcomes as outcomes
    account = {"ad_account_id": "owned", "timezone": "America/Los_Angeles", "currency": "USD"}
    monkeypatch.setattr(accounts, "list_enabled_accounts", AsyncMock(return_value=[account]))
    monkeypatch.setattr(accounts, "resolve_account", AsyncMock(return_value=account))
    perf = AsyncMock(return_value={"rows": [{"campaign_id": "c", "campaign_name": "Campaign", "status": "ACTIVE", "source_fact_count": 1,
        "spend_native": 448.21, "purchase_value_native": 503.07, "purchases": 8}], "totals": {"spend_native": 448.21, "purchases": 8, "purchase_value_native": 503.07}})
    monkeypatch.setattr(routes, "_entity_performance_report", perf)
    async def fx(db, **kwargs):
        for r in [*kwargs["rows"], kwargs["totals"]]:
            r.update(spend_sar=round(r["spend_native"] * 3.75, 2), exchange_rate_to_sar=3.75)
    monkeypatch.setattr(routes, "_add_sar_spend", fx)
    commerce = AsyncMock(return_value={"by_campaign": {}, "summary": {}})
    monkeypatch.setattr(outcomes, "load_salla_campaign_outcomes", commerce)
    result = await reports.snapchat_report(object(), "owner", date(2026,10,1), date(2026,10,1), "owned")
    assert perf.await_args.kwargs["timezone_name"] == "America/Los_Angeles"
    assert perf.await_args.kwargs["action_report_time"] == "conversion"
    assert result["rows"][0]["spend_native"] == 448.21
    assert result["rows"][0]["platform"]["sales_sar"] == 1886.51
    assert commerce.await_args.kwargs["include_report_costs"] is True


def test_canonical_spend_rounding_reconciles_net_in_footer():
    total = dict(sales_sar=100, spend_sar=10.01, product_cost_sar=20, shipping_cost_sar=3, payment_fees_sar=2, orders=2, net_profit_sar=65)
    assert reports.reconcile_financial_totals(total, "salla")["net_profit_sar"] == 64.99
    assert total["net_margin_pct"] == pytest.approx(64.99)


@pytest.mark.asyncio
async def test_salla_failure_preserves_native_snap_counts_and_missing_costs(monkeypatch):
    import snapchat_v2.accounts as accounts
    import snapchat_v2.routes as routes
    import snapchat_v2.salla_outcomes as outcomes
    account = {"ad_account_id": "owned", "timezone": "America/Los_Angeles", "currency": "USD"}
    monkeypatch.setattr(accounts, "list_enabled_accounts", AsyncMock(return_value=[account]))
    monkeypatch.setattr(accounts, "resolve_account", AsyncMock(return_value=account))
    monkeypatch.setattr(routes, "_entity_performance_report", AsyncMock(return_value={
        "rows": [{"campaign_id": "c", "campaign_name": "C", "status": "ACTIVE", "source_fact_count": 1, "purchases": 68, "spend_native": 100, "purchase_value_native": 200}],
        "totals": {"purchases": 68, "spend_native": 100, "purchase_value_native": 200}, "performance_sync_status": "complete"}))
    async def fx(db, **kw):
        for row in [*kw["rows"], kw["totals"]]: row.update(spend_sar=375, exchange_rate_to_sar=3.75)
    monkeypatch.setattr(routes, "_add_sar_spend", fx)
    monkeypatch.setattr(outcomes, "load_salla_campaign_outcomes", AsyncMock(side_effect=RuntimeError("offline")))
    result = await reports.snapchat_report(object(), "owner", date(2026,10,1), date(2026,10,1), "owned")
    row = result["rows"][0]
    assert row["platform"]["orders"] == 68
    assert row["salla"]["orders"] is None
    assert row["profitability"] is None
    assert result["commerce_available"] is False
    assert result["warnings"]


def tiktok_window(entities=None, *, total=1, pages=1, page=1, spend=2221.6):
    return {"entities": entities if entities is not None else [{
        "entity_id": "c1", "entity_name": "حملة تيك توك", "account_id": "a1",
        "status": "ENABLE", "currency": "SAR", "data_complete": True,
        "spend_sar": 20, "spend_native": 20, "conversions": 4, "impressions": 100, "clicks": 10}],
        "totals": {"spend_sar": spend, "conversions": 730, "impressions": 2000,
                   "clicks": 400, "data_complete": True},
        "accounts": [{"account_id": "a1", "account_name": "تيك توك أماسي", "timezone": "Asia/Riyadh"}],
        "campaign_pagination": {"total": total, "pages": pages, "page": page},
        "ai_readiness": {"campaign_identity_ready": True}}


@pytest.mark.asyncio
async def test_tiktok_uses_native_campaign_reader_not_aggregate_only_overview(monkeypatch):
    import integrations_control_center.tiktok_native_hierarchy as native
    from ads_manager.google_native_overview import GoogleAdsManagerService
    workspace = AsyncMock(return_value=tiktok_window())
    monkeypatch.setattr(native, "tiktok_workspace", workspace)
    legacy = AsyncMock(side_effect=AssertionError("wrong source"))
    monkeypatch.setattr(GoogleAdsManagerService, "overview", legacy)
    commerce = AsyncMock()
    monkeypatch.setattr(reports, "attach_commerce", commerce)
    data = await reports.other_report(object(), "owner", "tiktok", date(2026,10,29), date(2026,10,29))
    assert data["source"] == "tiktok_native_hierarchy_v2"
    assert data["rows"][0]["name"] == "حملة تيك توك"
    assert data["rows"][0]["active"] is True
    assert data["rows"][0]["platform"] == {"orders": 4, "sales_sar": None}
    assert data["source_totals"]["spend_sar"] == 2221.6
    assert data["source_totals"]["conversions"] == 730
    legacy.assert_not_called()
    assert commerce.await_args.args[2] == "tiktok"
    assert workspace.await_args.kwargs["entity_type"] == "campaign"


@pytest.mark.parametrize("source,expected_orders,expected_sales", [("platform",730,None),("salla",2,100)])
def test_tiktok_route_preserves_account_spend_and_separates_event_counts(monkeypatch, source, expected_orders, expected_sales):
    monkeypatch.setattr(reports, "other_report", AsyncMock(return_value={
        "provider": "tiktok", "status": "available", "source_totals": {"spend_sar": 2221.6, "conversions": 730},
        "rows": [{"spend_sar": 20, "platform": {"orders": 4, "sales_sar": None},
                  "salla": {"orders": 2, "sales_sar": 100}, "profitability": {
                      "product_cost_sar": 30, "shipping_cost_sar": 10, "payment_fees_sar": 2}}]}))
    response = app_for({"id":"owner", "role":"owner"}).get(f"/marketing-reports?provider=tiktok&source={source}&date_from=2026-10-29&date_to=2026-10-29")
    assert response.status_code == 200
    total = response.json()["totals"]
    assert total["spend_sar"] == 2221.6  # Full account, not the campaign subtotal20.
    assert total["orders"] == expected_orders
    assert total["sales_sar"] == expected_sales
    if source == "platform":
        assert total["net_profit_sar"] is None and total["roas"] is None
    else:
        assert total["net_profit_sar"] == -2163.6


@pytest.mark.asyncio
async def test_tiktok_collects_all_campaign_pages_and_counts_account_totals_once(monkeypatch):
    import integrations_control_center.tiktok_native_hierarchy as native
    first = tiktok_window(total=2, pages=2)
    second = deepcopy(first)
    second["campaign_pagination"]["page"] = 2
    second["entities"][0]["entity_id"] = "c2"
    workspace = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(native, "tiktok_workspace", workspace)
    monkeypatch.setattr(reports, "attach_commerce", AsyncMock())
    data = await reports.tiktok_report(object(), "owner", date(2026,10,29), date(2026,10,29))
    assert {r["campaign_id"] for r in data["rows"]} == {"c1", "c2"}
    assert data["source_totals"]["conversions"] == 730
    assert [c.kwargs["page"] for c in workspace.await_args_list] == [1, 2]


@pytest.mark.asyncio
async def test_tiktok_rejects_duplicate_or_missing_campaign_pages(monkeypatch):
    import integrations_control_center.tiktok_native_hierarchy as native
    first = tiktok_window(total=2, pages=2)
    duplicate = deepcopy(first)
    duplicate["campaign_pagination"]["page"] = 2
    monkeypatch.setattr(native, "tiktok_workspace", AsyncMock(side_effect=[first, duplicate]))
    with pytest.raises(ValueError, match="truncated"):
        await reports.tiktok_report(object(), "owner", date(2026,10,29), date(2026,10,29))


@pytest.mark.asyncio
async def test_tiktok_empty_campaign_catalog_keeps_valid_account_spend(monkeypatch):
    import integrations_control_center.tiktok_native_hierarchy as native
    monkeypatch.setattr(native, "tiktok_workspace", AsyncMock(return_value=tiktok_window([], total=0, pages=0)))
    monkeypatch.setattr(reports, "attach_commerce", AsyncMock())
    data = await reports.tiktok_report(object(), "owner", date(2026,10,29), date(2026,10,29))
    assert data["status"] == "available" and data["rows"] == []
    assert data["source_totals"]["spend_sar"] == 2221.6


@pytest.mark.asyncio
async def test_tiktok_long_period_splits_canonical_windows_and_preserves_missing_costs(monkeypatch):
    import integrations_control_center.tiktok_native_hierarchy as native
    second = tiktok_window(spend=None)
    second["totals"]["data_complete"] = False
    second["entities"][0].update(spend_sar=None, conversions=None, data_complete=False)
    workspace = AsyncMock(side_effect=[tiktok_window(), second])
    monkeypatch.setattr(native, "tiktok_workspace", workspace)
    monkeypatch.setattr(reports, "attach_commerce", AsyncMock(side_effect=RuntimeError("unavailable")))
    data = await reports.tiktok_report(object(), "owner", date(2026,9,1), date(2026,10,2))
    assert [(c.kwargs["from_date"], c.kwargs["to_date"]) for c in workspace.await_args_list] == [
        ("2026-09-01", "2026-10-01"), ("2026-10-02", "2026-10-02")]
    assert data["source_totals"]["spend_sar"] is None
    assert data["rows"][0]["platform"]["orders"] is None
    assert data["rows"][0]["profitability"] is None
    assert data["commerce_available"] is False
    assert data["coverage"]["status"] == "partial"
