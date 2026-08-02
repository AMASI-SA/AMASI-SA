from __future__ import annotations

from ads_manager.models import CampaignRow


def test_unified_ads_response_contract_accepts_snapchat_campaign() -> None:
    row = CampaignRow.model_validate(
        {
            "provider": "snapchat",
            "provider_label": "Snapchat",
            "account_id": "snap-account-1",
            "campaign_id": "snap-campaign-1",
            "campaign_name": "حملة سناب",
            "status": "ACTIVE",
            "delivery_status": "ACTIVE",
            "objective": "WEB_CONVERSIONS",
            "start_time": None,
            "end_time": None,
            "budget": {
                "currency": "USD",
                "daily_native": 100.0,
                "lifetime_native": None,
            },
            "spend_reported": 50.0,
            "spend_currency": "USD",
            "spend_sar_equivalent": 187.5,
            "revenue_reported": 120.0,
            "revenue_sar_equivalent": 450.0,
            "purchases": 2,
            "impressions": 10000,
            "clicks": 200,
            "roas": 2.4,
            "cpa_reported": 25.0,
            "cpc_reported": 0.25,
            "cpm_reported": 5.0,
            "ctr_pct": 2.0,
            "spend_share_pct": 100.0,
            "last_observed_date": "2026-08-03",
            "data_source": "mezan_snapchat_performance_daily_v2",
            "currency_evidence": "account_metadata",
        }
    )

    assert row.provider == "snapchat"
    assert row.campaign_id == "snap-campaign-1"
    assert row.spend_sar_equivalent == 187.5
