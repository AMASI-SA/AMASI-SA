"""DAY parent scope must reach child requests even when HOUR is zero."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import mongomock
import pytest

from snapchat_v2 import client as client_module
from snapchat_v2 import sync_pipeline as pipeline_module
from snapchat_v2.client import SnapchatClientError, SnapchatV2Client
from snapchat_v2.committed_totals import COLLECTION as COMMITTED
from snapchat_v2.entities import SNAPCHAT_ENTITY_FACTS_COLLECTION
from snapchat_v2.period_evidence import total_period_complete
from snapchat_v2.projections import business_day_window
from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from snapchat_v2.total_facts import SNAPCHAT_TOTAL_FACTS_COLLECTION, load_total_facts


NOW = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)
DAYS = [date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 24)]
CAMPAIGNS = ("Opaque-Campaign-MiXeD", "ABCDEFAB-1234-5678-9012-ABCDEFABCDEF")
ACCOUNTS = {
    "usd-account": {"ad_account_id": "usd-account", "currency": "USD", "timezone": "America/Los_Angeles"},
    "sar-account": {"ad_account_id": "sar-account", "currency": "SAR", "timezone": "Asia/Riyadh"},
}


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def sort(self, *args):
        self.cursor = self.cursor.sort(*args)
        return self

    def limit(self, count):
        self.cursor = self.cursor.limit(count)
        return self

    async def to_list(self, length):
        return list(self.cursor)[:length]


class Collection:
    def __init__(self, collection):
        self.collection = collection

    def find(self, *args, **options):
        return Cursor(self.collection.find(*args, **options))

    async def find_one(self, *args, **options):
        return self.collection.find_one(*args, **options)

    async def create_index(self, *args, **options):
        return self.collection.create_index(*args, **options)

    async def insert_one(self, *args, **options):
        return self.collection.insert_one(*args, **options)

    async def update_one(self, *args, **options):
        return self.collection.update_one(*args, **options)

    async def update_many(self, *args, **options):
        return self.collection.update_many(*args, **options)

    async def bulk_write(self, *args, **options):
        return self.collection.bulk_write(*args, **options)


class Database:
    def __init__(self):
        self.raw = mongomock.MongoClient(tz_aware=True).db

    def __getitem__(self, name):
        return Collection(self.raw[name])


class Tokens:
    async def get_access_token(self, user_id, **options):
        assert user_id == "owner"
        return "synthetic-report-token"


class Connection:
    async def ensure_indexes(self):
        return None

    async def validate_ready(self, user_id, *, ad_account_id=None):
        assert user_id == "owner" and ad_account_id in ACCOUNTS
        return {}, dict(ACCOUNTS[ad_account_id])


class Reports:
    def __init__(self, *, truncated_day=False, empty_day=False):
        self.requests = []
        self.truncated_day, self.empty_day = truncated_day, empty_day

    async def handle(self, request):
        assert request.method == "GET" and request.url.host == "adsapi.snapchat.com"
        path = request.url.path.removeprefix("/v1")
        query = dict(request.url.params)
        self.requests.append((path, query))
        pieces = path.strip("/").split("/")
        if pieces[0] == "adaccounts":
            account_id = pieces[1]
            assert account_id in ACCOUNTS
            account = ACCOUNTS[account_id]
            if pieces[-1] == "stats":
                granularity = query["granularity"]
                if granularity == "HOUR":
                    start = datetime.fromisoformat(query["start_time"])
                    # Valid zero financial observations cannot select a parent
                    # using a positive HOUR-metric predicate.
                    campaigns = [{"id": CAMPAIGNS[0], "timeseries": [{
                        "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat(),
                        "stats": {"spend": 0, "impressions": 0, "swipes": 0},
                    }]}]
                else:
                    assert granularity == "DAY"
                    zone = ZoneInfo(account["timezone"])
                    campaigns = [] if self.empty_day else [{"id": campaign_id, "timeseries": [{
                        "start_time": datetime.combine(day, time.min, zone).isoformat(),
                        "end_time": datetime.combine(day + timedelta(days=1), time.min, zone).isoformat(),
                        # This is synthetic late-conversion-only evidence for
                        # one parent and an actual all-zero row for the other.
                        "stats": {"spend": 0, "impressions": 0, "swipes": 0,
                                  "conversion_purchases": int(index == 0),
                                  "conversion_purchases_value": 4_000_000 if index == 0 else 0},
                    } for day in DAYS]} for index, campaign_id in enumerate(CAMPAIGNS)]
                paging = ({"next_link": str(request.url.copy_add_param("cursor", "next"))}
                          if self.truncated_day and granularity == "DAY" else {})
                return httpx.Response(200, json={"request_status": "SUCCESS", "paging": paging,
                    "timeseries_stats": [{"sub_request_status": "SUCCESS", "timeseries_stat": {
                        "granularity": granularity, "breakdown_stats": {"campaign": campaigns},
                    }}]})
            plural, singular = {"campaigns": ("campaigns", "campaign"),
                                "adsquads": ("adsquads", "adsquad"), "ads": ("ads", "ad")}[pieces[-1]]
            entities = []
            for campaign_id in (*CAMPAIGNS, "Catalog-Only-No-Report"):
                row = {"id": campaign_id if singular == "campaign" else
                       ("group-" if singular == "adsquad" else "ad-") + campaign_id,
                       "name": "Historical identity", "status": "ACTIVE" if campaign_id == "Catalog-Only-No-Report" else "PAUSED",
                       "ad_account_id": account_id}
                if singular != "campaign":
                    row["campaign_id"] = campaign_id
                if singular == "ad":
                    row["ad_squad_id"] = "group-" + campaign_id
                entities.append({"sub_request_status": "SUCCESS", singular: row})
            return httpx.Response(200, json={"request_status": "SUCCESS", plural: entities})
        assert pieces[0] == "campaigns" and pieces[1] in CAMPAIGNS and pieces[-1] == "stats"
        assert query["granularity"] == "TOTAL" and query["breakdown"] in {"adsquad", "ad"}
        # The exact request carries each account's local midnight boundaries;
        # both currency paths use this same handler and producer implementation.
        start, end = datetime.fromisoformat(query["start_time"]), datetime.fromisoformat(query["end_time"])
        assert start.hour == end.hour == 0 and end - start == timedelta(days=1)
        prefix = "group-" if query["breakdown"] == "adsquad" else "ad-"
        return httpx.Response(200, json={"request_status": "SUCCESS", "total_stats": [{
            "sub_request_status": "SUCCESS", "total_stat": {"granularity": "TOTAL",
                "breakdown_stats": {query["breakdown"]: [{"id": prefix + pieces[1], "stats": {
                    "spend": 0, "impressions": 2, "swipes": 0, "uniques": 2, "frequency": 1,
                }}]},
            },
        }]})

    def factory(self, **options):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle), **options)


@pytest.fixture
def isolated_support(monkeypatch):
    # Host admission and unrelated financial projections/reconciliation are
    # outside this regression. Real sync runs, levels, identities, facts and
    # committed hierarchy publication remain in the test.
    async def admit(*args, **options):
        return "test-admission", None

    async def release(*args, **options):
        return None

    async def projections(db, *, report_dates, account, **options):
        return [{"report_date": day.isoformat(), "projection_timezone": zone,
                 "window_start_utc": business_day_window(day, zone)[0],
                 "window_end_utc": business_day_window(day, zone)[1]}
                for day in report_dates for zone in {account["timezone"], "Asia/Riyadh"}]

    async def provider_total(*args, **options):
        return {"spend_native": 0}

    async def reconcile(*args, **options):
        return {"reconciled": True}

    monkeypatch.setattr(pipeline_module.governor, "acquire", admit)
    monkeypatch.setattr(pipeline_module.governor, "release", release)
    monkeypatch.setattr(pipeline_module.governor, "safe_checkpoint", lambda: None)
    monkeypatch.setattr(pipeline_module, "build_and_persist_daily_projections", projections)
    monkeypatch.setattr(pipeline_module, "fetch_provider_total", provider_total)
    monkeypatch.setattr(pipeline_module, "reconcile_day", reconcile)


async def run_account(db, reports, account_id):
    def client_factory(database, user_id):
        return SnapchatV2Client(database, user_id, token_store=Tokens(), client_factory=reports.factory, now=lambda: NOW)

    return await pipeline_module.SnapchatV2SyncPipeline(
        db, now=lambda: NOW, connection_manager=Connection(), client_factory=client_factory,
    ).run("owner", account_id, date_from=DAYS[0], date_to=DAYS[-1])


@pytest.mark.asyncio
async def test_same_producer_requests_day_parents_with_zero_hour_activity_for_usd_and_sar(isolated_support):
    db = Database()
    # Overlapping identifiers in another tenant/account must never supply an
    # ad's parent. Catalog membership itself is not a reporting scope proof.
    for owner, account_id in (("foreign-owner", "sar-account"), ("owner", "foreign-account")):
        db.raw[SNAPCHAT_ENTITY_FACTS_COLLECTION].insert_one({"user_id": owner, "provider": "snapchat_ads",
            "ad_account_id": account_id, "entity_type": "ad", "external_id": "ad-" + CAMPAIGNS[0],
            "ad_squad_id": "foreign-parent", "active": True, "name": "Foreign identity"})
    for account_id, account in ACCOUNTS.items():
        reports = Reports()
        outcome = await run_account(db, reports, account_id)
        assert outcome["status"] == "complete"
        run = db.raw[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one({"sync_run_id": outcome["sync_run_id"]})
        assert run["coverage"]["financial"]["data_state"] == "confirmed_zero"
        assert "report_campaign_ids" not in outcome["summary"]["breakdown_performance"]["campaign"]
        for level, breakdown in (("ad_squad", "adsquad"), ("ad", "ad")):
            requests = [(path, query) for path, query in reports.requests if query.get("breakdown") == breakdown]
            assert len(requests) == len(CAMPAIGNS) * len(DAYS)
            assert {path.split("/")[2] for path, _ in requests} == set(CAMPAIGNS)
            assert all(datetime.fromisoformat(query["start_time"]).utcoffset() ==
                       datetime.combine(DAYS[0], time.min, ZoneInfo(account["timezone"])).utcoffset()
                       for _, query in requests)
            coverage = run["coverage"][level]
            assert coverage["expected_requests"] == coverage["completed_requests"] == 6
            assert coverage["campaigns_requested"] == 2 and coverage["rows_received"] == 6
            facts = await load_total_facts(db, user_id="owner", ad_account_id=account_id,
                entity_type=level, date_from=DAYS[0], date_to=DAYS[-1], account_timezone=account["timezone"])
            assert len(facts) == 6
            assert {row["currency"] for row in facts} == {account["currency"]}
            assert {row["account_timezone"] for row in facts} == {account["timezone"]}
            assert {row["ad_account_id"] for row in facts} == {account_id}
            assert {row["campaign_id"] for row in facts} == set(CAMPAIGNS)
            assert {row["sync_run_id"] for row in facts} == {outcome["sync_run_id"]}
            assert await total_period_complete(db, user_id="owner", account_id=account_id,
                entity_type=level, date_from=DAYS[0], date_to=DAYS[-1], action_report_time="conversion", facts=facts)
            if level == "ad":
                assert all(row["ad_squad_id"] == "group-" + row["campaign_id"] for row in facts)
        assert db.raw[COMMITTED].count_documents({"user_id": "owner", "ad_account_id": account_id}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_type", ["ad_squad", "ad"])
async def test_zero_child_requests_cannot_claim_provider_confirmed_no_data(entity_type):
    reports = Reports()
    client = SnapchatV2Client(Database(), "owner", token_store=Tokens(), client_factory=reports.factory)
    with pytest.raises(SnapchatClientError) as caught:
        await client.fetch_breakdown_daily_total_facts(ACCOUNTS["sar-account"], campaign_ids=[],
            entity_type=entity_type, report_dates=DAYS, sync_run_id="unproven")
    assert caught.value.code == f"snapchat_{entity_type}_campaign_scope_unproven"
    assert caught.value.coverage == {"status": "incomplete", "data_state": "unknown_incomplete",
        "expected_requests": 0, "completed_requests": 0, "rows_received": 0, "reason": "campaign_scope_unproven"}
    assert reports.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["truncated", "empty", "publish"])
async def test_unpublished_or_unproven_day_scope_does_not_become_complete_children(isolated_support, monkeypatch, failure):
    db = Database()
    reports = Reports(truncated_day=failure == "truncated", empty_day=failure == "empty")
    if failure == "truncated":
        monkeypatch.setattr(client_module, "MAX_PAGES", 1)
    if failure == "publish":
        real_upsert = pipeline_module.upsert_total_facts

        async def fail_campaign_publish(database, rows, **options):
            if rows and rows[0]["entity_type"] == "campaign":
                await real_upsert(database, rows[:1], **options)
                raise RuntimeError("synthetic_campaign_fact_publish_failure")
            return await real_upsert(database, rows, **options)

        monkeypatch.setattr(pipeline_module, "upsert_total_facts", fail_campaign_publish)
    outcome = await run_account(db, reports, "sar-account")
    assert outcome["status"] == "partial"
    assert not any(query.get("granularity") == "TOTAL" for _, query in reports.requests)
    run = db.raw[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one({"sync_run_id": outcome["sync_run_id"]})
    for level in ("ad_squad", "ad"):
        assert run[f"{level}_sync_status"] == "partial"
        assert run["coverage"][level]["data_state"] == "unknown_incomplete"
        assert run["coverage"][level]["reason"] == f"snapchat_{level}_campaign_scope_unproven"
        assert db.raw[SNAPCHAT_TOTAL_FACTS_COLLECTION].count_documents({"entity_type": level}) == 0
    assert db.raw[COMMITTED].count_documents({}) == 0


@pytest.mark.asyncio
async def test_real_child_parent_scope_still_obeys_provider_request_budget(monkeypatch):
    reports = Reports()
    monkeypatch.setattr(client_module, "MAX_PROVIDER_CALLS", 1)
    client = SnapchatV2Client(Database(), "owner", token_store=Tokens(), client_factory=reports.factory)
    with pytest.raises(SnapchatClientError) as caught:
        await client.fetch_breakdown_daily_total_facts(ACCOUNTS["usd-account"], campaign_ids=CAMPAIGNS,
            entity_type="ad", report_dates=[DAYS[0]], sync_run_id="bounded")
    assert caught.value.code == "snapchat_ad_provider_call_budget_exceeded"
    assert caught.value.coverage["status"] == "incomplete"
    assert caught.value.coverage["expected_requests"] == 2 and caught.value.coverage["completed_requests"] == 0
    assert reports.requests == []
