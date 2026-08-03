from dashboard_v2_ad_costs import (
    apply_cost_settings_to_fact_rows,
    bank_commission_payment_breakdown,
    merge_ad_bank_fees_into_dashboard,
)


def test_native_spend_uses_per_account_rate_and_bank_fee_once():
    rows = {
        "snapchat": [{
            "ad_account_id": "snap-usd",
            "spend_native": 1000,
            "spend_sar": 3750,
            "currency": "USD",
        }],
        "meta": [{
            "ad_account_id": "meta-sar",
            "spend_native": 500,
            "spend_sar": 500,
            "currency_native": "SAR",
        }],
        "tiktok": [],
    }
    accounts = [
        {
            "provider": "snapchat_ads",
            "external_account_id": "snap-usd",
            "mezan_integration_account_id": "m-snap",
            "display_name": "أماسي سناب",
            "currency": "USD",
        },
        {
            "provider": "meta_ads",
            "external_account_id": "meta-sar",
            "mezan_integration_account_id": "m-meta",
            "display_name": "أماسي ميتا",
            "currency": "SAR",
        },
    ]
    settings = [{
        "provider": "snapchat_ads",
        "external_account_id": "snap-usd",
        "mezan_integration_account_id": "m-snap",
        "native_currency": "USD",
        "exchange_rate_to_sar": 3.7544,
        "bank_commission_pct": 2.3,
        "apply_bank_commission": True,
    }]

    result = apply_cost_settings_to_fact_rows(rows, accounts, settings)

    snap = result["platform_rows"]["snapchat"][0]
    meta = result["platform_rows"]["meta"][0]
    assert snap["effective_spend_sar"] == 3754.4
    assert meta["effective_spend_sar"] == 500
    assert result["total_effective_spend_sar"] == 4254.4
    assert result["total_fee_sar"] == 86.35
    assert result["coverage"]["legacy_ads_currency_settings_read"] is False

    entry = bank_commission_payment_breakdown(result)
    assert entry["key"] == "ad_bank_commissions"
    assert entry["fee_amount"] == 86.35
    assert len(entry["sub_methods"]) == 2

    response = {
        "totals": {
            "total_payment_fees": 110,
            "net_profit": 1000,
            "net_sales": 1200,
        },
        "net_sales_config": {"deduct_payment_fees": True},
        "payment_breakdown": [{"key": "tamara", "name": "تمارا", "fee_amount": 110}],
    }
    merge_ad_bank_fees_into_dashboard(response, {"bank_commissions": result})
    assert response["totals"]["ad_bank_commission_fees"] == 86.35
    assert response["totals"]["total_payment_fees"] == 196.35
    assert response["totals"]["net_profit"] == 913.65
    assert response["totals"]["net_sales"] == 1113.65
    assert [row["key"] for row in response["payment_breakdown"]] == [
        "tamara",
        "ad_bank_commissions",
    ]


def test_snapchat_defaults_apply_before_account_is_explicitly_saved():
    result = apply_cost_settings_to_fact_rows(
        {
            "snapchat": [{
                "ad_account_id": "snap-default",
                "spend_native": 100,
                "currency": "USD",
            }],
            "meta": [],
            "tiktok": [],
        },
        [{
            "provider": "snapchat_ads",
            "external_account_id": "snap-default",
            "mezan_integration_account_id": "m-default",
            "display_name": "سناب افتراضي",
            "currency": "USD",
        }],
        [],
    )
    account = result["accounts"][0]
    assert account["configured"] is False
    assert account["exchange_rate_to_sar"] == 3.7544
    assert account["bank_commission_pct"] == 2.3
    assert account["spend_sar"] == 375.44
    assert account["bank_commission_fee_sar"] == 8.64
