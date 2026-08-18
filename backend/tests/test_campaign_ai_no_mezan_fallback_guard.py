import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import campaign_ai_worker_runner as worker
from campaign_ai_public_guard import (
    _public_document,
    _public_recent_openai_after_fallback,
)


def test_public_guard_suppresses_legacy_mezan_fallback_snapshot():
    result = _public_document({
        "snapshot_id": "snap-1",
        "recommendation_source": "mezan_fallback",
        "decision_authority": "mezan_fallback",
        "summary": "legacy fallback",
        "recommendations": [{
            "recommendation_id": "legacy-1",
            "provider": "meta",
            "recommendation_source": "mezan_fallback",
        }],
        "limitations": [],
        "execution_targets": {"legacy-1": {"provider": "meta"}},
        "user_id": "owner",
    })

    assert result["available"] is True
    assert result["recommendations"] == []
    assert result["recommendation_source"] == "openai_unavailable"
    assert result["decision_authority"] == "openai_unavailable"
    assert result["legacy_fallback_suppressed"] is True
    assert "legacy_mezan_fallback_suppressed" in result["limitations"]
    assert "execution_targets" not in result
    assert "user_id" not in result
    assert "توصيات احتياطية" not in result["summary"]


def test_public_guard_keeps_valid_openai_snapshot_unchanged():
    result = _public_document({
        "snapshot_id": "snap-openai",
        "recommendation_source": "openai",
        "decision_authority": "openai",
        "summary": "تحليل OpenAI",
        "recommendations": [{
            "recommendation_id": "ai-1",
            "provider": "snapchat",
            "recommendation_source": "openai",
            "approval_available": True,
        }],
    })

    assert result["available"] is True
    assert result["recommendation_source"] == "openai"
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["approval_available"] is True


def test_public_guard_also_blocks_item_level_fallback_marker():
    result = _public_document({
        "snapshot_id": "mixed",
        "recommendation_source": "openai",
        "recommendations": [{
            "recommendation_id": "bad-item",
            "recommendation_source": "mezan_fallback",
        }],
    })

    assert result["recommendations"] == []
    assert result["recommendation_source"] == "openai_unavailable"


def test_recent_openai_snapshot_survives_a_newer_legacy_fallback_write():
    now = datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc)
    result = _public_recent_openai_after_fallback({
        "snapshot_id": "snap-openai",
        "generated_at": (now - timedelta(minutes=8)).isoformat(),
        "recommendation_source": "openai",
        "decision_authority": "openai",
        "summary": "تحليل OpenAI صالح",
        "recommendations": [{
            "recommendation_id": "ai-1",
            "provider": "snapchat",
            "recommendation_source": "openai",
            "approval_available": True,
        }],
        "limitations": [],
    }, now=now)

    assert result is not None
    assert result["recommendation_source"] == "openai"
    assert len(result["recommendations"]) == 1
    assert result["serving_previous_valid_openai_snapshot"] is True
    assert result["legacy_fallback_suppressed"] is True
    assert "newer_legacy_mezan_fallback_suppressed" in result["limitations"]


def test_expired_openai_snapshot_is_not_reused_after_fallback():
    now = datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc)
    result = _public_recent_openai_after_fallback({
        "snapshot_id": "old-openai",
        "generated_at": (now - timedelta(hours=5, seconds=1)).isoformat(),
        "recommendation_source": "openai",
        "recommendations": [{"recommendation_id": "old"}],
    }, now=now)

    assert result is None


def test_guarded_read_route_is_registered_before_legacy_route():
    source = Path("ads_manager/__init__.py").read_text(encoding="utf-8")
    guarded = source.index("attach_campaign_ai_public_guard(router, db, current_user)")
    legacy = source.index("attach_campaign_ai_routes(router, db, current_user, _require_owner)")
    assert guarded < legacy


def test_worker_requests_short_retry_when_openai_is_unavailable(monkeypatch):
    async def fake_run_once():
        return {
            "users": 1,
            "completed": 1,
            "failed": 0,
            "retryable_ai_runs": 1,
        }

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    assert asyncio.run(worker._main()) == 2


def test_worker_accepts_completed_openai_cycle(monkeypatch):
    async def fake_run_once():
        return {
            "users": 1,
            "completed": 1,
            "failed": 0,
            "retryable_ai_runs": 0,
        }

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    assert asyncio.run(worker._main()) == 0
