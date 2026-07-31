from datetime import date

from integrations_control_center.snapchat_dashboard_summary_routes import (
    summarize_snapchat_dashboard_rows,
)


def selected_accounts():
    return [
        {
            "ad_account_id": "snap-1",
            "display_name": "أماسي USD",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
        },
        {
            "ad_account_id": "snap-2",
            "display_name": "أماسي SAR",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
        },
    ]


def test_snapchat_dashboard_summary_uses_selected_account_rows():
    rows = [
        {
            "ad_account_id": "snap-1",
            "date": "2026-07-31",
            "spend_sar": 100.0,
            "spend_native": 26.666667,
            "purchases": 2,
            "purchase_value_sar": 500.0,
            "observed_at": "2026-07-31T18:00:00+00:00",
        },
        {
            "ad_account_id": "snap-2",
            "date": "2026-07-31",
            "spend_sar": 25.0,
            "spend_native": 25.0,
            "purchases": 1,
            "purchase_value_sar": 100.0,
            "observed_at": "2026-07-31T18:01:00+00:00",
        },
    ]
    result = summarize_snapchat_dashboard_rows(
        rows,
        selected_accounts=selected_accounts(),
        snapshot={"connection_status": "connected"},
        today=date(2026, 7, 31),
    )

    assert result["connection_status"] == "ok"
    assert result["source"] == "snapchat_v2"
    assert result["today"]["spend"] == 125.0
    assert result["today"]["orders"] == 3
    assert result["today"]["orders_raw"] == 3.0
    assert result["today"]["revenue"] == 600.0
    assert result["today"]["roas"] == 4.8
    assert result["selected_account_count"] == 2
    assert result["accounts"][0]["today"]["spend_sar"] == 100.0
    assert result["conversion_reporting"] == {
        "metric": "conversion_purchases",
        "source_types": ["total"],
        "action_report_time": "conversion",
        "swipe_up_attribution_window": "28_DAY",
        "view_attribution_window": "1_DAY",
        "today_is_provisional": True,
    }
    assert result["source_only"] is True
    assert result["accounting_write_reached"] is False


def test_snapchat_dashboard_prefers_top_level_provider_purchase_count():
    result = summarize_snapchat_dashboard_rows(
        [
            {
                "ad_account_id": "snap-1",
                "date": "2026-07-31",
                "spend_sar": 100,
                "purchases": 8,
                "metrics": {"conversion_purchases": 3},
                "purchase_value_sar": 600,
            }
        ],
        selected_accounts=selected_accounts(),
        snapshot={"connection_status": "connected"},
        today=date(2026, 7, 31),
    )

    assert result["today"]["orders"] == 8
    assert result["today"]["orders_raw"] == 8.0


def test_modeled_half_purchase_uses_half_up_not_bankers_rounding():
    result = summarize_snapchat_dashboard_rows(
        [
            {
                "ad_account_id": "snap-1",
                "date": "2026-07-31",
                "spend_sar": 100,
                "purchases": 6.5,
                "purchase_value_sar": 600,
            }
        ],
        selected_accounts=selected_accounts(),
        snapshot={"connection_status": "connected"},
        today=date(2026, 7, 31),
    )

    assert result["today"]["orders_raw"] == 6.5
    assert result["today"]["orders"] == 7
    assert result["today"]["cost_per_order"] == 15.38
