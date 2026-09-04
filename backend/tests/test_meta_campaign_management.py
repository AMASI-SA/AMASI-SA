from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from integrations_control_center import meta_campaign_management as management


class Result:
    modified_count = 1


class Collection:
    def __init__(self):
        self.rows = []

    async def find_one(self, query, projection=None):
        row = next((row for row in self.rows if all(row.get(k) == v for k, v in query.items())), None)
        if not row:
            return None
        value = deepcopy(row)
        if projection and projection.get("user_id") == 0:
            value.pop("user_id", None)
        return value

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))
        return object()

    async def update_one(self, query, update):
        row = next((row for row in self.rows if all(row.get(k) == v for k, v in query.items())), None)
        if not row:
            result = Result()
            result.modified_count = 0
            return result
        row.update(deepcopy(update.get("$set") or {}))
        return Result()


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return deepcopy(self.payload)


class Client:
    posts = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        fields = kwargs["params"]["fields"]
        entity_id = url.rsplit("/", 1)[-1]
        if "bid_amount" in fields:
            return Response({
                "id": entity_id,
                "name": "مجموعة المبيعات",
                "account_id": "799",
                "campaign_id": "campaign-1",
                "status": "ACTIVE",
                "effective_status": "ACTIVE",
                "daily_budget": "10000",
                "bid_amount": "5600",
                "bid_strategy": "COST_CAP",
            })
        return Response({"id": entity_id, "status": "PAUSED"})

    async def post(self, url, **kwargs):
        type(self).posts.append((url, deepcopy(kwargs.get("data") or {})))
        return Response({"success": True})


async def _credential(*args):
    return "opaque"


async def _selection(*args):
    return {"accounts": [{"account_id": "act_799", "selected": True}]}


@pytest.mark.asyncio
async def test_bid_preview_targets_adset_bid_without_changing_budget(monkeypatch):
    db = DB()
    monkeypatch.setattr(management, "_credential", _credential)
    monkeypatch.setattr(management, "get_meta_account_selection", _selection)
    monkeypatch.setattr(management.httpx, "AsyncClient", Client)
    monkeypatch.setattr(management, "meta_appsecret_proof", lambda token: "proof")
    monkeypatch.setattr(management, "meta_graph_base", lambda: "https://graph.facebook.com/v25.0")

    result = await management.preview_meta_mutation(
        db,
        "owner-1",
        management.MetaMutationPreviewInput(
            account_id="act_799",
            entity_type="adset",
            entity_id="adset-1",
            action="update_bid",
            amount_native=67.5,
            idempotency_key="bid-adset-1-6750",
        ),
    )
    assert result["field"] == "bid_amount"
    assert result["planned"] == {"bid_amount": "6750"}
    assert result["before"]["daily_budget"] == "10000"
    assert result["provider_write_reached"] is False


@pytest.mark.asyncio
async def test_ad_pause_preview_never_reads_or_mutates_daily_budget(monkeypatch):
    class AdClient(Client):
        async def get(self, url, **kwargs):
            assert "daily_budget" not in kwargs["params"]["fields"]
            return Response({
                "id": "ad-1",
                "name": "الإعلان الخاسر",
                "account_id": "799",
                "campaign_id": "campaign-1",
                "adset_id": "adset-1",
                "status": "ACTIVE",
                "effective_status": "ACTIVE",
            })

    db = DB()
    monkeypatch.setattr(management, "_credential", _credential)
    monkeypatch.setattr(management, "get_meta_account_selection", _selection)
    monkeypatch.setattr(management.httpx, "AsyncClient", AdClient)
    monkeypatch.setattr(management, "meta_appsecret_proof", lambda token: "proof")
    monkeypatch.setattr(management, "meta_graph_base", lambda: "https://graph.facebook.com/v25.0")

    result = await management.preview_meta_mutation(
        db,
        "owner-1",
        management.MetaMutationPreviewInput(
            account_id="act_799",
            entity_type="ad",
            entity_id="ad-1",
            action="update_status",
            status="PAUSED",
            idempotency_key="pause-ad-1-safe",
        ),
    )
    assert result["field"] == "status"
    assert result["planned"] == {"status": "PAUSED"}
    assert "daily_budget" not in result["before"]


@pytest.mark.asyncio
async def test_execute_blocks_provider_state_drift_before_meta_write(monkeypatch):
    class DriftClient(Client):
        reads = 0
        posts = []

        async def get(self, url, **kwargs):
            type(self).reads += 1
            budget = "10000" if type(self).reads == 1 else "12000"
            return Response({
                "id": "campaign-1",
                "name": "Campaign",
                "account_id": "799",
                "status": "ACTIVE",
                "effective_status": "ACTIVE",
                "daily_budget": budget,
                "lifetime_budget": None,
            })

        async def post(self, url, **kwargs):
            type(self).posts.append((url, kwargs))
            return Response({"success": True})

    async def readiness(*_args):
        return {"accounts": [{"account_id": "act_799", "ready": True}]}

    db = DB()
    monkeypatch.setattr(management, "_credential", _credential)
    monkeypatch.setattr(management, "get_meta_account_selection", _selection)
    monkeypatch.setattr(management, "inspect_meta_management_readiness", readiness)
    monkeypatch.setattr(management.httpx, "AsyncClient", DriftClient)
    monkeypatch.setattr(management, "meta_appsecret_proof", lambda token: "proof")
    monkeypatch.setattr(management, "meta_graph_base", lambda: "https://graph.test")

    preview = await management.preview_meta_mutation(
        db,
        "owner-1",
        management.MetaMutationPreviewInput(
            account_id="act_799",
            entity_type="campaign",
            entity_id="campaign-1",
            action="update_budget",
            amount_native=105.0,
            idempotency_key="phase5-budget-campaign-1",
        ),
    )
    assert preview["expires_at"] > datetime.now(timezone.utc)

    with pytest.raises(management.HTTPException) as caught:
        await management.execute_meta_proposal(db, "owner-1", preview["proposal_id"])

    assert caught.value.detail == {
        "code": "meta_proposal_provider_state_changed",
        "changed_fields": ["daily_budget"],
    }
    assert DriftClient.posts == []
    assert db[management.COLLECTION].rows[0]["status"] == "previewed"


@pytest.mark.asyncio
async def test_execute_blocks_expired_meta_preview_without_provider_call(monkeypatch):
    db = DB()
    db[management.COLLECTION].rows.append({
        "proposal_id": "expired-1",
        "user_id": "owner-1",
        "status": "previewed",
        "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
    })

    with pytest.raises(management.HTTPException) as caught:
        await management.execute_meta_proposal(db, "owner-1", "expired-1")

    assert caught.value.detail["code"] == "meta_proposal_expired"
