import asyncio

import pytest

from boot_runtime import process_local_readiness_event
from integrations_control_center import ads_auto_sync_scheduler as ads
from snapchat_v2 import scheduler as snapchat


class Router:
    def __init__(self):
        self.on_startup = []
        self.on_shutdown = []

    def get(self, *args, **kwargs):
        return lambda function: function


@pytest.mark.asyncio
async def test_ads_and_snapchat_wait_for_local_readiness_and_cancel_safely(monkeypatch):
    process_local_readiness_event.clear()
    calls = []
    blockers = {"ads": asyncio.Event(), "snapchat": asyncio.Event()}

    async def ads_loop(db):
        calls.append("ads")
        await blockers["ads"].wait()

    async def snapchat_loop(db):
        calls.append("snapchat")
        await blockers["snapchat"].wait()

    monkeypatch.setattr(ads, "auto_sync_enabled", lambda: True)
    monkeypatch.setattr(ads, "auto_sync_loop", ads_loop)
    monkeypatch.setattr(snapchat, "shadow_scheduler_enabled", lambda: True)
    monkeypatch.setattr(snapchat, "shadow_scheduler_loop", snapchat_loop)
    router = Router()
    ads.attach_ads_auto_sync_scheduler(router, object(), lambda: None, lambda x: x)
    snapchat.attach_shadow_scheduler(router, object())

    for callback in router.on_startup:
        await callback()
    await asyncio.sleep(0.01)
    assert calls == []

    process_local_readiness_event.set()
    for _ in range(20):
        if len(calls) == 2:
            break
        await asyncio.sleep(0)
    assert sorted(calls) == ["ads", "snapchat"]

    for callback in reversed(router.on_shutdown):
        await callback()
    process_local_readiness_event.clear()


@pytest.mark.asyncio
async def test_initialization_failure_never_releases_heavy_schedulers(monkeypatch):
    process_local_readiness_event.clear()
    called = asyncio.Event()

    async def provider_loop(db):
        called.set()

    monkeypatch.setattr(ads, "auto_sync_enabled", lambda: True)
    monkeypatch.setattr(ads, "auto_sync_loop", provider_loop)
    router = Router()
    ads.attach_ads_auto_sync_scheduler(router, object(), lambda: None, lambda x: x)
    await router.on_startup[0]()
    await asyncio.sleep(0.01)
    assert not called.is_set()
    await router.on_shutdown[0]()
    assert not called.is_set()
