from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from integrations_control_center.google_ads_account_selection import (
    GoogleAdsAccountSelectionInput,
)
from integrations_control_center.google_ads_native_reporting import (
    GOOGLE_ADS_REPORTING_COLLECTION,
    GoogleAdsReportingSyncInput,
    _gaql,
    _normalized_row,
    google_ads_reporting_enabled,
    run_google_ads_reporting_sync,
)


def test_google_ads_account_ids_are_canonicalized():
    payload = GoogleAdsAccountSelectionInput(
        account_ids=["123-456-7890", "1234567890", "098-765-4321"]
    )
    assert payload.account_ids == ["1234567890", "0987654321"]


def test_gaql_is_bounded_and_read_only():
    query = _gaql(date(2026, 8, 1), date(2026, 8, 2))
    assert "FROM campaign" in query
    assert "metrics.cost_micros" in query
    assert "metrics.conversions_value" in query
    assert "segments.date BETWEEN '2026-08-01' AND '2026-08-02'" in query
    assert "campaign.status != 'REMOVED'" in query
    assert "MUTATE" not in query.upper()


def test_normalized_google_ads_row_converts_sar_micros():
    row = _normalized_row(
        {
            "segments": {"date": "2026-08-01"},
            "customer": {
                "id": "1234567890",
                "descriptiveName": "Amasi",
                "currencyCode": "SAR",
                "timeZone": "Asia/Riyadh",
            },
            "campaign": {"id": "77", "name": "Search", "status": "ENABLED"},
            "metrics": {
                "costMicros": "12500000",
                "impressions": "1000",
                "clicks": "25",
                "conversions": 3.5,
                "conversionsValue": 700,
            },
        },
        fallback_customer_id="1234567890",
        observed_at="2026-08-02T00:00:00+00:00",
        request_id="req-1",
    )
    assert row is not None
    assert row["spend_native"] == 12.5
    assert row["spend_sar"] == 12.5
    assert row["purchase_value_sar"] == 700
    assert row["conversions"] == 3.5
    assert row["source_only"] is True
    assert row["accounting_eligible"] is False


def test_unknown_currency_never_invents_sar(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_USD_TO_SAR_RATE", raising=False)
    row = _normalized_row(
        {
            "segments": {"date": "2026-08-01"},
            "customer": {"id": "123", "currencyCode": "EUR"},
            "campaign": {"id": "1", "name": "EU"},
            "metrics": {"costMicros": 1000000, "conversionsValue": 2},
        },
        fallback_customer_id="123",
        observed_at="2026-08-02T00:00:00+00:00",
        request_id=None,
    )
    assert row["spend_native"] == 1
    assert row["spend_sar"] is None
    assert row["purchase_value_sar"] is None


def test_reporting_flag_is_fail_closed(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_NATIVE_REPORTING_SYNC_ENABLED", raising=False)
    assert google_ads_reporting_enabled() is False
    monkeypatch.setenv("GOOGLE_ADS_NATIVE_REPORTING_SYNC_ENABLED", "true")
    assert google_ads_reporting_enabled() is True


class FakeCollection:
    def __init__(self):
        self.updates = []

    async def create_index(self, *args, **kwargs):
        return "ok"

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))
        return type("Result", (), {"modified_count": 1})()


class FakeDB:
    def __init__(self):
        self.collections = {}
        self.mezan_integration_accounts_v2 = FakeCollection()
        self.mezan_integrations_v2 = FakeCollection()

    def __getitem__(self, name):
        self.collections.setdefault(name, FakeCollection())
        return self.collections[name]


@pytest.mark.asyncio
async def test_sync_writes_only_google_v2_facts(monkeypatch):
    from integrations_control_center import google_ads_native_reporting as reporting

    db = FakeDB()
    monkeypatch.setattr(reporting, "google_ads_reporting_missing_configuration", lambda: [])
    monkeypatch.setattr(reporting, "google_ads_reporting_enabled", lambda: True)

    async def fake_credential(db_arg, user_id, *, now):
        assert db_arg is db
        return "access"

    async def fake_accounts(db_arg, user_id):
        return [
            {
                "ad_account_id": "1234567890",
                "external_account_id": "1234567890",
                "display_name": "Amasi",
                "currency": "SAR",
                "timezone": "Asia/Riyadh",
            }
        ]

    async def fake_fetch(client, *, access_token, customer_id, start, end):
        return [
            {
                "segments": {"date": "2026-08-01"},
                "customer": {
                    "id": customer_id,
                    "descriptiveName": "Amasi",
                    "currencyCode": "SAR",
                    "timeZone": "Asia/Riyadh",
                },
                "campaign": {"id": "99", "name": "Campaign"},
                "metrics": {
                    "costMicros": 5000000,
                    "impressions": 100,
                    "clicks": 5,
                    "conversions": 1,
                    "conversionsValue": 250,
                },
            }
        ], "request-1"

    monkeypatch.setattr(reporting, "_credential", fake_credential)
    monkeypatch.setattr(reporting, "load_selected_google_ads_accounts", fake_accounts)
    monkeypatch.setattr(reporting, "_fetch_rows", fake_fetch)

    result = await run_google_ads_reporting_sync(
        db,
        "owner-1",
        GoogleAdsReportingSyncInput(
            from_date="2026-08-01",
            to_date="2026-08-01",
        ),
        now=lambda: datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "complete"
    assert result["rows_saved"] == 1
    assert result["provider_write_reached"] is False
    assert result["campaign_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False
    fact_updates = db.collections[GOOGLE_ADS_REPORTING_COLLECTION].updates
    assert len(fact_updates) == 1
    saved = fact_updates[0][1]["$set"]
    assert saved["spend_sar"] == 5
    assert saved["purchase_value_sar"] == 250
    assert saved["source_only"] is True
    assert set(db.collections) == {GOOGLE_ADS_REPORTING_COLLECTION}
