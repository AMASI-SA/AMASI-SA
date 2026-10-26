import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import campaign_ai_execution_quality_gate as gate
import campaign_ai_unified_execution_evidence as unified
from unified_marketing.decision_binding import source_evidence_fingerprint

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
STAMP = "2026-10-16T11:50:00+00:00"


@pytest.fixture
def evidence(monkeypatch):
    account = {"id": "a1", "timezone": "Asia/Riyadh", "currency": "SAR", "last_sync_at": STAMP}
    period = {"date_from": "2026-10-15", "date_to": "2026-10-15", "timezone": "Asia/Riyadh", "action_report_time": "conversion"}
    row = {
        "provider": "snapchat_ads", "account": deepcopy(account), "period": period,
        "entity": {"id": "c1", "level": "campaign"},
        "delivery": {"spend": {"amount": 100, "currency": "SAR"}, "spend_sar": {"amount": 100, "currency": "SAR"}, "impressions": 1000, "clicks": 25},
        "platform_outcomes": {"revenue": {"amount": 200, "currency": "SAR"}, "conversions": 2},
        "commerce_outcomes": {"status": "complete", "orders": 2,
                              "revenue": {"amount": 150, "currency": "SAR"},
                              "attribution_scope": "exact_campaign_match"},
        "commerce_profitability": {"status": "complete", "orders": 2, "cost_status": "complete",
                                   "missing_cost_orders": 0,
                                   "sales": {"amount": 150, "currency": "SAR"},
                                   "product_cost": {"amount": 20, "currency": "SAR"},
                                   "ad_spend": {"amount": 100, "currency": "SAR"},
                                   "contribution_profit": {"amount": 30, "currency": "SAR"}},
        "quality": {"source_fact_count": 1, "sync_status": "complete", "coverage_status": "complete"},
        "lineage": {"source_collection": "facts", "adapter": "snapchat-v2", "source_version": "v2"},
    }
    management = {"status": "ACTIVE", "daily_budget_native": 50, "updated_at": STAMP}
    decision = {"contract_version": unified.UNIFIED_CONTRACT, "provider": "snapchat_ads", "decision_ready": True,
                "blocked_by": [], "account": deepcopy(account), "period": deepcopy(period),
                "gates": {name: {"passed": True} for name in unified.REQUIRED_DECISION_GATES}}
    decision["gates"]["freshness"]["last_sync_at"] = STAMP
    target = {
        "provider": "snapchat", "account_id": "a1", "entity_id": "c1", "entity_level": "campaign", "campaign_id": "c1",
        "account_timezone": "Asia/Riyadh", "currency_native": "SAR", "source_mode": unified.SOURCE_MODE,
        "unified_contract_version": unified.UNIFIED_CONTRACT,
        "provider_result_source": "unified-marketing-data-v1:snapchat-v2", "source_fact_collection": "facts",
        "result_source": "platform", "action_report_time": "conversion", "source_date_from": "2026-10-15", "source_date_to": "2026-10-15",
        "source_observed_at": STAMP, "spend_sar": 100, "revenue_sar": 200, "purchases": 2, "clicks": 25, "impressions": 1000,
        "fx_rate_to_sar": 1, "fx_source": "unified_marketing_cost_contract", "status": "ACTIVE", "current_daily_budget_native": 50,
        "data_complete": True, "pagination_complete": True,
    }
    target["source_evidence_fingerprint"] = source_evidence_fingerprint("owner-1", row, management)
    reads = []
    async def identity(db, user_id, **kwargs):
        assert kwargs["account_id"] == "a1"
        reads.append(user_id)
        return deepcopy(account)
    async def report(db, user_id, **kwargs):
        assert kwargs["account_id"] == "a1"
        reads.append(user_id)
        return {"rows": [deepcopy(row)], "management_context": {"c1": deepcopy(management)}}
    async def readiness(db, user_id, **kwargs):
        assert kwargs["account_id"] == "a1"
        reads.append(user_id)
        assert kwargs["max_freshness_hours"] <= 1.5
        return deepcopy(decision)
    monkeypatch.setattr(unified.gateway, "load_unified_marketing_account_identity", identity)
    monkeypatch.setattr(unified.gateway, "load_unified_marketing_entity_report", report)
    monkeypatch.setattr(unified, "load_decision_evidence", readiness)
    return target, account, row, decision, management, reads


def collect(target, baseline=None):
    # No collection or provider-write capability is exposed by this sentinel.
    return asyncio.run(gate.collect_execution_quality_evidence(
        object(), "owner-1", target, snapshot_generated_at=STAMP,
        snapshot_range={"from": "2026-10-15", "to": "2026-10-15"},
        now=lambda: NOW, baseline=baseline,
    ))


