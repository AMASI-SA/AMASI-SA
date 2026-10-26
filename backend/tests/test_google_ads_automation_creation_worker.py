"""Hermetic creation cycles: no provider, model, or actual Mongo connection."""
from copy import deepcopy
from datetime import timedelta
import asyncio

import pytest

from integrations_control_center import google_ads_automation_creation_worker as worker
from integrations_control_center.google_ads_automation_creation_contract import prepare_google_creation_intent
from tests.test_google_ads_automation_creation_contract import prepared_case, NOW
from tests.test_google_ads_automation_worker import DB


def setup(monkeypatch):
    for flag in ("WORKER", "EXECUTION", "CREATION"):
        monkeypatch.setenv(f"GOOGLE_ADS_AUTOMATION_{flag}_ENABLED", "true")
    values = prepared_case()
    db = DB()
    db[worker.POLICIES].rows = [deepcopy(values["policy"])]
    intent = prepare_google_creation_intent(**values)
    plan = {"provider": "google_ads", "account_id": "123", "provider_writes": 0,
        "executable": False, "initial_status": "PAUSED", "ready_for_paused_preview": True,
        "blockers": [], "intent": intent, "choice": values["choice"]}
    calls = []
    async def builder(database, **kwargs): calls.append("plan"); return deepcopy(plan)
    async def preview(database, **kwargs):
        calls.append("preview")
        assert set(kwargs) == {"user_id", "account_id", "policy_revision", "choice"}
        assert kwargs["choice"] == values["choice"]
        return {"provider": "google_ads", "account_id": "123", "actor_id": "mezan_google_automation_v1",
            "action": "create_campaign", "proposal_id": "proposal1", "status": "previewed",
            "automation_source": {"kind": "standing_creation", "creation_identity": intent["creation_identity"]},
            "planned": {"initial_status": "PAUSED"}}
    async def execute(database, **kwargs): calls.append("execute"); return {"status": "completed"}
    async def reconcile(database, **kwargs): calls.append("reconcile"); return {"status": "uncertain"}
    deps = dict(plan_builder=builder, previewer=preview, executor=execute, reconciler=reconcile, clock=lambda: NOW)
    return db, calls, deps, plan


def pending(status="uncertain", submitted=True):
    return {"user_id": "owner", "account_id": "123", "proposal_id": "proposal1", "status": status,
        "action": "create_campaign", "actor_id": "mezan_google_automation_v1", "submission_started": submitted,
        "execution_claim_id": "claim1", "automation_source": {"kind": "standing_creation"}}


@pytest.mark.asyncio
async def test_default_off_no_database(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", raising=False)
    class NoDB:
        def __getitem__(self, name): raise AssertionError("database touched")
    assert (await worker.run_google_creation_cycle(NoDB()))["status"] == "disabled"


@pytest.mark.asyncio
async def test_one_paused_creation_and_independent_cadence(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    result = await worker.run_google_creation_cycle(db, **deps)
    assert calls == ["plan", "preview", "execute"]
    assert result["accounts"] == 1 and result["results"][0]["activation_performed"] is False
    cycle = db[worker.CYCLES].rows[0]
    assert "next_run_at" not in cycle
    second = await worker.run_google_creation_cycle(db, **deps)
    assert second["accounts"] == 0 and second["results"][0]["status"] == "not_due"


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["EXECUTION", "CREATION"])
async def test_revoked_recovery_even_with_write_flag_off(monkeypatch, flag):
    db, calls, deps, _ = setup(monkeypatch)
    monkeypatch.setenv(f"GOOGLE_ADS_AUTOMATION_{flag}_ENABLED", "false")
    db[worker.POLICIES].rows[0]["enabled"] = False
    db[worker.PROPOSALS].rows = [pending()]
    before = deepcopy(db[worker.PROPOSALS].rows)
    result = await worker.run_google_creation_cycle(db, **deps)
    assert result["status"] == "recovery_only" and calls == ["reconcile"]
    assert db[worker.PROPOSALS].rows == before


@pytest.mark.asyncio
async def test_terminal_orphan_private_claim_recovery(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_CREATION_ENABLED", "false")
    db[worker.POLICIES].rows[0].update(enabled=False, _creation_claim={"claim_id": "claim1", "execution_claim_id": "claim1", "proposal_id": "proposal1"})
    db[worker.PROPOSALS].rows = [pending("completed")]
    assert (await worker.run_google_creation_cycle(db, **deps))["accounts"] == 1
    assert calls == ["reconcile"]


@pytest.mark.asyncio
@pytest.mark.parametrize("age,expected", [(299, []), (301, ["reconcile"])])
async def test_unsubmitted_requires_stale_original_native_fence(monkeypatch, age, expected):
    db, calls, deps, _ = setup(monkeypatch)
    db[worker.PROPOSALS].rows = [pending("executing", False)]
    db[worker.FENCES].rows = [{"user_id": "owner", "account_id": "123", "proposal_id": "proposal1",
        "execution_claim_id": "claim1", "status": "claimed", "claimed_at": (NOW-timedelta(seconds=age)).isoformat()}]
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == expected
    assert db[worker.FENCES].rows[0]["status"] == "claimed"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("enabled", False), ("activation_version", None), ("activation_revision", 1), ("activated_by", "other"), ("auto_enable_new", True)])
