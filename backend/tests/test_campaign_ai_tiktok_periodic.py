import asyncio
from datetime import datetime, timezone

import pytest

import campaign_ai_tiktok_periodic as periodic
from tests.test_tiktok_campaign_management import environment


async def setup(db, monkeypatch):
    from campaign_ai_monitor_legacy import RECOMMENDATION_COLLECTION
    monkeypatch.setenv("MEZAN_TIKTOK_AI_PERIODIC_ENABLED", "true")
    snapshot = {"user_id": "owner", "snapshot_id": "cycle1", "recommendations": [{"recommendation_id": "original", "provider": "snapchat"}], "providers": ["snapchat"], "execution_targets": {"original": {"immutable": True}}}
    await db[RECOMMENDATION_COLLECTION].insert_one(dict(snapshot))
    return snapshot, db[RECOMMENDATION_COLLECTION]


async def loader(*args):
    return [{"account_id": "70001", "entity_id": "1111", "entity_name": "Winter"}], {"total": 100, "page": 1, "limit": 3}


def result(row, **changes):
    return {"context": {"campaign": {"account_id": row["account_id"], "campaign_id": row["entity_id"]}}, "recommendation": {"focus": "tracking", "summary": "راجع التتبع قبل قرارات الإنفاق", "next_step": "راجع أحداث الشراء المؤكدة"}, "model": "fixture", **changes}


@pytest.mark.asyncio
async def test_periodic_default_off_does_not_touch_data_or_model(monkeypatch):
    monkeypatch.delenv("MEZAN_TIKTOK_AI_PERIODIC_ENABLED", raising=False)
    snapshot = {"snapshot_id": "cycle1"}
    assert await periodic.append_tiktok_periodic_recommendations(object(), "owner", snapshot) is snapshot


@pytest.mark.asyncio
async def test_periodic_joins_shared_snapshot_without_financial_authority(environment, monkeypatch):
    db, _ = environment
    snapshot, collection = await setup(db, monkeypatch)
    calls = []
    async def analyzer(db_arg, user, row, start, end):
        calls.append((user, start, end)); return result(row)
    response = await periodic.append_tiktok_periodic_recommendations(db, "owner", snapshot, loader=loader, analyzer=analyzer, current=datetime(2026, 10, 25, 12, tzinfo=timezone.utc))
    assert calls == [("owner", "2026-10-22", "2026-10-24")]
    note = response["recommendations"][-1]
    assert note["provider"] == "tiktok" and note["executable"] is False and note["profit_sar"] is None
    stored = await collection.find_one({"user_id": "owner", "snapshot_id": "cycle1"})
    assert stored["execution_targets"] == snapshot["execution_targets"]
    assert stored["recommendations"][0] == snapshot["recommendations"][0]
    assert stored["tiktok_periodic"]["coverage"]["total"] == 100
    assert stored["tiktok_periodic"]["campaign_limit"] == 3
    await periodic.append_tiktok_periodic_recommendations(db, "owner", snapshot, loader=loader, analyzer=analyzer)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_replicas_claim_one_billable_cycle(environment, monkeypatch):
    db, _ = environment
    snapshot, collection = await setup(db, monkeypatch)
    calls = []
    async def analyzer(db_arg, user, row, start, end):
        calls.append(row); await asyncio.sleep(0); return result(row)
    await asyncio.gather(*(periodic.append_tiktok_periodic_recommendations(db, "owner", snapshot, loader=loader, analyzer=analyzer) for _ in range(3)))
    assert len(calls) == 1
    stored = await collection.find_one({"user_id": "owner", "snapshot_id": "cycle1"})
    assert len(stored["recommendations"]) == 2


@pytest.mark.asyncio
async def test_foreign_or_unavailable_analysis_creates_no_fabricated_advice(environment, monkeypatch):
    db, _ = environment
    snapshot, collection = await setup(db, monkeypatch)
    async def analyzer(*args):
        return result({"account_id": "foreign", "entity_id": "1111"})
    response = await periodic.append_tiktok_periodic_recommendations(db, "owner", snapshot, loader=loader, analyzer=analyzer)
    assert response["recommendations"] == snapshot["recommendations"]
    assert response["tiktok_periodic"]["status"] == "unavailable"
    assert await db[periodic.ANALYSES].count_documents({}) == 0
