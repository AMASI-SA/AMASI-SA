"""Shared overview provenance and financial partial-coverage safeguards."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from ads_manager.google_native_overview import augment_google_overview
from ads_manager.models import ProviderSummary
from integrations_control_center.google_ads_reporting import GOOGLE_ADS_DAILY_COLLECTION, GOOGLE_ADS_REPORTING_SOURCE_MODE
from integrations_control_center.google_ads_workspace import FACTS, FACTSETS


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    async def to_list(self, *, length):
        return deepcopy(self.rows[:length])


class Collection:
    def __init__(self, rows=()):
        self.rows, self.scopes = list(rows), []
    def find(self, scope, projection):
        self.scopes.append(scope)
        return Cursor(self.rows)
    async def find_one(self, scope, projection):
        self.scopes.append(scope)
        return deepcopy(self.rows[0]) if self.rows else None


class DB:
    def __init__(self, *, timezone_name="Asia/Riyadh", proof=True, spend="10"):
        self.mezan_integration_accounts_v2 = Collection([{"ad_account_id": "1234567890", "mezan_selected": True}])
        self.mezan_integrations_v2 = Collection([{"connection_status": "connected"}])
        self.collections = {GOOGLE_ADS_DAILY_COLLECTION: Collection([{"ad_account_id": "1234567890", "date": "2026-10-13", "account_timezone": timezone_name, "spend_sar": spend, "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
            "source_close_proof": {"complete": proof, "account_id": "1234567890", "business_date": "2026-10-13"}}]), FACTS: Collection([]), FACTSETS: Collection([])}
    def __getitem__(self, name):
        return self.collections[name]


def overview():
    return {"range": {"date_from": "2026-10-13", "date_to": "2026-10-13", "provider": "all"}, "providers": [], "campaigns": [], "metrics": {"provider_reported_spend_sar": 30, "platform_roas": 4}, "coverage": {"provider_spend_is_partial": False}, "daily_spend": [{"date": "2026-10-13", "snapchat": 30}], "sources": [], "insights": []}


@pytest.mark.asyncio
async def test_google_joins_same_dashboard_source_only_with_proven_riyadh_days():
    db = DB()
    result = await augment_google_overview(db, "owner-a", overview())
    assert result["metrics"]["provider_reported_spend_sar"] == 40
    assert result["daily_spend"][0]["google"] == 10
    assert result["metrics"]["platform_roas"] is None
    assert result["coverage"]["revenue_is_partial"] is True
    summary = ProviderSummary.model_validate(result["providers"][0])
    assert summary.provider == "google" and summary.metric_availability["purchases"] is False
    assert summary.metrics.platform_attributed_revenue_sar is None
    for collection in (db.mezan_integration_accounts_v2, db.mezan_integrations_v2, db[GOOGLE_ADS_DAILY_COLLECTION], db[FACTSETS]):
        assert all(scope["user_id"] == "owner-a" for scope in collection.scopes)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"timezone_name": "America/New_York"}, {"proof": False}, {"spend": None}])
async def test_unaligned_or_unproved_google_amount_cannot_be_zero_or_join_sar_total(changes):
    result = await augment_google_overview(DB(**changes), "owner-a", overview())
    assert result["metrics"]["provider_reported_spend_sar"] == 30
    assert result["daily_spend"][0]["google"] is None
    assert result["providers"][0]["metrics"]["provider_reported_spend_sar"] is None
    assert result["coverage"]["provider_spend_is_partial"] is True


@pytest.mark.asyncio
async def test_google_filter_isolated_from_other_provider_metrics_and_controls():
    result = await augment_google_overview(DB(), "owner-a", overview(), provider="google")
    assert result["metrics"]["provider_reported_spend_sar"] == 10
    assert result["range"]["provider"] == "google"
    assert result["daily_spend"][0]["snapchat"] is None
    assert [row["provider"] for row in result["providers"]] == ["google"]
