from __future__ import annotations

from fastapi import APIRouter

from integrations_control_center import ads_auto_sync_scheduler as scheduler


def test_scheduler_defaults_to_enabled_five_minutes_and_two_days(monkeypatch):
    monkeypatch.delenv(scheduler.ADS_AUTO_SYNC_ENABLED_ENV, raising=False)
    monkeypatch.delenv(scheduler.ADS_AUTO_SYNC_INTERVAL_ENV, raising=False)
    monkeypatch.delenv(scheduler.ADS_AUTO_SYNC_DAYS_ENV, raising=False)

    assert scheduler.ads_auto_sync_enabled() is True
    assert scheduler.ads_auto_sync_interval_seconds() == 300
    assert scheduler.ads_auto_sync_rolling_days() == 2


def test_scheduler_never_allows_frequency_faster_than_five_minutes(monkeypatch):
    monkeypatch.setenv(scheduler.ADS_AUTO_SYNC_INTERVAL_ENV, "30")
    assert scheduler.ads_auto_sync_interval_seconds() == 300

    monkeypatch.setenv(scheduler.ADS_AUTO_SYNC_INTERVAL_ENV, "900")
    assert scheduler.ads_auto_sync_interval_seconds() == 900


def test_scheduler_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv(scheduler.ADS_AUTO_SYNC_ENABLED_ENV, "false")
    assert scheduler.ads_auto_sync_enabled() is False


def test_target_pairs_are_deduplicated_and_limited_to_native_providers():
    rows = [
        {"user_id": "owner-1", "provider": "meta_ads"},
        {"user_id": "owner-1", "provider": "meta_ads"},
        {"user_id": "owner-1", "provider": "snapchat_ads"},
        {"user_id": "owner-1", "provider": "tiktok_ads"},
        {"user_id": "", "provider": "meta_ads"},
    ]
    assert scheduler._target_pairs(rows) == [
        ("owner-1", "meta_ads"),
        ("owner-1", "snapchat_ads"),
    ]


def test_router_registers_status_route_and_backend_lifecycle(monkeypatch):
    monkeypatch.setenv(scheduler.ADS_AUTO_SYNC_ENABLED_ENV, "true")
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


def test_public_scheduler_document_never_exposes_worker_lease():
    public = scheduler._public_scheduler_document(
        {
            "status": "complete",
            "lease_owner": "private-worker-id",
            "lease_expires_at": "private-deadline",
            "last_started_at": "2026-07-31T18:00:00+00:00",
            "last_finished_at": "2026-07-31T18:01:00+00:00",
            "next_due_at": "2026-07-31T18:05:00+00:00",
        }
    )
    assert "lease_owner" not in public
    assert "lease_expires_at" not in public
    assert public["status"] == "complete"
