from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest

from integrations_control_center import snapchat_campaign_management as management
import campaign_ai_snapchat_autonomy as autonomy
import campaign_ai_monitor_legacy as legacy
from tests.test_campaign_ai_snapchat_autonomy import checked
from tests.test_campaign_ai_snapchat_autonomy_mongo import db, grant, install_preflight
from tests.test_snapchat_management_execution_hardening_v1 import (
    _DB, _Provider, _approved_row, _operation, _prepare,
)


async def transport_binding(database, monkeypatch):
    _, authorization = await grant(database)
    proof = checked(action="pause")
    install_preflight(monkeypatch, proof)
    claim = await legacy.claim_campaign_recommendation_execution(
        database, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    execution = await database[autonomy.EXECUTION_COLLECTION].find_one({"execution_id": claim["execution_id"]})
    await autonomy.prepare_autonomous_execution(database, "owner", execution, proof)
    changes = {"status": "PAUSED"}
    await autonomy.bind_autonomous_proposal(
        database, "owner", claim["execution_id"], "proposal-a",
        proof["recommendation"], proof["target"], changes,
    )
    operation = management.build_snapchat_operation(management.SnapchatManagementProposalInput(
        action="campaign.update", account_id="account-a", target_id="rec-a",
        payload=changes, reason="Synthetic transport fence test", idempotency_key="transport-fence-test",
    ))
    return operation


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_during_token", [False, True])
async def test_real_final_fence_runs_after_token_resolution_before_transport(
    db, monkeypatch, revoke_during_token,
):
    operation = await transport_binding(db, monkeypatch)
    events, requests = [], []
    provider = management.SnapchatManagementProvider(db, "owner")

    async def access_token():
        events.append("token")
        if revoke_during_token:
            await autonomy.revoke_snapchat_autonomy_policy(db, "owner", "owner", "account-a", 1)
        return "synthetic-token"

    provider.context.access_token = access_token

    async def authorize():
        events.append("authorization")
        await autonomy.require_snapchat_autonomy_write_authorization(
            db, user_id="owner", proposal_id="proposal-a", account_id="account-a",
            target_id="rec-a", action="campaign.update", changes={"status": "PAUSED"},
        )

    async def handler(request):
        requests.append(request)
        events.append("transport")
        return httpx.Response(200, json={"request_status": "SUCCESS", "campaigns": [{
            "sub_request_status": "SUCCESS", "campaign": {"id": "rec-a", "status": "PAUSED"},
        }]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(management.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    if revoke_during_token:
        with pytest.raises(management.SnapchatFinalAuthorizationNotSent) as caught:
            await provider.execute(operation, final_authorizer=authorize)
        assert caught.value.detail["code"] == "snapchat_autonomy_policy_revoked"
        assert requests == [] and events == ["token", "authorization"]
    else:
        result = await provider.execute(operation, final_authorizer=authorize)
        assert result["id"] == "rec-a"
        assert len(requests) == 1 and requests[0].method == "PATCH"
        assert events == ["token", "authorization", "transport"]


@pytest.mark.asyncio
async def test_typed_authorization_not_sent_resets_management_without_uncertainty(monkeypatch):
    _prepare(monkeypatch)
    database = _DB()
    operation = _operation()
    row = _approved_row("proposal-no-send", operation)
    database[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(row["original_snapshot"])
    provider.execute = AsyncMock(side_effect=management.SnapchatFinalAuthorizationNotSent(
        status_code=409, detail={"code": "synthetic_policy_revoked_before_transport"},
    ))
    with pytest.raises(management.SnapchatFinalAuthorizationNotSent):
        await management.execute_snapchat_management_proposal(
            database, "owner-1", "policy-actor", row["proposal_id"], provider=provider,
        )
    stored = database[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "approved"
    assert stored["provider_write_reached"] is False
    assert stored["provider_write_state"] == "not_attempted"
    assert stored.get("provider_write_uncertain") is False
    assert provider.executions == []
    assert database[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is False


@pytest.mark.asyncio
async def test_governed_management_passes_final_fence_through_real_token_transport(db, monkeypatch):
    operation = await transport_binding(db, monkeypatch)
    original = {"id": "rec-a", "ad_account_id": "account-a", "name": "Synthetic campaign",
                "status": "ACTIVE", "daily_budget_micro": 100_000_000}
    row = _approved_row("proposal-a", operation)
    row.update(user_id="owner", account_id="account-a", target_id="rec-a", original_snapshot=original)
    await db[management.PROPOSAL_COLLECTION].insert_one(row)
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")
    monkeypatch.setattr(management, "_selected_account", AsyncMock(return_value={
        "ad_account_id": "account-a", "currency": "USD",
    }))
    provider = management.SnapchatManagementProvider(db, "owner")
    provider.management_role = AsyncMock(return_value={"allowed": True, "role": "admin"})
    writing = False
    original_request = provider._request

    async def request(method, path, **options):
        nonlocal writing
        writing = method.upper() != "GET"
        return await original_request(method, path, **options)

    provider._request = request

    async def access_token():
        if writing:
            await autonomy.revoke_snapchat_autonomy_policy(db, "owner", "owner", "account-a", 1)
        return "synthetic-token"

    provider.context.access_token = access_token
    methods = []
    async def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json={"request_status": "SUCCESS", "campaigns": [{
            "sub_request_status": "SUCCESS", "campaign": original,
        }]})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(management.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    with pytest.raises(management.SnapchatFinalAuthorizationNotSent):
        await management.execute_snapchat_management_proposal(
            db, "owner", "synthetic-policy-actor", "proposal-a", provider=provider,
        )
    assert methods and set(methods) == {"GET"}
    stored = await db[management.PROPOSAL_COLLECTION].find_one({"user_id": "owner", "proposal_id": "proposal-a"})
    assert stored["status"] == "approved"
    assert stored["provider_write_state"] == "not_attempted"
    assert stored["provider_write_reached"] is False
    assert stored.get("provider_write_uncertain", False) is False
    assert await db[management.ENTITY_LEASE_COLLECTION].count_documents({"active": True}) == 0
