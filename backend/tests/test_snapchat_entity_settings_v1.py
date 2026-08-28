from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect

import pytest
from fastapi import APIRouter

from integrations_control_center import snapchat_entity_settings as module


CAMPAIGN_ID = "da5049b7-5417-4be9-a596-20a74f9fd54c"
AD_SQUAD_ID = "7c0f5bfa-3f59-437b-bb89-1c70b11d0526"
ACCOUNT_ID = "provider-account-usd"
USER_ID = "owner-1"
NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            continue
        if actual != expected:
            return False
    return True


def _project(row, projection):
    if not projection:
        return deepcopy(row)
    included = {
        key for key, enabled in projection.items()
        if enabled and key != "_id"
    }
    if included:
        return {
            key: deepcopy(value)
            for key, value in row.items()
            if key in included
        }
    output = deepcopy(row)
    if projection.get("_id") == 0:
        output.pop("_id", None)
    return output


class Cursor:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]
        self._limit = None

    def sort(self, key, direction):
        self.rows.sort(
            key=lambda row: str(row.get(key) or ""),
            reverse=direction < 0,
        )
        return self

    def limit(self, value):
        self._limit = value
        return self

    async def to_list(self, length):
        limit = min(length, self._limit or length)
        return deepcopy(self.rows[:limit])


class Collection:
    def __init__(self, rows=None, write_log=None):
        self.rows = [deepcopy(row) for row in (rows or [])]
        self.write_log = write_log if write_log is not None else []

    def find(self, query, projection=None):
        return Cursor(
            _project(row, projection)
            for row in self.rows
            if _matches(row, query)
        )

    async def find_one(self, query, projection=None, **kwargs):
        for row in self.rows:
            if _matches(row, query):
                return _project(row, projection)
        return None

    async def update_one(self, *args, **kwargs):
        self.write_log.append(("update_one", args, kwargs))
        raise AssertionError("settings GET must not write")

    async def insert_one(self, *args, **kwargs):
        self.write_log.append(("insert_one", args, kwargs))
        raise AssertionError("settings GET must not write")

    async def delete_one(self, *args, **kwargs):
        self.write_log.append(("delete_one", args, kwargs))
        raise AssertionError("settings GET must not write")


