from copy import deepcopy
from datetime import datetime, timezone

import pytest
from bson import BSON

from integrations_control_center.google_linked_campaign_reader import linked_campaign_evidence, SNAPSHOTS, RUNS
from integrations_control_center.campaign_product_associations import CAMPAIGN_PRODUCT_LINK_COLLECTION as LINKS
from tests.test_google_linked_campaign_evidence import source, NOW


def event(product="product", **changes):
    return dict(user_id="owner", provider="snapchat_ads", account_id="account", campaign_id="campaign",
                event_id="event-" + product, association_key="key-" + product, scope_type="campaign",
                product_id=product, state="active", valid_from="2026-10-01T00:00:00+00:00", valid_to=None,
                recorded_at="2026-10-01T00:00:00+00:00",
                evidence={"source": "manual", "verification_status": "verified", "observed_at": "2026-10-01T00:00:00+00:00"}) | changes


def matches(row, query):
    for key, value in query.items():
        actual = row.get(key)
        if isinstance(value, dict):
            if "$in" in value and actual not in value["$in"]:
                return False
            if "$gte" in value and not actual >= value["$gte"]:
                return False
            if "$lte" in value and not actual <= value["$lte"]:
                return False
        elif actual != value:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class Collection:
    def __init__(self, rows):
        self.rows = rows

    def aggregate(self, pipeline, maxTimeMS):
        assert maxTimeMS == 2000
        selected = [r for r in self.rows if matches(r, pipeline[0]["$match"])][:pipeline[1]["$limit"]]
        cap = pipeline[2]["$project"]["row"]["$cond"][0]["$lte"][1]
        return Cursor([{"row": r if len(BSON.encode(r)) <= cap else None, "size": len(BSON.encode(r))} for r in selected])


def database(events=None):
    snapshot, run = source()
    return {LINKS: Collection(events if events is not None else [event()]),
            SNAPSHOTS: Collection([snapshot]), RUNS: Collection([run])}


async def read(db, pins=None):
    return await linked_campaign_evidence(db, owner_id="owner", product_ids=pins or ["product"], as_of=NOW)


@pytest.mark.asyncio
async def test_multiproduct_campaign_context_is_emitted_once_no_allocated_metrics():
    result = await read(database([event(), event("second")]), ["product", "second"])
    assert len(result["campaign_contexts"]) == 1
    assert result["campaign_contexts"][0]["associated_product_count"] == 2
    first, second = result["products"]
    assert first["context_refs"] == second["context_refs"]
    assert "metrics" not in first and "metrics" not in second
    assert result["source_coverage_complete"] is False
    assert result["campaign_contexts"][0]["window_ids"] == ["last14", "last30", "last90"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"recorded_at": "2026-10-29T00:00:00+00:00"},
    {"valid_from": "2026-10-29T00:00:00+00:00"},
    {"evidence": {"verification_status": "inferred", "observed_at": "2026-10-01T00:00:00+00:00"}},
    {"ad_squad_id": "squad", "scope_type": "ad_squad"},
    {"user_id": "other"},
])
async def test_no_retroactive_or_childscope_or_foreign_product_context(changes):
    result = await read(database([event(**changes)]))
    assert result["campaign_contexts"] == []


@pytest.mark.asyncio
async def test_detached_history_and_reattachment_inside_day_prevent_false_continuity():
    detached = event(event_id="detached", state="inactive", valid_from="2026-10-28T01:00:00+00:00")
    attached = event(event_id="reattached", valid_from="2026-10-28T02:00:00+00:00")
    result = await read(database([event(), detached, attached]))
    assert result["campaign_contexts"] == []


@pytest.mark.asyncio
async def test_meta_and_tiktok_gaps_preserve_association_without_claiming_performance():
    result = await read(database([event(provider="meta"), event("second", provider="tiktok")]), ["product", "second"])
    assert result["campaign_contexts"] == []
    assert result["products"][0]["associations"][0]["missing"] == ["persisted_native_timezone", "atomic_campaign_snapshot_completeness"]
    assert result["products"][1]["associations"][0]["missing"] == ["persisted_native_currency", "persisted_native_timezone"]


@pytest.mark.asyncio
async def test_duplicate_source_days_reject_all_instead_of_accepting_first():
    db = database()
    db[SNAPSHOTS].rows.append(deepcopy(db[SNAPSHOTS].rows[0]))
    result = await read(db)
    assert result["campaign_contexts"] == []
    assert result["products"][0]["associations"][0]["rejected_source_snapshots"] == 1


@pytest.mark.asyncio
async def test_history_and_bytes_bounds_fail_closed():
    db = database([event(event_id=str(i)) for i in range(501)])
    with pytest.raises(ValueError, match="source_row_limit"):
        await read(db)
    db = database([event(oversized="x" * 33000)])
    with pytest.raises(ValueError, match="source_document_limit"):
        await read(db)
