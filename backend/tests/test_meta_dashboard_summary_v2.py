from datetime import date

from integrations_control_center.meta_dashboard_summary_routes import (
    summarize_meta_dashboard_rows,
)


def test_meta_dashboard_summary_aggregates_selected_v2_rows():
    rows = [
        {
            "date": "2026-07-31",
            "spend_sar": 120.25,
            "purchases": 2,
            "purchase_value_sar": 510.0,
            "observed_at": "2026-07-31T17:00:00+00:00",
        },
        {
            "date": "2026-07-31",
            "spend_sar": 29.75,
            "purchases": 1,
            "purchase_value_sar": 90.0,
            "observed_at": "2026-07-31T17:05:00+00:00",
        },
        {
            "date": "2026-07-30",
            "spend_sar": 50.0,
            "purchases": 1,
            "purchase_value_sar": 125.0,
            "observed_at": "2026-07-30T20:00:00+00:00",
        },
    ]

    result = summarize_meta_dashboard_rows(
        rows,
        selected_count=1,
        snapshot={"connection_status": "connected"},
        today=date(2026, 7, 31),
    )

    assert result["connection_status"] == "ok"
    assert result["today"] == {
        "date": "2026-07-31",
        "spend": 150.0,
        "orders": 3,
        "revenue": 600.0,
        "roas": 4.0,
        "cost_per_order": 50.0,
    }
    assert result["month"]["spend"] == 200.0
    assert result["month"]["orders"] == 4
    assert result["month"]["revenue"] == 725.0
    assert result["last_30d"]["spend"] == 200.0
    assert len(result["history"]) == 30
    assert result["history"][-1] == {"date": "2026-07-31", "spend": 150.0}
    assert result["last_sync_at"] == "2026-07-31T17:05:00+00:00"
    assert result["source_only"] is True
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False


def test_meta_dashboard_summary_maps_v2_reauth_and_selection_states():
    expired = summarize_meta_dashboard_rows(
        [],
        selected_count=1,
        snapshot={"connection_status": "needs_reauth"},
        latest_error={"message": "token expired"},
        today=date(2026, 7, 31),
    )
    assert expired["connection_status"] == "expired"
    assert expired["last_error_message"] == "token expired"

    needs_selection = summarize_meta_dashboard_rows(
        [],
        selected_count=0,
        snapshot={"connection_status": "connected"},
        today=date(2026, 7, 31),
    )
    assert needs_selection["connection_status"] == "needs_selection"
    assert "اختر حساب Meta" in needs_selection["last_error_message"]
