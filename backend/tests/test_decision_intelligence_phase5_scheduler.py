from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import decision_intelligence.evidence_adapter as evidence_adapter
import decision_intelligence.scheduler as scheduler_module
from decision_intelligence.phase5 import run_phase5_shadow_from_evidence
from decision_intelligence.scheduler import (
    Phase5SchedulerConfig,
    Phase5ShadowScheduler,
)


def _ready_result(provider: str, *, recommendations: int = 1) -> dict[str, Any]:
    decisions = [
        {
            "status": "RECOMMENDATION_SHADOW",
            "recommendation": {"confidence": None, "priority_score": None},
        }
        for _ in range(recommendations)
    ]
    return {
        "mode": "recommendation_shadow",
        "provider": provider,
        "decision_ready": True,
        "evidence_timestamp": "2026-09-04T00:00:00+00:00",
        "gates": {
            "freshness": {
                "passed": True,
                "reason": "fresh",
                "freshness_hours": 2.0,
            }
        },
        "decisions": decisions,
        "summary": {"recommendations": recommendations},
        "approval_workflow": {"approval_can_execute": False},
        "scheduler_integration": {"automatic_execution_connected": False},
        "write_policy": {
            "platform_writes_enabled": False,
            "platform_writes_performed": False,
            "database_writes_performed": False,
        },
    }


def _not_ready_result(
    provider: str,
    *,
    gate: str,
    reason: str,
    acceptance_reasons: list[str] | None = None,
) -> dict[str, Any]:
    gate_value: dict[str, Any] = {"passed": False, "reason": reason}
    if acceptance_reasons is not None:
        gate_value["acceptance_reasons"] = acceptance_reasons
    return {
        "mode": "recommendation_shadow",
        "provider": provider,
        "decision_ready": False,
        "evidence_timestamp": "2026-09-04T00:00:00+00:00",
        "gates": {gate: gate_value},
        "decisions": [],
        "summary": {"recommendations": 0},
        "approval_workflow": {"approval_can_execute": False},
        "scheduler_integration": {"automatic_execution_connected": False},
        "write_policy": {
            "platform_writes_enabled": False,
            "platform_writes_performed": False,
            "database_writes_performed": False,
        },
    }


def _config(**overrides: Any) -> Phase5SchedulerConfig:
    values = {
        "enabled": True,
        "interval_seconds": 3600.0,
        "initial_delay_seconds": 0.0,
        "max_tenants": 20,
        "max_entities": 25,
        "timeout_seconds": 1.0,
        "max_provider_concurrency": 2,
        "max_freshness_hours": 36.0,
    }
    values.update(overrides)
    return Phase5SchedulerConfig(**values)


def _tenant_loader(rows: list[dict[str, Any]]):
    async def load(_db: Any, *, max_tenants: int):
        return rows[:max_tenants], len(rows) > max_tenants

    return load


