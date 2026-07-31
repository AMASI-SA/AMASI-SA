import asyncio
from datetime import date, datetime, timezone

from integrations_control_center import dashboard_authoritative_summary_routes as module


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=None):
        return list(self.rows[:length]) if length else list(self.rows)


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def find(self, query, projection=None):
        self.queries.append((query, projection))
        return FakeCursor(self.rows)


class FakeDb:
    def __init__(self):
        self.meta = FakeCollection([
            {"spend_sar": 50.0},
            {"spend_sar": 25.0},
        ])
        self.daily_costs = FakeCollection([
            {
                "tiktok_ads": 20.0,
                "google_ads": 5.0,
                # Must never be included by the V2 summary.
                "snapchat_ads": 999.0,
                "snapchat_ads_2": 888.0,
                "instagram_ads": 777.0,
            }
        ])

    def __getitem__(self, name):
        assert name == module.META_REPORTING_COLLECTION
        return self.meta


def test_authoritative_summary_uses_selected_v2_sources_only(monkeypatch):
    async def fake_snapchat(*args, **kwargs):
        return {
            "spend_sar": 100.0,
            "rows_included": 2,
            "selected_account_count": 2,
        }

    async def fake_meta_selection(*args, **kwargs):
        return {
            "accounts": [
                {"account_id": "act_selected", "selected": True},
                {"account_id": "act_other", "selected": False},
            ]
        }

    monkeypatch.setattr(module, "selected_snapchat_performance_summary", fake_snapchat)
    monkeypatch.setattr(module, "get_meta_account_selection", fake_meta_selection)

    result = asyncio.run(module.build_dashboard_authoritative_summary(
        FakeDb(),
        "owner-1",
        from_date="2026-07-31",
        to_date="2026-07-31",
        now=lambda: datetime(2026, 7, 31, 18, tzinfo=timezone.utc),
    ))

    assert result["total_ads_cost"] == 200.0
    assert result["breakdown"] == {
        "snapchat_v2": 100.0,
        "meta_v2": 75.0,
        "tiktok_transitional": 20.0,
        "google_transitional": 5.0,
    }
    assert "daily_costs.snapchat_ads" in result["excluded_legacy_fields"]
    assert "daily_costs.instagram_ads" in result["excluded_legacy_fields"]
    assert result["source_only"] is True
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False


def test_range_defaults_to_riyadh_today():
    today = date(2026, 7, 31)
    start, end = module._parse_range(None, None, today=today)
    assert start == today
    assert end == today
