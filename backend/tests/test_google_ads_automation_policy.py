"""Standing policy configuration is owner-scoped, revisioned and write-free."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from pymongo.errors import DuplicateKeyError

from integrations_control_center import google_ads_automation_policy as policy

CURRENT = datetime(2026, 10, 26, 0, tzinfo=timezone.utc)


class Collection:
    def __init__(self, rows=()):
        self.rows, self.indexes = deepcopy(list(rows)), []

    async def find_one(self, scope, projection=None):
        return next((deepcopy(row) for row in self.rows if all(row.get(k) == v for k, v in scope.items())), None)

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))

    async def insert_one(self, row):
        if any(all(prior.get(k) == row.get(k) for k in ("user_id", "account_id")) for prior in self.rows):
            raise DuplicateKeyError("duplicate")
        self.rows.append(deepcopy(row))

    async def update_one(self, scope, update):
        for row in self.rows:
            if all(row.get(k) == v for k, v in scope.items()):
                row.update(deepcopy(update["$set"]))
                for key, value in update.get("$push", {}).items():
                    row[key] = (row.get(key, []) + deepcopy(value["$each"]))[value["$slice"]:]
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class DB:
    def __init__(self):
        self.users = Collection([{"id": "owner", "role": "owner"}])
        self.collection = Collection()

    def __getitem__(self, name):
        assert name == policy.AUTOMATION_POLICIES
        return self.collection


def body(**changes):
    return {"expected_revision": 0, "campaign_ids": ["456"], "allowed_actions": ["pause", "set_budget"],
        "max_campaign_daily_budget_native": "40", "max_account_daily_budget_native": "55",
        "max_budget_change_percent": "10", "expires_at": (CURRENT + timedelta(days=1)).isoformat(), **changes}


def app(monkeypatch, *, db=None, user=None, metadata=None, connected=True):
    db = db or DB()
    calls = {"metadata": 0, "closed": 0, "accounts": 0, "mutations": 0}

    async def current_user():
        return user or {"id": "owner", "role": "owner"}

    async def accounts(database, user_id):
        assert database is db
        calls["accounts"] += 1
        return [{"account_id": "123"}] if connected and user_id == "owner" else []

    async def account_client(database, user_id, account_id, **kwargs):
        assert database is db and user_id == "owner" and account_id == "123"
        async def read():
            calls["metadata"] += 1
            return metadata if metadata is not None else {"account_id": "123", "identity_verified": True, "currency_code": "SAR", "time_zone": "Asia/Riyadh"}
        async def close(): calls["closed"] += 1
        async def mutate(*args, **kwargs):
            calls["mutations"] += 1
            raise AssertionError("policy configuration must never mutate Google")
        return SimpleNamespace(account_metadata=read, close=close, mutate=mutate), "private-epoch"

    monkeypatch.setattr(policy, "available_accounts", accounts)
    monkeypatch.setattr(policy, "account_client", account_client)
    monkeypatch.setattr(policy, "now", lambda: CURRENT)
    instance, router = FastAPI(), APIRouter()
    policy.attach_google_ads_automation_policy_routes(router, db, current_user)
    instance.include_router(router)
    return instance, db, calls


@pytest.mark.asyncio
async def test_default_disabled_get_creates_no_policy_and_never_reads_provider(monkeypatch):
    instance, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.get("/google_ads/automation/policy/123")
    result = response.json()
    assert response.status_code == 200
    assert result["revision"] == 0 and result["enabled"] is result["effective_enabled"] is False
    assert result["executor_available"] is False and result["provider_writes"] == 0
    assert not db.collection.rows and calls["metadata"] == calls["mutations"] == 0


@pytest.mark.asyncio
async def test_save_native_identity_disabled_by_default_and_redacts_private_fields(monkeypatch):
    instance, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body())
    result = response.json()
    assert response.status_code == 200, response.text
    assert result["revision"] == 1 and result["enabled"] is False
    assert (result["currency_code"], result["time_zone"]) == ("SAR", "Asia/Riyadh")
    assert result["budget_semantics"] == "average_daily_not_hard_spend_cap"
    assert "user_id" not in result and "auth_epoch" not in result
    assert db.collection.rows[0]["user_id"] == "owner"
    assert db.collection.indexes[0][1]["unique"] is True
    assert calls["metadata"] == calls["closed"] == 1 and calls["mutations"] == 0


@pytest.mark.asyncio
async def test_owner_opt_in_still_cannot_claim_an_executor_is_running(monkeypatch):
    instance, _, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body(enabled=True))
    result = response.json()
    assert result["enabled"] is True and result["effective_enabled"] is result["executor_available"] is False
    assert result["status"] == "executor_not_installed" and calls["mutations"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("persisted", [
    {"id": "owner", "role": "owner", "disabled": True},
    {"id": "owner", "role": "viewer", "created_by": "other"},
    {"id": "owner", "role": "admin", "created_by": "other"},
])
async def test_forged_or_stale_owner_role_is_rejected_before_account_or_policy_read(monkeypatch, persisted):
    db = DB()
    db.users.rows = [persisted, {"id": "other", "role": "owner"}]
    instance, _, calls = app(monkeypatch, db=db, user={"id": "owner", "role": "owner"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body())
    assert response.status_code == 403 and not db.collection.rows and calls["accounts"] == calls["metadata"] == 0


@pytest.mark.asyncio
async def test_unlinked_account_cannot_be_configured(monkeypatch):
    instance, db, calls = app(monkeypatch, connected=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body())
    assert response.status_code == 404 and not db.collection.rows and calls["metadata"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"campaign_ids": ["456", "456"]}, {"campaign_ids": ["٤٥٦"]}, {"campaign_ids": ["x"]},
    {"campaign_ids": [str(i) for i in range(51)]}, {"allowed_actions": ["create_campaign"]},
    {"allowed_actions": ["pause", "pause"]}, {"enabled": True, "campaign_ids": []},
    {"enabled": True, "allowed_actions": []}, {"enabled": True, "expires_at": None},
    {"max_campaign_daily_budget_native": "0"}, {"max_account_daily_budget_native": "39"},
    {"max_budget_change_percent": "101"}, {"max_budget_change_percent": "NaN"},
    {"max_campaign_daily_budget_native": "1e10"}, {"max_campaign_daily_budget_native": "1.0000001"},
    {"cooldown_seconds": 3599}, {"cooldown_seconds": True}, {"max_actions_per_day": 21},
    {"expected_revision": True}, {"enabled": "true"}, {"user_id": "other"},
])
async def test_invalid_policy_is_rejected_without_provider_reads(monkeypatch, changes):
    instance, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body(**changes))
    assert response.status_code == 422 and not db.collection.rows and calls["metadata"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", ["2026-10-27T00:00:00", CURRENT.isoformat(), (CURRENT + timedelta(days=31)).isoformat(), "bad"])
async def test_expiry_must_be_aware_future_and_bounded(monkeypatch, expiry):
    instance, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body(expires_at=expiry))
    assert response.status_code == 422 and not db.collection.rows and calls["metadata"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{}, {"currency_code": "sar", "time_zone": "Asia/Riyadh"},
    {"account_id": "123", "identity_verified": True, "currency_code": "SAR", "time_zone": "Not/AZone"},
    {"account_id": "123", "identity_verified": True, "currency_code": "١٢٣", "time_zone": "UTC"},
    {"account_id": "999", "identity_verified": True, "currency_code": "SAR", "time_zone": "UTC"},
    {"account_id": "123", "identity_verified": False, "currency_code": "SAR", "time_zone": "UTC"}])
async def test_unverified_native_metadata_cannot_pin_policy(monkeypatch, metadata):
    instance, db, calls = app(monkeypatch, metadata=metadata)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body())
    assert response.status_code == 409 and not db.collection.rows
    assert calls["closed"] == 1 and calls["mutations"] == 0


@pytest.mark.asyncio
async def test_concurrent_create_and_update_allow_one_winner_each(monkeypatch):
    instance, db, _ = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        first = await asyncio.gather(*(client.put("/google_ads/automation/policy/123", json=body()) for _ in range(2)))
        assert sorted(response.status_code for response in first) == [200, 409]
        second = await asyncio.gather(*(client.put("/google_ads/automation/policy/123", json=body(expected_revision=1, enabled=True)) for _ in range(2)))
    assert sorted(response.status_code for response in second) == [200, 409]
    row = db.collection.rows[0]
    assert row["revision"] == 2 and [event["revision"] for event in row["events"]] == [1, 2]


@pytest.mark.asyncio
async def test_revocation_works_when_disconnected_and_stale_save_cannot_reenable(monkeypatch):
    instance, db, _ = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/policy/123", json=body(enabled=True))).status_code == 200
    disconnected, _, calls = app(monkeypatch, db=db, connected=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(disconnected), base_url="http://test") as client:
        revoked = await client.post("/google_ads/automation/policy/123/revoke", json={"expected_revision": 1})
        stale = await client.post("/google_ads/automation/policy/123/revoke", json={"expected_revision": 1})
    assert revoked.status_code == 200 and revoked.json()["revision"] == 2 and revoked.json()["enabled"] is False
    assert stale.status_code == 409 and calls["accounts"] == calls["metadata"] == calls["mutations"] == 0
    reconnected, _, _ = app(monkeypatch, db=db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(reconnected), base_url="http://test") as client:
        stale_save = await client.put("/google_ads/automation/policy/123", json=body(expected_revision=1, enabled=True))
    assert stale_save.status_code == 409 and db.collection.rows[0]["enabled"] is False


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_or_revoke_policy(monkeypatch):
    db = DB()
    db.collection.rows = [{"user_id": "other", "account_id": "123", "revision": 1, "enabled": True}]
    instance, _, _ = app(monkeypatch, db=db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        result = await client.get("/google_ads/automation/policy/123")
        revoked = await client.post("/google_ads/automation/policy/123/revoke", json={"expected_revision": 1})
    assert result.json()["configured"] is False and revoked.status_code == 409
    assert db.collection.rows[0]["enabled"] is True


def test_expired_or_malformed_policy_is_never_effectively_enabled():
    for expiry in ["bad", None, (CURRENT - timedelta(seconds=1)).isoformat()]:
        result = policy.public_policy({"enabled": True, "expires_at": expiry}, account_id="123", current=CURRENT)
        assert result["expired"] is True and result["effective_enabled"] is False


@pytest.mark.asyncio
async def test_policy_update_keeps_bounded_atomic_revocation_history(monkeypatch):
    instance, db, _ = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/policy/123", json=body())).status_code == 200
        for revision in range(1, 55):
            result = await client.put("/google_ads/automation/policy/123", json=body(expected_revision=revision))
            assert result.status_code == 200
    events = db.collection.rows[0]["events"]
    assert len(events) == 50 and events[0]["revision"] == 6 and events[-1]["revision"] == 55
