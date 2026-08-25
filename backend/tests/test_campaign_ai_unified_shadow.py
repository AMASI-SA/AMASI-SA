from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import campaign_ai_unified_shadow as shadow
import campaign_ai_unified_source as source


def _row(
    level: str,
    entity_id: str,
    *,
    spend: float = 100.0,
    source_fact_collection: str | None = None,
):
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
        "source_fact_collection": source_fact_collection,
    }


def test_exact_total_facts_are_complete_even_while_level_status_is_shadow_pending():
    row = {
        "delivery": {"spend_sar": {"amount": 375.0, "currency": "SAR"}},
        "quality": {
            "sync_status": "partial",
            "coverage_status": "partial",
            "source_fact_count": 1,
        },
        "lineage": {
            "source_collection": "mezan_snapchat_daily_total_facts_v2",
        },
    }

    assert source._row_complete(row) is True


def test_partial_hourly_facts_remain_incomplete():
    row = {
        "delivery": {"spend_sar": {"amount": 375.0, "currency": "SAR"}},
        "quality": {
            "sync_status": "partial",
            "coverage_status": "partial",
            "source_fact_count": 1,
        },
        "lineage": {"source_collection": "mezan_snapchat_hourly_facts_v2"},
    }

    assert source._row_complete(row) is False


def test_shadow_range_uses_last_closed_account_day(monkeypatch):
    monkeypatch.setattr(
        source._legacy,
        "_utcnow",
        lambda: datetime(2026, 8, 25, 7, 20, tzinfo=timezone.utc),
    )

    start, end = source._local_range(
        "America/Los_Angeles",
        date(2026, 8, 25),
        date(2026, 8, 25),
        1,
    )

    assert start == date(2026, 8, 24)
    assert end == date(2026, 8, 24)


@pytest.mark.asyncio
async def test_ai_shadow_passes_overlap_without_requiring_v1_entity_set_equality(
    monkeypatch,
):
    offsets = []

    async def v1_campaigns(*_args):
        offsets.append(_args[-1])
        return [_row("campaign", "c-1")]

    async def v1_children(*_args):
        offsets.append(_args[-1])
        return [_row("ad_group", "g-1"), _row("ad", "a-1")]

    async def unified(*_args):
        offsets.append(_args[-1])
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

    monkeypatch.setattr(shadow._v1_policy, "_snapchat_v1_campaign_entities", v1_campaigns)
    monkeypatch.setattr(shadow._v1_policy, "_snapchat_v1_child_entities", v1_children)
    monkeypatch.setattr(shadow, "load_snapchat_unified_ai_entities", unified)

    result = await shadow.build_campaign_ai_unified_shadow(
        object(), "owner", days=1, today=date(2026, 8, 24)
    )

    assert result["shadow_passed"] is True
    assert result["cutover_ready"] is True
    assert result["cutover_active"] is True
    assert result["acceptance_basis"] == "exact_v1_overlap_match"
    assert result["period_policy"] == "last_closed_account_day"
    assert result["period_closed"] is True
    assert offsets == [1, 1, 1]
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

    monkeypatch.setattr(shadow._v1_policy, "_snapchat_v1_campaign_entities", v1_campaigns)
    monkeypatch.setattr(shadow._v1_policy, "_snapchat_v1_child_entities", v1_children)
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


@pytest.mark.asyncio
async def test_ai_shadow_accepts_exact_provider_total_facts_and_keeps_v1_drift(
    monkeypatch,
):
    total_collection = "mezan_snapchat_daily_total_facts_v2"

    async def v1_campaigns(*_args):
        return [_row("campaign", "c-1")]

    async def v1_children(*_args):
        return [_row("ad_group", "g-1"), _row("ad", "a-1")]

    async def unified(*_args):
        campaigns = [
            _row(
                "campaign",
                "c-1",
                spend=104.0,
                source_fact_collection=total_collection,
            )
        ]
        children = [
            _row(
                "ad_group",
                "g-1",
                spend=104.0,
                source_fact_collection=total_collection,
            ),
            _row(
                "ad",
                "a-1",
                spend=104.0,
                source_fact_collection=total_collection,
            ),
        ]
        return {
            "campaigns": campaigns,
            "children": children,
            "period": None,
            "account": {"id": "snap-1"},
        }

    monkeypatch.setattr(shadow._v1_policy, "_snapchat_v1_campaign_entities", v1_campaigns)
    monkeypatch.setattr(shadow._v1_policy, "_snapchat_v1_child_entities", v1_children)
    monkeypatch.setattr(shadow, "load_snapchat_unified_ai_entities", unified)

    result = await shadow.build_campaign_ai_unified_shadow(
        object(), "owner", days=1, today=date(2026, 8, 24)
    )

    assert result["shadow_passed"] is True
    assert result["acceptance_basis"] == (
        "provider_total_facts_fallback_v1_observer_drift"
    )
    assert result["decision_eligibility"]["eligible"] is False
    assert all(level["mismatch_count"] == 1 for level in result["levels"])
    assert all(level["provider_total_facts_fallback"] for level in result["levels"])


@pytest.mark.asyncio
async def test_ai_shadow_rejects_mismatch_without_exact_total_facts(monkeypatch):
    async def v1_campaigns(*_args):
        return [_row("campaign", "c-1")]

    async def v1_children(*_args):
        return [_row("ad_group", "g-1"), _row("ad", "a-1")]

    async def unified(*_args):
        return {
            "campaigns": [_row("campaign", "c-1", spend=104.0)],
            "children": [
                _row("ad_group", "g-1", spend=104.0),
                _row("ad", "a-1", spend=104.0),
            ],
            "period": None,
            "account": {"id": "snap-1"},
        }

    monkeypatch.setattr(shadow._v1_policy, "_snapchat_v1_campaign_entities", v1_campaigns)
    monkeypatch.setattr(shadow._v1_policy, "_snapchat_v1_child_entities", v1_children)
    monkeypatch.setattr(shadow, "load_snapchat_unified_ai_entities", unified)

    result = await shadow.build_campaign_ai_unified_shadow(
        object(), "owner", days=1, today=date(2026, 8, 24)
    )

    assert result["shadow_passed"] is False
    assert result["acceptance_basis"] == "not_accepted"
