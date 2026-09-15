from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integrations_control_center.snapchat_entity_settings import (
    list_financial_management_settings,
)
from integrations_control_center.snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.requested_limit = None

    def limit(self, value):
        self.requested_limit = value
        return self

    def sort(self, *_args):
        return self

    async def to_list(self, length=None):
        return list(self.rows[:length])


class Collection:
    def __init__(self, *, find_rows=None, aggregate_rows=None):
        self.find_rows = list(find_rows or [])
        self.aggregate_rows = list(aggregate_rows or [])
        self.find_calls = []
        self.aggregate_calls = []

    def find(self, query, projection):
        cursor = Cursor(self.find_rows)
        self.find_calls.append((query, projection, cursor))
        return cursor

    def aggregate(self, pipeline, **kwargs):
        self.aggregate_calls.append((pipeline, kwargs))
        return Cursor(self.aggregate_rows)


class DB(dict):
    pass


@pytest.mark.asyncio
async def test_visible_campaign_settings_use_exact_ids_and_scalar_children_only():
    campaign_ids = [f"campaign-{index:05d}" for index in range(25)]
    observed = datetime(2026, 9, 5, 12, 3, tzinfo=timezone.utc)
    campaigns = [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "ad_account_id": "account-1",
            "entity_type": "campaign",
            "external_id": campaign_id,
            "display_name": campaign_id,
            "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
            "last_observed_at": observed,
            "provider_snapshot": {"id": campaign_id},
        }
        for campaign_id in campaign_ids
    ]
    entities = Collection(
        find_rows=campaigns,
        aggregate_rows=[
            {
                "_id": {"account_id": "account-1", "campaign_id": campaign_id},
                "ad_squad_count": 400,
                "budget_count": 400,
                "daily_budget_sum_micro": 400_000_000,
                "status_count": 400,
                "active_count": 375,
                "strategy_count": 400,
                "strategies": ["TARGET_COST"],
                "mapping_valid_count": 400,
                "oldest_observed_at": observed,
            }
            for campaign_id in campaign_ids
        ],
    )
    accounts = Collection(find_rows=[{
        "external_account_id": "account-1",
        "ad_account_id": "account-1",
        "currency": "USD",
    }])
    runs = Collection(find_rows=[{
        "run_id": "run-1",
        "status": "complete",
        "started_at": datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 9, 5, 12, 5, tzinfo=timezone.utc),
    }])
    db = DB({
        SNAPCHAT_ENTITY_COLLECTION: entities,
        "mezan_integration_accounts_v2": accounts,
        "mezan_integration_sync_runs_v2": runs,
    })

    result = await list_financial_management_settings(
        db,
        "owner-1",
        "campaign",
        unified_entity_ids=campaign_ids,
        limit=25,
        now=datetime(2026, 9, 5, 12, 10, tzinfo=timezone.utc),
    )

    entity_query, _projection, entity_cursor = entities.find_calls[0]
    assert entity_query["external_id"] == {"$in": campaign_ids}
    assert entity_cursor.requested_limit == 26
    assert result["requested_entity_ids"] == campaign_ids
    assert result["settings_rows_materialized"] == 25
    assert result["child_rows_materialized"] == 0
    assert len(result["items"]) == 25

    assert len(entities.aggregate_calls) == 1
    child_pipeline = entities.aggregate_calls[0][0]
    assert child_pipeline[0]["$match"]["campaign_id"] == {"$in": campaign_ids}
    assert child_pipeline[0]["$match"]["entity_type"] == "ad_squad"
    assert child_pipeline[-1] == {"$limit": 100}
    assert all(
        item["campaign_aggregate"]["catalog_coverage"]["python_child_rows_materialized"] == 0
        for item in result["items"]
    )


@pytest.mark.asyncio
async def test_visible_settings_batch_rejects_more_than_one_hundred_ids():
    with pytest.raises(ValueError, match="cannot exceed 100"):
        await list_financial_management_settings(
            DB(),
            "owner-1",
            "campaign",
            unified_entity_ids=[f"campaign-{index}" for index in range(101)],
            limit=100,
        )
