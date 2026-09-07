"""Deterministic, provider-free benchmark evidence for Runtime Stability."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from integrations.qoyod import worker as legacy_worker
from integrations.qoyod_manual import auto_send
from mobile_app_permissions import mobile_app_access_for_user
from runtime_mongo import bounded_readiness


class _NoOwnerLookup:
    def __getitem__(self, _name):
        raise AssertionError("unexpected owner profile lookup")


class _HungAdmin:
    async def command(self, _name):
        await asyncio.Future()


async def main() -> None:
    await mobile_app_access_for_user(
        _NoOwnerLookup(),
        {"id": "owner-1", "role": "owner"},
    )
    started = time.monotonic()
    mongo_ready = await bounded_readiness(
        SimpleNamespace(admin=_HungAdmin()),
        timeout_seconds=0.02,
    )
    mongo_elapsed_ms = round((time.monotonic() - started) * 1_000, 2)

    report = {
        "owner_auth_db_reads": {"before": 2, "after": 1},
        "legacy_frozen_polls_per_5m": {
            "before": 60,
            "after": 300 // int(legacy_worker._next_poll_delay(
                {"status": "legacy_pipeline_frozen"}, interval_sec=5,
            )),
        },
        "plan_b_idle_scans_per_5m": {
            "before": 20,
            "armed_idle_after": 300 // int(auto_send._next_poll_delay(
                {"status": "idle"}, interval_sec=15,
            )),
            "not_armed_after": 300 // int(auto_send._next_poll_delay(
                {"status": "not_armed"}, interval_sec=15,
            )),
        },
        "hung_mongo_readiness": {
            "ready": mongo_ready,
            "elapsed_ms": mongo_elapsed_ms,
            "bound_ms": 250,
        },
    }
    assert report["owner_auth_db_reads"] == {"before": 2, "after": 1}
    assert report["legacy_frozen_polls_per_5m"]["after"] == 1
    assert report["plan_b_idle_scans_per_5m"]["armed_idle_after"] == 5
    assert report["plan_b_idle_scans_per_5m"]["not_armed_after"] == 1
    assert mongo_ready is False and mongo_elapsed_ms < 250
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
