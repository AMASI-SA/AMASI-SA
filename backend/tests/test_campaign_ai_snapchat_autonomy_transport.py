import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from integrations_control_center import snapchat_campaign_management as management
import campaign_ai_snapchat_autonomy as autonomy
import campaign_ai_monitor_legacy as legacy
from tests.test_campaign_ai_snapchat_autonomy import NOW, checked
from tests.test_campaign_ai_snapchat_autonomy_mongo import db, grant, install_preflight, snapshot
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


@pytest.mark.asyncio
@pytest.mark.parametrize("reconciled_applied,tamper", [(False, None), (True, None), (False, "binding"), (True, "proof")])
async def test_provider_timeout_and_failed_readback_remain_uncertain_in_runner_and_next_cycle(
    db, monkeypatch, reconciled_applied, tamper,
):
    operation = await transport_binding(db, monkeypatch)
    proof = checked(action="pause")
    await snapshot(db, [proof])
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
    provider.context.access_token = AsyncMock(return_value="synthetic-token")
    patch_attempts, failed_readbacks = [], []
    recovered_snapshot = None

    async def handler(request):
        if request.method == "PATCH":
            patch_attempts.append(request)
            # The provider may have applied this request before the socket
            # timed out. The caller has no acknowledgement either way.
            raise httpx.ReadTimeout("synthetic write acknowledgement unavailable", request=request)
        if patch_attempts and recovered_snapshot is None:
            failed_readbacks.append(request)
            raise httpx.ReadTimeout("synthetic provider reread unavailable", request=request)
        return httpx.Response(200, json={"request_status": "SUCCESS", "campaigns": [{
            "sub_request_status": "SUCCESS", "campaign": recovered_snapshot or original,
        }]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(management.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs,
    ))

    async def execute_seeded_approved_proposal(_db, user_id, **options):
        # Proposal creation/evidence is already represented by the server-owned
        # binding fixture. Exercise the real governed write and reconciliation.
        return await management.execute_snapchat_management_proposal(
            db, user_id, options["actor_id"], "proposal-a", provider=provider,
        )

    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute_seeded_approved_proposal)
    binding = await db[autonomy.BINDING_COLLECTION].find_one({"user_id": "owner", "proposal_id": "proposal-a"})
    result = await legacy.run_claimed_campaign_execution(db, "owner", binding["execution_id"])
    proposal = await db[management.PROPOSAL_COLLECTION].find_one({"user_id": "owner", "proposal_id": "proposal-a"})
    # Assert the actual management shape which caused the regression, rather
    # than injecting a convenient provider_state_uncertain proposal status.
    assert proposal["status"] == "failed"
    assert proposal["provider_write_state"] == "unknown_needs_reconciliation"
    assert proposal["provider_write_uncertain"] is True
    assert proposal["provider_write_reached"] is False
    assert len(patch_attempts) == 1 and failed_readbacks
    assert result["status"] == "provider_state_uncertain"
    assert result["provider_write_outcome_known"] is False
    assert result["writes_performed"] is False

    # Remove lease/cooldown as alternative explanations and publish a later
    # snapshot. The uncertain execution itself must fence the next account run.
    await db[autonomy.LEASE_COLLECTION].update_one({"user_id": "owner", "account_id": "account-a"}, {
        "$set": {"owner": None, "lease_until": NOW - timedelta(minutes=1),
                  "last_cycle_at": NOW - timedelta(days=1)},
    })
    await db[autonomy.RECOMMENDATION_COLLECTION].insert_one({
        "user_id": "owner", "snapshot_id": "snapshot-next",
        "generated_at": autonomy._utcnow().isoformat(),
        "recommendations": [proof["recommendation"]],
        "execution_targets": {"rec-a": proof["target"]},
    })
    next_cycle = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-next")
    assert next_cycle["accounts"][0]["blockers"] == ["previous_execution_requires_reconciliation"]
    assert len(patch_attempts) == 1

    # Owner reconciliation reads the actual mocked provider; it must send no
    # second PATCH and must not itself replay the execution claim.
    recovered_snapshot = {**original, "status": "PAUSED"} if reconciled_applied else original
    reconciled = await management.reconcile_snapchat_management_proposal(
        db, "owner", "owner", "proposal-a", provider=provider,
    )
    assert reconciled["status"] == ("completed" if reconciled_applied else "failed")
    if tamper == "binding":
        await db[autonomy.BINDING_COLLECTION].update_one(
            {"user_id": "owner", "proposal_id": "proposal-a"}, {"$set": {"account_id": "foreign"}},
        )
    if tamper == "proof":
        await db[management.PROPOSAL_COLLECTION].update_one(
            {"user_id": "owner", "proposal_id": "proposal-a"},
            {"$set": {"verification.field_changes_verified": False}},
        )
    updated = await autonomy.refresh_linked_execution_outcomes(db, "owner", "account-a")
    execution = await db[autonomy.EXECUTION_COLLECTION].find_one({"execution_id": binding["execution_id"]})
    if tamper:
        assert updated["refreshed"] == 0
        assert execution["status"] == "provider_state_uncertain"
        assert execution["provider_write_outcome_known"] is False
        blocked = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-next")
        assert blocked["accounts"][0]["blockers"] == ["previous_execution_requires_reconciliation"]
        assert len(patch_attempts) == 1
        return
    assert updated["refreshed"] == 1
    assert execution["status"] == ("completed" if reconciled_applied else "failed")
    assert execution["provider_write_outcome_known"] is True
    assert execution["writes_performed"] is reconciled_applied
    assert execution["approved_by"] is None
    assert execution["reconciliation"]["outcome"] == ("applied" if reconciled_applied else "not_applied")
    assert execution["audit"][-1]["actor_id"] == "snapchat-reconciliation-projection"
    assert len(patch_attempts) == 1
    # Neither the deterministic old claim nor its runner can issue a replay.
    duplicate = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a",
    )
    assert duplicate["claimed"] is False
    await legacy.run_claimed_campaign_execution(db, "owner", binding["execution_id"])
    assert len(patch_attempts) == 1
    old_cycle = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-next")
    assert old_cycle["accounts"][0]["blockers"] == ["fresh_snapshot_required_after_reconciliation"]
    claim = AsyncMock(return_value={"claimed": False, "execution_id": "future-existing",
                                   "execution": {"status": "completed"}})
    monkeypatch.setattr(legacy, "claim_campaign_recommendation_execution", claim)
    await db[autonomy.RECOMMENDATION_COLLECTION].insert_one({
        "user_id": "owner", "snapshot_id": "snapshot-fresh",
        "generated_at": (autonomy._utcnow() + timedelta(microseconds=1)).isoformat(),
        "recommendations": [proof["recommendation"]], "execution_targets": {"rec-a": proof["target"]},
    })
    future_cycle = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-fresh")
    assert future_cycle["accounts"][0]["actions"][0]["status"] == "completed"
    claim.assert_awaited_once()
    assert claim.await_args.kwargs["snapshot_id"] == "snapshot-fresh"
    allowance = await db[autonomy.ALLOWANCE_COLLECTION].find_one({"user_id": "owner"})
    assert allowance["execution_ids"] == [binding["execution_id"]]
    assert len(patch_attempts) == 1