class DB:
    def __init__(self, *, entities=None, accounts=None, runs=None):
        self.write_log = []
        self.collections = {
            module.SNAPCHAT_ENTITY_COLLECTION: Collection(
                entities, self.write_log
            ),
            module.INTEGRATION_ACCOUNTS_COLLECTION: Collection(
                accounts, self.write_log
            ),
            module.SETTINGS_SYNC_RUN_COLLECTION: Collection(
                runs, self.write_log
            ),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(
            name, Collection(write_log=self.write_log)
        )


def account(currency="USD"):
    row = {
        "user_id": USER_ID,
        "provider": "snapchat_ads",
        "external_account_id": ACCOUNT_ID,
        "ad_account_id": ACCOUNT_ID,
    }
    if currency is not None:
        row["currency"] = currency
    return row


def entity(
    entity_type,
    entity_id,
    *,
    campaign_id=None,
    observed_at=None,
    snapshot=None,
):
    provider_snapshot = {
        "id": entity_id,
        "name": f"{entity_type}-{entity_id}",
        "status": "ACTIVE",
        "updated_at": "2026-08-28T11:45:00Z",
    }
    if campaign_id:
        provider_snapshot["campaign_id"] = campaign_id
    provider_snapshot.update(snapshot or {})
    return {
        "user_id": USER_ID,
        "provider": "snapchat_ads",
        "ad_account_id": ACCOUNT_ID,
        "entity_type": entity_type,
        "external_id": entity_id,
        "campaign_id": (
            entity_id if entity_type == "campaign" else campaign_id
        ),
        "ad_squad_id": (
            entity_id if entity_type == "ad_squad" else None
        ),
        "display_name": provider_snapshot["name"],
        "deleted": False,
        "source_mode": module.SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "last_observed_at": (
            observed_at
            or (NOW - timedelta(minutes=5)).isoformat()
        ),
        "updated_at_provider": "2026-08-28T11:44:00Z",
        "provider_snapshot": provider_snapshot,
    }


@pytest.mark.asyncio
async def test_adsquad_settings_preserve_raw_micro_and_exact_provider_mapping():
    row = entity(
        "ad_squad",
        AD_SQUAD_ID,
        campaign_id=CAMPAIGN_ID,
        snapshot={
            "daily_budget_micro": 25_500_000,
            "bid_micro": 6_250_000,
            "bid_strategy": "TARGET_COST",
            "optimization_goal": "PIXEL_PURCHASE",
            "billing_event": "IMPRESSION",
            "conversion_window": {
                "view_attribution_window": "1_DAY",
                "swipe_attribution_window": "28_DAY",
            },
        },
    )
    db = DB(entities=[row], accounts=[account()])

    result = await module.resolve_financial_management_settings(
        db,
        USER_ID,
        "ad_squad",
        AD_SQUAD_ID,
        provider_entity_id=AD_SQUAD_ID,
        parent_unified_id=CAMPAIGN_ID,
        now=NOW,
    )

    assert result["unified_entity_id"] == AD_SQUAD_ID
    assert result["provider_entity_id"] == AD_SQUAD_ID
    assert result["provider_parent_id"] == CAMPAIGN_ID
    assert result["mapping_status"] == "verified"
    assert result["mapping_verified"] is True
    assert result["daily_budget_micro"] == 25_500_000
    assert result["daily_budget_usd"] == 25.5
    assert result["bid_micro"] == 6_250_000
    assert result["bid_usd"] == 6.25
    assert result["bid_semantic"] == "target_cost"
    assert result["optimization_goal"] == "PIXEL_PURCHASE"
    assert result["billing_event"] == "IMPRESSION"
    assert result["quality"]["settings_status"] == "settings_complete"
    assert result["quality"]["freshness_seconds"] == 300
    assert result["quality"]["financial_controls_allowed"] is True
    assert result["settings_synced_at"] != result["provider_updated_at"]
    assert db.write_log == []


@pytest.mark.parametrize(
    ("strategy", "semantic"),
    [
        ("TARGET_COST", "target_cost"),
        ("LOWEST_COST_WITH_MAX_BID", "max_bid"),
        ("AUTO_BID", "bid"),
        ("", "bid"),
    ],
)
def test_bid_micro_is_only_target_cost_for_target_cost_strategy(
    strategy, semantic
):
    assert module.bid_semantic_for_strategy(strategy) == semantic


@pytest.mark.asyncio
async def test_non_usd_or_unknown_currency_never_gets_usd_fallback():
    row = entity(
        "ad_squad",
        AD_SQUAD_ID,
        campaign_id=CAMPAIGN_ID,
        snapshot={
            "daily_budget_micro": 12_500_000,
            "bid_micro": 4_000_000,
            "bid_strategy": "LOWEST_COST_WITH_MAX_BID",
        },
    )

    sar = await module.list_financial_management_settings(
        DB(entities=[row], accounts=[account("SAR")]),
        USER_ID,
        "ad_squad",
        AD_SQUAD_ID,
        now=NOW,
    )
    sar_item = sar["items"][0]
    assert sar_item["account_currency"] == "SAR"
    assert sar_item["daily_budget_account_currency"] == 12.5
    assert sar_item["daily_budget_usd"] is None
    assert sar_item["bid_usd"] is None
    assert sar_item["quality"]["financial_controls_allowed"] is False

    unknown = await module.list_financial_management_settings(
        DB(entities=[row], accounts=[account(None)]),
        USER_ID,
        "ad_squad",
        AD_SQUAD_ID,
        now=NOW,
    )
    unknown_item = unknown["items"][0]
    assert unknown_item["account_currency"] is None
    assert unknown_item["daily_budget_usd"] is None
    assert unknown_item["quality"]["reason"] == (
        "account_currency_unknown_or_not_usd"
    )


@pytest.mark.asyncio
async def test_campaign_budget_missing_is_unsupported_and_never_replaced_by_sum():
    campaign = entity(
        "campaign",
        CAMPAIGN_ID,
        snapshot={
            "shared_properties": {
                "shared_ad_squad_bid_strategy": "TARGET_COST"
            }
        },
    )
    active = entity(
        "ad_squad",
        AD_SQUAD_ID,
        campaign_id=CAMPAIGN_ID,
        snapshot={
            "daily_budget_micro": 10_000_000,
            "bid_micro": 2_000_000,
            "bid_strategy": "TARGET_COST",
            "status": "ACTIVE",
        },
    )
    paused = entity(
        "ad_squad",
        "provider-squad-2",
        campaign_id=CAMPAIGN_ID,
        snapshot={
            "daily_budget_micro": 20_000_000,
            "bid_micro": 3_000_000,
            "bid_strategy": "LOWEST_COST_WITH_MAX_BID",
            "status": "PAUSED",
        },
    )
    result = await module.list_financial_management_settings(
        DB(entities=[campaign, active, paused], accounts=[account()]),
        USER_ID,
        "campaign",
        CAMPAIGN_ID,
        now=NOW,
    )
    item = result["items"][0]

    assert item["daily_budget_micro"] is None
    assert item["daily_budget_usd"] is None
    assert item["daily_budget_availability"] == (
        "unsupported_at_provider_level"
    )
    assert item["quality"]["financial_controls_allowed"] is False
    assert item["quality"]["reason"] == "unsupported_at_provider_level"
    assert item["daily_budget_unavailable_message_ar"] == (
        "غير متاح من Snapchat على هذا المستوى"
    )
    assert item["ad_squad_daily_budget_sum_micro"] == 30_000_000
    assert item["ad_squad_daily_budget_sum_usd"] == 30.0
    assert item["ad_squads_daily_budget_micro"] == 30_000_000
    assert item["ad_squads_daily_budget_usd"] == 30.0
    assert item["active_ad_squad_count"] == 1
    assert item["active_ad_squads"] == 1
    assert item["shared_ad_squad_bid_strategy"] == "TARGET_COST"
    assert item["ad_squad_bid_strategies"] == [
        "LOWEST_COST_WITH_MAX_BID",
        "TARGET_COST",
    ]
    assert item["daily_budget_micro"] != (
        item["ad_squad_daily_budget_sum_micro"]
    )


@pytest.mark.asyncio
async def test_campaign_child_aggregate_is_fail_closed_not_partial_sum():
    campaign = entity(
        "campaign",
        CAMPAIGN_ID,
        snapshot={"daily_budget_micro": 50_000_000},
    )
    loaded = entity(
        "ad_squad",
        AD_SQUAD_ID,
        campaign_id=CAMPAIGN_ID,
        snapshot={
            "daily_budget_micro": 10_000_000,
            "bid_strategy": "AUTO_BID",
        },
    )
    missing = entity(
        "ad_squad",
        "provider-squad-missing-budget",
        campaign_id=CAMPAIGN_ID,
        snapshot={"bid_strategy": "AUTO_BID"},
    )
    result = await module.list_financial_management_settings(
        DB(entities=[campaign, loaded, missing], accounts=[account()]),
        USER_ID,
        "campaign",
        CAMPAIGN_ID,
        now=NOW,
    )
    item = result["items"][0]

    assert item["ad_squad_daily_budget_sum_micro"] is None
    assert item["ad_squad_daily_budget_sum_usd"] is None
    assert item["active_ad_squad_count"] is None
    assert item["ad_squad_daily_budget_sum_availability"] == (
        "child_budget_field_missing"
    )
    assert item["campaign_aggregate"]["budget_coverage"] == {
        "complete": False,
        "loaded_count": 1,
        "total_count": 2,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_missing_stale_and_failed_sync_states_are_distinct():
    missing = await module.list_financial_management_settings(
        DB(accounts=[account()]),
        USER_ID,
        "campaign",
        CAMPAIGN_ID,
        now=NOW,
    )
    missing_item = missing["items"][0]
    assert missing_item["quality"]["settings_status"] == (
        "settings_not_loaded"
    )
    assert missing_item["daily_budget_micro"] is None
    assert missing_item["daily_budget_usd"] is None

    stale_row = entity(
        "campaign",
        CAMPAIGN_ID,
        observed_at=(
            NOW
            - timedelta(
                seconds=module.SETTINGS_FRESHNESS_MAX_AGE_SECONDS + 1
            )
        ).isoformat(),
        snapshot={"daily_budget_micro": 45_000_000},
    )
    stale = await module.list_financial_management_settings(
        DB(entities=[stale_row], accounts=[account()]),
        USER_ID,
        "campaign",
        CAMPAIGN_ID,
        now=NOW,
    )
    stale_item = stale["items"][0]
    assert stale_item["quality"]["settings_status"] == "settings_stale"
    assert stale_item["quality"]["freshness_seconds"] == (
        module.SETTINGS_FRESHNESS_MAX_AGE_SECONDS + 1
    )
    assert stale_item["quality"]["financial_controls_allowed"] is False

    fresh_time = NOW - timedelta(minutes=20)
    failed_run = {
        "user_id": USER_ID,
        "provider": "snapchat_ads",
        "run_type": "analytics_refresh",
        "run_id": "failed-settings-run",
        "status": "failed",
        "started_at": (NOW - timedelta(minutes=10)).isoformat(),
        "finished_at": (NOW - timedelta(minutes=9)).isoformat(),
    }
    failed_row = entity(
        "campaign",
        CAMPAIGN_ID,
        observed_at=fresh_time.isoformat(),
        snapshot={"daily_budget_micro": 45_000_000},
    )
    failed = await module.list_financial_management_settings(
        DB(
            entities=[failed_row],
            accounts=[account()],
            runs=[failed_run],
        ),
        USER_ID,
        "campaign",
        CAMPAIGN_ID,
        now=NOW,
    )
    failed_item = failed["items"][0]
    assert failed_item["quality"]["settings_status"] == (
        "settings_sync_failed"
    )
    assert failed_item["quality"]["reason"] == (
        "latest_native_settings_sync_failed"
    )
    assert failed_item["quality"]["financial_controls_allowed"] is False


@pytest.mark.asyncio
async def test_performance_only_partial_run_does_not_hide_fresh_settings():
    row = entity(
        "ad_squad",
        AD_SQUAD_ID,
        campaign_id=CAMPAIGN_ID,
        observed_at=(NOW - timedelta(minutes=4)).isoformat(),
        snapshot={
            "daily_budget_micro": 25_000_000,
            "bid_strategy": "AUTO_BID",
        },
    )
    performance_partial = {
        "user_id": USER_ID,
        "provider": "snapchat_ads",
        "run_type": "analytics_refresh",
        "run_id": "performance-partial",
        "status": "partial",
        "started_at": (NOW - timedelta(minutes=5)).isoformat(),
        "finished_at": (NOW - timedelta(minutes=3)).isoformat(),
        "summary": {"errors_count": 1},
        "error": {"code": "snapchat_native_sync_partial"},
    }
    result = await module.list_financial_management_settings(
        DB(
            entities=[row],
            accounts=[account()],
            runs=[performance_partial],
        ),
        USER_ID,
        "ad_squad",
        AD_SQUAD_ID,
        now=NOW,
    )
    item = result["items"][0]

    assert item["quality"]["settings_status"] == "settings_complete"
    assert item["quality"]["reason"] == "provider_snapshot_fresh"
    assert item["quality"]["latest_sync_run_status"] == "partial"


@pytest.mark.asyncio
async def test_entity_count_never_substitutes_entity_specific_sync_proof():
    old_row = entity(
        "ad_squad",
        AD_SQUAD_ID,
        campaign_id=CAMPAIGN_ID,
        observed_at=(NOW - timedelta(minutes=10)).isoformat(),
        snapshot={
            "daily_budget_micro": 25_000_000,
            "bid_strategy": "AUTO_BID",
        },
    )
    later_partial = {
        "user_id": USER_ID,
        "provider": "snapchat_ads",
        "run_type": "analytics_refresh",
        "run_id": "later-partial",
        "status": "partial",
        "started_at": (NOW - timedelta(minutes=5)).isoformat(),
        "finished_at": (NOW - timedelta(minutes=4)).isoformat(),
        "summary": {
            "entity_counts": {
                ACCOUNT_ID: {"campaign": 50, "ad_squad": 500}
            },
            "errors_count": 1,
        },
    }
    result = await module.list_financial_management_settings(
        DB(
            entities=[old_row],
            accounts=[account()],
            runs=[later_partial],
        ),
        USER_ID,
        "ad_squad",
        AD_SQUAD_ID,
        now=NOW,
    )
    item = result["items"][0]

    assert item["quality"]["settings_status"] == "settings_sync_failed"
    assert item["quality"]["reason"] == (
        "latest_native_sync_missing_entity_specific_proof"
    )
    assert item["quality"]["financial_controls_allowed"] is False


@pytest.mark.asyncio
async def test_provider_snapshot_absence_is_settings_not_loaded():
    row = entity(
        "campaign",
        CAMPAIGN_ID,
        snapshot={"daily_budget_micro": 40_000_000},
    )
    row.pop("provider_snapshot")

    result = await module.list_financial_management_settings(
        DB(entities=[row], accounts=[account()]),
        USER_ID,
        "campaign",
        CAMPAIGN_ID,
        now=NOW,
    )
    item = result["items"][0]

    assert item["quality"]["settings_status"] == "settings_not_loaded"
    assert item["quality"]["reason"] == "provider_snapshot_missing"
    assert item["mapping_verified"] is False
    assert item["daily_budget_micro"] is None
    assert item["daily_budget_usd"] is None


@pytest.mark.asyncio
async def test_provider_id_or_parent_mismatch_is_fail_closed():
    row = entity(
        "ad_squad",
        AD_SQUAD_ID,
        campaign_id=CAMPAIGN_ID,
        snapshot={
            "daily_budget_micro": 25_000_000,
            "bid_micro": 5_000_000,
            "bid_strategy": "TARGET_COST",
        },
    )
    db = DB(entities=[row], accounts=[account()])

    wrong_provider = await module.resolve_financial_management_settings(
        db,
        USER_ID,
        "ad_squad",
        AD_SQUAD_ID,
        provider_entity_id="wrong-provider-id",
        parent_unified_id=CAMPAIGN_ID,
        now=NOW,
    )
    assert wrong_provider["mapping_status"] == "mismatch"
    assert wrong_provider["quality"]["settings_status"] == (
        "settings_sync_failed"
    )
    assert wrong_provider["quality"]["financial_controls_allowed"] is False

    wrong_parent = await module.resolve_financial_management_settings(
        db,
        USER_ID,
        "ad_squad",
        AD_SQUAD_ID,
        provider_entity_id=AD_SQUAD_ID,
        parent_unified_id="wrong-parent-id",
        now=NOW,
    )
    assert wrong_parent["mapping_status"] == "parent_mismatch"
    assert wrong_parent["quality"]["reason"] == (
        "provider_parent_id_mismatch"
    )


@pytest.mark.asyncio
async def test_entity_settings_get_route_is_database_read_only():
    row = entity(
        "ad_squad",
        AD_SQUAD_ID,
        campaign_id=CAMPAIGN_ID,
        snapshot={
            "daily_budget_micro": 25_000_000,
            "bid_strategy": "AUTO_BID",
        },
    )
    db = DB(entities=[row], accounts=[account()])
    router = APIRouter()

    async def current_user():
        return {"id": USER_ID}

    module.attach_snapchat_entity_settings_routes(
        router,
        db,
        current_user,
        lambda user: user,
    )
    route = next(
        item
        for item in router.routes
        if item.path.endswith("/management/entity-settings")
    )
    result = await route.endpoint(
        entity_type="ad_squad",
        unified_entity_id=AD_SQUAD_ID,
        limit=50,
        user={"id": USER_ID},
    )

    assert route.methods == {"GET"}
    assert result["provider_write_calls"] == 0
    assert result["source_collection"] == (
        "mezan_snapchat_entities_v2"
    )
    assert result["items"][0]["provider_entity_id"] == AD_SQUAD_ID
    assert db.write_log == []

    source = inspect.getsource(module)
    assert "httpx" not in source
    assert ".update_one(" not in source
    assert ".insert_one(" not in source
    assert ".delete_one(" not in source


def test_financial_settings_freshness_window_is_explicitly_thirty_minutes():
    # Financial preview is fail-closed against entity settings older than 30m.
    assert module.SETTINGS_FRESHNESS_MAX_AGE_SECONDS == 30 * 60


def test_micro_conversion_rejects_missing_invalid_or_fractional_raw_values():
    assert module.micro_to_usd(None, "USD") is None
    assert module.micro_to_usd("not-a-number", "USD") is None
    assert module.micro_to_usd(1.5, "USD") is None
    assert module.micro_to_usd(0, "USD") == 0.0
    assert module.micro_to_usd(1_234_567, "USD") == 1.234567
