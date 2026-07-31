from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from integrations_control_center import ads_auto_sync_scheduler as scheduler


def test_defaults_to_enabled_five_minutes_and_two_days(monkeypatch):
    monkeypatch.delenv(scheduler.ENABLED_ENV, raising=False)
    monkeypatch.delenv(scheduler.INTERVAL_ENV, raising=False)
    monkeypatch.delenv(scheduler.ROLLING_DAYS_ENV, raising=False)

    assert scheduler.auto_sync_enabled() is True
    assert scheduler.interval_seconds() == 300
    assert scheduler.rolling_days() == 2


def test_interval_cannot_be_faster_than_five_minutes(monkeypatch):
    monkeypatch.setenv(scheduler.INTERVAL_ENV, "30")
    assert scheduler.interval_seconds() == 300

    monkeypatch.setenv(scheduler.INTERVAL_ENV, "900")
    assert scheduler.interval_seconds() == 900


def test_scheduler_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv(scheduler.ENABLED_ENV, "false")
    assert scheduler.auto_sync_enabled() is False


def test_rolling_window_uses_riyadh_calendar_day():
    # 21:30 UTC is 00:30 in Riyadh on Aug 1.
    start, end = scheduler.riyadh_date_range(
        datetime(2026, 7, 31, 21, 30, tzinfo=timezone.utc),
        2,
    )
    assert start.isoformat() == "2026-07-31"
    assert end.isoformat() == "2026-08-01"


def test_router_registers_status_and_backend_lifecycle(monkeypatch):
    monkeypatch.setenv(scheduler.ENABLED_ENV, "true")
    router = APIRouter(prefix="/integrations-v2")

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    def require_owner(user):
        return user

    before_startup = len(router.on_startup)
    before_shutdown = len(router.on_shutdown)
    scheduler.attach_ads_auto_sync_scheduler(
        router,
        object(),
        current_user,
        require_owner,
    )

    assert any(
        route.path == "/integrations-v2/ads-auto-sync/status"
        for route in router.routes
    )
    assert len(router.on_startup) == before_startup + 1
    assert len(router.on_shutdown) == before_shutdown + 1