def test_scale_requires_commercial_evidence_beyond_provider_attribution(evidence):
    target, _, row, _, management, _ = evidence
    row.pop("commerce_profitability")
    target["source_evidence_fingerprint"] = source_evidence_fingerprint("owner-1", row, management)
    result = collect(evidence[0])
    assert result["attribution"]["status"] == "trusted"
    assert gate.evaluate_execution_quality(result, action="scale")["allowed"] is False
    assert gate.evaluate_execution_quality(result, action="pause")["allowed"] is True
    assert gate.evaluate_execution_quality(result, action="reduce")["allowed"] is True


def test_local_report_and_provider_timestamps_do_not_prove_upstream_commerce_freshness(evidence):
    target, account, row, decision, management, _ = evidence
    fresh = NOW.isoformat()
    target["source_observed_at"] = fresh
    account["last_sync_at"] = fresh
    row["account"]["last_sync_at"] = fresh
    decision["gates"]["freshness"]["last_sync_at"] = fresh
    management["updated_at"] = fresh
    # These ad-hoc timestamps do not come from a supported commerce contract
    # and must not silently enable authority in a subsequent release.
    row["commerce_profitability"]["generated_at"] = fresh
    row["commerce_outcomes"]["updated_at"] = fresh
    row["commerce_freshness"] = {"status": "fresh", "observed_at": fresh}
    target["source_evidence_fingerprint"] = source_evidence_fingerprint("owner-1", row, management)
    result = collect(target)
    assert result["freshness"]["status"] == "fresh"
    assert result["commercial_scale"]["errors"] == ["campaign_commerce_freshness_unproven"]
    assert result["commercial_scale"]["commerce_freshness"]["observed_at"] is None
    assert not gate.evaluate_execution_quality(result, action="scale")["allowed"]
    assert gate.evaluate_execution_quality(result, action="pause")["allowed"]


@pytest.mark.parametrize("mutation", [
    lambda row: row["commerce_outcomes"].update(status="partial"),
    lambda row: row["commerce_outcomes"].update(attribution_scope="campaign_only"),
    lambda row: row["commerce_outcomes"].update(orders=3),
    lambda row: row["commerce_profitability"].update(status="partial"),
    lambda row: row["commerce_profitability"].update(cost_status="missing"),
    lambda row: row["commerce_profitability"].update(missing_cost_orders=1),
    lambda row: row["commerce_profitability"].pop("missing_cost_orders"),
    lambda row: row["commerce_profitability"]["sales"].update(amount=180),
    lambda row: row["commerce_profitability"]["product_cost"].update(amount=None),
    lambda row: row["commerce_profitability"]["contribution_profit"].update(currency="USD"),
    lambda row: row["commerce_profitability"]["contribution_profit"].update(amount=100),
])
def test_incomplete_or_unmatched_commerce_blocks_only_scale(evidence, mutation):
    target, _, row, _, management, _ = evidence
    mutation(row)
    target["source_evidence_fingerprint"] = source_evidence_fingerprint("owner-1", row, management)
    result = collect(target)
    assert not gate.evaluate_execution_quality(result, action="scale")["allowed"]
    assert gate.evaluate_execution_quality(result, action="reduce")["allowed"]


def test_scale_does_not_replace_marketing_judgment_with_profit_threshold(evidence):
    target, _, row, _, management, _ = evidence
    row["commerce_profitability"]["product_cost"]["amount"] = 60
    row["commerce_profitability"]["contribution_profit"]["amount"] = -10
    target["source_evidence_fingerprint"] = source_evidence_fingerprint("owner-1", row, management)
    # A negative, arithmetically complete contribution does not add a marketing
    # threshold blocker. Upstream freshness remains independently unproven.
    result = collect(target)
    assert result["commercial_scale"]["errors"] == ["campaign_commerce_freshness_unproven"]
    assert not gate.evaluate_execution_quality(result, action="scale")["allowed"]


@pytest.mark.parametrize("level", ["ad_group", "ad"])
def test_child_scale_requires_exact_fresh_parent_commerce(evidence, monkeypatch, level):
    target, _, row, _, management, _ = evidence
    parent = deepcopy(row)
    row["entity"] = {"level": level, "id": "child1", "campaign_id": "c1", "ad_group_id": "g1" if level == "ad" else None}
    # Children have no directly attributed store revenue. The campaign remains
    # explicitly the source; its sales must not be assigned to this child.
    row.pop("commerce_outcomes")
    row.pop("commerce_profitability")
    target.update(entity_level=level, entity_id="child1", ad_group_id="g1" if level == "ad" else "child1")

    async def report(db, user_id, **kwargs):
        return {"rows": [deepcopy(parent if kwargs["entity_level"] == "campaign" else row)],
                "management_context": {"child1": deepcopy(management)}}

    monkeypatch.setattr(unified.gateway, "load_unified_marketing_entity_report", report)
    def capture():
        target["source_evidence_fingerprint"] = source_evidence_fingerprint("owner-1", row, management, parent_campaign=parent)
        return collect(target)

    proof = capture()
    assert proof["commercial_scale"]["errors"] == ["campaign_commerce_freshness_unproven"]
    assert not gate.evaluate_execution_quality(proof, action="scale")["allowed"]
    assert proof["commercial_scale"]["campaign_id"] == "c1"
    parent["account"]["last_sync_at"] = "2026-10-16T09:00:00+00:00"
    result = capture()
    assert "campaign_commerce_source_stale" in result["commercial_scale"]["errors"]
    assert not gate.evaluate_execution_quality(result, action="scale")["allowed"]
    assert gate.evaluate_execution_quality(result, action="pause")["allowed"]