def test_scheduler_is_disabled_by_default(monkeypatch):
    for name in (
        scheduler_module.ENABLED_ENV,
        scheduler_module.INTERVAL_ENV,
        scheduler_module.MAX_TENANTS_ENV,
        scheduler_module.MAX_ENTITIES_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    config = scheduler_module.load_scheduler_config()

    assert config.enabled is False
    assert config.interval_seconds == 3600
    assert config.max_tenants == 20
    assert config.max_entities == 25


def test_scheduler_configuration_is_bounded(monkeypatch):
    monkeypatch.setenv(scheduler_module.ENABLED_ENV, "true")
    monkeypatch.setenv(scheduler_module.INTERVAL_ENV, "1")
    monkeypatch.setenv(scheduler_module.MAX_TENANTS_ENV, "99999")
    monkeypatch.setenv(scheduler_module.MAX_ENTITIES_ENV, "99999")
    monkeypatch.setenv(scheduler_module.TIMEOUT_ENV, "99999")
    monkeypatch.setenv(scheduler_module.MAX_PROVIDER_CONCURRENCY_ENV, "99")

    config = scheduler_module.load_scheduler_config()

    assert config.enabled is True
    assert config.interval_seconds == scheduler_module.MIN_INTERVAL_SECONDS
    assert config.max_tenants == scheduler_module.MAX_TENANTS_LIMIT
    assert config.max_entities == scheduler_module.MAX_ENTITIES_LIMIT
    assert config.timeout_seconds == scheduler_module.MAX_TIMEOUT_SECONDS
    assert config.max_provider_concurrency == 2


@pytest.mark.asyncio
async def test_enabled_scheduler_invokes_phase5_with_exact_bounds():
    calls: list[dict[str, Any]] = []

    async def runner(_db: Any, user_id: str, **kwargs: Any):
        calls.append({"user_id": user_id, **kwargs})
        return _ready_result(kwargs["provider"])

    instance = Phase5ShadowScheduler(
        object(),
        config=_config(max_entities=17, max_freshness_hours=24),
        phase5_runner=runner,
        tenant_loader=_tenant_loader(
            [{"user_id": "owner-1", "providers": ("snapchat_ads",)}]
        ),
    )

    cycle = await instance.run_cycle()

    assert cycle["status"] == "success"
    assert calls == [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "max_freshness_hours": 24,
            "max_candidates": 17,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["snapchat_ads", "meta_ads"])
async def test_ready_provider_completes_shadow_run(provider):
    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        return _ready_result(kwargs["provider"])

    instance = Phase5ShadowScheduler(
        object(), config=_config(), phase5_runner=runner
    )

    event = await instance.run_provider("owner-1", provider)

    assert event["status"] == "success"
    assert event["readiness_result"] is True
    assert event["recommendations_count"] == 1
    assert event["evidence_timestamp"]


@pytest.mark.asyncio
async def test_meta_not_accepted_is_skipped_with_explicit_reason():
    async def runner(_db: Any, _user_id: str, **_kwargs: Any):
        return _not_ready_result(
            "meta_ads",
            gate="shadow_acceptance",
            reason="meta_shadow_not_accepted",
            acceptance_reasons=["settings_evidence_incomplete"],
        )

    instance = Phase5ShadowScheduler(
        object(), config=_config(), phase5_runner=runner
    )
    event = await instance.run_provider("owner-1", "meta_ads")

    assert event["status"] == "skipped"
    assert event["reason"] == "meta_shadow_not_accepted"
    assert event["readiness_reasons"] == [
        "meta_shadow_not_accepted",
        "settings_evidence_incomplete",
    ]
    assert event["recommendations_count"] == 0


@pytest.mark.asyncio
async def test_one_provider_failure_does_not_block_second_provider():
    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        if kwargs["provider"] == "snapchat_ads":
            raise RuntimeError("provider fixture failure")
        return _ready_result("meta_ads")

    instance = Phase5ShadowScheduler(
        object(),
        config=_config(),
        phase5_runner=runner,
        tenant_loader=_tenant_loader(
            [
                {
                    "user_id": "owner-1",
                    "providers": ("snapchat_ads", "meta_ads"),
                }
            ]
        ),
    )

    cycle = await instance.run_cycle()
    outcomes = {item["provider"]: item for item in cycle["outcomes"]}

    assert outcomes["snapchat_ads"]["status"] == "failed"
    assert outcomes["meta_ads"]["status"] == "success"


@pytest.mark.asyncio
async def test_one_tenant_failure_does_not_block_next_tenant():
    async def runner(_db: Any, user_id: str, **kwargs: Any):
        if user_id == "owner-1":
            raise RuntimeError("tenant fixture failure")
        return _ready_result(kwargs["provider"])

    instance = Phase5ShadowScheduler(
        object(),
        config=_config(),
        phase5_runner=runner,
        tenant_loader=_tenant_loader(
            [
                {"user_id": "owner-1", "providers": ("snapchat_ads",)},
                {"user_id": "owner-2", "providers": ("snapchat_ads",)},
            ]
        ),
    )

    cycle = await instance.run_cycle()

    assert [item["status"] for item in cycle["outcomes"]] == [
        "failed",
        "success",
    ]


@pytest.mark.asyncio
async def test_same_tenant_provider_overlap_is_prevented():
    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        started.set()
        await release.wait()
        return _ready_result(kwargs["provider"])

    instance = Phase5ShadowScheduler(
        object(), config=_config(), phase5_runner=runner
    )
    first = asyncio.create_task(instance.run_provider("owner-1", "snapchat_ads"))
    await started.wait()

    duplicate = await instance.run_provider("owner-1", "snapchat_ads")
    release.set()
    completed = await first

    assert duplicate["status"] == "skipped"
    assert duplicate["reason"] == "same_tenant_provider_run_active"
    assert completed["status"] == "success"
    assert instance.overlap_prevented_count == 1


@pytest.mark.asyncio
async def test_cancellation_releases_single_flight_key():
    started = asyncio.Event()
    attempts = 0

    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            started.set()
            await asyncio.Event().wait()
        return _ready_result(kwargs["provider"])

    instance = Phase5ShadowScheduler(
        object(), config=_config(), phase5_runner=runner
    )
    first = asyncio.create_task(instance.run_provider("owner-1", "snapchat_ads"))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = await instance.run_provider("owner-1", "snapchat_ads")

    assert second["status"] == "success"
    assert attempts == 2


@pytest.mark.asyncio
async def test_provider_timeout_is_enforced():
    async def runner(_db: Any, _user_id: str, **_kwargs: Any):
        await asyncio.Event().wait()

    instance = Phase5ShadowScheduler(
        object(),
        config=_config(timeout_seconds=0.01),
        phase5_runner=runner,
    )

    event = await instance.run_provider("owner-1", "snapchat_ads")

    assert event["status"] == "failed"
    assert event["reason"] == "provider_shadow_timeout"


@pytest.mark.asyncio
async def test_unsafe_phase5_contract_is_skipped_fail_closed():
    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        result = _ready_result(kwargs["provider"])
        result["approval_workflow"]["approval_can_execute"] = True
        return result

    instance = Phase5ShadowScheduler(
        object(), config=_config(), phase5_runner=runner
    )

    event = await instance.run_provider("owner-1", "snapchat_ads")

    assert event["status"] == "skipped"
    assert event["reason"] == "shadow_approval_contract_failed"
    assert event["recommendations_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gate", "reason"),
    [
        ("freshness", "freshness_failed"),
        ("reconciliation", "reconciliation_incomplete"),
        ("financial_coverage", "financial_coverage_incomplete"),
    ],
)
async def test_readiness_failures_skip_provider_fail_closed(gate, reason):
    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        return _not_ready_result(kwargs["provider"], gate=gate, reason=reason)

    instance = Phase5ShadowScheduler(
        object(), config=_config(), phase5_runner=runner
    )

    event = await instance.run_provider("owner-1", "snapchat_ads")

    assert event["status"] == "skipped"
    assert event["readiness_result"] is False
    assert event["reason"] == reason
    assert event["recommendations_count"] == 0


