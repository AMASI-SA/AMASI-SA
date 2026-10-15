from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integrations_control_center import ads_auto_sync_scheduler as scheduler


@pytest.mark.asyncio
async def test_five_minute_cycle_dispatches_tiktok_native_reporting(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_targets(db):
        assert db is marker_db
        return [
            ("owner-1", scheduler.META_PROVIDER_ID),
            ("owner-1", scheduler.SNAPCHAT_PROVIDER_ID),
            ("owner-1", scheduler.TIKTOK_PROVIDER_ID),
        ]

    async def fake_meta(db, *, user_id, start_date, end_date, now):
        calls.append((user_id, scheduler.META_PROVIDER_ID))
        return {"provider": scheduler.META_PROVIDER_ID, "status": "complete"}

    async def fake_snap(db, *, user_id, start_date, end_date, now):
        calls.append((user_id, scheduler.SNAPCHAT_PROVIDER_ID))
        return {"provider": scheduler.SNAPCHAT_PROVIDER_ID, "status": "complete"}

    async def fake_tiktok(db, *, user_id, start_date, end_date, now):
        calls.append((user_id, scheduler.TIKTOK_PROVIDER_ID))
        return {
            "provider": scheduler.TIKTOK_PROVIDER_ID,
            "status": "complete",
            "rows_saved": 2,
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    marker_db = object()
    monkeypatch.setattr(scheduler, "_targets", fake_targets)
    monkeypatch.setattr(scheduler, "_refresh_meta", fake_meta)
    monkeypatch.setattr(scheduler, "_refresh_snapchat", fake_snap)
    monkeypatch.setattr(scheduler, "_refresh_tiktok", fake_tiktok)
    monkeypatch.setattr(scheduler, "tiktok_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "tiktok_reporting_enabled", lambda: True)

    result = await scheduler.run_auto_sync_cycle(
        marker_db,
        now=lambda: datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "complete"
    assert result["targets"] == 3
    assert result["succeeded"] == 3
    assert {
        (item["user_id"], item["provider"])
        for item in result["results"]
    } == {
        ("owner-1", scheduler.META_PROVIDER_ID),
        ("owner-1", scheduler.SNAPCHAT_PROVIDER_ID),
        ("owner-1", scheduler.TIKTOK_PROVIDER_ID),
    }
    assert set(calls) == {
        ("owner-1", scheduler.META_PROVIDER_ID),
        ("owner-1", scheduler.SNAPCHAT_PROVIDER_ID),
        ("owner-1", scheduler.TIKTOK_PROVIDER_ID),
    }
    assert result["tiktok"] == {
        "mode": "native_polling",
        "status": "native_polling",
        "native_polling": True,
        "reason": None,
    }


def test_scheduler_interval_is_never_faster_than_five_minutes(monkeypatch):
    monkeypatch.delenv(scheduler.INTERVAL_ENV, raising=False)
    assert scheduler.interval_seconds() == 300

    monkeypatch.setenv(scheduler.INTERVAL_ENV, "60")
    assert scheduler.interval_seconds() == 300

    monkeypatch.setenv(scheduler.INTERVAL_ENV, "900")
    assert scheduler.interval_seconds() == 900


def test_tiktok_keeps_webhook_fallback_until_native_oauth_is_ready(monkeypatch):
    monkeypatch.setattr(scheduler, "tiktok_oauth_configured", lambda: False)
    monkeypatch.setattr(scheduler, "tiktok_reporting_enabled", lambda: False)
    assert scheduler._tiktok_scheduler_state() == {
        "mode": "automatic_webhook_feed",
        "status": "automatic_webhook_feed",
        "native_polling": False,
        "reason": "awaiting_tiktok_oauth_approval",
    }

    monkeypatch.setattr(scheduler, "tiktok_oauth_configured", lambda: True)
    assert scheduler._tiktok_scheduler_state()["reason"] == "native_reporting_disabled"


def test_scheduler_status_and_targets_include_tiktok_contract():
    import inspect

    target_source = inspect.getsource(scheduler._targets)
    status_source = inspect.getsource(scheduler.auto_sync_status)
    cycle_source = inspect.getsource(scheduler.run_auto_sync_cycle)

    assert "TIKTOK_PROVIDER_ID" in target_source
    assert "TIKTOK_PROVIDER_ID" in status_source
    assert "_refresh_tiktok" in cycle_source
    assert "automatic_webhook_feed" not in cycle_source
