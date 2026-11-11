"""Google saved spend must reach the actual dashboard financial routes.

All owners, accounts, orders and amounts here are synthetic. The real Google
reader and financial reducers run against a read-only Mongo-compatible facade;
only the unrelated Snapchat observation and commerce fixture are supplied.
"""
from copy import deepcopy

import pytest

import dashboard_v2_routes as dashboard
from integrations_control_center.ad_daily_close_proof import close_proof
from integrations_control_center.google_ads_reporting import (
    GOOGLE_ADS_DAILY_COLLECTION,
    GOOGLE_ADS_REPORTING_SOURCE_MODE,
)
from tests.test_dashboard_card_snapshots import PARAMS, card_app  # noqa: F401


REAL_ADS_READER = dashboard.build_mezan_v2_ads
DAY = "2026-11-11"
ACCOUNT = "synthetic-google-account"


async def install_sources(env, monkeypatch, *, native_amount=96.48, native_present=True):
    monkeypatch.setattr(dashboard, "build_mezan_v2_ads", REAL_ADS_READER)
    await env.db.unified_orders.update_one(
        {"user_id": "a", "order_number": "selected"},
        {"$set": {"utm_source": "google_ads"}},
    )
    await env.db.mezan_integrations_v2.insert_one({
        "user_id": "a", "provider": "google_ads", "connection_status": "connected",
        "connection_provenance": "api_connection",
    })
    await env.db.mezan_integration_accounts_v2.insert_one({
        "user_id": "a", "provider": "google_ads", "connection_status": "connected",
        "connection_provenance": "api_connection", "ad_account_id": ACCOUNT,
        "external_account_id": ACCOUNT, "mezan_selected": True,
        "currency": "SAR", "timezone": "Asia/Riyadh",
    })
    # Daily-cost creation defaults unrelated advertising fields to zero. It
    # neither overrides a native observation nor proves missing Google zero.
    await env.db.daily_costs.insert_one({"user_id": "a", "date": DAY, "google_ads": 0})
    if native_present:
        proof = close_proof(
            account_id=ACCOUNT, business_date=DAY, timezone="Asia/Riyadh",
            currency="SAR", spend=native_amount, provider_row_count=1,
            complete_response=True, explicit_spend_present=True, identity_proven=True,
            source_mode=GOOGLE_ADS_REPORTING_SOURCE_MODE,
            observed_at="2026-11-11T19:00:00+00:00",
        )
        await env.db[GOOGLE_ADS_DAILY_COLLECTION].insert_one({
            "user_id": "a", "provider": "google_ads", "ad_account_id": ACCOUNT,
            "date": DAY, "account_timezone": "Asia/Riyadh", "currency_native": "SAR",
            "spend_native": native_amount, "spend_sar": native_amount,
            "fx_rate_to_sar": 1, "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
            "source_close_proof": proof, "observed_at": "2026-11-11T19:00:00+00:00",
        })

    async def saved_snapchat(*_args, **_kwargs):
        return deepcopy({
            "rows": [{"date": DAY, "spend_sar": 4}],
            "total_sar": 4, "known_total_sar": 4, "daily_sar": {DAY: 4},
            "quality": {
                "connected": True, "status": "provisional", "amount_available": True,
                "amount_complete": False, "provisional": True,
                "data_state": "provisional", "coverage_complete": False,
                "reason_codes": ["snapchat_operational_hour_observation"],
            },
            "bank_commissions": {
                "total_fee_sar": 0.08, "fee_subject_spend_sar": 4,
                "total_effective_spend_sar": 4, "coverage": {"complete": True},
                "accounts": [{
                    "external_account_id": "synthetic-snap-account", "provider": "snapchat_ads",
                    "provider_label": "Snapchat", "display_name": "Synthetic Snapchat",
                    "spend_sar": 4, "spend_native": 4, "native_currency": "SAR",
                    "bank_commission_pct": 2, "bank_commission_fee_sar": 0.08,
                    "apply_bank_commission": True, "configured": True,
                }],
            },
        })

    monkeypatch.setattr(dashboard, "load_unified_marketing_dashboard_spend", saved_snapchat)


@pytest.mark.asyncio
async def test_native_google_reaches_executive_profit_and_cost_detail_with_provisional_snap(card_app, monkeypatch):
    env = card_app
    await install_sources(env, monkeypatch)
    response = await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)
    assert response.status_code == 200, response.text
    body = response.json()
    totals, ads = body["totals"], body["ads_v2"]
    google = ads["executive_breakdown"]["providers"]["google"]
    assert google["spend_sar"] == 96.48
    assert google["salla_orders"] == 1
    assert google["salla_sales_sar"] == 100
    assert google["platform_reported_orders"] is None
    assert totals["total_ads_cost"] == ads["total"] == 100.48
    assert totals["total_product_cost"] == 10
    assert totals["ad_bank_commission_fees"] == totals["total_payment_fees"] == 0.08
    # Independent reconciliation: 100 sales - 10 product - 100.48 ads - 0.08 fee.
    assert totals["net_profit"] == totals["net_sales"] == -10.56
    assert totals["ads_spend_provisional"] is True
    assert ads["providers"]["snapchat"]["orders"] is None
    assert ads["providers"]["snapchat"]["platform_reported_metrics_complete"] is False
    assert len([row for row in body["payment_breakdown"] if row["key"] == "ad_bank_commissions"]) == 1
    assert body["snapshot"]["refresh_interval_seconds"] == 60

    full = await env.client.get("/dashboard-v2", params=PARAMS)
    assert full.status_code == 200, full.text
    assert full.json()["totals"] == totals
    detail = await env.client.get("/dashboard-v2/ads-cost-breakdown", params={
        "from_date": DAY, "to_date": DAY,
    })
    assert detail.status_code == 200, detail.text
    detail = detail.json()
    assert detail["total_amount"] == 100.48
    assert detail["by_provider"]["google"] == 96.48
    google_items = [row for row in detail["items"] if row["ad_provider"] == "google"]
    assert len(google_items) == 1
    assert GOOGLE_ADS_DAILY_COLLECTION in google_items[0]["source"]
    assert ads["history"][0]["google"] == 96.48

    # Recomputing a snapshot observes newly saved facts without provider sync;
    # it does not retain the wrong zero or apply bank fees twice.
    await env.db[GOOGLE_ADS_DAILY_COLLECTION].delete_many({"user_id": "a"})
    refreshed = await env.client.get("/dashboard-v2/cards/executive", params={**PARAMS, "refresh": True})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["totals"]["total_ads_cost"] is None
    assert refreshed.json()["totals"]["net_profit"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("native_present, expected_google, expected_profit", [
    (False, None, None),
    (True, 0, 85.92),
])
async def test_missing_google_is_not_zero_and_proven_zero_keeps_financials_available(
    card_app, monkeypatch, native_present, expected_google, expected_profit,
):
    env = card_app
    await install_sources(env, monkeypatch, native_amount=0, native_present=native_present)
    response = await env.client.get("/dashboard-v2/cards/executive", params=PARAMS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ads_v2"]["executive_breakdown"]["providers"]["google"]["spend_sar"] == expected_google
    assert body["totals"]["net_profit"] == expected_profit
    assert body["totals"]["ads_spend_amount_available"] is native_present
    assert body["totals"]["total_ads_cost"] == (4 if native_present else None)
    assert body["totals"]["total_payment_fees"] == (0.08 if native_present else None)
    assert body["ads_v2"]["providers"]["snapchat"]["amount_available"] is True
