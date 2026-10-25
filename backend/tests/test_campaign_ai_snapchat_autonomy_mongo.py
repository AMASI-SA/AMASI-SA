"""Real isolated Mongo claims/revocation; provider boundary is synthetic."""
import asyncio
import os
import uuid
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlsplit
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from bson.codec_options import CodecOptions
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

import campaign_ai_snapchat_autonomy as autonomy
import campaign_ai_monitor_legacy as legacy
from tests.test_campaign_ai_snapchat_autonomy import NOW, ACCOUNT, payload, checked


@pytest_asyncio.fixture
async def db(monkeypatch):
    uri = os.environ.get("SNAPCHAT_AUTONOMY_TEST_MONGO_URL")
    if not uri:
        if os.environ.get("REQUIRE_SNAPCHAT_AUTONOMY_MONGO") == "1":
            pytest.fail("dedicated isolated Mongo test URI is required")
        pytest.skip("dedicated isolated Mongo test URI not configured")
    if urlsplit(uri).hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("autonomy tests require loopback-only disposable Mongo")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    database = client["snap_autonomy_" + uuid.uuid4().hex]
    await client.admin.command("ping")
    monkeypatch.setattr(autonomy, "executor_ready", lambda: True)
    async def selected(_db, user_id):
        return [deepcopy(ACCOUNT)] if user_id == "owner" else []
    monkeypatch.setattr(autonomy, "_load_selected_accounts", selected)
    try:
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


async def grant(db, **changes):
    await autonomy.save_snapchat_autonomy_policy(
        db, "owner", "owner", "account-a", payload(**changes), now=lambda: NOW,
    )
    policy = await db[autonomy.POLICY_COLLECTION].find_one({"user_id": "owner"})
    token = await autonomy._claim_account_cycle(db, "owner", policy, now=lambda: NOW)
    authorization = {"kind": "account_policy", "policy_id": policy["_id"],
                     "account_id": "account-a", "revision": policy["revision"],
                     "grant_id": policy["grant_id"], "lease_owner": token,
                     "actor_id": f"snapchat-policy:{policy['_id']}:{policy['revision']}",
                     "granted_by": "owner", "version": autonomy.FENCE_VERSION}
    return policy, authorization


async def snapshot(db, proofs):
    await db[autonomy.RECOMMENDATION_COLLECTION].insert_one({
        "user_id": "owner", "snapshot_id": "snapshot-a", "generated_at": NOW.isoformat(),
        "recommendations": [proof["recommendation"] for proof in proofs],
        "execution_targets": {proof["recommendation"]["recommendation_id"]: proof["target"] for proof in proofs},
    })


def install_preflight(monkeypatch, proof=None):
    proof = proof or checked()
    monkeypatch.setattr(legacy._execution_quality, "preflight_approved_execution", AsyncMock(return_value=deepcopy(proof)))


