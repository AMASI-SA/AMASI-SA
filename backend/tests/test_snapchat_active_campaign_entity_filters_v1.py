from integrations_control_center.snapchat_active_campaign_filtering import (
    aggregate_entity_rows,
    is_active_provider_status,
    sort_entity_rows,
)


def test_active_provider_status_is_explicit():
    assert is_active_provider_status("ACTIVE") is True
    assert is_active_provider_status("enabled") is True
    assert is_active_provider_status("PAUSED") is False
    assert is_active_provider_status(None) is False


def test_entity_sorting_happens_globally_before_pagination():
    rows = [
        {"name": "قديم", "orders": 1, "spend_sar": 100, "created_at_provider": "2026-08-01T00:00:00Z"},
        {"name": "طلبات", "orders": 7, "spend_sar": 20, "created_at_provider": "2026-08-02T00:00:00Z"},
        {"name": "صرف", "orders": 2, "spend_sar": 300, "created_at_provider": "2026-08-03T00:00:00Z"},
    ]
    assert [row["name"] for row in sort_entity_rows(rows, "orders", name_field="name")] == ["طلبات", "صرف", "قديم"]
    assert [row["name"] for row in sort_entity_rows(rows, "spend", name_field="name")] == ["صرف", "قديم", "طلبات"]
    assert [row["name"] for row in sort_entity_rows(rows, "newest", name_field="name")] == ["صرف", "طلبات", "قديم"]


def test_filtered_entity_totals_match_visible_rows():
    totals = aggregate_entity_rows([
        {"orders": 2, "spend_sar": 50, "sales_sar": 100, "impressions": 1000, "swipes": 20},
        {"orders": 3, "spend_sar": 70, "sales_sar": 210, "impressions": 2000, "swipes": 30},
    ])
    assert totals["orders"] == 5
    assert totals["spend_sar"] == 120
    assert totals["sales_sar"] == 310
    assert round(totals["roas"], 6) == round(310 / 120, 6)
    assert totals["cpa_sar"] == 24
