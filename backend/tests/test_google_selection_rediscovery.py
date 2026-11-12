"""Durable owner policy across OAuth rediscovery and all saved Google readers."""
from types import SimpleNamespace

import pytest

from ads_manager.google_native_overview import augment_google_overview
from integrations_control_center import google_account_selection as selection
from integrations_control_center import google_projection as projection
from integrations_control_center.google_ads_reporting import _accounts, GoogleAdsReportingError
from tests.test_google_reporting_account_selection import (
    DB, DAY, IDS, OWNER, native_fact, run, seed, spend,
)


NEW = "4444444401"


def discovery_docs(ids):
    return [{
        "user_id": OWNER, "provider": "google_ads", "external_account_id": account_id,
        "ad_account_id": account_id, "display_name": "Discovered fixture account",
        "connection_status": "connected", "connection_provenance": "api_connection",
        "currency": None, "timezone": None, "last_observed_at": "new-discovery-time",
        "created_at": "new-discovery-time",
    } for account_id in ids]


async def rediscover(db, ids, *, complete=True):
    await projection._persist_google_ads_accounts(
        db.raw, user_id=OWNER, docs=discovery_docs(ids), now_iso="new-discovery-time",
        complete_discovery=complete,
    )


async def overview(db):
    initial = {
        "range": {"date_from": DAY, "date_to": DAY, "provider": "google"},
        "providers": [], "campaigns": [], "metrics": {}, "coverage": {},
        "daily_spend": [{"date": DAY}], "sources": [], "insights": [],
    }
    return await augment_google_overview(db.raw, OWNER, initial, provider="google")


async def seed_reports(db):
    await db.raw.mezan_google_ads_performance_daily_v2.insert_many([
        native_fact(IDS[0], 25.4), native_fact(IDS[1], 800), native_fact(IDS[2], 900),
    ])
    await db.raw.daily_costs.insert_one({"user_id": OWNER, "date": DAY, "google_ads": 777})


async def assert_all_readers_choose(db, account_id, amount):
    assert [row["ad_account_id"] for row in await _accounts(db.raw, OWNER)] == [account_id]
    dashboard = await spend(db)
    assert dashboard["account_ids"] == [account_id]
    assert dashboard["total_sar"] == amount and dashboard["quality"]["amount_complete"] is True
    manager = await overview(db)
    assert manager["providers"][0]["metrics"]["provider_reported_spend_sar"] == amount
    assert manager["daily_spend"][0]["google"] == amount


async def assert_all_readers_incomplete(db):
    with pytest.raises(GoogleAdsReportingError):
        await _accounts(db.raw, OWNER)
    dashboard = await spend(db)
    assert dashboard["total_sar"] is None and dashboard["daily_sar"][DAY] is None
    assert dashboard["quality"]["amount_available"] is False
    manager = await overview(db)
    assert manager["providers"][0]["metrics"]["provider_reported_spend_sar"] is None
    assert manager["daily_spend"][0]["google"] is None
    assert manager["coverage"]["provider_spend_is_partial"] is True


def test_legacy_without_any_selection_metadata_keeps_compatibility_but_explicit_false_never_expands():
    legacy = [{"ad_account_id": IDS[0]}, {"ad_account_id": IDS[1]}]
    result = selection.google_reporting_scope(legacy)
    assert result["accounts"] == legacy and result["configured"] is False and result["complete"] is True
    explicit_empty = [{**row, "mezan_selected": False} for row in legacy]
    result = selection.google_reporting_scope(explicit_empty)
    assert result["accounts"] == [] and result["configured"] is True and result["complete"] is False


@pytest.mark.asyncio
async def test_rediscovery_keeps_owner_choice_existing_metadata_and_reports_new_accounts_are_unselected():
    db = DB()
    await seed(db)
    await seed_reports(db)
    await run(db, apply=True)
    selected_before = await db.raw[selection.ACCOUNTS].find_one({"_id": "account-" + IDS[0]})
    parent_before = await db.raw[selection.INTEGRATIONS].find_one({"user_id": OWNER, "provider": "google_ads"})
    saved_reports = await db.raw.mezan_google_ads_performance_daily_v2.find({}).to_list(length=None)
    await rediscover(db, [*IDS, NEW])
    selected_after = await db.raw[selection.ACCOUNTS].find_one({"_id": "account-" + IDS[0]})
    assert {key: selected_after.get(key) for key in selection.SELECTION_FIELDS} == {
        key: selected_before.get(key) for key in selection.SELECTION_FIELDS
    }
    assert selected_after["currency"] == "SAR" and selected_after["timezone"] == "Asia/Riyadh"
    assert selected_after["opaque_payload"] == selected_before["opaque_payload"]
    assert await db.raw[selection.INTEGRATIONS].find_one({"_id": parent_before["_id"]}) == parent_before
    fresh = await db.raw[selection.ACCOUNTS].find_one({"external_account_id": NEW, "user_id": OWNER})
    assert fresh["mezan_selected"] is False
    assert await db.raw.mezan_google_ads_performance_daily_v2.find({}).to_list(length=None) == saved_reports
    await assert_all_readers_choose(db, IDS[0], 25.4)


@pytest.mark.asyncio
async def test_selected_disappearance_zero_discovery_and_other_account_return_never_use_fallback_or_zero():
    db = DB()
    await seed(db)
    await seed_reports(db)
    await run(db, apply=True)
    for remaining in (list(IDS[1:]), [], [IDS[2]]):
        await rediscover(db, remaining)
        await assert_all_readers_incomplete(db)
        state = await selection.get_google_account_selection(db, OWNER)
        assert state["missing_selected_account_ids"] == [IDS[0]]
        assert state["selection_configured"] is True and state["selection_required"] is True
    tombstone = await db.raw[selection.ACCOUNTS].find_one({"_id": "account-" + IDS[0]})
    assert tombstone["connection_status"] == "disconnected" and tombstone["mezan_selected"] is True
    await rediscover(db, [IDS[0], IDS[2]])
    await assert_all_readers_choose(db, IDS[0], 25.4)


