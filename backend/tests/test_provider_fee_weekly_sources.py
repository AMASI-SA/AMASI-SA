"""Bounded accepted reads, exact operational fallback and report restatement."""
from copy import deepcopy
from datetime import date, timedelta

import pytest
from mongomock_motor import AsyncMongoMockClient

from operational_provider_statement_fees import (
    derive_statement_average_profile, select_statement_average_profile,
    statement_average_summary,
)
from provider_fee_weekly_sources import (
    STATES, STATEMENTS, load_weekly_statement_averages, weekly_fee_source_revision,
)
from tests.test_provider_fee_weekly_average import sale, source

REPORT_DAY = "2026-11-22"
KNOWLEDGE = "2026-11-22T12:00:00+03:00"


class RecordingCollection:
    def __init__(self, collection, reads):
        self.collection, self.reads = collection, reads

    async def find_one(self, query, projection):
        self.reads.append((self.collection.name, deepcopy(query), deepcopy(projection)))
        return await self.collection.find_one(query, projection)

    def find(self, query, projection):
        self.reads.append((self.collection.name, deepcopy(query), deepcopy(projection)))
        return self.collection.find(query, projection)

    def __getattr__(self, name):
        raise AssertionError("weekly fee report attempted a write: " + name)


class ReadOnlyDB:
    def __init__(self, db):
        self.db, self.reads = db, []

    def __getitem__(self, name):
        return RecordingCollection(self.db[name], self.reads)


def accepted_profile(document):
    return derive_statement_average_profile({"owner_id": document["owner_id"]}, document["statement"],
        statement_id=document["id"], accepted_at=document["uploaded_at"])


async def seed(db, documents, *, active_profiles=()):
    summaries = {}
    for original in documents:
        document = deepcopy(original)
        document.pop("accepted_summary", None)
        document.pop("accepted_profile", None)
        document["metadata"] = {"row_count": len(document["statement"]["rows"])}
        await db[STATEMENTS].insert_one(document)
        summaries[document["id"]] = {"statement_average": statement_average_summary(accepted_profile(original))}
    profiles = {profile["scope_key"]: deepcopy(profile) for profile in active_profiles}
    state = {"_id": "owner", "owner_id": "owner", "status": "active", "revision": 4,
        "started_at": "2026-10-01T00:00:00+03:00",
        "engine": {"as_of": "2026-11-22T08:00:00+03:00", "provider_fee_inputs": {
            "average_profiles": list(profiles.values())}},
        "provider_statement_reconciliation": {"statement_summaries": summaries, "average_profiles": profiles}}
    await db[STATES].insert_one(deepcopy(state))
    return state


async def read(db, **changes):
    return await load_weekly_statement_averages(db, "owner", as_of=changes.pop("as_of", REPORT_DAY),
        evidence_as_of=changes.pop("evidence_as_of", KNOWLEDGE), **changes)


@pytest.mark.asyncio
async def test_long_accepted_history_reads_only_files_that_can_overlap_the_week():
    db = AsyncMongoMockClient().weekly_source_history
    old = [source("old-" + str(index), [sale("old-order-" + str(index),
        (date(2026, 10, 1) + timedelta(days=index)).isoformat())]) for index in range(40)]
    current = source("current", [sale("current-order", "2026-11-21")])
    await seed(db, [*old, current])
    readonly = ReadOnlyDB(db)
    result = (await read(readonly))["SAR"]
    assert result["available"] is True
    assert result["source_statement_ids"] == ["current"]
    assert result["profile"]["rate_percent"] == "7.00000000"
    statement_reads = [query for collection, query, _ in readonly.reads if collection == STATEMENTS]
    assert statement_reads == [{"owner_id": "owner", "_id": {"$in": ["current"]}}] * 2
    state_projection = readonly.reads[0][2]
    assert state_projection["engine.as_of"] == 1
    assert "engine.provider_fee_inputs" not in state_projection


@pytest.mark.asyncio
async def test_more_than_32_potentially_overlapping_sources_are_refused_before_raw_reads():
    db = AsyncMongoMockClient().weekly_source_limits
    documents = [source("weekly-" + str(index), [sale("order-" + str(index), "2026-11-21")])
                 for index in range(33)]
    await seed(db, documents)
    readonly = ReadOnlyDB(db)
    result = (await read(readonly))["SAR"]
    assert result["reason_code"] == "weekly_statement_source_scope_too_large"
    assert all(collection != STATEMENTS for collection, _, _ in readonly.reads)
    assert "operational_fallback" not in result


@pytest.mark.asyncio
async def test_row_budget_preflight_withholds_original_documents_and_fallback():
    db = AsyncMongoMockClient().weekly_source_row_budget
    document = source("weekly", [sale("order", "2026-11-21")])
    await seed(db, [document], active_profiles=[accepted_profile(document)])
    await db[STATEMENTS].update_one({}, {"$set": {"metadata.row_count": 10001}})
    readonly = ReadOnlyDB(db)
    result = (await read(readonly))["SAR"]
    assert result["reason_code"] == "weekly_statement_source_scope_too_large"
    assert not any(projection.get("statement") for _, _, projection in readonly.reads)
    assert "operational_fallback" not in result