def test_phase5_output_retains_all_shadow_execution_invariants():
    evidence = {
        "contract_version": "unified-marketing-v1",
        "provider": "snapchat_ads",
        "account": {"id": "account-1"},
        "period": {"closed": True},
        "gates": {},
        "decision_ready": True,
        "candidates": [
            {
                "evidence_id": "snapchat_ads:campaign:campaign-1",
                "entity": {"level": "campaign", "id": "campaign-1"},
                "metrics": {},
                "lineage": {},
                "decision_eligible": True,
                "blocked_by": [],
            }
        ],
        "source": {"reader": "unified_marketing.gateway", "contract_only": True},
        "write_policy": {
            "platform_writes_enabled": False,
            "platform_writes_performed": False,
            "database_writes_performed": False,
        },
    }

    result = run_phase5_shadow_from_evidence(evidence)

    assert result["mode"] == "recommendation_shadow"
    assert result["approval_workflow"]["approval_can_execute"] is False
    assert result["scheduler_integration"]["automatic_execution_connected"] is False
    assert result["write_policy"]["database_writes_performed"] is False
    assert result["write_policy"]["platform_writes_performed"] is False


def test_candidate_evaluation_is_bounded_to_highest_spend():
    rows = [
        {"entity": {"id": str(index)}, "delivery": {"spend_sar": {"amount": index}}}
        for index in range(250)
    ]

    selected, source_count, limit_reached = evidence_adapter._bounded_campaign_rows(
        {"rows": rows}, max_candidates=25
    )

    assert source_count == 250
    assert len(selected) == 25
    assert limit_reached is True
    assert [row["entity"]["id"] for row in selected] == [
        str(index) for index in range(249, 224, -1)
    ]


@pytest.mark.asyncio
async def test_latest_closed_day_loads_identity_once_and_each_gateway_report_once(
    monkeypatch,
):
    calls: list[tuple[str, str | None]] = []

    async def identity(*_args: Any, **_kwargs: Any):
        calls.append(("identity", None))
        return {
            "id": "account-1",
            "timezone": "Asia/Riyadh",
            "last_sync_at": "2026-09-03T23:00:00+00:00",
        }

    async def account(*_args: Any, **kwargs: Any):
        calls.append(("account", None))
        return {}

    async def entity(*_args: Any, **kwargs: Any):
        calls.append(("entity", kwargs["entity_level"]))
        return {}

    monkeypatch.setattr(
        evidence_adapter.unified_gateway,
        "load_unified_marketing_account_identity",
        identity,
    )
    monkeypatch.setattr(
        evidence_adapter.unified_gateway,
        "load_unified_marketing_account_report",
        account,
    )
    monkeypatch.setattr(
        evidence_adapter.unified_gateway,
        "load_unified_marketing_entity_report",
        entity,
    )

    result = await evidence_adapter.load_decision_evidence_for_latest_closed_day(
        object(),
        "owner-1",
        provider="snapchat_ads",
        now=datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc),
        max_candidates=25,
    )

    assert result["period"]["date_from"] == "2026-09-03"
    assert sorted(calls) == [
        ("account", None),
        ("entity", "ad"),
        ("entity", "ad_group"),
        ("entity", "campaign"),
        ("identity", None),
    ]


