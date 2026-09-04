from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import decision_intelligence.proposal_bridge as bridge
import decision_intelligence.scheduler as scheduler_module
from decision_intelligence.scheduler import Phase5SchedulerConfig, Phase5ShadowScheduler

NOW = datetime.now(timezone.utc)


def _set_dotted(row: dict[str, Any], key: str, value: Any) -> None:
    target = row
    parts = key.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = deepcopy(value)


def _matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    return all(row.get(key) == value for key, value in query.items())


class Collection:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.indexes: list[str] = []

    async def create_index(self, _keys, **kwargs):
        self.indexes.append(str(kwargs.get("name") or ""))
        return kwargs.get("name")

    async def find_one(self, query, projection=None):
        row = next((item for item in self.rows if _matches(item, query)), None)
        if row is None:
            return None
        result = deepcopy(row)
        if projection:
            exclusions = {key for key, value in projection.items() if value == 0}
            inclusions = {key for key, value in projection.items() if value == 1}
            if inclusions:
                result = {key: result.get(key) for key in inclusions if key in result}
            for key in exclusions:
                result.pop(key, None)
        return result

    async def update_one(self, query, update, upsert=False):
        row = next((item for item in self.rows if _matches(item, query)), None)
        inserted = False
        if row is None and upsert:
            row = {**deepcopy(query), **deepcopy(update.get("$setOnInsert") or {})}
            self.rows.append(row)
            inserted = True
        if row is None:
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
        if not inserted:
            for key, value in (update.get("$setOnInsert") or {}).items():
                row.setdefault(key, deepcopy(value))
        for key, value in (update.get("$set") or {}).items():
            _set_dotted(row, key, value)
        for key, value in (update.get("$inc") or {}).items():
            _set_dotted(row, key, int(row.get(key) or 0) + int(value))
        for key, value in (update.get("$addToSet") or {}).items():
            values = row.setdefault(key, [])
            if value not in values:
                values.append(deepcopy(value))
        return SimpleNamespace(
            matched_count=1,
            modified_count=1,
            upserted_id=row.get("_id") if inserted else None,
        )


class DB:
    def __init__(self) -> None:
        self.collections: dict[str, Collection] = {}

    def __getitem__(self, name: str) -> Collection:
        return self.collections.setdefault(name, Collection())


