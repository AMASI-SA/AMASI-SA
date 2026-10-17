from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import campaign_ai_google_periodic as google


class Collection:
    def __init__(self, document=None):
        self.document = deepcopy(document)
        self.upserts = []

    async def find_one(self, scope, projection=None):
        if not self.document or any(self.document.get(k) != v for k, v in scope.items()):
            return None
        return {k: deepcopy(v) for k, v in self.document.items() if k != "_id" and (not projection or projection.get(k))}

    async def update_one(self, scope, update, upsert=False):
        if upsert:
            self.upserts.append((deepcopy(scope), deepcopy(update)))
            return SimpleNamespace(modified_count=0)
        if not self.document or self.document.get("user_id") != scope.get("user_id") or self.document.get("snapshot_id") != scope.get("snapshot_id") or "google_periodic" in self.document:
            return SimpleNamespace(modified_count=0)
        self.document["recommendations"].extend(deepcopy(update["$push"]["recommendations"]["$each"]))
        self.document["providers"] = list(dict.fromkeys([*self.document["providers"], update["$addToSet"]["providers"]]))
        self.document.update(deepcopy(update["$set"]))
        return SimpleNamespace(modified_count=1)


class DB:
    def __init__(self):
        from campaign_ai_monitor_legacy import RECOMMENDATION_COLLECTION
        from integrations_control_center.google_ads_management import ANALYSES
        self.snapshot = {"user_id": "owner", "snapshot_id": "cycle1", "providers": ["snapchat", "meta"],
            "recommendations": [{"recommendation_id": "snap1", "provider": "snapchat", "execution_status": "awaiting_approval"}],
            "execution_targets": {"snap1": {"immutable": "existing target"}}}
        self.collections = {RECOMMENDATION_COLLECTION: Collection(self.snapshot), ANALYSES: Collection()}
        self.collection = self.collections[RECOMMENDATION_COLLECTION]
        self.analyses = self.collections[ANALYSES]

    def __getitem__(self, name):
        return self.collections[name]


def analysis(account_id="123"):
    return {"provider": "google_ads", "account_id": account_id,
        "period": {"date_from": "2026-10-12", "date_to": "2026-10-14", "currency_code": "USD", "time_zone": "America/New_York"},
        "summary": "راجع إعدادات التتبع", "recommendations": [{"recommendation_id": "g1", "account_id": account_id,
            "campaign_id": "456", "campaign_name": "Campaign", "action": "REVIEW_TRACKING", "title": "مراجعة التتبع",
            "rationale": "عينة التحويلات صغيرة", "priority": "high", "confidence": "medium",
            "observation_plan": "راجع الأحداث خلال خمس ساعات", "executable": True, "approval_available": True}],
        "read_only": True, "provider_writes": 0}


@pytest.mark.asyncio
async def test_existing_cycle_combines_google_advice_without_legacy_execution_or_currency_conversion():
    db = DB()
    calls = []

    async def accounts(db_arg, user_id):
        assert db_arg is db and user_id == "owner"
        return [{"account_id": "123", "name": "Google account", "selected": True}]

    async def analyze(db_arg, user_id, account, current):
        calls.append(account["account_id"])
        return analysis()

    result = await google.append_google_periodic_recommendations(db, "owner", db.snapshot,
        account_loader=accounts, account_analyzer=analyze)
    assert calls == ["123"]
    row = result["recommendations"][-1]
    assert row["provider"] == "google" and row["period_native"]["currency_code"] == "USD"
    assert row["executable"] is False and row["approval_available"] is False
    assert "financial_impact" not in row and "spend_sar" not in row
    assert result["google_periodic"]["decision_interval_hours"] == 5
    assert result["google_periodic"]["provider_writes"] == 0
    assert db.collection.document["execution_targets"] == db.snapshot["execution_targets"]
    assert db.collection.document["recommendations"][0] == db.snapshot["recommendations"][0]
    assert db.analyses.upserts[0][0]["user_id"] == "owner"
    repeated = await google.append_google_periodic_recommendations(db, "owner", db.snapshot,
        account_loader=accounts, account_analyzer=analyze)
    assert calls == ["123"] and len(repeated["recommendations"]) == 2