def test_unified_quality_accepts_closed_complete_source_without_legacy_run(evidence):
    target, _, _, _, _, reads = evidence
    result = collect(target)
    assert result["blockers"] == []
    assert result["status"] == "complete"
    assert not gate.evaluate_execution_quality(result, action="scale")["allowed"]
    assert result["commercial_scale"]["commerce_freshness"]["status"] == "unproven"
    assert result["commercial_scale"]["commerce_freshness"]["observed_at"] is None
    assert result["commercial_scale"]["provider_observed_at"] == STAMP
    assert reads == ["owner-1"] * 3
    assert collect(target, result)["status"] == "complete"


@pytest.mark.parametrize("name", unified.REQUIRED_DECISION_GATES)
def test_each_missing_decision_gate_blocks(evidence, name):
    target, _, _, decision, _, _ = evidence
    del decision["gates"][name]
    assert collect(target)["status"] == "blocked"


@pytest.mark.parametrize("mutation", [
    lambda t,a,r,d,m: a.update(id="other-owner-account"),
    lambda t,a,r,d,m: r["account"].update(id="other-account"),
    lambda t,a,r,d,m: d["account"].update(id="other-account"),
    lambda t,a,r,d,m: r["entity"].update(id="other-entity"),
    lambda t,a,r,d,m: r["entity"].update(level="ad"),
    lambda t,a,r,d,m: r["period"].update(date_to="2026-10-14"),
    lambda t,a,r,d,m: r["lineage"].update(source_collection="legacy"),
    lambda t,a,r,d,m: r["account"].update(currency="USD"),
    lambda t,a,r,d,m: r["delivery"]["spend_sar"].update(amount=101),
    lambda t,a,r,d,m: r["platform_outcomes"].update(conversions=3),
    lambda t,a,r,d,m: m.update(status="PAUSED"),
    lambda t,a,r,d,m: m.update(daily_budget_native=60),
    lambda t,a,r,d,m: m.update(updated_at="2026-10-16T09:00:00+00:00"),
    lambda t,a,r,d,m: a.update(last_sync_at="2026-10-16T09:00:00+00:00"),
    lambda t,a,r,d,m: t.update(source_observed_at="2026-10-16T12:01:00+00:00"),
    lambda t,a,r,d,m: t.update(provider="unregistered"),
    lambda t,a,r,d,m: t.pop("unified_contract_version"),
    lambda t,a,r,d,m: r["quality"].update(coverage_status="partial"),
    lambda t,a,r,d,m: r["quality"].update(sync_status="running"),
    lambda t,a,r,d,m: t.update(pagination_complete=False),
])
def test_changed_unknown_or_stale_source_blocks(evidence, mutation):
    target, account, row, decision, management, _ = evidence
    mutation(target, account, row, decision, management)
    assert collect(target)["status"] == "blocked"


def test_commercial_revision_blocks_even_when_delivery_is_unchanged(evidence):
    target, _, row, _, _, _ = evidence
    baseline = collect(target)
    row["commerce_outcomes"] = {"orders": 99}
    result = collect(target, baseline)
    assert "execution_source_revision_changed" in result["blockers"]


def test_financial_change_during_model_generation_blocks_initial_capture(evidence):
    target, _, row, _, _, _ = evidence
    # Initial execution-quality capture occurs after the model call. It must
    # compare to the original candidate evidence, not adopt a new baseline.
    row["commerce_profitability"] = {"status": "complete", "contribution_profit": {"amount": -500, "currency": "SAR"}}
    result = collect(target)
    assert result["status"] == "blocked"
    assert "unified_candidate_source_revision_changed" in result["source_validation"]["errors"]


def test_missing_original_candidate_binding_blocks(evidence):
    target = evidence[0]
    del target["source_evidence_fingerprint"]
    assert collect(target)["status"] == "blocked"


