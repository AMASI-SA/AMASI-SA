"""Completed producers survive post-completion publication/bookkeeping failures.

Exercise actual run helpers and committed-day readers with real Mongo query
semantics in an isolated mongomock store; no provider or live data is used.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.test_snapchat_reporting_v2_refresh_visibility import Collection, Database
from snapchat_v2.committed_totals import COLLECTION, publish_committed_totals
from snapchat_v2.period_evidence import total_period_complete
from snapchat_v2.sync_runs import complete_sync_run, fail_sync_run
from snapchat_v2.total_facts import load_total_facts, upsert_total_facts

DAYS = [date(2026, 10, 22), date(2026, 10, 23)]
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


class InjectedCollection(Collection):
    def __init__(self, db, name):
        super().__init__(db.raw[name])
        self.db, self.name = db, name

    async def update_one(self, query, update, **kwargs):
        if (self.name == COLLECTION and self.db.fail_publish_day_two
                and update.get("$set", {}).get("sync_run_id") == "new"
                and query.get("report_date") == DAYS[1].isoformat()):
            raise RuntimeError("injected_commit_day_two_failure")
        if self.name == "mezan_snapchat_connections_v2" and self.db.fail_connection:
            raise RuntimeError("injected_connection_bookkeeping_failure")
        return await super().update_one(query, update, **kwargs)


class FailureDatabase(Database):
    fail_publish_day_two = False
    fail_connection = False

    def __getitem__(self, name):
        return InjectedCollection(self, name)


def producer(run_id, status):
    return {"sync_run_id": run_id, "user_id": "owner", "provider": "snapchat_ads",
            "ad_account_id": "saudi", "status": status, "financial_sync_status": "complete",
            "identity_sync_status": "complete", "campaign_sync_status": "complete",
            "ad_squad_sync_status": "complete", "ad_sync_status": "complete", "finished_at": NOW,
            "request_window": {"date_from": DAYS[0].isoformat(), "date_to": DAYS[-1].isoformat(),
                               "action_report_time": "conversion"}}


def hierarchy(run_id, spend):
    return [{"user_id": "owner", "ad_account_id": "saudi", "entity_type": level,
             "external_id": f"{level}-entity", "campaign_id": "campaign-entity",
             "report_date": day.isoformat(), "account_timezone": "Asia/Riyadh", "currency": "SAR",
             "window_start_utc": datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
             "window_end_utc": datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc),
             "coverage": {"status": "complete"}, "spend_native": spend, "sync_run_id": run_id}
            for day in DAYS for level in ("campaign", "ad_squad", "ad")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["second_commit_day", "connection_bookkeeping"])
async def test_complete_producer_not_demoted_and_published_days_remain_proven(failure):
    db = FailureDatabase()
    db.raw.mezan_snapchat_sync_runs_v2.insert_many([producer("old", "complete"), producer("new", "running")])
    await upsert_total_facts(db, hierarchy("old", 10))
    assert await publish_committed_totals(db, "old") == 2
    await upsert_total_facts(db, hierarchy("new", 20))
    await complete_sync_run(db, "new", summary={"provider_read": "complete"}, now=lambda: NOW)
    assert db.raw.mezan_snapchat_sync_runs_v2.find_one({"sync_run_id": "new"})["status"] == "complete"

    db.fail_publish_day_two = failure == "second_commit_day"
    db.fail_connection = failure == "connection_bookkeeping"
    with pytest.raises(RuntimeError, match="injected_") as caught:
        # This is the production pipeline's actual ordering. Test its storage
        # contract, without replacing fail_sync_run with a looser fake helper.
        await publish_committed_totals(db, "new")
        await db["mezan_snapchat_connections_v2"].update_one(
            {"user_id": "owner"}, {"$set": {"last_sync_status": "complete"}}, upsert=True)
    await fail_sync_run(db, "new", caught.value, stage="post_financial_sync_failed", now=lambda: NOW)
    assert db.raw.mezan_snapchat_sync_runs_v2.find_one({"sync_run_id": "new"})["status"] == "complete"

    expected_runs = ["new", "old"] if failure == "second_commit_day" else ["new", "new"]
    for level in ("campaign", "ad_squad", "ad"):
        rows = await load_total_facts(db, user_id="owner", ad_account_id="saudi", entity_type=level,
            date_from=DAYS[0], date_to=DAYS[-1], account_timezone="Asia/Riyadh")
        assert [row["sync_run_id"] for row in rows] == expected_runs
        assert [row["spend_native"] for row in rows] == ([20, 10] if failure == "second_commit_day" else [20, 20])
        assert await total_period_complete(db, user_id="owner", account_id="saudi", entity_type=level,
            date_from=DAYS[0], date_to=DAYS[-1], action_report_time="conversion", facts=rows)


@pytest.mark.asyncio
async def test_precompletion_failure_still_marks_a_running_producer_failed():
    db = FailureDatabase(); db.raw.mezan_snapchat_sync_runs_v2.insert_one(producer("new", "running"))
    await fail_sync_run(db, "new", RuntimeError("before_completion"), now=lambda: NOW)
    assert db.raw.mezan_snapchat_sync_runs_v2.find_one({"sync_run_id": "new"})["status"] == "failed"
    assert await publish_committed_totals(db, "new") == 0
