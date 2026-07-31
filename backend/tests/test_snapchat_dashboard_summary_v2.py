from datetime import date

from integrations_control_center.snapchat_dashboard_summary_routes import (
    summarize_snapchat_dashboard_rows,
)


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
        selected_accounts=[
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
        ],
        snapshot={"connection_status": "connected"},
        today=date(2026, 7, 31),
    )

    assert result["connection_status"] == "ok"
    assert result["source"] == "snapchat_v2"
    assert result["today"]["spend"] == 125.0
    assert result["today"]["orders"] == 3
    assert result["today"]["revenue"] == 600.0
    assert result["today"]["roas"] == 4.8
    assert result["selected_account_count"] == 2
    assert result["accounts"][0]["today"]["spend_sar"] == 100.0
    assert result["source_only"] is True
    assert result["accounting_write_reached"] is False
