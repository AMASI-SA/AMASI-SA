from __future__ import annotations

from datetime import date

import pytest

import campaign_ai_unified_shadow as shadow


def _row(level: str, entity_id: str, *, spend: float = 100.0):
    return {
        "provider": "snapchat",
        "entity_level": level,
        "account_id": "snap-1",
        "entity_id": entity_id,
        "entity_name": entity_id,
        "spend_sar": spend,
        "impressions": 1000,
        "clicks": 50,
        "purchases": 4,
        "data_complete": True,
    }


@pytest.mark.asyncio
async def test_ai_shadow_passes_overlap_without_requiring_v1_entity_set_equality(
    monkeypatch,
):
    async def v1_campaigns(*_args):
        return [_row("campaign", "c-1")]

    async def v1_children(*_args):
        return [_row("ad_group", "g-1"), _row("ad", "a-1")]

    async def unified(*_args):
        extra = _row("campaign", "c-v2-extra", spend=0.0)
        return {
            "campaigns": [_row("campaign", "c-1"), extra],
            "children": [_row("ad_group", "g-1"), _row("ad", "a-1")],
            "period": {
                "date_from": "2026-08-24",
                "date_to": "2026-08-24",
                "timezone": "America/Los_Angeles",
                "action_report_time": "conversion",
            },
            "account": {"id": "snap-1"},
        }

    monkeypatch.setattr(shadow._v1_policy, "_snapchat_campaign_entities", v1_campaigns)
    monkeypatch.setattr(shadow._v1_policy, "_snapchat_child_entities", v1_children)
    monkeypatch.setattr(shadow, "load_snapchat_unified_ai_entities", unified)

    result = await shadow.build_campaign_ai_unified_shadow(
        object(), "owner", days=1, today=date(2026, 8, 24)
    )

    assert result["shadow_passed"] is True
    assert result["cutover_ready"] is True
    assert result["writes_performed"] is False
    assert result["openai_called"] is False
    assert result["decision_eligibility"]["eligible"] is False
    campaign = next(row for row in result["levels"] if row["level"] == "campaign")
    assert campaign["v1_rows"] == 1
    assert campaign["unified_v2_rows"] == 2
    assert campaign["entity_set_equality_required"] is False


@pytest.mark.asyncio
async def test_ai_shadow_fails_closed_on_metric_mismatch(monkeypatch):
    async def v1_campaigns(*_args):
        return [_row("campaign", "c-1")]

    async def v1_children(*_args):
        return [_row("ad_group", "g-1"), _row("ad", "a-1")]

    async def unified(*_args):
        campaign = _row("campaign", "c-1")
        campaign["purchases"] = 9
        return {
            "campaigns": [campaign],
            "children": [_row("ad_group", "g-1"), _row("ad", "a-1")],
            "period": None,
            "account": {"id": "snap-1"},
        }

    monkeypatch.setattr(shadow._v1_policy, "_snapchat_campaign_entities", v1_campaigns)
    monkeypatch.setattr(shadow._v1_policy, "_snapchat_child_entities", v1_children)
    monkeypatch.setattr(shadow, "load_snapchat_unified_ai_entities", unified)

    result = await shadow.build_campaign_ai_unified_shadow(
        object(), "owner", days=1, today=date(2026, 8, 24)
    )

    assert result["shadow_passed"] is False
    assert result["cutover_ready"] is False
    assert result["decision_eligibility"]["eligible"] is False
    campaign = next(row for row in result["levels"] if row["level"] == "campaign")
    assert campaign["mismatch_count"] == 1
    assert "purchases" in campaign["mismatches"][0]["metrics"]
