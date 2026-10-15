import asyncio
from datetime import datetime, timezone

import pytest

from integrations_control_center import ads_auto_sync_scheduler as scheduler
from resource_governor import ResourceGovernor
from snapchat_v2 import scheduler as snapchat_scheduler


@pytest.mark.asyncio
async def test_ads_worker_pool_is_bounded_two_and_snapchat_one(monkeypatch):
    monkeypatch.setenv("HEAVY_ADS_CONCURRENCY", "2")
    monkeypatch.setenv("HEAVY_GLOBAL_CAPACITY", "2")
    monkeypatch.setenv("HEAVY_DASHBOARD_WEIGHT", "1")
    monkeypatch.setattr(scheduler, "governor", ResourceGovernor())
    targets = [
        ("u1", scheduler.META_PROVIDER_ID),
        ("u2", scheduler.SNAPCHAT_PROVIDER_ID),
        ("u3", scheduler.TIKTOK_PROVIDER_ID),
        ("u4", scheduler.SNAPCHAT_PROVIDER_ID),
        ("u5", scheduler.GOOGLE_ADS_PROVIDER_ID),
    ]
    monkeypatch.setattr(scheduler, "_targets", lambda db: asyncio.sleep(0, result=targets))
    active = 0
    peak = 0
    snap_active = 0
    snap_peak = 0

    def refresh(provider):
        async def run(db, **kwargs):
            nonlocal active, peak, snap_active, snap_peak
            active += 1
            peak = max(peak, active)
            if provider == scheduler.SNAPCHAT_PROVIDER_ID:
                snap_active += 1
                snap_peak = max(snap_peak, snap_active)
            await asyncio.sleep(0.02)
            if provider == scheduler.SNAPCHAT_PROVIDER_ID:
                snap_active -= 1
            active -= 1
            return {"provider": provider, "status": "complete"}
        return run

    monkeypatch.setattr(scheduler, "_refresh_meta", refresh(scheduler.META_PROVIDER_ID))
    monkeypatch.setattr(scheduler, "_refresh_snapchat", refresh(scheduler.SNAPCHAT_PROVIDER_ID))
    monkeypatch.setattr(scheduler, "_refresh_tiktok", refresh(scheduler.TIKTOK_PROVIDER_ID))
    monkeypatch.setattr(scheduler, "_refresh_google", refresh(scheduler.GOOGLE_ADS_PROVIDER_ID))
    result = await scheduler.run_auto_sync_cycle(
        object(), now=lambda: datetime(2026, 8, 30, tzinfo=timezone.utc)
    )
    assert peak == 2
    assert snap_peak == 1
    assert [(row["user_id"], row["provider"]) for row in result["results"]] == targets


def test_snapchat_scheduler_parallelism_is_fixed_one(monkeypatch):
    monkeypatch.setenv("HEAVY_SNAPCHAT_CONCURRENCY", "4")
    assert snapchat_scheduler.max_parallel_accounts() == 1