def _result(
    provider: str = "snapchat_ads",
    *,
    action: str = "TEST",
    budget_change_pct: float = 5.0,
    contribution_profit_sar: float = 825.0,
    current_state: dict[str, Any] | None = None,
    entity_type: str = "campaign",
    entity_id: str = "campaign-1",
    proposed_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = {
        "status": "ACTIVE",
        "effective_status": "ACTIVE" if provider == "meta_ads" else None,
        "active": True,
        "daily_budget_native": 100.0,
        "currency_scope": "account_native",
        "campaign_id": "campaign-1",
        "ad_group_id": "ad-group-1",
    }
    state.update(current_state or {})
    recommendation = {
        "action": action,
        "reason": "Bounded change supported by reconciled closed-day evidence.",
        "confidence": None,
        "priority_score": None,
    }
    if proposed_state is not None:
        recommendation["proposed_state"] = proposed_state
    return {
        "mode": "recommendation_shadow",
        "provider": provider,
        "account": {"id": "act-1", "currency": "USD"},
        "period": {
            "date_from": "2026-09-02",
            "date_to": "2026-09-02",
            "timezone": "Asia/Riyadh",
            "closed": True,
        },
        "evidence_timestamp": "2026-09-04T06:00:00+00:00",
        "decision_ready": True,
        "gates": {
            "contract": {"passed": True, "reason": "contract_valid"},
            "financial_coverage": {
                "passed": True,
                "reason": "financial_coverage_complete",
            },
        },
        "decisions": [
            {
                "decision_id": f"{provider}:{entity_type}:{entity_id}",
                "entity": {
                    "level": entity_type,
                    "id": entity_id,
                    "status": state["status"],
                    "active": state["active"],
                    "campaign_id": state.get("campaign_id"),
                    "ad_group_id": state.get("ad_group_id"),
                },
                "status": "RECOMMENDATION_SHADOW",
                "recommendation": recommendation,
                "simulation": {
                    "proposed_change": {"budget_change_pct": budget_change_pct},
                    "execution_performed": False,
                },
                "evidence": {
                    "metrics": {
                        "contribution_profit_sar": contribution_profit_sar,
                        "salla_roas": 2.4,
                    },
                    "quality": {"coverage_status": "complete"},
                    "lineage": {"source_version": "v2"},
                    "current_state_snapshot": state,
                },
                "execution_allowed": False,
            }
        ],
        "summary": {"recommendations": 1},
        "approval_workflow": {"approval_can_execute": False},
        "scheduler_integration": {"automatic_execution_connected": False},
        "write_policy": {
            "platform_writes_enabled": False,
            "platform_writes_performed": False,
            "database_writes_performed": False,
        },
    }


async def _persist_one(db: DB, result: dict[str, Any] | None = None) -> dict[str, Any]:
    persisted = await bridge.persist_phase5_proposals(
        db,
        "owner-1",
        result or _result(),
        source_run_id="run-1",
        now=NOW,
    )
    assert len(persisted["proposals"]) == 1
    return persisted["proposals"][0]


def _preview_input(proposal: dict[str, Any]) -> bridge.Phase5ProposalPreviewInput:
    return bridge.Phase5ProposalPreviewInput(
        provider=proposal["provider"],
        entity_type=proposal["entity_type"],
        entity_id=proposal["entity_id"],
        recommendation_fingerprint=proposal["recommendation_fingerprint"],
        expected_revision=proposal["revision"],
    )


async def _preview_one(
    db: DB,
    proposal: dict[str, Any],
    *,
    state: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    calls: list[str] = []

    async def dispatcher(_db, _user, _actor, current):
        calls.append(current["proposal_id"])
        return {
            "provider_proposal_id": "provider-proposal-1",
            "current_state": state or current["current_state_snapshot"],
            "proposal": {
                "proposal_id": "provider-proposal-1",
                "status": "previewed",
                "revision": 1,
                "confirm_token": "confirmation-token-12345",
                "provider_write_reached": False,
            },
        }

    previewed = await bridge.preview_phase5_proposal(
        db,
        "owner-1",
        "owner-1",
        proposal["proposal_id"],
        _preview_input(proposal),
        dispatcher=dispatcher,
    )
    return previewed, calls


def _approval_input(
    proposal: dict[str, Any], **overrides: Any
) -> bridge.Phase5ProposalApprovalInput:
    values = {
        "provider": proposal["provider"],
        "entity_type": proposal["entity_type"],
        "entity_id": proposal["entity_id"],
        "recommendation_fingerprint": proposal["recommendation_fingerprint"],
        "provider_state_fingerprint": proposal["provider_state_fingerprint"],
        "expected_revision": proposal["revision"],
        "confirm_token": "confirmation-token-12345",
        "provider_proposal_revision": 1,
    }
    values.update(overrides)
    return bridge.Phase5ProposalApprovalInput(**values)


@pytest.mark.asyncio
async def test_phase5_recommendation_creates_complete_durable_proposal():
    db = DB()
    proposal = await _persist_one(db)

    assert proposal["status"] == "pending_preview"
    assert proposal["action_type"] == "budget_scale"
    assert proposal["proposed_state"]["amount_native"] == 105.0
    assert proposal["source"] == "decision_intelligence_phase5"
    assert proposal["source_run_id"] == "run-1"
    assert proposal["tenant_id"] == "owner-1"
    assert proposal["trace"] == {
        "run_id": "run-1",
        "recommendation_id": proposal["recommendation_id"],
        "proposal_id": proposal["proposal_id"],
        "approval_id": None,
        "execution_id": None,
        "verification": None,
    }
    required = {
        "current_state_snapshot",
        "evidence",
        "confidence",
        "priority",
        "created_at",
        "expires_at",
        "recommendation_fingerprint",
        "idempotency_key",
        "readiness_snapshot",
        "profitability_accounting_snapshot",
        "provider_state_fingerprint",
    }
    assert required.issubset(proposal)


@pytest.mark.asyncio
async def test_unsupported_action_remains_non_executable_and_is_not_persisted():
    db = DB()
    persisted = await bridge.persist_phase5_proposals(
        db,
        "owner-1",
        _result(action="create_campaign"),
        source_run_id="run-unsupported",
        now=NOW,
    )

    assert persisted["proposals"] == []
    assert persisted["non_executable_recommendations"][0]["executable"] is False
    assert (
        "unsupported_action"
        in persisted["non_executable_recommendations"][0]["blocked_by"]
    )
    assert db[bridge.PROPOSAL_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_same_material_recommendation_deduplicates_across_runs():
    db = DB()
    first = await bridge.persist_phase5_proposals(
        db, "owner-1", _result(), source_run_id="run-1", now=NOW
    )
    second = await bridge.persist_phase5_proposals(
        db,
        "owner-1",
        _result(),
        source_run_id="run-2",
        now=NOW + timedelta(minutes=5),
    )

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["deduplicated"] == 1
    assert len(db[bridge.PROPOSAL_COLLECTION].rows) == 1
    assert db[bridge.PROPOSAL_COLLECTION].rows[0]["source_run_ids"] == [
        "run-1",
        "run-2",
    ]


@pytest.mark.asyncio
async def test_material_evidence_change_creates_new_proposal():
    db = DB()
    await bridge.persist_phase5_proposals(
        db, "owner-1", _result(), source_run_id="run-1", now=NOW
    )
    changed = await bridge.persist_phase5_proposals(
        db,
        "owner-1",
        _result(contribution_profit_sar=900.0),
        source_run_id="run-2",
        now=NOW + timedelta(minutes=5),
    )

    assert changed["created"] == 1
    assert len(db[bridge.PROPOSAL_COLLECTION].rows) == 2


@pytest.mark.asyncio
async def test_no_owner_approval_means_zero_execution():
    db = DB()
    proposal = await _persist_one(db)
    previewed, preview_calls = await _preview_one(db, proposal)

    assert preview_calls == [proposal["proposal_id"]]
    assert previewed["status"] == "previewed"
    assert previewed["provider_write_reached"] is False
    assert previewed["trace"]["approval_id"] is None
    assert previewed["trace"]["execution_id"] is None


@pytest.mark.asyncio
async def test_default_snapchat_preview_dispatcher_reuses_management_gate(monkeypatch):
    from integrations_control_center import snapchat_campaign_management as snapchat

    db = DB()
    public = await _persist_one(db)
    proposal = db[bridge.PROPOSAL_COLLECTION].rows[0]
    captured: list[Any] = []

    async def existing_gate(_db, _user, _actor, payload):
        captured.append(payload)
        return {
            "proposal_id": "snap-gate-proposal-1",
            "status": "previewed_v2",
            "revision": 1,
            "confirm_token": "confirmation-token-12345",
        }

    monkeypatch.setattr(snapchat, "create_snapchat_management_proposal", existing_gate)
    db[bridge.SNAPCHAT_GATE_COLLECTION].rows.append(
        {
            "proposal_id": "snap-gate-proposal-1",
            "user_id": "owner-1",
            "original_snapshot": {
                "status": "ACTIVE",
                "daily_budget_micro": 100_000_000,
            },
        }
    )

    result = await bridge._preview_existing_action_gate(
        db, "owner-1", "owner-1", proposal
    )

    assert public["action_type"] == "budget_scale"
    assert result["provider_proposal_id"] == "snap-gate-proposal-1"
    assert captured[0].action == "campaign.update"
    assert captured[0].payload == {"daily_budget_micro": 105_000_000}
    assert captured[0].safety_protocol_version == 2


@pytest.mark.asyncio
async def test_default_snapchat_execute_dispatcher_uses_approve_then_execute(
    monkeypatch,
):
    from integrations_control_center import snapchat_campaign_management as snapchat

    db = DB()
    proposal = await _persist_one(db)
    proposal["provider_proposal_id"] = "snap-gate-proposal-1"
    calls: list[str] = []

    async def approve(_db, _user, _actor, proposal_id, payload):
        calls.append(f"approve:{proposal_id}:{payload.expected_revision}")
        return {"status": "approved_v2"}

    async def execute(_db, _user, _actor, proposal_id):
        calls.append(f"execute:{proposal_id}")
        return {
            "proposal_id": proposal_id,
            "status": "completed",
            "verification": {"verified": True},
            "provider_write_reached": True,
            "provider_write_state": "verified",
        }

    monkeypatch.setattr(snapchat, "approve_snapchat_management_proposal", approve)
    monkeypatch.setattr(snapchat, "execute_snapchat_management_proposal", execute)
    payload = _approval_input({**proposal, "revision": 2})

    result = await bridge._execute_existing_action_gate(
        db, "owner-1", "owner-1", proposal, payload
    )

    assert calls == ["approve:snap-gate-proposal-1:1", "execute:snap-gate-proposal-1"]
    assert result["proposal"]["status"] == "completed"


@pytest.mark.asyncio
async def test_explicit_approval_dispatches_existing_action_gate_and_links_trace():
    db = DB()
    proposal = await _persist_one(db)
    previewed, _ = await _preview_one(db, proposal)
    calls: list[str] = []

    async def execute(_db, _user, _actor, current, _payload):
        calls.append(current["provider_proposal_id"])
        return {
            "proposal": {
                "proposal_id": current["provider_proposal_id"],
                "status": "completed",
                "verified": True,
                "provider_write_reached": True,
                "provider_write_state": "verified",
                "executed_at": NOW,
            }
        }

    async def accounting(_db, _user, action):
        assert action == "scale"
        return {"complete": True, "scale_gate_applied": True}

    completed = await bridge.approve_and_execute_phase5_proposal(
        db,
        "owner-1",
        "owner-1",
        proposal["proposal_id"],
        _approval_input(previewed),
        dispatcher=execute,
        accounting_gate=accounting,
    )

    assert calls == ["provider-proposal-1"]
    assert completed["status"] == "succeeded"
    assert completed["trace"]["approval_id"].startswith("di-p5-appr-")
    assert completed["trace"]["execution_id"].startswith("di-p5-exec-")
    assert completed["trace"]["verification"]["verified"] is True


@pytest.mark.asyncio
async def test_expired_proposal_blocks_approval_before_execution():
    db = DB()
    proposal = await _persist_one(db)
    previewed, _ = await _preview_one(db, proposal)
    db[bridge.PROPOSAL_COLLECTION].rows[0]["expires_at"] = NOW - timedelta(seconds=1)
    called = False

    async def execute(*_args):
        nonlocal called
        called = True
        return {}

    with pytest.raises(HTTPException) as caught:
        await bridge.approve_and_execute_phase5_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal["proposal_id"],
            _approval_input(previewed),
            dispatcher=execute,
        )
    assert caught.value.detail["code"] == "phase5_proposal_expired"
    assert called is False


@pytest.mark.asyncio
async def test_provider_state_drift_at_preview_requires_revalidation():
    db = DB()
    proposal = await _persist_one(db)
    changed = {**proposal["current_state_snapshot"], "daily_budget_native": 120.0}

    with pytest.raises(HTTPException) as caught:
        await _preview_one(db, proposal, state=changed)

    assert caught.value.detail["code"] == "phase5_proposal_provider_state_changed"
    row = db[bridge.PROPOSAL_COLLECTION].rows[0]
    assert row["status"] == "revalidation_required"
    assert row["provider_write_reached"] is False


@pytest.mark.asyncio
async def test_existing_gate_stale_state_failure_is_preserved_without_write():
    db = DB()
    proposal = await _persist_one(db)
    previewed, _ = await _preview_one(db, proposal)

    async def execute(_db, _user, _actor, current, _payload):
        return {
            "proposal": {
                "proposal_id": current["provider_proposal_id"],
                "status": "approved",
                "provider_write_reached": False,
                "provider_write_state": "not_attempted",
            },
            "error": {"code": "provider_state_changed"},
        }

    async def accounting(*_args):
        return {"complete": True}

    blocked = await bridge.approve_and_execute_phase5_proposal(
        db,
        "owner-1",
        "owner-1",
        proposal["proposal_id"],
        _approval_input(previewed),
        dispatcher=execute,
        accounting_gate=accounting,
    )

    assert blocked["status"] == "failed_before_write"
    assert blocked["verification_result"]["error"]["code"] == "provider_state_changed"
    assert blocked["provider_write_reached"] is False


@pytest.mark.asyncio
async def test_failed_profit_accounting_gate_blocks_scale_before_dispatch():
    db = DB()
    proposal = await _persist_one(db)
    previewed, _ = await _preview_one(db, proposal)
    called = False

    async def accounting(*_args):
        raise HTTPException(
            status_code=409,
            detail={"code": "campaign_ai_profit_accounting_incomplete"},
        )

    async def execute(*_args):
        nonlocal called
        called = True
        return {}

    with pytest.raises(HTTPException) as caught:
        await bridge.approve_and_execute_phase5_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal["proposal_id"],
            _approval_input(previewed),
            dispatcher=execute,
            accounting_gate=accounting,
        )
    assert caught.value.detail["code"] == "campaign_ai_profit_accounting_incomplete"
    assert called is False
    assert db[bridge.PROPOSAL_COLLECTION].rows[0]["status"] == "previewed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("provider", "meta_ads", "phase5_proposal_provider_mismatch"),
        ("entity_id", "campaign-2", "phase5_proposal_entity_id_mismatch"),
        (
            "recommendation_fingerprint",
            "f" * 64,
            "phase5_proposal_recommendation_fingerprint_mismatch",
        ),
    ],
)
async def test_identity_and_recommendation_mismatches_block_preview(field, value, code):
    db = DB()
    proposal = await _persist_one(db)
    values = _preview_input(proposal).model_dump()
    values[field] = value
    payload = bridge.Phase5ProposalPreviewInput(**values)

    with pytest.raises(HTTPException) as caught:
        await bridge.preview_phase5_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal["proposal_id"],
            payload,
            dispatcher=lambda *_args: None,
        )
    assert caught.value.detail["code"] == code


