from pathlib import Path

import pytest

import campaign_ai_execution_quality_gate as gate


class _Collection:
    def __init__(self, row):
        self.row = row
    async def find_one(self, *args, **kwargs):
        return self.row


class _DB:
    def __init__(self, row):
        self.row = row
    def __getitem__(self, name):
        return _Collection(self.row)


def _snapshot(status):
    recommendation = {
        "recommendation_id": "r1",
        "provider": "meta",
        "entity_level": "campaign",
        "entity_id": "c1",
        "account_id": "a1",
        "action": "scale",
        "change_percent": 10,
        "approval_available": True,
        "execution_status": status,
    }
    target = {
        "provider": "meta",
        "entity_level": "campaign",
        "entity_id": "c1",
        "account_id": "a1",
        "execution_quality": {
            "contract_version": gate.CONTRACT_VERSION,
            "status": "complete",
            "blockers": [],
            "provider": "meta",
            "entity_level": "campaign",
            "entity_id": "c1",
            "account_id": "a1",
            "entity_facts": {"fingerprint": "fp"},
        },
    }
    return {
        "snapshot_id": "s1",
        "generated_at": "2026-08-21T12:00:00+00:00",
        "range": {"from": "2026-08-18", "to": "2026-08-20"},
        "recommendations": [recommendation],
        "execution_targets": {"r1": target},
    }, recommendation, target


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    "executing",
    "completed",
    "provider_state_uncertain",
    "verification_required",
])
async def test_p1_3_terminal_or_uncertain_state_cannot_be_approved_again(status):
    row, recommendation, target = _snapshot(status)
    digest = gate.execution_snapshot_digest("s1", recommendation, target)
    with pytest.raises(gate.ExecutionQualityBlocked) as caught:
        await gate.preflight_approved_execution(
            _DB(row),
            recommendation_collection="recs",
            user_id="u1",
            snapshot_id="s1",
            recommendation_id="r1",
            expected_digest=digest,
            now=lambda: __import__("datetime").datetime(
                2026, 8, 21, 12, 30, tzinfo=__import__("datetime").timezone.utc
            ),
        )
    assert "execution_recommendation_not_approvable" in caught.value.blockers
    assert caught.value.evidence["execution_status"] == status


def test_p1_3_snapchat_runs_shared_preflight_before_provider_proposal():
    source = Path(__file__).resolve().parents[1] / "campaign_ai_monitor_legacy.py"
    text = source.read_text(encoding="utf-8")
    start = text.index("async def _execute_snapchat_approval(")
    end = text.index("def _meta_state_matches_mutation", start)
    body = text[start:end]
    assert body.index("preflight_approved_execution(") < body.index(
        "create_snapchat_management_proposal("
    )
    # Keep the second execution-time preflight as defense in depth.
    assert body.count("preflight_approved_execution(") >= 2