@pytest.mark.parametrize("level", ["ad_group", "ad"])
def test_child_approval_binds_parent_finances_before_and_after_capture(evidence, monkeypatch, level):
    target, _, row, _, management, _ = evidence
    parent = deepcopy(row)
    parent["commerce_profitability"] = {"status": "complete", "contribution_profit": {"amount": 100, "currency": "SAR"}}
    row["entity"] = {"level": level, "id": "child1", "campaign_id": "c1", "ad_group_id": "g1" if level == "ad" else None}
    target.update(entity_level=level, entity_id="child1", ad_group_id="g1" if level == "ad" else "child1")
    target["source_evidence_fingerprint"] = source_evidence_fingerprint("owner-1", row, management, parent_campaign=parent)

    async def report(db, user_id, **kwargs):
        assert user_id == "owner-1"
        return {"rows": [deepcopy(parent if kwargs["entity_level"] == "campaign" else row)],
                "management_context": {"child1": deepcopy(management)}}
    monkeypatch.setattr(unified.gateway, "load_unified_marketing_entity_report", report)
    baseline = collect(target)
    assert baseline["status"] == "complete"
    parent["commerce_profitability"]["contribution_profit"]["amount"] = -500
    assert "unified_candidate_source_revision_changed" in collect(target)["source_validation"]["errors"]
    assert "execution_source_revision_changed" in collect(target, baseline)["blockers"]
    parent["entity"]["id"] = "wrong-parent"
    assert collect(target)["status"] == "blocked"


def test_unchanged_resync_preserves_approval_but_stale_resync_does_not(evidence):
    target, account, row, decision, management, _ = evidence
    baseline = collect(target)
    refreshed = "2026-10-16T11:55:00+00:00"
    management["updated_at"] = refreshed
    account["last_sync_at"] = refreshed
    row["account"]["last_sync_at"] = refreshed
    decision["gates"]["freshness"]["last_sync_at"] = refreshed
    assert collect(target, baseline)["status"] == "complete"
    management["updated_at"] = "2026-10-16T09:00:00+00:00"
    assert collect(target, baseline)["status"] == "blocked"


def test_revalidating_forged_unified_proof_does_not_skip_seven_gates(evidence):
    result = collect(evidence[0])
    result["unified_decision"]["gates"]["coverage"]["passed"] = False
    assert "execution_provider_sync_errors" in gate.evaluate_execution_quality(result)["blockers"]


def test_loader_failure_is_read_only_block(evidence, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(unified, "load_decision_evidence", fail)
    result = collect(evidence[0])
    assert "unified_evidence_load_failed" in result["source_validation"]["errors"]


def test_unified_preflight_preserves_owner_digest_and_revision_guards(evidence):
    target, _, row, _, _, _ = evidence
    target["execution_quality"] = collect(target)
    recommendation = {key: target[key] for key in ("provider", "account_id", "entity_id", "entity_level")}
    recommendation.update(recommendation_id="r1", approval_available=True, action="pause", execution_status="awaiting_approval")
    snapshot = {"snapshot_id": "s1", "generated_at": STAMP, "recommendations": [recommendation],
                "execution_targets": {"r1": target}, "range": {"from": "2026-10-15", "to": "2026-10-15"}}

    class SnapshotDB:
        def __getitem__(self, name):
            assert name == "snapshots"
            return self

        async def find_one(self, query, projection, sort=None):
            return deepcopy(snapshot) if query == {"user_id": "owner-1"} else None

    digest = gate.execution_snapshot_digest("s1", recommendation, target)
    def preflight(user="owner-1", expected=digest):
        return asyncio.run(gate.preflight_approved_execution(
            SnapshotDB(), recommendation_collection="snapshots", user_id=user,
            snapshot_id="s1", recommendation_id="r1", expected_digest=expected, now=lambda: NOW,
        ))
    assert preflight()["execution_quality"]["status"] == "complete"
    with pytest.raises(gate.ExecutionQualityBlocked, match="execution_snapshot_drift"):
        preflight(user="different-owner")
    with pytest.raises(gate.ExecutionQualityBlocked, match="execution_snapshot_drift"):
        preflight(expected="tampered")
    row["commerce_outcomes"] = {"orders": 3}
    with pytest.raises(gate.ExecutionQualityBlocked, match="execution_source_revision_changed"):
        preflight()


def test_provider_preflight_still_requires_live_budget_and_active_status(evidence):
    target = evidence[0]
    assert gate.provider_state_drift_blockers("snapchat", {"action": "scale"}, target,
                                               {"id": "c1", "status": "ACTIVE", "daily_budget_micro": 50_000_000}) == []
    assert "execution_provider_budget_drift" in gate.provider_state_drift_blockers(
        "snapchat", {"action": "scale"}, target,
        {"id": "c1", "status": "ACTIVE", "daily_budget_micro": 60_000_000},
    )
    assert "execution_provider_status_drift" in gate.provider_state_drift_blockers(
        "snapchat", {"action": "pause"}, target, {"id": "c1", "status": "PAUSED"},
    )
