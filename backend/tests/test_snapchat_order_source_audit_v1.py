from datetime import datetime, timezone

from integrations_control_center.snapchat_order_source_audit import (
    build_order_audit_rows,
    classify_order_origin,
    platform_purchases_for_audit,
)


def _order(number, **overrides):
    row = {
        "order_number": number,
        "created_at": "2026-08-05T10:00:00+00:00",
        "order_date": "2026-08-05",
        "order_status": "completed",
        "total_amount": 100,
    }
    row.update(overrides)
    return row


def test_classify_order_origin_keeps_gift_separate_from_campaign_match():
    category, label = classify_order_origin({"order_type": "هدية"})
    assert category == "gift"
    assert label == "هدية"


def test_audit_counts_campaign_and_non_campaign_orders_without_distribution():
    identities = [{
        "account_id": "account-1",
        "campaign_id": "campaign-1",
        "campaign_name": "حملة الرياض",
    }]
    orders = [
        _order("1001", campaign_id="campaign-1", total_amount=250),
        _order("1002", source="WhatsApp", total_amount=150),
        _order("1003", order_type="هدية", total_amount=80),
    ]

    result = build_order_audit_rows(
        orders,
        identities=identities,
        timezone_name="Asia/Riyadh",
        date_from="2026-08-05",
        date_to="2026-08-05",
        included_statuses=["completed"],
        platform_attributed_purchases=2,
    )

    summary = result["summary"]
    assert summary["total_salla_created_orders"] == 3
    assert summary["campaign_matched_orders"] == 1
    assert summary["non_campaign_orders"] == 2
    assert summary["platform_attributed_purchases"] == 2
    assert summary["non_campaign_distribution_allowed"] is False
    assert summary["campaign_matched_financial_sales_sar"] == 250
    rows = {row["order_number"]: row for row in result["orders"]}
    assert rows["1001"]["classification"] == "matched"
    assert rows["1001"]["match_method"] == "campaign_id"
    assert rows["1002"]["origin_category"] == "whatsapp"
    assert rows["1002"]["campaign_id"] is None
    assert rows["1003"]["is_gift"] is True
    assert rows["1003"]["classification"] == "non_campaign"


def test_audit_uses_account_timezone_day_boundary():
    orders = [
        _order(
            "2001",
            created_at=datetime(2026, 8, 6, 2, 30, tzinfo=timezone.utc).isoformat(),
            source="direct",
        ),
    ]
    result = build_order_audit_rows(
        orders,
        identities=[],
        timezone_name="America/Los_Angeles",
        date_from="2026-08-05",
        date_to="2026-08-05",
        included_statuses=["completed"],
        platform_attributed_purchases=0,
    )
    assert result["summary"]["total_salla_created_orders"] == 1
    assert result["orders"][0]["local_date"] == "2026-08-05"


def test_platform_purchase_audit_prefers_campaign_rows_over_stale_account_row():
    purchases, source = platform_purchases_for_audit(
        [{"date": "2026-08-05", "purchases": 11}],
        [
            {"date": "2026-08-05", "purchases": 26},
            {"date": "2026-08-05", "purchases": 13},
            {"date": "2026-08-05", "purchases": 9},
        ],
        requested_days=1,
    )
    assert purchases == 48
    assert source == "campaign_rows"


def test_platform_purchase_audit_falls_back_to_account_rows_without_campaign_detail():
    purchases, source = platform_purchases_for_audit(
        [{"date": "2026-08-05", "purchases": 11}],
        [],
        requested_days=1,
    )
    assert purchases == 11
    assert source == "account_rows_fallback"