async def test_legacy_or_unapproved_policy_never_creates(monkeypatch, field, value):
    db, calls, deps, _ = setup(monkeypatch)
    db[worker.POLICIES].rows[0][field] = value
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == []


@pytest.mark.asyncio
async def test_management_mutex_and_due_timestamp_preserved(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    claim = await worker._claim(db, {"user_id": "owner", "account_id": "123"}, NOW, False)
    db[worker.CYCLES].rows[0]["next_run_at"] = NOW+timedelta(hours=5)
    assert (await worker.run_google_creation_cycle(db, **deps))["accounts"] == 0
    assert not calls
    await worker._release(db, claim, {"status": "completed"}, NOW, False)
    assert db[worker.CYCLES].rows[0]["next_run_at"] == NOW+timedelta(hours=5)


@pytest.mark.asyncio
async def test_foreign_pending_blocks_without_adoption(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    row = pending(); row["automation_source"]["kind"] = "standing_policy"
    db[worker.PROPOSALS].rows = [row]
    await worker.run_google_creation_cycle(db, **deps)
    assert not calls


@pytest.mark.asyncio
async def test_change_during_plan_blocks_preview(monkeypatch):
    db, calls, deps, plan = setup(monkeypatch)
    async def builder(database, **kwargs):
        db[worker.POLICIES].rows[0]["enabled"] = False
        return plan
    deps["plan_builder"] = builder
    await worker.run_google_creation_cycle(db, **deps)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("provider_writes", True), ("executable", True), ("initial_status", "ENABLED")])
async def test_untrusted_plan_never_previews(monkeypatch, field, value):
    db, calls, deps, plan = setup(monkeypatch)
    plan[field] = value
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == ["plan"]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, 5, "4"])
async def test_strict_account_budget(monkeypatch, limit):
    db, _, deps, _ = setup(monkeypatch)
    with pytest.raises(ValueError): await worker.run_google_creation_cycle(db, max_accounts=limit, **deps)


@pytest.mark.asyncio
async def test_unknown_execution_is_not_resent(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    async def execute(database, **kwargs):
        calls.append("execute")
        db[worker.PROPOSALS].rows = [pending()]
        raise RuntimeError("sensitive provider output")
    deps["executor"] = execute
    first = await worker.run_google_creation_cycle(db, **deps)
    assert first["results"][0]["code"] == "google_creation_cycle_failed"
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == ["plan", "preview", "execute", "reconcile"]


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_flag,exec_flag,creation_flag,expected", [
    ("enabled", "true", "true", True), ("true", "enabled", "true", False),
    ("true", "true", "enabled", False), ("false", "true", "true", False)])
async def test_flag_semantics_match_gateway(monkeypatch, worker_flag, exec_flag, creation_flag, expected):
    from integrations_control_center.google_ads_automation_creation_gateway import creation_dispatch_enabled
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", worker_flag)
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", exec_flag)
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_CREATION_ENABLED", creation_flag)
    assert worker.creation_execution_enabled() is expected
    assert creation_dispatch_enabled() is expected