@pytest.mark.asyncio
async def test_provider_state_fingerprint_mismatch_blocks_approval():
    db = DB()
    proposal = await _persist_one(db)
    previewed, _ = await _preview_one(db, proposal)
    payload = _approval_input(previewed, provider_state_fingerprint="e" * 64)

    with pytest.raises(HTTPException) as caught:
        await bridge.approve_and_execute_phase5_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal["proposal_id"],
            payload,
        )
    assert caught.value.detail["code"] == (
        "phase5_proposal_provider_state_fingerprint_mismatch"
    )


@pytest.mark.asyncio
async def test_ambiguous_provider_outcome_is_not_relabeled_failure():
    db = DB()
    proposal = await _persist_one(db)
    previewed, _ = await _preview_one(db, proposal)

    async def execute(_db, _user, _actor, current, _payload):
        return {
            "proposal": {
                "proposal_id": current["provider_proposal_id"],
                "status": "verification_failed",
                "provider_write_reached": True,
                "provider_write_state": "uncertain",
                "provider_write_uncertain": True,
            }
        }

    async def accounting(*_args):
        return {"complete": True}

    result = await bridge.approve_and_execute_phase5_proposal(
        db,
        "owner-1",
        "owner-1",
        proposal["proposal_id"],
        _approval_input(previewed),
        dispatcher=execute,
        accounting_gate=accounting,
    )

    assert result["status"] == "outcome_unknown"
    assert result["verification_result"]["provider_write_uncertain"] is True