@pytest.mark.asyncio
async def test_empty_week_reuses_exact_operational_period_profile_without_reaveraging():
    db = AsyncMongoMockClient().weekly_source_fallback
    document = source("operational-period", [
        sale("a", "2026-11-02", "100.00", "10.00", "1.00", "1.65"),
        sale("b", "2026-11-14", "300.00", "15.00", "2.00", "2.55"),
    ])
    profile = accepted_profile(document)
    state = await seed(db, [document], active_profiles=[profile])
    before = deepcopy(state)
    readonly = ReadOnlyDB(db)
    result = (await read(readonly))["SAR"]
    assert result["available"] is False and result["reason_code"] == "source_weekly_average_unavailable"
    assert (result["window_start"], result["window_end"]) == ("2026-11-15", "2026-11-21")
    fallback = result["operational_fallback"]
    assert fallback["available"] is True and fallback["basis"] == "operational_balance_average"
    assert fallback["profile"] == select_statement_average_profile(state, "tabby", "SAR")
    assert fallback["profile"]["rate_percent"] == "6.25000000"  # The whole saved period, not the older week's5%.
    assert fallback["period_start"] == state["started_at"] and fallback["period_end"] == state["engine"]["as_of"]
    assert not any(collection == STATEMENTS for collection, _, _ in readonly.reads)
    assert await db[STATES].find_one({"_id": "owner"}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["content_hash", "missing_date", "missing_file"])
async def test_invalid_week_evidence_never_enables_operational_fallback(defect):
    db = AsyncMongoMockClient().weekly_source_invalid
    old = source("older", [sale("older", "2026-11-01")])
    current = source("current", [sale("current", None if defect == "missing_date" else "2026-11-21")])
    await seed(db, [old, current], active_profiles=[accepted_profile(old)])
    if defect == "content_hash":
        await db[STATEMENTS].update_one({"_id": "current"}, {"$set": {"content_hash": "f" * 64}})
    elif defect == "missing_file":
        await db[STATEMENTS].delete_one({"_id": "current"})
    result = (await read(ReadOnlyDB(db)))["SAR"]
    assert result["available"] is False and result["reason_code"] != "source_weekly_average_unavailable"
    assert "operational_fallback" not in result


@pytest.mark.asyncio
async def test_operational_fallback_preserves_account_and_currency_ambiguity():
    db = AsyncMongoMockClient().weekly_source_accounts
    documents = [source("account-" + account, [sale("order-" + account, "2026-11-01")], account=account)
                 for account in ("a", "b")]
    await seed(db, documents, active_profiles=[accepted_profile(document) for document in documents])
    result = await read(ReadOnlyDB(db), currencies=("SAR", "USD"))
    assert result["SAR"]["operational_fallback"]["available"] is False
    assert result["USD"]["operational_fallback"]["available"] is False


@pytest.mark.asyncio
async def test_newly_accepted_statement_restates_the_original_historical_week():
    db = AsyncMongoMockClient().weekly_source_restatement
    document = source("late-import", [sale("historical", "2026-10-10")], uploaded="2026-11-20T10:00:00+03:00")
    await seed(db, [document], active_profiles=[accepted_profile(document)])
    before = (await read(ReadOnlyDB(db), as_of="2026-10-15", evidence_as_of="2026-11-19T12:00:00+03:00"))["SAR"]
    assert before["available"] is False and before["operational_fallback"]["available"] is False
    restated = (await read(ReadOnlyDB(db), as_of="2026-10-15"))["SAR"]
    assert restated["available"] is True
    assert (restated["window_start"], restated["window_end"]) == ("2026-10-08", "2026-10-14")
    assert restated["source_statement_ids"] == ["late-import"]
    assert restated["profile"]["rate_percent"] == "7.00000000"


@pytest.mark.asyncio
async def test_historical_empty_week_uses_current_operational_knowledge_and_period():
    db = AsyncMongoMockClient().weekly_source_historical_fallback
    document = source("late-import", [sale("earlier", "2026-10-10")], uploaded="2026-11-20T10:00:00+03:00")
    state = await seed(db, [document], active_profiles=[accepted_profile(document)])
    result = (await read(ReadOnlyDB(db), as_of="2026-11-01"))["SAR"]
    assert result["available"] is False
    assert result["operational_fallback"]["available"] is True
    assert result["operational_fallback"]["profile"] == select_statement_average_profile(state, "tabby", "SAR")
    assert result["operational_fallback"]["period_end"] == "2026-11-22T08:00:00+03:00"


def test_source_revision_tracks_proofs_and_calculated_period_without_other_engine_payloads():
    state = {"owner_id": "owner", "engine": {"as_of": "2026-11-22", "private_order": "ignored"},
             "provider_statement_reconciliation": {"statement_summaries": {}}}
    baseline = weekly_fee_source_revision(state)
    state["engine"]["private_order"] = "changed"
    assert weekly_fee_source_revision(state) == baseline
    state["engine"]["as_of"] = "2026-11-23"
    assert weekly_fee_source_revision(state) != baseline
    baseline = weekly_fee_source_revision(state)
    state["provider_statement_reconciliation"]["statement_summaries"]["new"] = {"statement_average": {"available": True}}
    assert weekly_fee_source_revision(state) != baseline
