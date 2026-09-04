"""Deterministic load envelope for the Phase 5 shadow scheduler.

The fixture counts the five Unified evidence entry-point reads per provider
(identity, account, campaign, ad group, ad) plus one bounded tenant-discovery
read.  It deliberately exposes no provider or mutation method.
"""
from __future__ import annotations

import asyncio
import json
import time
import tracemalloc
from typing import Any

from decision_intelligence.scheduler import (
    Phase5SchedulerConfig,
    Phase5ShadowScheduler,
)


class FixtureCounters:
    def __init__(self) -> None:
        self.db_reads = 0
        self.db_writes = 0
        self.provider_calls = 0
        self.active_tasks = 0
        self.max_concurrent_tasks = 0

    async def evidence_reads(self) -> None:
        self.db_reads += 5
        self.active_tasks += 1
        self.max_concurrent_tasks = max(
            self.max_concurrent_tasks, self.active_tasks
        )
        try:
            await asyncio.sleep(0.005)
        finally:
            self.active_tasks -= 1


def _config() -> Phase5SchedulerConfig:
    return Phase5SchedulerConfig(
        enabled=True,
        interval_seconds=3600,
        initial_delay_seconds=0,
        max_tenants=20,
        max_entities=25,
        timeout_seconds=60,
        max_provider_concurrency=2,
        max_freshness_hours=36,
    )


def _result(provider: str, *, ready: bool) -> dict[str, Any]:
    recommendations = 25 if ready else 0
    return {
        "provider": provider,
        "mode": "recommendation_shadow",
        "decision_ready": ready,
        "evidence_timestamp": "2026-09-04T00:00:00+00:00",
        "gates": {
            "freshness": {
                "passed": ready,
                "reason": "fresh" if ready else "freshness_failed",
                "freshness_hours": 2.0 if ready else 72.0,
            }
        },
        "decisions": [
            {
                "status": "RECOMMENDATION_SHADOW",
                "recommendation": {"confidence": None, "priority_score": None},
            }
            for _ in range(recommendations)
        ],
        "summary": {"recommendations": recommendations},
        "approval_workflow": {"approval_can_execute": False},
        "scheduler_integration": {"automatic_execution_connected": False},
        "write_policy": {
            "platform_writes_enabled": False,
            "platform_writes_performed": False,
            "database_writes_performed": False,
        },
    }


async def _cycle_scenario(
    name: str,
    providers: tuple[str, ...],
    *,
    not_ready: set[str] | None = None,
) -> dict[str, Any]:
    counters = FixtureCounters()

    async def tenant_loader(_db: Any, *, max_tenants: int):
        counters.db_reads += 1
        return [
            {"user_id": "fixture-tenant", "providers": providers}
        ][:max_tenants], False

    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        await counters.evidence_reads()
        provider = kwargs["provider"]
        return _result(provider, ready=provider not in (not_ready or set()))

    instance = Phase5ShadowScheduler(
        object(),
        config=_config(),
        phase5_runner=runner,
        tenant_loader=tenant_loader,
    )
    tracemalloc.start()
    started = time.perf_counter()
    cycle = await instance.run_cycle()
    duration_ms = (time.perf_counter() - started) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "scenario": name,
        "duration_ms": round(duration_ms, 3),
        "db_reads": counters.db_reads,
        "provider_calls": counters.provider_calls,
        "db_writes": counters.db_writes,
        "recommendations_evaluated": sum(
            int(item.get("recommendations_count") or 0)
            for item in cycle.get("outcomes") or []
        ),
        "peak_memory_bytes": peak,
        "concurrent_tasks": counters.max_concurrent_tasks,
        "skipped_duplicate_runs": instance.overlap_prevented_count,
        "status_counts": {
            status: sum(
                item.get("status") == status for item in cycle.get("outcomes") or []
            )
            for status in ("success", "skipped", "failed")
        },
    }


async def _overlap_scenario() -> dict[str, Any]:
    counters = FixtureCounters()
    started_event = asyncio.Event()
    release = asyncio.Event()

    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        await counters.evidence_reads()
        started_event.set()
        await release.wait()
        return _result(kwargs["provider"], ready=True)

    instance = Phase5ShadowScheduler(
        object(), config=_config(), phase5_runner=runner
    )
    tracemalloc.start()
    started = time.perf_counter()
    first = asyncio.create_task(
        instance.run_provider("fixture-tenant", "snapchat_ads")
    )
    await started_event.wait()
    duplicate = await instance.run_provider("fixture-tenant", "snapchat_ads")
    release.set()
    completed = await first
    duration_ms = (time.perf_counter() - started) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "scenario": "overlapping_trigger",
        "duration_ms": round(duration_ms, 3),
        "db_reads": counters.db_reads,
        "provider_calls": counters.provider_calls,
        "db_writes": counters.db_writes,
        "recommendations_evaluated": completed["recommendations_count"],
        "peak_memory_bytes": peak,
        "concurrent_tasks": counters.max_concurrent_tasks,
        "skipped_duplicate_runs": instance.overlap_prevented_count,
        "status_counts": {
            "success": int(completed["status"] == "success"),
            "skipped": int(duplicate["status"] == "skipped"),
            "failed": 0,
        },
    }


async def benchmark() -> list[dict[str, Any]]:
    return [
        await _cycle_scenario("one_tenant_snapchat", ("snapchat_ads",)),
        await _cycle_scenario("one_tenant_meta", ("meta_ads",)),
        await _cycle_scenario(
            "one_tenant_both", ("snapchat_ads", "meta_ads")
        ),
        await _cycle_scenario(
            "not_ready_provider",
            ("meta_ads",),
            not_ready={"meta_ads"},
        ),
        await _overlap_scenario(),
    ]


if __name__ == "__main__":
    print(json.dumps(asyncio.run(benchmark()), indent=2, sort_keys=True))