def test_rollback_capability_is_reported_without_claiming_meta_support():
    matrix = bridge.phase5_proposal_capability_matrix()

    assert matrix["snapchat_ads"]["pause"]["rollback"] is True
    assert matrix["snapchat_ads"]["budget_scale"]["rollback"] is True
    assert matrix["meta_ads"]["pause"]["rollback"] is False
    assert matrix["meta_ads"]["budget_scale"]["rollback"] is False
    assert matrix["snapchat_ads"]["bid_adjust"]["entity_types"] == []


@pytest.mark.parametrize(
    ("provider_proposal", "expected"),
    [
        ({"status": "completed", "verified": True}, "succeeded"),
        (
            {"status": "approved", "provider_write_state": "not_attempted"},
            "failed_before_write",
        ),
        ({"status": "verification_required"}, "verification_pending"),
        ({"status": "failed", "provider_write_uncertain": True}, "outcome_unknown"),
        ({"status": "rollback_required"}, "rollback_required"),
        ({"status": "failed", "rollback": {"status": "completed"}}, "rolled_back"),
    ],
)
def test_common_execution_semantics_preserve_provider_outcome(
    provider_proposal, expected
):
    assert bridge._execution_semantics({"proposal": provider_proposal}) == expected


@pytest.mark.parametrize(
    ("provider", "action", "entity_type", "state", "proposed"),
    [
        ("snapchat_ads", "pause", "campaign", {}, None),
        (
            "snapchat_ads",
            "resume",
            "ad",
            {"status": "PAUSED", "active": False},
            None,
        ),
        ("snapchat_ads", "reduce", "ad_group", {}, None),
        ("meta_ads", "pause", "ad", {}, None),
        (
            "meta_ads",
            "resume",
            "campaign",
            {"status": "PAUSED", "effective_status": "PAUSED", "active": False},
            None,
        ),
        ("meta_ads", "scale", "campaign", {}, None),
        (
            "meta_ads",
            "update_bid",
            "ad_group",
            {"bid_amount_native": 50.0, "bid_strategy": "COST_CAP"},
            {"bid_amount_native": 55.0},
        ),
    ],
)
def test_only_proven_provider_action_matrix_normalizes_executable(
    provider, action, entity_type, state, proposed
):
    result = _result(
        provider,
        action=action,
        budget_change_pct=-5 if action == "reduce" else 5,
        current_state=state,
        entity_type=entity_type,
        proposed_state=proposed,
    )
    normalized = bridge.normalize_phase5_recommendation(
        result, result["decisions"][0], tenant_id="owner-1"
    )

    assert normalized["executable"] is True


