from integrations_control_center.snapchat_campaign_result_source_routes import (
    RESULT_SOURCE_PLATFORM,
    RESULT_SOURCE_SALLA,
    _match_order_campaign,
    _selected_metrics,
)


def test_campaign_id_match_does_not_require_source_label():
    key, kind = _match_order_campaign(
        {"utm_campaign": "campaign-123", "source": ""},
        id_lookup={"campaign-123": ("account-1", "campaign-123")},
        name_lookup={},
    )
    assert key == ("account-1", "campaign-123")
    assert kind == "campaign_id"


def test_campaign_name_match_requires_snapchat_source():
    key, kind = _match_order_campaign(
        {"utm_campaign": "حملة الأقحوان", "source": "snapchat"},
        id_lookup={},
        name_lookup={"حملة الأقحوان": ("account-1", "campaign-1")},
    )
    assert key == ("account-1", "campaign-1")
    assert kind == "campaign_name"

    missing, missing_kind = _match_order_campaign(
        {"utm_campaign": "حملة الأقحوان", "source": "meta"},
        id_lookup={},
        name_lookup={"حملة الأقحوان": ("account-1", "campaign-1")},
    )
    assert missing is None
    assert missing_kind == "unmatched"


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
