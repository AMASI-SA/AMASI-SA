#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "backend" / "campaign_ai_execution_quality_gate.py"
MONITOR = ROOT / "backend" / "campaign_ai_monitor_legacy.py"
TEST = ROOT / "backend" / "tests" / "test_campaign_ai_approval_preflight_p1_3.py"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"{label}: already patched")
        return
    if old not in text:
        raise SystemExit(f"{label}: expected anchor not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"{label}: patched")

# P1-3: a recommendation that is already executing, completed, or has an
# unresolved provider outcome can never be approved/executed again from the same
# snapshot. This check lives in the shared preflight so both providers inherit it.
replace_once(
    GATE,
    '''    if (\n        not isinstance(recommendation, dict)\n        or not isinstance(target, dict)\n        or recommendation.get("approval_available") is not True\n    ):\n        raise ExecutionQualityBlocked(["execution_snapshot_target_unavailable"])\n    baseline = target.get("execution_quality")\n''',
    '''    if (\n        not isinstance(recommendation, dict)\n        or not isinstance(target, dict)\n        or recommendation.get("approval_available") is not True\n    ):\n        raise ExecutionQualityBlocked(["execution_snapshot_target_unavailable"])\n    execution_status = _text(recommendation.get("execution_status"), limit=80).lower()\n    if execution_status in {\n        "executing",\n        "completed",\n        "provider_state_uncertain",\n        "verification_required",\n    }:\n        raise ExecutionQualityBlocked([\n            "execution_recommendation_not_approvable"\n        ], {\n            "execution_status": execution_status,\n            "recommendation_id": recommendation_id,\n        })\n    baseline = target.get("execution_quality")\n''',
    "shared recommendation state preflight",
)

# Snapchat previously created/approved a provider proposal before running the
# shared Campaign-AI preflight. Run the same durable preflight before any proposal
# or confirm-token work, and keep the existing second preflight immediately before
# the actual provider mutation.
replace_once(
    MONITOR,
    '''async def _execute_snapchat_approval(\n    db: Any,\n    user_id: str,\n    recommendation: dict[str, Any],\n    target: dict[str, Any],\n    *,\n    idempotency_key: str,\n    snapshot_id: str,\n    recommendation_id: str,\n    snapshot_digest: str,\n) -> dict[str, Any]:\n    from integrations_control_center.snapchat_campaign_management import (\n''',
    '''async def _execute_snapchat_approval(\n    db: Any,\n    user_id: str,\n    recommendation: dict[str, Any],\n    target: dict[str, Any],\n    *,\n    idempotency_key: str,\n    snapshot_id: str,\n    recommendation_id: str,\n    snapshot_digest: str,\n) -> dict[str, Any]:\n    await _execution_quality.preflight_approved_execution(\n        db,\n        recommendation_collection=RECOMMENDATION_COLLECTION,\n        user_id=user_id,\n        snapshot_id=snapshot_id,\n        recommendation_id=recommendation_id,\n        expected_digest=snapshot_digest,\n    )\n    from integrations_control_center.snapchat_campaign_management import (\n''',
    "snapchat early approval preflight",
)

TEST.write_text('''from pathlib import Path\n\nimport pytest\n\nimport campaign_ai_execution_quality_gate as gate\n\n\nclass _Collection:\n    def __init__(self, row):\n        self.row = row\n    async def find_one(self, *args, **kwargs):\n        return self.row\n\n\nclass _DB:\n    def __init__(self, row):\n        self.row = row\n    def __getitem__(self, name):\n        return _Collection(self.row)\n\n\ndef _snapshot(status):\n    recommendation = {\n        "recommendation_id": "r1",\n        "provider": "meta",\n        "entity_level": "campaign",\n        "entity_id": "c1",\n        "account_id": "a1",\n        "action": "scale",\n        "change_percent": 10,\n        "approval_available": True,\n        "execution_status": status,\n    }\n    target = {\n        "provider": "meta",\n        "entity_level": "campaign",\n        "entity_id": "c1",\n        "account_id": "a1",\n        "execution_quality": {\n            "contract_version": gate.CONTRACT_VERSION,\n            "status": "complete",\n            "blockers": [],\n            "provider": "meta",\n            "entity_level": "campaign",\n            "entity_id": "c1",\n            "account_id": "a1",\n            "entity_facts": {"fingerprint": "fp"},\n        },\n    }\n    return {\n        "snapshot_id": "s1",\n        "generated_at": "2026-08-21T12:00:00+00:00",\n        "range": {"from": "2026-08-18", "to": "2026-08-20"},\n        "recommendations": [recommendation],\n        "execution_targets": {"r1": target},\n    }, recommendation, target\n\n\n@pytest.mark.asyncio\n@pytest.mark.parametrize("status", [\n    "executing",\n    "completed",\n    "provider_state_uncertain",\n    "verification_required",\n])\nasync def test_p1_3_terminal_or_uncertain_state_cannot_be_approved_again(status):\n    row, recommendation, target = _snapshot(status)\n    digest = gate.execution_snapshot_digest("s1", recommendation, target)\n    with pytest.raises(gate.ExecutionQualityBlocked) as caught:\n        await gate.preflight_approved_execution(\n            _DB(row),\n            recommendation_collection="recs",\n            user_id="u1",\n            snapshot_id="s1",\n            recommendation_id="r1",\n            expected_digest=digest,\n            now=lambda: __import__("datetime").datetime(\n                2026, 8, 21, 12, 30, tzinfo=__import__("datetime").timezone.utc\n            ),\n        )\n    assert "execution_recommendation_not_approvable" in caught.value.blockers\n    assert caught.value.evidence["execution_status"] == status\n\n\ndef test_p1_3_snapchat_runs_shared_preflight_before_provider_proposal():\n    source = Path(__file__).resolve().parents[1] / "campaign_ai_monitor_legacy.py"\n    text = source.read_text(encoding="utf-8")\n    start = text.index("async def _execute_snapchat_approval(")\n    end = text.index("def _meta_state_matches_mutation", start)\n    body = text[start:end]\n    assert body.index("preflight_approved_execution(") < body.index(\n        "create_snapchat_management_proposal("\n    )\n    # Keep the second execution-time preflight as defense in depth.\n    assert body.count("preflight_approved_execution(") >= 2\n''', encoding="utf-8")
print("wrote backend/tests/test_campaign_ai_approval_preflight_p1_3.py")