def test_snapchat_bid_stays_non_executable_without_unified_bid_baseline():
    result = _result(
        action="update_bid",
        entity_type="ad_group",
        current_state={"bid_amount_native": 50.0, "bid_strategy": "AUTO_BID"},
        proposed_state={"bid_amount_native": 55.0},
    )
    normalized = bridge.normalize_phase5_recommendation(
        result, result["decisions"][0], tenant_id="owner-1"
    )

    assert normalized["executable"] is False
    assert "phase5_snapchat_bid_baseline_unavailable" in normalized["blocked_by"]


@pytest.mark.asyncio
async def test_scheduler_persists_only_pending_proposal_and_never_executes():
    db = DB()

    async def runner(*_args, **_kwargs):
        return _result()

    scheduler = Phase5ShadowScheduler(
        db,
        config=Phase5SchedulerConfig(
            enabled=True,
            interval_seconds=3600,
            initial_delay_seconds=0,
            max_tenants=1,
            max_entities=1,
            timeout_seconds=2,
            max_provider_concurrency=1,
            max_freshness_hours=36,
        ),
        phase5_runner=runner,
    )
    event = await scheduler.run_provider("owner-1", "snapchat_ads")

    assert event["status"] == "success"
    assert event["proposals_created"] == 1
    assert event["automatic_execution"] is False
    row = db[bridge.PROPOSAL_COLLECTION].rows[0]
    assert row["status"] == "pending_preview"
    assert row.get("approval_id") is None
    assert row["provider_write_reached"] is False