@pytest.mark.asyncio
async def test_global_acquired_account_cap_and_not_due_does_not_count(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    db[worker.POLICIES].rows = []
    db[worker.PROPOSALS].rows = [dict(pending(), account_id=str(i)) for i in range(100, 106)]
    # A management worker owns the first account; its locked cycle must not
    # consume any of the four real account processing slots.
    await worker._claim(db, {"user_id": "owner", "account_id": "100"}, NOW, True)
    result = await worker.run_google_creation_cycle(db, **deps)
    assert result["accounts"] == 4 and result["considered"] == 5
    assert calls == ["reconcile"]*4
    assert result["deferred"] == 1


@pytest.mark.asyncio
async def test_replica_concurrency_admits_single_account(monkeypatch):
    db, calls, deps, plan = setup(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    async def builder(database, **kwargs):
        calls.append("plan"); entered.set(); await release.wait(); return plan
    deps["plan_builder"] = builder
    task = asyncio.create_task(worker.run_google_creation_cycle(db, **deps))
    await entered.wait()
    try:
        second = await worker.run_google_creation_cycle(db, **deps)
        assert second["accounts"] == 0
    finally: release.set()
    await task
    assert calls == ["plan", "preview", "execute"]


@pytest.mark.asyncio
async def test_cancel_after_submission_preserves_native_fence_and_recovers(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    async def execute(database, **kwargs):
        calls.append("execute")
        db[worker.PROPOSALS].rows = [pending()]
        db[worker.FENCES].rows = [{"user_id": "owner", "account_id": "123", "status": "claimed"}]
        raise asyncio.CancelledError()
    deps["executor"] = execute
    with pytest.raises(asyncio.CancelledError): await worker.run_google_creation_cycle(db, **deps)
    assert db[worker.FENCES].rows[0]["status"] == "claimed"
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", "false")
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == ["plan", "preview", "execute", "reconcile"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoked", "kill", "claim_lost"])
async def test_change_during_preview_blocks_dispatch(monkeypatch, change):
    db, calls, deps, _ = setup(monkeypatch)
    original = deps["previewer"]
    async def preview(database, **kwargs):
        result = await original(database, **kwargs)
        if change == "revoked": db[worker.POLICIES].rows[0]["enabled"] = False
        elif change == "kill": monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_CREATION_ENABLED", "false")
        else: db[worker.CYCLES].rows[0]["claim_token"] = "another"
        return result
    deps["previewer"] = preview
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == ["plan", "preview"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("actor_id", "human"), ("account_id", "999"), ("user_id", "foreign"), ("action", "enable")])
async def test_untrusted_preview_never_executes(monkeypatch, field, value):
    db, calls, deps, _ = setup(monkeypatch)
    original = deps["previewer"]
    async def preview(database, **kwargs):
        result = await original(database, **kwargs); result[field] = value; return result
    deps["previewer"] = preview
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == ["plan", "preview"]


@pytest.mark.asyncio
async def test_ambiguous_pending_never_reconciles_or_creates(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    db[worker.PROPOSALS].rows = [pending(), dict(pending(), proposal_id="proposal2")]
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == []


@pytest.mark.asyncio
async def test_recovery_preserves_bson_naive_creation_and_management_cadence(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    scope = {"user_id": "owner", "account_id": "123"}
    claim = await worker._claim(db, scope, NOW, False)
    await worker._release(db, claim, {"status": "completed"}, NOW, False)
    due = NOW+timedelta(hours=8)
    db[worker.CYCLES].rows[0].update(creation_next_run_at=due.replace(tzinfo=None), next_run_at=NOW+timedelta(hours=9))
    db[worker.PROPOSALS].rows = [pending()]
    await worker.run_google_creation_cycle(db, **deps)
    assert calls == ["reconcile"]
    assert db[worker.CYCLES].rows[0]["creation_next_run_at"] == due
    assert db[worker.CYCLES].rows[0]["next_run_at"] == NOW+timedelta(hours=9)


@pytest.mark.asyncio
async def test_off_without_pending_has_no_cycle_claim(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", "false")
    result = await worker.run_google_creation_cycle(db, **deps)
    assert result["accounts"] == 0 and not calls and worker.CYCLES not in db.collections


@pytest.mark.asyncio
async def test_persisted_disabled_owner_blocks_creation(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    db.users.rows[0]["disabled"] = True
    await worker.run_google_creation_cycle(db, **deps)
    assert not calls


@pytest.mark.asyncio
async def test_shared_native_fence_blocks_new_creation(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    db[worker.FENCES].rows = [{"user_id": "owner", "account_id": "123", "status": "claimed"}]
    await worker.run_google_creation_cycle(db, **deps)
    assert not calls


@pytest.mark.asyncio
async def test_account_timeout_releases_only_cycle_mutex(monkeypatch):
    db, calls, deps, _ = setup(monkeypatch)
    monkeypatch.setattr(worker, "ACCOUNT_TIMEOUT_SECONDS", 0.01)
    async def builder(database, **kwargs): await asyncio.Event().wait()
    deps["plan_builder"] = builder
    result = await worker.run_google_creation_cycle(db, **deps)
    assert result["results"][0] == {"account_id": "123", "status": "failed", "code": "google_creation_cycle_failed"}
    assert db[worker.CYCLES].rows[0]["state"] == "idle"
    assert db[worker.CYCLES].rows[0]["creation_next_run_at"] == NOW+timedelta(minutes=15)
    assert not calls