@pytest.mark.asyncio
async def test_cancellation_before_binding_is_known_no_send_and_never_replays(db, monkeypatch):
    _, authorization = await grant(db)
    proof = checked()
    await snapshot(db, [proof]); install_preflight(monkeypatch, proof)
    claim = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    entered, forever = asyncio.Event(), asyncio.Event()
    async def prepare(*args, **kwargs):
        entered.set()
        await forever.wait()
    monkeypatch.setattr(autonomy, "prepare_autonomous_execution", prepare)
    execute = AsyncMock()
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    task = asyncio.create_task(legacy.run_claimed_campaign_execution(db, "owner", claim["execution_id"]))
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await db[autonomy.EXECUTION_COLLECTION].find_one({"execution_id": claim["execution_id"]})
    assert row["status"] == "cancelled"
    assert row["provider_write_outcome_known"] is True and row["writes_performed"] is False
    assert row["failure"]["code"] == "campaign_execution_cancelled_before_provider_write"
    assert await db[autonomy.BINDING_COLLECTION].count_documents({}) == 0
    assert await db[management.PROPOSAL_COLLECTION].count_documents({}) == 0
    await legacy.run_claimed_campaign_execution(db, "owner", claim["execution_id"])
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_during_transport_retains_uncertainty_and_account_fence(db, monkeypatch):
    operation = await transport_binding(db, monkeypatch)
    proof = checked(action="pause")
    await snapshot(db, [proof])
    original = {"id": "rec-a", "ad_account_id": "account-a", "name": "Synthetic campaign",
                "status": "ACTIVE", "daily_budget_micro": 100_000_000}
    row = _approved_row("proposal-a", operation)
    row.update(user_id="owner", account_id="account-a", target_id="rec-a", original_snapshot=original)
    await db[management.PROPOSAL_COLLECTION].insert_one(row)
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")
    monkeypatch.setattr(management, "_selected_account", AsyncMock(return_value={"ad_account_id": "account-a", "currency": "USD"}))
    provider = management.SnapchatManagementProvider(db, "owner")
    provider.management_role = AsyncMock(return_value={"allowed": True, "role": "admin"})
    provider.context.access_token = AsyncMock(return_value="synthetic-token")
    entered, forever = asyncio.Event(), asyncio.Event()
    writes = []
    async def handler(request):
        if request.method == "PATCH":
            writes.append(request)
            entered.set()
            await forever.wait()
        return httpx.Response(200, json={"request_status": "SUCCESS", "campaigns": [{
            "sub_request_status": "SUCCESS", "campaign": original,
        }]})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(management.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    async def execute(_db, user_id, **options):
        return await management.execute_snapchat_management_proposal(db, user_id, options["actor_id"], "proposal-a", provider=provider)
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    binding = await db[autonomy.BINDING_COLLECTION].find_one({"user_id": "owner", "proposal_id": "proposal-a"})
    task = asyncio.create_task(legacy.run_claimed_campaign_execution(db, "owner", binding["execution_id"]))
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    execution = await db[autonomy.EXECUTION_COLLECTION].find_one({"execution_id": binding["execution_id"]})
    assert execution["status"] == "provider_state_uncertain"
    assert execution["provider_write_outcome_known"] is False
    assert execution["writes_performed"] is False
    assert execution["failure"]["code"] == "campaign_execution_cancelled_outcome_unknown"
    assert len(writes) == 1
    await legacy.run_claimed_campaign_execution(db, "owner", binding["execution_id"])
    assert len(writes) == 1
    later = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-a")
    assert later["accounts"][0]["blockers"] == ["previous_execution_requires_reconciliation"]
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_cancelled_runner_claim_ack_loss_is_known_no_send_and_never_replays(db, monkeypatch):
    _, authorization = await grant(db)
    proof = checked()
    await snapshot(db, [proof]); install_preflight(monkeypatch, proof)
    claim = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    real_collection = db[autonomy.EXECUTION_COLLECTION]
    persisted, forever = asyncio.Event(), asyncio.Event()
    lost_once = False

    class LostAckCollection:
        def __getattr__(self, name):
            return getattr(real_collection, name)
        async def update_one(self, query, update, **options):
            nonlocal lost_once
            result = await real_collection.update_one(query, update, **options)
            if (not lost_once and result.matched_count
                    and "runner_claim_token" in update.get("$set", {})):
                lost_once = True
                persisted.set()
                await forever.wait()
            return result

    wrapped_collection = LostAckCollection()
    class WrappedDB:
        def __getitem__(self, name):
            return wrapped_collection if name == autonomy.EXECUTION_COLLECTION else db[name]

    execute = AsyncMock()
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    task = asyncio.create_task(legacy.run_claimed_campaign_execution(WrappedDB(), "owner", claim["execution_id"]))
    await asyncio.wait_for(persisted.wait(), timeout=3)
    started = await real_collection.find_one({"execution_id": claim["execution_id"]})
    assert started["runner_started_at"] and started["runner_claim_token"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancelled = await real_collection.find_one({"execution_id": claim["execution_id"]})
    assert cancelled["status"] == "cancelled"
    assert cancelled["provider_write_outcome_known"] is True
    assert cancelled["writes_performed"] is False
    assert cancelled["runner_claim_token"] == started["runner_claim_token"]
    assert await db[autonomy.BINDING_COLLECTION].count_documents({}) == 0
    await legacy.run_claimed_campaign_execution(db, "owner", claim["execution_id"])
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_during_initial_read_cannot_cancel_concurrent_runner(db, monkeypatch):
    _, authorization = await grant(db)
    proof = checked()
    await snapshot(db, [proof]); install_preflight(monkeypatch, proof)
    claim = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    real_collection = db[autonomy.EXECUTION_COLLECTION]
    read_entered, read_forever = asyncio.Event(), asyncio.Event()
    read_once = False

    class DelayedReadCollection:
        def __getattr__(self, name):
            return getattr(real_collection, name)
        async def find_one(self, query, **options):
            nonlocal read_once
            result = await real_collection.find_one(query, **options)
            if not read_once:
                read_once = True
                read_entered.set()
                await read_forever.wait()
            return result

    wrapped_collection = DelayedReadCollection()
    class WrappedDB:
        def __getitem__(self, name):
            return wrapped_collection if name == autonomy.EXECUTION_COLLECTION else db[name]

    winner_entered, resume_winner = asyncio.Event(), asyncio.Event()
    original_prepare = autonomy.prepare_autonomous_execution
    async def prepare(*args, **options):
        winner_entered.set()
        await resume_winner.wait()
        return await original_prepare(*args, **options)
    monkeypatch.setattr(autonomy, "prepare_autonomous_execution", prepare)
    execute = AsyncMock(return_value={"status": "completed", "provider_write_reached": False})
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    cancelled_reader = asyncio.create_task(legacy.run_claimed_campaign_execution(WrappedDB(), "owner", claim["execution_id"]))
    await asyncio.wait_for(read_entered.wait(), timeout=3)
    winner = asyncio.create_task(legacy.run_claimed_campaign_execution(db, "owner", claim["execution_id"]))
    await asyncio.wait_for(winner_entered.wait(), timeout=3)
    owned = await real_collection.find_one({"execution_id": claim["execution_id"]})
    cancelled_reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_reader
    after_cancel = await real_collection.find_one({"execution_id": claim["execution_id"]})
    resume_winner.set()
    result = await asyncio.wait_for(winner, timeout=3)
    assert after_cancel["status"] == "executing"
    assert after_cancel["runner_claim_token"] == owned["runner_claim_token"]
    assert "cancelled_at" not in after_cancel
    assert result["status"] == "completed"
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_during_initial_read_without_winner_is_known_no_send(db, monkeypatch):
    _, authorization = await grant(db)
    proof = checked()
    await snapshot(db, [proof]); install_preflight(monkeypatch, proof)
    claim = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    real_collection = db[autonomy.EXECUTION_COLLECTION]
    read_entered, read_forever = asyncio.Event(), asyncio.Event()
    read_once = False

    class DelayedReadCollection:
        def __getattr__(self, name):
            return getattr(real_collection, name)
        async def find_one(self, query, **options):
            nonlocal read_once
            result = await real_collection.find_one(query, **options)
            if not read_once:
                read_once = True
                read_entered.set()
                # The database has returned the row, but the caller has not
                # received it. Cancellation therefore leaves runner row=None.
                await read_forever.wait()
            return result

    wrapped_collection = DelayedReadCollection()
    class WrappedDB:
        def __getitem__(self, name):
            return wrapped_collection if name == autonomy.EXECUTION_COLLECTION else db[name]

    execute = AsyncMock()
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    task = asyncio.create_task(legacy.run_claimed_campaign_execution(WrappedDB(), "owner", claim["execution_id"]))
    await asyncio.wait_for(read_entered.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancelled = await real_collection.find_one({"execution_id": claim["execution_id"]})
    assert cancelled["status"] == "cancelled"
    assert cancelled["provider_write_outcome_known"] is True
    assert cancelled["writes_performed"] is False
    assert cancelled["failure"]["code"] == "campaign_execution_cancelled_before_provider_write"
    assert cancelled.get("runner_started_at") is None
    assert cancelled.get("runner_claim_token") is None
    stored_snapshot = await db[autonomy.RECOMMENDATION_COLLECTION].find_one({
        "user_id": "owner", "snapshot_id": "snapshot-a",
    })
    stored_recommendation = next(
        item for item in stored_snapshot["recommendations"] if item["recommendation_id"] == "rec-a"
    )
    assert stored_recommendation["execution_status"] == "cancelled"
    assert await db[autonomy.BINDING_COLLECTION].count_documents({}) == 0
    assert await db[management.PROPOSAL_COLLECTION].count_documents({}) == 0
    await legacy.run_claimed_campaign_execution(db, "owner", claim["execution_id"])
    execute.assert_not_awaited()