def test_scheduler_stays_disabled_by_default(monkeypatch):
    monkeypatch.delenv(scheduler_module.ENABLED_ENV, raising=False)
    assert scheduler_module.load_scheduler_config().enabled is False


@pytest.mark.asyncio
async def test_persistence_makes_no_provider_or_accounting_calls():
    db = DB()
    persisted = await bridge.persist_phase5_proposals(
        db, "owner-1", _result(), source_run_id="run-no-live", now=NOW
    )

    assert persisted["provider_calls"] == 0
    assert persisted["provider_writes"] == 0
    assert persisted["automatic_execution"] is False
    assert persisted["proposals"][0]["accounting_write_reached"] is False


@pytest.mark.asyncio
async def test_proposal_never_persists_provider_credentials():
    db = DB()
    result = _result()
    result["decisions"][0]["evidence"]["lineage"]["access_token"] = "secret"
    result["decisions"][0]["evidence"]["lineage"]["client_secret"] = "secret"

    proposal = await _persist_one(db, result)

    encoded = str(proposal).lower()
    assert "access_token" not in encoded
    assert "client_secret" not in encoded
    assert "secret" not in encoded


def test_bridge_has_no_google_or_accounting_write_path():
    source = Path(bridge.__file__).read_text(encoding="utf-8").lower()

    assert "google_ads" not in source
    assert "general_ledger" not in source
    assert "insert_accounting" not in source
    assert 'accounting_write_reached": true' not in source
