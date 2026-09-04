import pytest

from integrations_control_center.snapchat_campaign_result_source_routes import (
    RESULT_SOURCE_PLATFORM,
    RESULT_SOURCE_SALLA,
    _match_order_campaign,
    _salla_outcomes,
    _selected_metrics,
)


def test_literal_utm_campaign_id_match_does_not_require_source_label():
    key, kind = _match_order_campaign(
        {"utm_campaign_id": "campaign-123", "source": ""},
        id_lookup={"campaign-123": ("account-1", "campaign-123")},
        name_lookup={},
    )
    assert key == ("account-1", "campaign-123")
    assert kind == "campaign_id"


def test_campaign_name_never_substitutes_for_literal_utm_id():
    key, kind = _match_order_campaign(
        {"utm_campaign": "حملة الأقحوان", "source": "snapchat"},
        id_lookup={},
        name_lookup={"حملة الأقحوان": ("account-1", "campaign-1")},
    )
    assert key is None
    assert kind == "unmatched"

    missing, missing_kind = _match_order_campaign(
        {"utm_campaign": "حملة الأقحوان", "source": "meta"},
        id_lookup={},
        name_lookup={"حملة الأقحوان": ("account-1", "campaign-1")},
    )
    assert missing is None
    assert missing_kind == "unmatched"


@pytest.mark.asyncio
async def test_salla_headline_totals_include_unattributed_snapchat_orders(monkeypatch):
    import dashboard_v2_routes

    async def fake_filtered_orders(*args, **kwargs):
        return [
            {
                "order_number": "1",
                "order_date": "2026-08-03",
                "source": "",
                "utm_campaign_id": "campaign-1",
                "total_amount": 100.0,
            },
            {
                "order_number": "2",
                "order_date": "2026-08-03",
                "source": "snapchat",
                "utm_campaign": "campaign-not-known",
                "total_amount": 200.0,
            },
            {
                "order_number": "3",
                "order_date": "2026-08-03",
                "source": "meta",
                "utm_campaign": "campaign-not-known",
                "total_amount": 300.0,
            },
        ]

    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", fake_filtered_orders)
    by_campaign, by_account, by_date, coverage = await _salla_outcomes(
        object(),
        "owner-1",
        date_from="2026-08-03",
        date_to="2026-08-03",
        identities=[{
            "account_id": "account-1",
            "campaign_id": "campaign-1",
            "campaign_name": "حملة 1",
        }],
    )

    assert by_campaign[("account-1", "campaign-1")] == {
        "orders": 1,
        "sales_sar": 100.0,
    }
    assert by_account["account-1"] == {"orders": 1, "sales_sar": 100.0}
    assert by_date["2026-08-03"] == {"orders": 2, "sales_sar": 300.0}
    assert coverage["salla_snapchat_orders"] == 2
    assert coverage["salla_snapchat_sales_sar"] == 300.0
    assert coverage["matched_orders"] == 1
    assert coverage["unattributed_snapchat_orders"] == 1
    assert coverage["campaign_rows_exact_match_only"] is True
    assert coverage["headline_includes_unattributed_snapchat"] is True


def test_salla_source_uses_salla_orders_and_sales_with_native_currency():
    selected = _selected_metrics(
        result_source=RESULT_SOURCE_SALLA,
        platform={"orders": 2, "sales_sar": 300.0, "sales_native": 80.0},
        salla={"orders": 5, "sales_sar": 750.0},
        spend_sar=375.0,
        spend_native=100.0,
        rate=3.75,
    )
    assert selected["orders"] == 5
    assert selected["sales_sar"] == 750.0
    assert selected["sales_native"] == 200.0
    assert selected["roas"] == 2.0
    assert selected["cpa_sar"] == 75.0
    assert selected["cpa_native"] == 20.0


def test_platform_source_preserves_provider_results():
    selected = _selected_metrics(
        result_source=RESULT_SOURCE_PLATFORM,
        platform={"orders": 2, "sales_sar": 300.0, "sales_native": 80.0},
        salla={"orders": 5, "sales_sar": 750.0},
        spend_sar=375.0,
        spend_native=100.0,
        rate=3.75,
    )
    assert selected["orders"] == 2
    assert selected["sales_sar"] == 300.0
    assert selected["sales_native"] == 80.0
    assert selected["roas"] == 0.8
    assert selected["cpa_sar"] == 187.5
    assert selected["cpa_native"] == 50.0


def test_platform_source_backfills_native_sales_when_old_row_lacks_it():
    selected = _selected_metrics(
        result_source=RESULT_SOURCE_PLATFORM,
        platform={"orders": 2, "sales_sar": 300.0, "sales_native": None},
        salla={"orders": 5, "sales_sar": 750.0},
        spend_sar=375.0,
        spend_native=100.0,
        rate=3.75,
    )
    assert selected["sales_native"] == 80.0