@pytest.mark.asyncio
async def test_tenant_discovery_is_one_bounded_read_without_writes():
    observed: dict[str, Any] = {}

    class Cursor:
        async def to_list(self, *, length: int):
            observed["length"] = length
            return [
                {"_id": "owner-1", "providers": ["snapchat_ads", "meta_ads"]},
                {"_id": "owner-2", "providers": ["snapchat_ads"]},
                {"_id": "owner-3", "providers": ["meta_ads"]},
            ]

    class Collection:
        def aggregate(self, pipeline: list[dict], **kwargs: Any):
            observed["pipeline"] = pipeline
            observed["kwargs"] = kwargs
            return Cursor()

        def __getattr__(self, name: str):
            if name in {"insert_one", "update_one", "update_many", "delete_one"}:
                raise AssertionError(f"unexpected database write: {name}")
            raise AttributeError(name)

    class Database:
        def __getitem__(self, name: str):
            assert name == scheduler_module.ACCOUNT_COLLECTION
            return Collection()

    tenants, truncated = await scheduler_module.load_scheduled_tenants(
        Database(), max_tenants=2
    )

    assert len(tenants) == 2
    assert truncated is True
    assert observed["length"] == 3
    assert observed["pipeline"][-1] == {"$limit": 3}
    assert observed["kwargs"] == {"allowDiskUse": False}


def test_scheduler_import_graph_excludes_execution_provider_clients_and_sync():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "decision_intelligence" / "scheduler.py",
        root / "decision_intelligence" / "phase5.py",
        root / "decision_intelligence" / "evidence_adapter.py",
    ]
    imports: list[str] = []
    scheduler_source = paths[0].read_text(encoding="utf-8")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")

    forbidden_prefixes = (
        "campaign_ai",
        "snapchat_v2.client",
        "integrations_control_center.meta_client",
        "google",
    )
    assert not any(name.startswith(forbidden_prefixes) for name in imports)
    assert "action_gate" not in scheduler_source.lower()
    assert "provider_sync" not in scheduler_source
    assert "insert_one" not in scheduler_source
    assert "update_one" not in scheduler_source
    assert "update_many" not in scheduler_source
    assert "delete_one" not in scheduler_source


def test_router_registration_is_idempotent_and_does_not_touch_server():
    registered = {"startup": [], "shutdown": []}

    class Router:
        def on_event(self, event: str):
            def decorator(func):
                registered[event].append(func)
                return func

            return decorator

    router = Router()
    scheduler_module.attach_phase5_shadow_scheduler(router, object())
    scheduler_module.attach_phase5_shadow_scheduler(router, object())

    assert len(registered["startup"]) == 1
    assert len(registered["shutdown"]) == 1
    root = Path(__file__).resolve().parents[1]
    assert "attach_phase5_shadow_scheduler" not in (
        root / "server.py"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_disabled_registration_creates_no_background_task(monkeypatch):
    registered = {"startup": [], "shutdown": []}

    class Router:
        def on_event(self, event: str):
            def decorator(func):
                registered[event].append(func)
                return func

            return decorator

    router = Router()
    scheduler_module.attach_phase5_shadow_scheduler(router, object())
    monkeypatch.setattr(
        scheduler_module,
        "load_scheduler_config",
        lambda: _config(enabled=False),
    )

    await registered["startup"][0]()

    state = router._decision_intelligence_phase5_scheduler_state
    assert state == {"task": None, "scheduler": None}


@pytest.mark.asyncio
async def test_telemetry_is_bounded_and_tenant_is_pseudonymous():
    async def runner(_db: Any, _user_id: str, **kwargs: Any):
        return _ready_result(kwargs["provider"])

    instance = Phase5ShadowScheduler(
        object(), config=_config(), phase5_runner=runner
    )
    for index in range(scheduler_module.TELEMETRY_LIMIT + 5):
        await instance.run_provider(f"owner-{index}", "snapchat_ads")

    telemetry = instance.telemetry_snapshot()

    assert len(telemetry) == scheduler_module.TELEMETRY_LIMIT
    assert all(item["tenant"].startswith("tenant-") for item in telemetry)
    assert all("owner-" not in item["tenant"] for item in telemetry)


def test_campaign_ai_scheduler_remains_a_separate_unchanged_role():
    root = Path(__file__).resolve().parents[1]
    phase5_source = (
        root / "decision_intelligence" / "scheduler.py"
    ).read_text(encoding="utf-8")
    campaign_source = (root / "campaign_ai_subprocess_scheduler.py").read_text(
        encoding="utf-8"
    )

    assert "run_campaign_ai_monitor" not in phase5_source
    assert "campaign_ai_worker_runner.py" in campaign_source
    assert "run_phase5_shadow_for_latest_closed_day" in phase5_source
