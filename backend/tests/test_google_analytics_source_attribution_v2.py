from datetime import date

from integrations_control_center.google_analytics_source_attribution_routes import (
    compose_source_period,
    normalize_session_source,
)


def _payload(rows):
    return {
        "dimensionHeaders": [{"name": "sessionSource"}],
        "metricHeaders": [
            {"name": "sessions"},
            {"name": "activeUsers"},
            {"name": "transactions"},
            {"name": "purchaseRevenue"},
        ],
        "rows": [
            {
                "dimensionValues": [{"value": source}],
                "metricValues": [
                    {"value": str(sessions)},
                    {"value": str(users)},
                    {"value": str(orders)},
                    {"value": str(revenue)},
                ],
            }
            for source, sessions, users, orders, revenue in rows
        ],
    }


def test_normalizes_known_platform_sources():
    assert normalize_session_source("snapchat")[:2] == ("snapchat", "Snapchat")
    assert normalize_session_source("snapchat.com")[:2] == ("snapchat", "Snapchat")
    assert normalize_session_source("tiktok")[:2] == ("tiktok", "TikTok")
    assert normalize_session_source("fb")[:2] == ("meta", "Meta / Instagram")
    assert normalize_session_source("instagram.com")[:2] == (
        "meta",
        "Meta / Instagram",
    )
    assert normalize_session_source("google")[:2] == ("google", "Google")
    assert normalize_session_source("(direct)")[:2] == ("direct", "Direct")


def test_aggregates_sessions_orders_and_revenue_by_source():
    result = compose_source_period(
        _payload(
            [
                ("snapchat", 21, 18, 4, 850.50),
                ("snapchat.com", 1, 1, 1, 210.00),
                ("tiktok", 9, 8, 2, 420.00),
                ("fb", 5, 4, 1, 190.00),
                ("google", 2, 2, 0, 0),
                ("(direct)", 5, 5, 1, 150.00),
            ]
        ),
        start_date=date(2026, 7, 31),
        end_date=date(2026, 7, 31),
    )

    snapchat = result["platforms"]["snapchat"]
    assert snapchat["sessions"] == 22
    assert snapchat["active_users"] == 19
    assert snapchat["orders"] == 5
    assert snapchat["purchase_revenue"] == 1060.50
    assert snapchat["raw_sources"] == ["snapchat", "snapchat.com"]

    assert result["platforms"]["tiktok"]["orders"] == 2
    assert result["platforms"]["meta"]["orders"] == 1
    assert result["sessions"] == 43
    assert result["orders"] == 9
    assert result["purchase_revenue"] == 1820.50


def test_zero_priority_platforms_remain_visible_for_stable_table():
    result = compose_source_period(
        _payload([("snapchat", 3, 3, 0, 0)]),
        start_date=date(2026, 7, 31),
        end_date=date(2026, 7, 31),
    )
    assert result["platforms"]["tiktok"]["sessions"] == 0
    assert result["platforms"]["meta"]["orders"] == 0
    assert result["platforms"]["google"]["purchase_revenue"] == 0