@pytest.mark.asyncio
async def test_later_owner_choice_wins_when_old_selected_tombstone_reappears():
    db = DB()
    await seed(db)
    await seed_reports(db)
    await run(db, apply=True)
    await rediscover(db, IDS[1:])
    await run(db, apply=True, selected_account_ids=[IDS[1]], expected_account_ids=list(IDS[1:]))
    await rediscover(db, IDS)
    old = await db.raw[selection.ACCOUNTS].find_one({"_id": "account-" + IDS[0]})
    assert old["mezan_selected"] is True  # Stale compatibility flags cannot overrule the parent policy.
    await assert_all_readers_choose(db, IDS[1], 800)
    state = await selection.get_google_account_selection(db, OWNER)
    assert [row["account_id"] for row in state["accounts"] if row["selected"]] == [IDS[1]]


@pytest.mark.asyncio
async def test_partly_missing_policy_keeps_known_selected_subtotal_incomplete():
    db = DB()
    await seed(db)
    await seed_reports(db)
    await run(db, apply=True, selected_account_ids=[IDS[0], IDS[1]])
    await rediscover(db, IDS[1:])
    await assert_all_readers_incomplete(db)
    result = await spend(db)
    assert result["account_ids"] == [IDS[1]] and result["known_total_sar"] == 800
    assert "google_reporting_selected_account_missing" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [
    {"account_selection_configured": True, "selected_account_ids": []},
    {"account_selection_configured": True, "selected_account_ids": None},
    {"account_selection_configured": True, "selected_account_ids": IDS[0]},
    {"account_selection_configured": True, "selected_account_ids": [IDS[0], IDS[0]]},
    {"account_selection_configured": True},
    {"account_selection_configured": None},
    {"account_selection_configured": False, "selected_account_ids": [IDS[0]]},
    {"account_selection_configured": True, "selected_account_ids": ["9999999901"]},
])
async def test_empty_malformed_or_unavailable_policy_cannot_fall_back_to_true_account_flags(policy):
    db = DB()
    await seed(db)
    await seed_reports(db)
    await db.raw[selection.ACCOUNTS].update_many({"user_id": OWNER}, {"$set": {"mezan_selected": True}})
    await db.raw[selection.INTEGRATIONS].update_one({"user_id": OWNER, "provider": "google_ads"}, {"$set": policy})
    await assert_all_readers_incomplete(db)


@pytest.mark.asyncio
async def test_owner_save_during_metadata_upsert_cannot_be_reverted():
    db = DB()
    await seed(db)
    await seed_reports(db)
    await run(db, apply=True)
    collection = db.raw[selection.ACCOUNTS]
    entered = False
    async def raced_update(query, update, **kwargs):
        nonlocal entered
        if not entered:
            entered = True
            await run(db, apply=True, selected_account_ids=[IDS[1]])
        return await collection.update_one(query, update, **kwargs)
    race_db = SimpleNamespace(mezan_integration_accounts_v2=SimpleNamespace(
        update_one=raced_update, update_many=collection.update_many,
    ))
    await projection._persist_google_ads_accounts(
        race_db, user_id=OWNER, docs=discovery_docs(IDS), now_iso="after-owner-save", complete_discovery=True,
    )
    assert entered
    await assert_all_readers_choose(db, IDS[1], 800)
    current = await collection.find_one({"_id": "account-" + IDS[1]})
    assert current["mezan_selected"] is True


@pytest.mark.asyncio
async def test_full_oauth_projection_preserves_parent_policy_and_other_provider_behavior(monkeypatch):
    db = DB()
    await seed(db)
    await seed_reports(db)
    await run(db, apply=True)
    parent = await db.raw[selection.INTEGRATIONS].find_one({"user_id": OWNER, "provider": "google_ads"})
    monkeypatch.setattr(projection, "encrypt_google_token", lambda value: "synthetic-ciphertext" if value else None)
    scopes = {projection.GOOGLE_SCOPE_BY_PROVIDER["google_ads"], projection.GOOGLE_SCOPE_BY_PROVIDER["google_merchant_center"]}
    await projection._persist_google_projection(
        db.raw, user_id=OWNER,
        token_payload={"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"},
        identity={"sub": "synthetic-subject"}, granted_scopes=scopes,
        accounts={"google_ads": discovery_docs([*IDS, NEW]), "google_merchant_center": [{"external_account_id": "merchant-fixture"}]},
        errors={},
    )
    after = await db.raw[selection.INTEGRATIONS].find_one({"_id": parent["_id"]})
    assert {key: after[key] for key in selection.POLICY_FIELDS} == {key: parent[key] for key in selection.POLICY_FIELDS}
    merchant = await db.raw[selection.ACCOUNTS].find_one({"user_id": OWNER, "provider": "google_merchant_center"})
    assert merchant["external_account_id"] == "merchant-fixture" and "mezan_selected" not in merchant
    await assert_all_readers_choose(db, IDS[0], 25.4)


@pytest.mark.asyncio
async def test_failed_discovery_does_not_invent_disconnected_accounts():
    db = DB()
    await seed(db)
    await run(db, apply=True)
    before = await db.raw[selection.ACCOUNTS].find({"user_id": OWNER}).to_list(length=None)
    await rediscover(db, [], complete=False)
    assert await db.raw[selection.ACCOUNTS].find({"user_id": OWNER}).to_list(length=None) == before
