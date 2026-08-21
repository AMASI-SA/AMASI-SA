#!/usr/bin/env python3
"""Apply P0-5 Snapchat Campaign AI budget-drift hardening.

The Campaign AI recommendation stores a percentage change but historically
materialized an absolute Snapchat budget from the recommendation snapshot before
Snapchat's governed proposal captured its fresh provider baseline.  This patch
fails closed if the provider budget has drifted before preview/approval, while
the existing management control plane continues to fence any drift between
preview and the actual provider write.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "backend" / "campaign_ai_monitor_legacy.py"
TEST = ROOT / "backend" / "tests" / "test_campaign_ai_snap_budget_drift_p0_5.py"

HELPER_ANCHOR = '''async def _execute_snapchat_approval(\n'''
HELPER = '''def _require_snapchat_budget_basis_unchanged(\n    recommendation: dict[str, Any],\n    target: dict[str, Any],\n    original_snapshot: dict[str, Any] | None,\n) -> None:\n    \"\"\"Fail closed when the Snapchat budget changed after recommendation.\n\n    Campaign AI recommends a relative percentage. The absolute provider payload\n    is safe only while the fresh proposal baseline still equals the exact budget\n    observed by the recommendation snapshot. Any drift requires a new preview /\n    recommendation instead of applying a stale absolute amount.\n    \"\"\"\n    if str(recommendation.get(\"action\") or \"\") not in {\"scale\", \"reduce\"}:\n        return\n    if str(recommendation.get(\"entity_level\") or \"\") == \"ad\":\n        return\n    expected_native = _number(target.get(\"current_daily_budget_native\"))\n    current_micro = _number((original_snapshot or {}).get(\"daily_budget_micro\"))\n    if expected_native is None or expected_native <= 0 or current_micro is None or current_micro <= 0:\n        raise HTTPException(\n            status_code=409,\n            detail={\n                \"code\": \"snapchat_recommendation_budget_basis_unavailable\",\n                \"message\": \"تعذر إثبات ميزانية Snapchat الحالية؛ أنشئ توصية جديدة قبل التنفيذ.\",\n            },\n        )\n    expected_micro = int(round(expected_native * 1_000_000))\n    if int(round(current_micro)) != expected_micro:\n        raise HTTPException(\n            status_code=409,\n            detail={\n                \"code\": \"snapchat_recommendation_budget_drift\",\n                \"message\": (\n                    \"تغيرت ميزانية Snapchat بعد إنشاء التوصية؛ أُوقف التنفيذ \"\n                    \"حتى تُعاد التوصية من الميزانية الحالية.\"\n                ),\n                \"expected_budget_micro\": expected_micro,\n                \"current_budget_micro\": int(round(current_micro)),\n                \"recovery_action\": \"create_fresh_campaign_ai_recommendation\",\n            },\n        )\n\n\nasync def _execute_snapchat_approval(\n'''

ROW_OLD = '''    proposal_row = await db[\"mezan_snapchat_campaign_proposals_v1\"].find_one(\n        {\n            \"user_id\": user_id,\n            \"proposal_id\": str(proposal.get(\"proposal_id\") or \"\"),\n        },\n        {\"_id\": 0, \"original_snapshot\": 1},\n    ) or {}\n    _execution_quality.require_provider_state_unchanged(\n'''
ROW_NEW = '''    proposal_row = await db[\"mezan_snapchat_campaign_proposals_v1\"].find_one(\n        {\n            \"user_id\": user_id,\n            \"proposal_id\": str(proposal.get(\"proposal_id\") or \"\"),\n        },\n        {\"_id\": 0, \"original_snapshot\": 1},\n    ) or {}\n    _require_snapchat_budget_basis_unchanged(\n        recommendation, target, proposal_row.get(\"original_snapshot\")\n    )\n    _execution_quality.require_provider_state_unchanged(\n'''

EXPECTED_OLD = '''                \"execution_quality_contract\": _execution_quality.CONTRACT_VERSION,\n            },\n'''
EXPECTED_NEW = '''                \"execution_quality_contract\": _execution_quality.CONTRACT_VERSION,\n                \"budget_semantics\": (\n                    \"relative_percent_from_recommendation_snapshot_fail_closed\"\n                    if requested in {\"scale\", \"reduce\"}\n                    else None\n                ),\n                \"budget_change_percent\": (\n                    percent if requested in {\"scale\", \"reduce\"} else None\n                ),\n            },\n'''

TEST_CONTENT = '''import pytest\nfrom fastapi import HTTPException\n\nimport campaign_ai_monitor_legacy as legacy\n\n\ndef _rec(action=\"scale\", level=\"campaign\"):\n    return {\"action\": action, \"entity_level\": level}\n\n\ndef _target(budget=10.0):\n    return {\"current_daily_budget_native\": budget}\n\n\ndef test_p0_5_exact_fresh_budget_basis_is_allowed():\n    legacy._require_snapchat_budget_basis_unchanged(\n        _rec(), _target(10.0), {\"daily_budget_micro\": 10_000_000}\n    )\n\n\ndef test_p0_5_budget_drift_blocks_stale_absolute_write():\n    with pytest.raises(HTTPException) as caught:\n        legacy._require_snapchat_budget_basis_unchanged(\n            _rec(), _target(10.0), {\"daily_budget_micro\": 13_000_000}\n        )\n    assert caught.value.status_code == 409\n    assert caught.value.detail[\"code\"] == \"snapchat_recommendation_budget_drift\"\n    assert caught.value.detail[\"recovery_action\"] == \"create_fresh_campaign_ai_recommendation\"\n\n\ndef test_p0_5_missing_fresh_budget_basis_fails_closed():\n    with pytest.raises(HTTPException) as caught:\n        legacy._require_snapchat_budget_basis_unchanged(_rec(), _target(10.0), {})\n    assert caught.value.detail[\"code\"] == \"snapchat_recommendation_budget_basis_unavailable\"\n\n\ndef test_p0_5_pause_does_not_require_budget_basis():\n    legacy._require_snapchat_budget_basis_unchanged(_rec(\"pause\"), _target(None), {})\n\n\ndef test_p0_5_ad_level_does_not_invent_budget_semantics():\n    legacy._require_snapchat_budget_basis_unchanged(_rec(\"scale\", \"ad\"), _target(None), {})\n'''


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"{label}: already patched")
        return
    if old not in text:
        raise SystemExit(f"{label}: expected anchor not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"{label}: patched")


def main() -> None:
    text = LEGACY.read_text(encoding="utf-8")
    if "def _require_snapchat_budget_basis_unchanged(" not in text:
        if HELPER_ANCHOR not in text:
            raise SystemExit("helper anchor not found")
        LEGACY.write_text(text.replace(HELPER_ANCHOR, HELPER, 1), encoding="utf-8")
        print("snap budget drift helper: patched")
    else:
        print("snap budget drift helper: already patched")
    replace_once(LEGACY, ROW_OLD, ROW_NEW, "snap proposal fresh-budget guard")
    replace_once(LEGACY, EXPECTED_OLD, EXPECTED_NEW, "snap budget audit semantics")
    TEST.write_text(TEST_CONTENT, encoding="utf-8")
    print(f"wrote {TEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
