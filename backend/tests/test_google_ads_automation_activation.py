"""Owner activation is an explicit, current revision acknowledgement only."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import asyncio

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from integrations_control_center import google_ads_automation_activation as activation
from integrations_control_center import google_ads_automation_policy as policies
from integrations_control_center.google_ads_automation_authority import activation_proof_valid
from tests.test_google_ads_automation_policy import CURRENT, app as policy_app, body


async def setup(monkeypatch, *, runtime=True, enabled=False, metadata=None, mutate=None):
    instance, db, calls = policy_app(monkeypatch, metadata=metadata)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body(enabled=enabled))
        assert response.status_code == 200
    if mutate: mutate(db.collection.rows[0])
    monkeypatch.setattr(policies, "automation_runtime_available", lambda: runtime)
    router, target = APIRouter(), FastAPI()
    async def current_user(): return {"id": "owner"}
    activation.attach_google_ads_automation_activation_routes(router, db, current_user)
    target.include_router(router)
    return target, db, calls


@pytest.mark.asyncio
async def test_activation_requires_explicit_ack_and_binds_new_revision_owner_and_time(monkeypatch):
    instance, db, calls = await setup(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/policy/123/activate", json={"expected_revision": 1, "acknowledge_execution": True})
    assert response.status_code == 200, response.text
    row = db.collection.rows[0]
    assert row["revision"] == row["activation_revision"] == 2 and row["activation_version"] == 1
    assert row["activated_by"] == "owner" and row["activated_at"] == CURRENT.isoformat()
    assert row["enabled"] is True and activation_proof_valid(row, CURRENT)
    assert response.json()["effective_enabled"] is True and row["events"][-1]["kind"] == "owner_activation"
    assert calls["mutations"] == 0 and calls["metadata"] == calls["closed"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [False, "true", 1, None])
async def test_activation_acknowledgement_cannot_be_coerced(monkeypatch, ack):
    instance, db, calls = await setup(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/policy/123/activate", json={"expected_revision": 1, "acknowledge_execution": ack})
    assert response.status_code == 422 and db.collection.rows[0]["enabled"] is False and calls["metadata"] == 1


@pytest.mark.asyncio
async def test_flags_off_preserves_legacy_enabled_without_creating_activation_proof(monkeypatch):
    instance, db, calls = await setup(monkeypatch, runtime=False, enabled=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/policy/123/activate", json={"expected_revision": 1, "acknowledge_execution": True})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "google_automation_execution_unavailable"
    assert not activation_proof_valid(db.collection.rows[0], CURRENT) and calls["metadata"] == 1


@pytest.mark.asyncio
async def test_generic_save_of_enabled_legacy_policy_never_activates_it(monkeypatch):
    _, db, _ = await setup(monkeypatch, enabled=True)
    row = db.collection.rows[0]
    assert row["enabled"] is True and not activation_proof_valid(row, CURRENT)
    result = policies.public_policy(row, account_id="123", current=CURRENT)
    assert result["activation_required"] is True and result["effective_enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"schema_version": True}, {"campaign_ids": []}, {"allowed_actions": []},
    {"allowed_actions": ["remove"]}, {"max_campaign_daily_budget_native": "0"},
    {"max_account_daily_budget_native": "39"}, {"max_budget_change_percent": "101"},
    {"cooldown_seconds": 0}, {"max_actions_per_day": 21},
    {"expires_at": CURRENT.isoformat()}, {"expires_at": None},
])
async def test_incomplete_expired_or_invalid_saved_configuration_cannot_activate(monkeypatch, changes):
    instance, db, calls = await setup(monkeypatch, mutate=lambda row: row.update(changes))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/policy/123/activate", json={"expected_revision": 1, "acknowledge_execution": True})
    assert response.status_code in {409, 422} and not activation_proof_valid(db.collection.rows[0], CURRENT)
    assert calls["metadata"] == 1 and calls["mutations"] == 0


@pytest.mark.asyncio
async def test_stale_revision_and_duplicate_ack_cannot_activate_again(monkeypatch):
    instance, db, _ = await setup(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        payload = {"expected_revision": 1, "acknowledge_execution": True}
        assert (await client.post("/google_ads/automation/policy/123/activate", json={**payload, "expected_revision": 2})).status_code == 409
        replies = await asyncio.gather(*(client.post("/google_ads/automation/policy/123/activate", json=payload) for _ in range(2)))
    assert sorted(response.status_code for response in replies) == [200, 409]
    assert db.collection.rows[0]["revision"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "owner", "currency", "flags", "expiry"])
async def test_revocation_permission_identity_flags_and_expiry_races_block_activation(monkeypatch, change):
    instance, db, calls = await setup(monkeypatch)
    async def account_client(*args, **kwargs):
        async def metadata():
            if change == "revoke": db.collection.rows[0].update(enabled=False, revision=2)
            if change == "owner": db.users.rows[0]["disabled"] = True
            if change == "flags": monkeypatch.setattr(policies, "automation_runtime_available", lambda: False)
            if change == "expiry": monkeypatch.setattr(policies, "now", lambda: CURRENT + timedelta(days=2))
            return {"account_id": "123", "identity_verified": True, "currency_code": "USD" if change == "currency" else "SAR", "time_zone": "Asia/Riyadh"}
        async def close(): calls["closed"] += 1
        return SimpleNamespace(account_metadata=metadata, close=close), "private-epoch"
    monkeypatch.setattr(policies, "account_client", account_client)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/policy/123/activate", json={"expected_revision": 1, "acknowledge_execution": True})
    assert response.status_code in {403, 409, 422} and not activation_proof_valid(db.collection.rows[0], CURRENT)
    assert calls["mutations"] == 0


@pytest.mark.asyncio
async def test_activation_keeps_authorized_inflight_claim_and_quota_private(monkeypatch):
    claim = {"claim_id": "private-claim", "operations_digest": "private-digest"}
    instance, db, _ = await setup(monkeypatch, mutate=lambda row: row.update(_dispatch_claim=deepcopy(claim), _dispatch_generation=8))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/policy/123/activate", json={"expected_revision": 1, "acknowledge_execution": True})
    assert response.status_code == 200 and response.json()["in_flight_authorized"] is True
    assert db.collection.rows[0]["_dispatch_claim"] == claim and db.collection.rows[0]["_dispatch_generation"] == 8
    assert "private-claim" not in response.text and "private-digest" not in response.text


def test_runtime_requires_both_default_off_flags_and_installed_native_gateway(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", raising=False)
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", raising=False)
    assert policies.automation_runtime_available() is False
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", "true")
    assert policies.automation_runtime_available() is False
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    assert policies.automation_runtime_available() is True


@pytest.mark.asyncio
async def test_configuration_save_invalidates_old_acknowledgement_without_clearing_quota(monkeypatch):
    instance, db, _ = await setup(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        assert (await client.post("/google_ads/automation/policy/123/activate", json={"expected_revision": 1, "acknowledge_execution": True})).status_code == 200
    db.collection.rows[0]["_dispatch_generation"] = 4
    ordinary, _, _ = policy_app(monkeypatch, db=db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(ordinary), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/policy/123", json=body(expected_revision=2))
    assert response.status_code == 200 and db.collection.rows[0]["revision"] == 3
    assert not activation_proof_valid(db.collection.rows[0], CURRENT) and db.collection.rows[0]["_dispatch_generation"] == 4


@pytest.mark.asyncio
async def test_missing_stored_controls_cannot_activate_with_implicit_defaults(monkeypatch):
    instance, db, _ = await setup(monkeypatch, mutate=lambda row: row.pop("cooldown_seconds"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(instance), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/policy/123/activate", json={"expected_revision": 1, "acknowledge_execution": True})
    assert response.status_code == 422 and not activation_proof_valid(db.collection.rows[0], CURRENT)