@pytest.mark.asyncio
async def test_failed_account_is_isolated_and_incomplete_facts_generate_no_replacement_advice():
    db = DB()

    async def accounts(*args):
        return [{"account_id": "1"}, {"account_id": "2"}, {"account_id": "3"}]

    async def analyze(db_arg, user_id, account, current):
        if account["account_id"] == "1":
            raise RuntimeError("private provider body must never escape")
        if account["account_id"] == "2":
            return None
        return analysis("3")

    result = await google.append_google_periodic_recommendations(db, "owner", db.snapshot,
        account_loader=accounts, account_analyzer=analyze)
    assert [item["status"] for item in result["google_periodic"]["accounts"]] == ["analysis_unavailable", "incomplete_provider_evidence", "analyzed"]
    assert len(result["recommendations"]) == 2
    assert "private provider body" not in str(result)


@pytest.mark.asyncio
async def test_another_tenant_snapshot_is_not_analyzed_or_modified():
    db = DB()

    async def forbidden(*args):
        raise AssertionError("cross-tenant analysis")

    result = await google.append_google_periodic_recommendations(db, "other", db.snapshot,
        account_loader=forbidden, account_analyzer=forbidden)
    assert result == db.snapshot
    assert "google_periodic" not in db.collection.document


def test_cross_account_model_result_is_rejected_at_shared_projection_boundary():
    with pytest.raises(ValueError, match="account_mismatch"):
        google.recommendation_rows(analysis("999"), {"account_id": "123"}, "cycle", "now")


@pytest.mark.asyncio
async def test_native_period_uses_account_timezone_and_worker_has_no_write_principal(monkeypatch):
    import integrations_control_center.google_ads_management as management
    import integrations_control_center.google_ads_workspace as workspace
    import integrations_control_center.google_ads_ai as ai
    closed = []

    class Client:
        async def account_metadata(self):
            return {"time_zone": "America/New_York", "currency_code": "USD"}

        async def close(self):
            closed.append(True)

    async def client(db, user_id, account_id):
        assert user_id == "owner" and account_id == "123"
        return Client(), "epoch"

    async def read(db, actor, **kwargs):
        assert actor == {"user_id": "owner", "actor_id": "owner", "owner": False, "can_draft": False}
        assert kwargs["from_date"] == "2026-10-11" and kwargs["to_date"] == "2026-10-13"
        return {"complete": True, "campaigns": []}

    async def analyze(db, user_id, body, *, facts):
        assert facts["permitted_actions"] == [] and facts["limits"] == {}
        assert body.date_to == "2026-10-13"
        return {"tested": True}

    monkeypatch.setattr(management, "account_client", client)
    monkeypatch.setattr(workspace, "read_google_workspace", read)
    monkeypatch.setattr(ai, "analyze_google_campaigns", analyze)
    result = await google._account_analysis(None, "owner", {"account_id": "123"}, datetime(2026, 10, 14, 1, tzinfo=timezone.utc))
    assert result == {"tested": True} and closed == [True]


@pytest.mark.asyncio
async def test_scheduler_adds_only_connected_native_google_tenants_without_losing_other_providers():
    import campaign_ai_monitor_legacy as monitor

    class Accounts:
        async def distinct(self, field, query):
            if query["provider"] == "google_ads":
                assert query["connection_provenance"] == "api_connection" and query["connection_status"] == "connected"
                return ["google-owner", "missing-connection"]
            return ["snap-owner"]

    class Connections:
        async def distinct(self, field, query):
            assert query == {"provider": "google_ads", "connection_status": "connected", "connection_provenance": "api_connection"}
            return ["google-owner"]

    db = SimpleNamespace(mezan_integration_accounts_v2=Accounts(), mezan_integrations_v2=Connections())
    assert await monitor._monitored_user_ids(db) == ["google-owner", "snap-owner"]


@pytest.mark.asyncio
async def test_established_monitor_entrypoint_appends_google_with_the_same_tenant_and_snapshot(monkeypatch):
    import campaign_ai_monitor as monitor
    db = object()
    calls = []

    async def base(db_arg, user_id, **kwargs):
        assert db_arg is db and user_id == "owner"
        return {"snapshot_id": "one-global-cycle"}

    async def append(db_arg, user_id, snapshot, **kwargs):
        calls.append((db_arg, user_id, snapshot["snapshot_id"]))
        return {**snapshot, "google_periodic": {"tested": True}}

    monkeypatch.setattr(monitor, "_base_run_campaign_ai_monitor", base)
    monkeypatch.setattr(google, "append_google_periodic_recommendations", append)
    result = await monitor.run_campaign_ai_monitor(db, "owner")
    assert calls == [(db, "owner", "one-global-cycle")]
    assert result["google_periodic"] == {"tested": True}
