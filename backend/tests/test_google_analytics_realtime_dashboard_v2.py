from integrations_control_center.google_analytics_realtime_routes import (
    DEFAULT_AMASI_GA4_PROPERTY_ID,
    compose_ga4_realtime_payload,
    ga4_property_id,
)


def _payload(dimensions, metrics, rows):
    return {
        "dimensionHeaders": [{"name": name} for name in dimensions],
        "metricHeaders": [{"name": name} for name in metrics],
        "rows": [
            {
                "dimensionValues": [{"value": value} for value in dimension_values],
                "metricValues": [{"value": str(value)} for value in metric_values],
            }
            for dimension_values, metric_values in rows
        ],
    }


def test_amasi_ga4_property_id_ends_with_93(monkeypatch):
    monkeypatch.delenv("GOOGLE_ANALYTICS_PROPERTY_ID", raising=False)
    assert DEFAULT_AMASI_GA4_PROPERTY_ID == "353865193"
    assert ga4_property_id() == "353865193"


def test_property_id_can_be_explicitly_overridden(monkeypatch):
    monkeypatch.setenv("GOOGLE_ANALYTICS_PROPERTY_ID", "353865193")
    assert ga4_property_id() == "353865193"


def test_realtime_payload_builds_three_dashboard_cards():
    result = compose_ga4_realtime_payload(
        property_id="353865193",
        property_name="اماسي - إحصاءات Google 4",
        pages_payload=_payload(
            ["unifiedScreenName"],
            ["screenPageViews"],
            [
                (["عناية صيفية لسن المحير | متجر أماسي"], [33]),
                (["شنط كوتش تابي | متجر أماسي"], [22]),
                (["(not set)"], [99]),
            ],
        ),
        active_30_payload=_payload([], ["activeUsers"], [([], [122])]),
        active_5_payload=_payload([], ["activeUsers"], [([], [12])]),
        minute_payload=_payload(
            ["minutesAgo"],
            ["activeUsers"],
            [(["00"], [7]), (["01"], [4]), (["29"], [3])],
        ),
        events_payload=_payload(
            ["eventName"],
            ["keyEvents"],
            [(["add_to_cart"], [2]), (["page_view"], [0])],
        ),
        observed_at="2026-07-31T20:00:00+00:00",
    )

    assert result["property_id"] == "353865193"
    assert result["active_users"]["last_30_minutes"] == 122
    assert result["active_users"]["last_5_minutes"] == 12
    assert len(result["active_users"]["per_minute"]) == 30
    assert result["active_users"]["per_minute"][0] == {
        "minutes_ago": 29,
        "active_users": 3,
    }
    assert result["active_users"]["per_minute"][-1] == {
        "minutes_ago": 0,
        "active_users": 7,
    }
    assert result["top_pages"] == [
        {"title": "عناية صيفية لسن المحير | متجر أماسي", "views": 33},
        {"title": "شنط كوتش تابي | متجر أماسي", "views": 22},
    ]
    assert result["key_events"] == [
        {"event_name": "add_to_cart", "count": 2}
    ]
    assert result["source_only"] is True
    assert result["provider_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False
