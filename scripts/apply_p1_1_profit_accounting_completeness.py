#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "backend" / "campaign_ai_monitor_legacy.py"
GOAL = ROOT / "backend" / "campaign_ai_monthly_profit_goal_v1.py"
GATE = ROOT / "backend" / "campaign_ai_profit_accounting_gate.py"
TEST = ROOT / "backend" / "tests" / "test_campaign_ai_profit_accounting_completeness_p1_1.py"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"{label}: already patched")
        return
    if old not in text:
        raise SystemExit(f"{label}: expected anchor not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"{label}: patched")


GATE.write_text('''"""Fail-closed store-level profit accounting gate for Campaign AI scaling."""\nfrom __future__ import annotations\n\nfrom datetime import datetime, timezone, timedelta\nfrom typing import Any\n\nfrom fastapi import HTTPException\n\nfrom mezan_campaign_profit_loader import build_mezan_profit_totals\n\nRIYADH = timezone(timedelta(hours=3))\n\n\ndef _count(value: Any) -> int | None:\n    if value is None or isinstance(value, bool):\n        return None\n    try:\n        parsed = int(value)\n    except (TypeError, ValueError, OverflowError):\n        return None\n    return parsed if parsed >= 0 else None\n\n\ndef accounting_quality_from_totals(totals: dict[str, Any] | None) -> dict[str, Any]:\n    source = totals if isinstance(totals, dict) else {}\n    missing = _count(source.get("missing_product_cost_count"))\n    incomplete = _count(source.get("incomplete_profit_orders_count"))\n    complete = missing == 0 and incomplete == 0\n    return {\n        "complete": complete,\n        "missing_product_cost_count": missing,\n        "incomplete_profit_orders_count": incomplete,\n        "source": source.get("profit_source") or "mezan_profit_engine_v2_read_only",\n    }\n\n\nasync def require_profit_accounting_complete_for_scale(\n    db: Any,\n    user_id: str,\n    action: str,\n) -> dict[str, Any]:\n    \"\"\"Allow defensive actions, but block spend expansion on incomplete P&L.\"\"\"\n    if str(action or "").strip().lower() != "scale":\n        return {"complete": True, "scale_gate_applied": False}\n    today = datetime.now(RIYADH).date()\n    totals = await build_mezan_profit_totals(\n        db,\n        user_id,\n        from_date=today.replace(day=1).isoformat(),\n        to_date=today.isoformat(),\n    )\n    quality = accounting_quality_from_totals(totals)\n    if not quality["complete"]:\n        raise HTTPException(\n            status_code=409,\n            detail={\n                "code": "campaign_ai_profit_accounting_incomplete",\n                "message": (\n                    "صافي الربح الحالي غير مكتمل محاسبيًا لبعض الطلبات؛ "\n                    "أُوقفت زيادة الإنفاق حتى تكتمل تكاليف المنتجات والطلبات."\n                ),\n                **quality,\n                "recovery_action": "complete_missing_profit_inputs_then_refresh_recommendation",\n            },\n        )\n    return {**quality, "scale_gate_applied": True}\n\n\n__all__ = [\n    "accounting_quality_from_totals",\n    "require_profit_accounting_complete_for_scale",\n]\n''', encoding='utf-8')
print("wrote backend/campaign_ai_profit_accounting_gate.py")

replace_once(
    MONITOR,
    '''    level = recommendation["entity_level"]\n    action = {"campaign": "campaign.update", "ad_group": "ad_squad.update", "ad": "ad.update"}[level]\n    requested = recommendation["action"]\n    payload: dict[str, Any]\n''',
    '''    level = recommendation["entity_level"]\n    action = {"campaign": "campaign.update", "ad_group": "ad_squad.update", "ad": "ad.update"}[level]\n    requested = recommendation["action"]\n    from campaign_ai_profit_accounting_gate import (\n        require_profit_accounting_complete_for_scale,\n    )\n    await require_profit_accounting_complete_for_scale(db, user_id, requested)\n    payload: dict[str, Any]\n''',
    "snap scale accounting gate",
)

replace_once(
    MONITOR,
    '''    await _execution_quality.preflight_approved_execution(\n        db,\n        recommendation_collection=RECOMMENDATION_COLLECTION,\n        user_id=user_id,\n        snapshot_id=snapshot_id,\n        recommendation_id=recommendation_id,\n        expected_digest=snapshot_digest,\n    )\n    access_token = await _meta_credential(db, user_id, _utcnow())\n''',
    '''    await _execution_quality.preflight_approved_execution(\n        db,\n        recommendation_collection=RECOMMENDATION_COLLECTION,\n        user_id=user_id,\n        snapshot_id=snapshot_id,\n        recommendation_id=recommendation_id,\n        expected_digest=snapshot_digest,\n    )\n    from campaign_ai_profit_accounting_gate import (\n        require_profit_accounting_complete_for_scale,\n    )\n    await require_profit_accounting_complete_for_scale(\n        db, user_id, str(recommendation.get("action") or "")\n    )\n    access_token = await _meta_credential(db, user_id, _utcnow())\n''',
    "meta scale accounting gate",
)

replace_once(
    GOAL,
    '''    if net_profit is None:\n        return {\n            **base,\n            "progress_available": False,\n''',
    '''    missing_costs = month_to_date.get("missing_product_cost_count")\n    incomplete_orders = month_to_date.get("incomplete_profit_orders_count")\n    try:\n        missing_costs = int(missing_costs)\n    except (TypeError, ValueError, OverflowError):\n        missing_costs = None\n    try:\n        incomplete_orders = int(incomplete_orders)\n    except (TypeError, ValueError, OverflowError):\n        incomplete_orders = None\n    accounting_complete = missing_costs == 0 and incomplete_orders == 0\n    base = {\n        **base,\n        "profit_accounting_complete": accounting_complete,\n        "scale_execution_allowed_by_profit_accounting": accounting_complete,\n        "profit_accounting_quality": {\n            "missing_product_cost_count": missing_costs,\n            "incomplete_profit_orders_count": incomplete_orders,\n        },\n    }\n    if net_profit is None:\n        return {\n            **base,\n            "progress_available": False,\n''',
    "goal accounting quality",
)

replace_once(
    GOAL,
    '''    if net_profit >= target:\n        status = "minimum_target_covered"\n        phase = "expand_above_floor"\n    elif projected >= target:\n        status = "on_track"\n        phase = "protect_target_path"\n    else:\n        status = "behind_target"\n        phase = "recover_profit_gap"\n''',
    '''    if not accounting_complete:\n        status = "profit_accounting_incomplete"\n        phase = "protect_data_quality"\n    elif net_profit >= target:\n        status = "minimum_target_covered"\n        phase = "expand_above_floor"\n    elif projected >= target:\n        status = "on_track"\n        phase = "protect_target_path"\n    else:\n        status = "behind_target"\n        phase = "recover_profit_gap"\n''',
    "goal incomplete accounting phase",
)

TEST.write_text('''import pytest\nfrom fastapi import HTTPException\n\nimport campaign_ai_profit_accounting_gate as gate\nimport campaign_ai_monthly_profit_goal_v1 as goal\n\n\ndef test_p1_1_accounting_quality_requires_zero_missing_and_incomplete():\n    assert gate.accounting_quality_from_totals({\n        "missing_product_cost_count": 0,\n        "incomplete_profit_orders_count": 0,\n    })["complete"] is True\n    assert gate.accounting_quality_from_totals({\n        "missing_product_cost_count": 1,\n        "incomplete_profit_orders_count": 0,\n    })["complete"] is False\n    assert gate.accounting_quality_from_totals({\n        "missing_product_cost_count": 0,\n        "incomplete_profit_orders_count": 2,\n    })["complete"] is False\n    assert gate.accounting_quality_from_totals({})["complete"] is False\n\n\n@pytest.mark.asyncio\nasync def test_p1_1_reduce_and_pause_are_not_blocked(monkeypatch):\n    async def forbidden(*args, **kwargs):\n        raise AssertionError("profit loader must not run for defensive action")\n    monkeypatch.setattr(gate, "build_mezan_profit_totals", forbidden)\n    assert (await gate.require_profit_accounting_complete_for_scale(object(), "u", "reduce"))["scale_gate_applied"] is False\n    assert (await gate.require_profit_accounting_complete_for_scale(object(), "u", "pause"))["scale_gate_applied"] is False\n\n\n@pytest.mark.asyncio\nasync def test_p1_1_scale_fails_closed_when_profit_inputs_incomplete(monkeypatch):\n    async def loader(*args, **kwargs):\n        return {\n            "missing_product_cost_count": 1,\n            "incomplete_profit_orders_count": 3,\n            "profit_source": "mezan_profit_engine_v2_read_only",\n        }\n    monkeypatch.setattr(gate, "build_mezan_profit_totals", loader)\n    with pytest.raises(HTTPException) as caught:\n        await gate.require_profit_accounting_complete_for_scale(object(), "u", "scale")\n    assert caught.value.status_code == 409\n    assert caught.value.detail["code"] == "campaign_ai_profit_accounting_incomplete"\n    assert caught.value.detail["recovery_action"] == "complete_missing_profit_inputs_then_refresh_recommendation"\n\n\n@pytest.mark.asyncio\nasync def test_p1_1_scale_allowed_only_when_profit_inputs_complete(monkeypatch):\n    async def loader(*args, **kwargs):\n        return {\n            "missing_product_cost_count": 0,\n            "incomplete_profit_orders_count": 0,\n            "profit_source": "mezan_profit_engine_v2_read_only",\n        }\n    monkeypatch.setattr(gate, "build_mezan_profit_totals", loader)\n    result = await gate.require_profit_accounting_complete_for_scale(object(), "u", "scale")\n    assert result["complete"] is True\n    assert result["scale_gate_applied"] is True\n\n\ndef test_p1_1_goal_context_stays_numeric_but_blocks_expansion_when_accounting_incomplete():\n    result = goal._derive_goal_progress(\n        goal={"minimum_net_profit_sar": 100000.0},\n        month_to_date={\n            "net_profit": 40000.0,\n            "missing_product_cost_count": 1,\n            "incomplete_profit_orders_count": 2,\n        },\n        end=__import__("datetime").date(2026, 8, 21),\n    )\n    assert result["progress_available"] is True\n    assert result["net_profit_to_date_sar"] == 40000.0\n    assert result["profit_accounting_complete"] is False\n    assert result["scale_execution_allowed_by_profit_accounting"] is False\n    assert result["status"] == "profit_accounting_incomplete"\n    assert result["phase"] == "protect_data_quality"\n''', encoding='utf-8')
print("wrote backend/tests/test_campaign_ai_profit_accounting_completeness_p1_1.py")