@pytest.mark.asyncio
async def test_real_mongo_manual_and_policy_race_share_one_execution_claim(db, monkeypatch):
    _, authorization = await grant(db)
    install_preflight(monkeypatch)
    claims = await asyncio.gather(
        legacy.claim_campaign_recommendation_execution(db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a"),
        legacy.claim_campaign_recommendation_execution(db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization),
    )
    assert sum(row["claimed"] for row in claims) == 1
    assert len({row["execution_id"] for row in claims}) == 1
    assert await db[autonomy.EXECUTION_COLLECTION].count_documents({}) == 1


@pytest.mark.asyncio
async def test_real_mongo_policy_revision_race_has_one_winner(db):
    await grant(db)
    results = await asyncio.gather(*[
        autonomy.save_snapchat_autonomy_policy(db, "owner", "owner", "account-a",
            payload(revision=1, max_change_percent=percent), now=lambda: NOW)
        for percent in (10, 20)
    ], return_exceptions=True)
    assert sum(isinstance(value, dict) for value in results) == 1
    assert sum(isinstance(value, HTTPException) and value.detail["code"] == "snapchat_autonomy_revision_conflict" for value in results) == 1
    row = await db[autonomy.POLICY_COLLECTION].find_one({"user_id": "owner"})
    assert row["revision"] == 2 and len(row["audit"]) == 2


@pytest.mark.asyncio
async def test_real_mongo_only_one_replica_claims_account_cycle(db):
    policy, _ = await grant(db)
    await db[autonomy.LEASE_COLLECTION].delete_many({})
    tokens = await asyncio.gather(*[
        autonomy._claim_account_cycle(db, "owner", policy, now=lambda: NOW) for _ in range(4)
    ])
    assert sum(token is not None for token in tokens) == 1


@pytest.mark.asyncio
async def test_real_mongo_native_increase_allowance_cannot_be_double_reserved(db):
    _, authorization = await grant(db)
    executions, proofs = [], []
    for index in range(2):
        proof = checked(f"rec-{index}", action="scale")
        execution = {"_id": f"execution-{index}", "execution_id": f"execution-{index}",
                     "user_id": "owner", "account_id": "account-a", "status": "executing",
                     "authorization": authorization, "authorization_kind": "account_policy"}
        await db[autonomy.EXECUTION_COLLECTION].insert_one(execution)
        executions.append(execution); proofs.append(proof)
    outcomes = await asyncio.gather(*[
        autonomy.prepare_autonomous_execution(db, "owner", execution, proof, now=lambda: NOW)
        for execution, proof in zip(executions, proofs)
    ], return_exceptions=True)
    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert sum(isinstance(value, HTTPException) and value.detail["code"] == "snapchat_autonomy_daily_increase_allowance_exceeded" for value in outcomes) == 1
    allowance = await db[autonomy.ALLOWANCE_COLLECTION].find_one({"user_id": "owner"})
    assert allowance["reserved_increase_micro"] == 15_000_000
    assert len(allowance["execution_ids"]) == 1


async def synthetic_execution(db, monkeypatch, *, revoke_before_write=False, uncertain=False):
    _, authorization = await grant(db)
    proof = checked()
    await snapshot(db, [proof]); install_preflight(monkeypatch, proof)
    attempted = []
    async def execute(_db, user_id, **options):
        assert options["actor_id"] == authorization["actor_id"]
        changes, _ = legacy._snapchat_recommendation_payload(proof["recommendation"], proof["target"])
        await autonomy.bind_autonomous_proposal(
            db, user_id, options["idempotency_key"], "proposal-a",
            proof["recommendation"], proof["target"], changes,
        )
        if revoke_before_write:
            await autonomy.revoke_snapchat_autonomy_policy(db, "owner", "owner", "account-a", 1)
        await autonomy.require_snapchat_autonomy_write_authorization(
            db, user_id="owner", proposal_id="proposal-a", account_id="account-a",
            target_id="rec-a", action="campaign.update", changes=changes,
        )
        attempted.append(changes)
        if uncertain:
            await db["mezan_snapchat_campaign_proposals_v1"].insert_one({
                "user_id": "owner", "proposal_id": "proposal-a",
                "provider_write_state": "attempting", "provider_write_reached": False,
                "status": "provider_state_uncertain",
            })
            raise HTTPException(status_code=502, detail={"code": "synthetic_timeout"})
        return {"status": "completed", "provider_write_reached": True}
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    claim = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    result = await legacy.run_claimed_campaign_execution(db, "owner", claim["execution_id"])
    return result, attempted, claim


@pytest.mark.asyncio
async def test_automatic_execution_has_truthful_actor_and_runs_only_once(db, monkeypatch):
    result, attempted, claim = await synthetic_execution(db, monkeypatch)
    assert result["status"] == "completed" and len(attempted) == 1
    row = await db[autonomy.EXECUTION_COLLECTION].find_one({"execution_id": claim["execution_id"]})
    assert row["approved_by"] is None and row["approved_at"] is None
    assert row["policy_authorized_by"] == "owner"
    assert row["executed_by"].startswith("snapchat-policy:")
    await legacy.run_claimed_campaign_execution(db, "owner", claim["execution_id"])
    assert len(attempted) == 1


@pytest.mark.asyncio
async def test_revocation_at_final_fence_prevents_synthetic_provider_write(db, monkeypatch):
    result, attempted, _ = await synthetic_execution(db, monkeypatch, revoke_before_write=True)
    assert attempted == []
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "snapchat_autonomy_policy_revoked"


@pytest.mark.asyncio
async def test_uncertain_provider_outcome_blocks_later_account_cycle(db, monkeypatch):
    result, attempted, _ = await synthetic_execution(db, monkeypatch, uncertain=True)
    assert result["status"] == "provider_state_uncertain" and len(attempted) == 1
    summary = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-a")
    assert summary["accounts"][0]["blockers"] == ["previous_execution_requires_reconciliation"]
    assert len(attempted) == 1


@pytest.mark.asyncio
async def test_disabled_policy_and_empty_grants_do_not_claim_execution(db, monkeypatch):
    await snapshot(db, [checked()])
    claim = AsyncMock()
    monkeypatch.setattr(legacy, "claim_campaign_recommendation_execution", claim)
    summary = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-a")
    assert summary["accounts"] == []
    claim.assert_not_awaited()
    assert await db[autonomy.EXECUTION_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_shadow_respects_combined_daily_allowance_without_reserving_or_writing(db, monkeypatch):
    await autonomy.save_snapchat_autonomy_policy(
        db, "owner", "owner", "account-a", payload(mode="shadow"),
    )
    proofs = [checked("rec-a", action="scale"), checked("rec-b", action="scale")]
    await snapshot(db, proofs)
    preflight = AsyncMock(return_value={})
    monkeypatch.setattr(legacy._execution_quality, "preflight_approved_execution", preflight)
    result = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-a")
    actions = result["accounts"][0]["actions"]
    assert [row["status"] for row in actions] == ["shadow_eligible", "blocked"]
    assert actions[1]["code"] == "snapchat_autonomy_daily_increase_allowance_exceeded"
    assert await db[autonomy.ALLOWANCE_COLLECTION].count_documents({}) == 0
    assert await db[autonomy.EXECUTION_COLLECTION].count_documents({}) == 0
    preflight.assert_awaited_once()


@pytest.mark.asyncio
async def test_account_failure_does_not_discard_second_account_cycle(db, monkeypatch):
    second = {**ACCOUNT, "ad_account_id": "account-b", "external_account_id": "account-b"}
    monkeypatch.setattr(autonomy, "_load_selected_accounts", AsyncMock(return_value=[ACCOUNT, second]))
    for account_id in ("account-a", "account-b"):
        await autonomy.save_snapchat_autonomy_policy(db, "owner", "owner", account_id, payload())
    await db[autonomy.POLICY_COLLECTION].update_one(
        {"user_id": "owner", "account_id": "account-a"},
        {"$set": {"expires_at": (NOW - timedelta(days=1)).isoformat()}},
    )
    proofs = [checked("rec-a"), checked("rec-b", account_id="account-b")]
    await snapshot(db, proofs)
    claim = AsyncMock(return_value={"claimed": False, "execution_id": "existing",
                                   "execution": {"status": "completed"}})
    monkeypatch.setattr(legacy, "claim_campaign_recommendation_execution", claim)
    result = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-a")
    by_account = {row["account_id"]: row for row in result["accounts"]}
    assert by_account["account-a"]["blockers"] == ["policy_expired"]
    assert by_account["account-b"]["actions"][0]["status"] == "completed"
    claim.assert_awaited_once()
    assert claim.await_args.kwargs["recommendation_id"] == "rec-b"


@pytest.mark.asyncio
async def test_day_rollover_cannot_spend_a_previous_day_reservation(db, monkeypatch):
    _, authorization = await grant(db)
    proof = checked(action="scale")
    install_preflight(monkeypatch, proof)
    claim = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    execution = await db[autonomy.EXECUTION_COLLECTION].find_one({"execution_id": claim["execution_id"]})
    changes = await autonomy.prepare_autonomous_execution(db, "owner", execution, proof)
    await autonomy.bind_autonomous_proposal(
        db, "owner", claim["execution_id"], "proposal-a", proof["recommendation"], proof["target"], changes,
    )
    await db[autonomy.EXECUTION_COLLECTION].update_one(
        {"execution_id": claim["execution_id"]}, {"$set": {"autonomy_reserved.date": "1900-01-01"}},
    )
    with pytest.raises(HTTPException) as caught:
        await autonomy.require_snapchat_autonomy_write_authorization(
            db, user_id="owner", proposal_id="proposal-a", account_id="account-a",
            target_id="rec-a", action="campaign.update", changes=changes,
        )
    assert caught.value.detail["code"] == "snapchat_autonomy_allowance_day_or_operation_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("orphan_evidence", [None, "binding", "proposal"])
async def test_lost_insert_ack_claim_recovers_only_after_old_lease_and_without_links(
    db, monkeypatch, orphan_evidence,
):
    _, authorization = await grant(db)
    proof = checked()
    await snapshot(db, [proof]); install_preflight(monkeypatch, proof)
    real_collection = db[autonomy.EXECUTION_COLLECTION]
    inserted, forever = asyncio.Event(), asyncio.Event()

    class LostInsertAckCollection:
        def __getattr__(self, name):
            return getattr(real_collection, name)
        async def insert_one(self, row, **options):
            result = await real_collection.insert_one(row, **options)
            inserted.set()
            await forever.wait()
            return result

    wrapped_collection = LostInsertAckCollection()
    class WrappedDB:
        def __getitem__(self, name):
            return wrapped_collection if name == autonomy.EXECUTION_COLLECTION else db[name]

    task = asyncio.create_task(legacy.claim_campaign_recommendation_execution(
        WrappedDB(), "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    ))
    await asyncio.wait_for(inserted.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    original = await real_collection.find_one({"user_id": "owner"})
    assert original["status"] == "executing"
    assert original.get("runner_started_at") is None and original.get("runner_claim_token") is None
    execute = AsyncMock()
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    active_lease = await db[autonomy.LEASE_COLLECTION].find_one({"user_id": "owner"})
    active_result = await autonomy.dispatch_snapchat_autonomy(db, "owner", "snapshot-a", now=lambda: NOW)
    assert active_result["accounts"][0]["blockers"] == ["previous_execution_requires_reconciliation"]
    assert await real_collection.find_one({"execution_id": original["execution_id"]}) == original
    assert await db[autonomy.LEASE_COLLECTION].find_one({"user_id": "owner"}) == active_lease
    await db[autonomy.LEASE_COLLECTION].update_one(
        {"user_id": "owner", "owner": authorization["lease_owner"]},
        {"$set": {"lease_until": NOW - timedelta(seconds=1)}},
    )
    expired_lease = await db[autonomy.LEASE_COLLECTION].find_one({"user_id": "owner"})
    # Unrelated existing reservations must survive no-send recovery unchanged.
    await db[autonomy.ALLOWANCE_COLLECTION].insert_one({
        "_id": "existing-allowance", "user_id": "owner", "account_id": "account-a",
        "reserved_increase_micro": 17, "execution_ids": ["previous-execution"],
    })
    allowance = await db[autonomy.ALLOWANCE_COLLECTION].find_one({"user_id": "owner"})
    if orphan_evidence == "binding":
        await db[autonomy.BINDING_COLLECTION].insert_one({
            "user_id": "owner", "execution_id": original["execution_id"], "proposal_id": "unacknowledged-binding",
        })
    elif orphan_evidence == "proposal":
        await db["mezan_snapchat_campaign_proposals_v1"].insert_one({
            "user_id": "owner", "idempotency_key": original["execution_id"], "proposal_id": "unacknowledged-proposal",
        })
    recovered = await autonomy.refresh_linked_execution_outcomes(db, "owner", "account-a", now=lambda: NOW)
    stored = await real_collection.find_one({"execution_id": original["execution_id"]})
    assert await db[autonomy.LEASE_COLLECTION].find_one({"user_id": "owner"}) == expired_lease
    assert await db[autonomy.ALLOWANCE_COLLECTION].find_one({"user_id": "owner"}) == allowance
    execute.assert_not_awaited()
    if orphan_evidence:
        assert recovered["refreshed"] == 0
        assert stored == original
        return
    assert recovered["refreshed"] == 1
    assert stored["status"] == "cancelled"
    assert stored["writes_performed"] is False and stored["provider_write_outcome_known"] is True
    assert stored["failure"]["code"] == "campaign_execution_unstarted_lease_expired"
    assert stored["reconciliation"]["outcome"] == "not_sent"
    assert stored["audit"][-1]["event"] == "unstarted_execution_recovered"
    assert stored["approved_by"] is None and stored["executed_by"] == authorization["actor_id"]
    duplicate = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a",
    )
    assert duplicate["claimed"] is False and duplicate["execution"]["status"] == "cancelled"
    assert (await legacy.run_claimed_campaign_execution(db, "owner", original["execution_id"]))["status"] == "cancelled"
    execute.assert_not_awaited()
    stored_snapshot = await db[autonomy.RECOMMENDATION_COLLECTION].find_one({"user_id": "owner"})
    assert stored_snapshot["recommendations"][0]["execution_status"] == "cancelled"


@pytest.mark.asyncio
async def test_stale_unstarted_recovery_cannot_cancel_runner_winning_its_cas(db, monkeypatch):
    _, authorization = await grant(db)
    proof = checked()
    await snapshot(db, [proof]); install_preflight(monkeypatch, proof)
    claim = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    await db[autonomy.LEASE_COLLECTION].update_one(
        {"user_id": "owner", "owner": authorization["lease_owner"]},
        {"$set": {"lease_until": NOW - timedelta(seconds=1)}},
    )
    real_collection = db[autonomy.EXECUTION_COLLECTION]
    runner_entered, resume_runner = asyncio.Event(), asyncio.Event()
    real_prepare = autonomy.prepare_autonomous_execution
    async def prepare(*args, **options):
        runner_entered.set()
        await resume_runner.wait()
        return await real_prepare(*args, **options)
    monkeypatch.setattr(autonomy, "prepare_autonomous_execution", prepare)
    execute = AsyncMock()
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    winner = None

    class RacingCollection:
        def __getattr__(self, name):
            return getattr(real_collection, name)
        async def update_one(self, query, update, **options):
            nonlocal winner
            if update.get("$set", {}).get("reconciliation", {}).get("source") == "stale_unstarted_account_policy_claim":
                winner = asyncio.create_task(legacy.run_claimed_campaign_execution(db, "owner", claim["execution_id"]))
                await asyncio.wait_for(runner_entered.wait(), timeout=3)
            return await real_collection.update_one(query, update, **options)

    wrapped_collection = RacingCollection()
    class WrappedDB:
        def __getitem__(self, name):
            return wrapped_collection if name == autonomy.EXECUTION_COLLECTION else db[name]

    recovered = await autonomy.refresh_linked_execution_outcomes(WrappedDB(), "owner", "account-a", now=lambda: NOW)
    owned = await real_collection.find_one({"execution_id": claim["execution_id"]})
    resume_runner.set()
    assert winner is not None
    finished = await asyncio.wait_for(winner, timeout=3)
    assert recovered["refreshed"] == 0
    assert owned["status"] == "executing" and owned["runner_started_at"] and owned["runner_claim_token"]
    assert "reconciliation" not in owned and "cancelled_at" not in owned
    assert not any(event["event"] == "unstarted_execution_recovered" for event in owned.get("audit", []))
    assert finished["status"] == "failed"
    assert finished["failure"]["code"] == "snapchat_autonomy_account_lease_lost"
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_stale_unstarted_recovery_interprets_naive_bson_lease_dates_as_utc(
    db, monkeypatch, expired,
):
    _, authorization = await grant(db)
    proof = checked()
    await snapshot(db, [proof]); install_preflight(monkeypatch, proof)
    claim = await legacy.claim_campaign_recommendation_execution(
        db, "owner", snapshot_id="snapshot-a", recommendation_id="rec-a", authorization=authorization,
    )
    if expired:
        await db[autonomy.LEASE_COLLECTION].update_one(
            {"user_id": "owner", "owner": authorization["lease_owner"]},
            {"$set": {"lease_until": NOW - timedelta(seconds=1)}},
        )
    # Match the production worker's default Motor BSON datetime decoding.
    naive_db = db.with_options(codec_options=CodecOptions(tz_aware=False))
    lease = await naive_db[autonomy.LEASE_COLLECTION].find_one({"user_id": "owner"})
    assert lease["owner"] == authorization["lease_owner"]
    assert lease["lease_until"].tzinfo is None
    execution = await naive_db[autonomy.EXECUTION_COLLECTION].find_one({"execution_id": claim["execution_id"]})
    execute = AsyncMock()
    monkeypatch.setattr(legacy, "_execute_approved_recommendation", execute)
    recovered = await autonomy.refresh_linked_execution_outcomes(
        naive_db, "owner", "account-a", now=lambda: NOW,
    )
    stored = await naive_db[autonomy.EXECUTION_COLLECTION].find_one({"execution_id": claim["execution_id"]})
    assert await naive_db[autonomy.LEASE_COLLECTION].find_one({"user_id": "owner"}) == lease
    if not expired:
        assert recovered["refreshed"] == 0 and stored == execution
        assert stored["status"] == "executing"
    else:
        assert recovered["refreshed"] == 1
        assert stored["status"] == "cancelled"
        assert stored["writes_performed"] is False and stored["provider_write_outcome_known"] is True
        assert stored["reconciliation"]["outcome"] == "not_sent"
        assert (await legacy.run_claimed_campaign_execution(
            naive_db, "owner", claim["execution_id"],
        ))["status"] == "cancelled"
    assert await naive_db[autonomy.BINDING_COLLECTION].count_documents({}) == 0
    assert await naive_db[autonomy.ALLOWANCE_COLLECTION].count_documents({}) == 0
    execute.assert_not_awaited()
