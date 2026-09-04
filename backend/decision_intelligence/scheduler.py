"""Bounded, recommendation-only scheduler for Decision Intelligence Phase 5.

The scheduler discovers only explicitly selected connected tenants.  Marketing
evidence is still loaded exclusively by the Unified Marketing gateway through
``run_phase5_shadow_for_latest_closed_day``.  It may persist idempotent Phase 5
proposals, but performs no provider sync/call, approval, or execution action.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable
import uuid

from .phase5 import run_phase5_shadow_for_latest_closed_day
from .proposal_bridge import persist_phase5_proposals


logger = logging.getLogger(__name__)

ENABLED_ENV = "DECISION_INTELLIGENCE_PHASE5_SCHEDULER_ENABLED"
INTERVAL_ENV = "DECISION_INTELLIGENCE_PHASE5_INTERVAL_SECONDS"
INITIAL_DELAY_ENV = "DECISION_INTELLIGENCE_PHASE5_INITIAL_DELAY_SECONDS"
MAX_TENANTS_ENV = "DECISION_INTELLIGENCE_PHASE5_MAX_TENANTS"
MAX_ENTITIES_ENV = "DECISION_INTELLIGENCE_PHASE5_MAX_ENTITIES"
TIMEOUT_ENV = "DECISION_INTELLIGENCE_PHASE5_TIMEOUT_SECONDS"
MAX_PROVIDER_CONCURRENCY_ENV = (
    "DECISION_INTELLIGENCE_PHASE5_MAX_PROVIDER_CONCURRENCY"
)
MAX_FRESHNESS_HOURS_ENV = "DECISION_INTELLIGENCE_PHASE5_MAX_FRESHNESS_HOURS"

ACCOUNT_COLLECTION = "mezan_integration_accounts_v2"
SUPPORTED_PROVIDERS = ("snapchat_ads", "meta_ads")

DEFAULT_INTERVAL_SECONDS = 60 * 60
MIN_INTERVAL_SECONDS = 15 * 60
MAX_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_INITIAL_DELAY_SECONDS = 120.0
MAX_INITIAL_DELAY_SECONDS = 60 * 60
DEFAULT_MAX_TENANTS = 20
MAX_TENANTS_LIMIT = 100
DEFAULT_MAX_ENTITIES = 25
MAX_ENTITIES_LIMIT = 100
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 5 * 60
DEFAULT_MAX_PROVIDER_CONCURRENCY = 2
DEFAULT_MAX_FRESHNESS_HOURS = 36.0
TELEMETRY_LIMIT = 200

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_SAFE_REASON = re.compile(r"^[a-z0-9_:-]{1,120}$")
_ACTIVE_PROVIDER_RUNS: set[tuple[int, str, str]] = set()

Phase5Runner = Callable[..., Awaitable[dict[str, Any]]]
TenantLoader = Callable[..., Awaitable[tuple[list[dict[str, Any]], bool]]]
ProposalPersister = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Phase5SchedulerConfig:
    enabled: bool = False
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS
    initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS
    max_tenants: int = DEFAULT_MAX_TENANTS
    max_entities: int = DEFAULT_MAX_ENTITIES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_provider_concurrency: int = DEFAULT_MAX_PROVIDER_CONCURRENCY
    max_freshness_hours: float = DEFAULT_MAX_FRESHNESS_HOURS


def _bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    logger.warning("Ignoring invalid %s value", name)
    return default


def _float_env(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        logger.warning("Ignoring invalid %s value", name)
        return float(default)
    if value != value or abs(value) == float("inf"):
        return float(default)
    return min(max(value, minimum), maximum)


def _int_env(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        logger.warning("Ignoring invalid %s value", name)
        return int(default)
    return min(max(value, minimum), maximum)


def load_scheduler_config() -> Phase5SchedulerConfig:
    """Read a fail-closed, fully bounded scheduler configuration."""
    return Phase5SchedulerConfig(
        enabled=_bool_env(ENABLED_ENV, False),
        interval_seconds=_float_env(
            INTERVAL_ENV,
            DEFAULT_INTERVAL_SECONDS,
            minimum=MIN_INTERVAL_SECONDS,
            maximum=MAX_INTERVAL_SECONDS,
        ),
        initial_delay_seconds=_float_env(
            INITIAL_DELAY_ENV,
            DEFAULT_INITIAL_DELAY_SECONDS,
            minimum=0.0,
            maximum=MAX_INITIAL_DELAY_SECONDS,
        ),
        max_tenants=_int_env(
            MAX_TENANTS_ENV,
            DEFAULT_MAX_TENANTS,
            minimum=1,
            maximum=MAX_TENANTS_LIMIT,
        ),
        max_entities=_int_env(
            MAX_ENTITIES_ENV,
            DEFAULT_MAX_ENTITIES,
            minimum=1,
            maximum=MAX_ENTITIES_LIMIT,
        ),
        timeout_seconds=_float_env(
            TIMEOUT_ENV,
            DEFAULT_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=MAX_TIMEOUT_SECONDS,
        ),
        max_provider_concurrency=_int_env(
            MAX_PROVIDER_CONCURRENCY_ENV,
            DEFAULT_MAX_PROVIDER_CONCURRENCY,
            minimum=1,
            maximum=len(SUPPORTED_PROVIDERS),
        ),
        max_freshness_hours=_float_env(
            MAX_FRESHNESS_HOURS_ENV,
            DEFAULT_MAX_FRESHNESS_HOURS,
            minimum=1.0,
            maximum=168.0,
        ),
    )


async def load_scheduled_tenants(
    db: Any,
    *,
    max_tenants: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Return a bounded tenant/provider inventory using one read aggregation."""
    limit = min(max(1, int(max_tenants)), MAX_TENANTS_LIMIT)
    pipeline = [
        {
            "$match": {
                "provider": {"$in": list(SUPPORTED_PROVIDERS)},
                "connection_status": "connected",
                "mezan_selected": True,
                "user_id": {"$type": "string", "$ne": ""},
            }
        },
        {"$group": {"_id": "$user_id", "providers": {"$addToSet": "$provider"}}},
        {"$sort": {"_id": 1}},
        {"$limit": limit + 1},
    ]
    cursor = db[ACCOUNT_COLLECTION].aggregate(pipeline, allowDiskUse=False)
    if hasattr(cursor, "to_list"):
        try:
            rows = list(await cursor.to_list(length=limit + 1))
        except TypeError:
            rows = list(await cursor.to_list(limit + 1))
    else:
        rows = []
        async for row in cursor:
            rows.append(row)
            if len(rows) > limit:
                break
    truncated = len(rows) > limit
    tenants: list[dict[str, Any]] = []
    for row in rows[:limit]:
        user_id = str(row.get("_id") or "").strip()
        providers = tuple(
            provider
            for provider in SUPPORTED_PROVIDERS
            if provider in set(row.get("providers") or [])
        )
        if user_id and providers:
            tenants.append({"user_id": user_id, "providers": providers})
    return tenants, truncated


def _tenant_fingerprint(user_id: str) -> str:
    digest = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]
    return f"tenant-{digest}"


def _safe_reason(exc: BaseException) -> str:
    rendered = str(exc).strip().lower()
    if _SAFE_REASON.fullmatch(rendered):
        return rendered
    return type(exc).__name__.lower()[:80]


def _readiness_reasons(result: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for gate_name, gate_value in dict(result.get("gates") or {}).items():
        gate = gate_value if isinstance(gate_value, dict) else {}
        if gate.get("passed") is True:
            continue
        reason = str(gate.get("reason") or gate_name).strip().lower()
        if _SAFE_REASON.fullmatch(reason) and reason not in reasons:
            reasons.append(reason)
        for detail in list(gate.get("acceptance_reasons") or []):
            detail_reason = str(detail or "").strip().lower()
            if _SAFE_REASON.fullmatch(detail_reason) and detail_reason not in reasons:
                reasons.append(detail_reason)
    return reasons or ["readiness_not_met"]


def _maximum_recommendation_value(
    result: dict[str, Any],
    field: str,
) -> float | None:
    values: list[float] = []
    for decision in list(result.get("decisions") or []):
        recommendation = decision.get("recommendation") or {}
        value = recommendation.get(field)
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed == parsed and abs(parsed) != float("inf"):
            values.append(parsed)
    return max(values) if values else None


def _shadow_safety_violation(
    result: dict[str, Any],
    *,
    provider: str,
) -> str | None:
    """Reject any future Phase 5 output that weakens the shadow-only contract."""
    if result.get("mode") != "recommendation_shadow":
        return "shadow_mode_contract_failed"
    if result.get("provider") != provider:
        return "shadow_provider_contract_failed"
    if (result.get("approval_workflow") or {}).get("approval_can_execute") is not False:
        return "shadow_approval_contract_failed"
    if (
        (result.get("scheduler_integration") or {}).get(
            "automatic_execution_connected"
        )
        is not False
    ):
        return "shadow_execution_contract_failed"
    write_policy = result.get("write_policy") or {}
    if any(
        write_policy.get(field) is not False
        for field in (
            "platform_writes_enabled",
            "platform_writes_performed",
            "database_writes_performed",
        )
    ):
        return "shadow_write_policy_contract_failed"
    return None


class Phase5ShadowScheduler:
    """Run bounded tenant/provider shadow evaluations in one process."""

    def __init__(
        self,
        db: Any,
        *,
        config: Phase5SchedulerConfig,
        phase5_runner: Phase5Runner = run_phase5_shadow_for_latest_closed_day,
        tenant_loader: TenantLoader = load_scheduled_tenants,
        proposal_persister: ProposalPersister = persist_phase5_proposals,
    ) -> None:
        self.db = db
        self.config = config
        self._phase5_runner = phase5_runner
        self._tenant_loader = tenant_loader
        self._proposal_persister = proposal_persister
        self._provider_slots = asyncio.Semaphore(config.max_provider_concurrency)
        self._telemetry: deque[dict[str, Any]] = deque(maxlen=TELEMETRY_LIMIT)
        self._cycle_active = False
        self.overlap_prevented_count = 0

    def telemetry_snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._telemetry]

    def _record(self, event: dict[str, Any]) -> dict[str, Any]:
        bounded = dict(event)
        self._telemetry.append(bounded)
        logger.info(
            "decision_intelligence_phase5_scheduler %s",
            json.dumps(bounded, sort_keys=True, separators=(",", ":")),
        )
        return bounded

    def _overlap_event(self, user_id: str, provider: str) -> dict[str, Any]:
        current = datetime.now(timezone.utc).isoformat()
        self.overlap_prevented_count += 1
        return self._record(
            {
                "provider": provider,
                "tenant": _tenant_fingerprint(user_id),
                "run_started": current,
                "run_finished": current,
                "duration_ms": 0.0,
                "status": "skipped",
                "readiness_result": None,
                "reason": "same_tenant_provider_run_active",
                "readiness_reasons": [],
                "recommendations_count": 0,
                "highest_confidence": None,
                "highest_priority": None,
                "evidence_timestamp": None,
                "freshness_hours": None,
                "overlap_prevented_count": self.overlap_prevented_count,
            }
        )

    async def run_provider(self, user_id: str, provider: str) -> dict[str, Any]:
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError("unsupported_phase5_scheduler_provider")
        key = (id(self.db), str(user_id), provider)
        if key in _ACTIVE_PROVIDER_RUNS:
            return self._overlap_event(str(user_id), provider)
        _ACTIVE_PROVIDER_RUNS.add(key)

        started_wall = datetime.now(timezone.utc)
        started_mono = time.monotonic()
        result: dict[str, Any] = {}
        run_id = f"di-p5-run-{uuid.uuid4().hex}"
        proposal_outcome: dict[str, Any] = {
            "created": 0,
            "deduplicated": 0,
            "non_executable_recommendations": [],
        }
        status = "failed"
        readiness: bool | None = None
        reason: str | None = None
        readiness_reasons: list[str] = []
        try:
            async with self._provider_slots:
                result = await asyncio.wait_for(
                    self._phase5_runner(
                        self.db,
                        str(user_id),
                        provider=provider,
                        max_freshness_hours=self.config.max_freshness_hours,
                        max_candidates=self.config.max_entities,
                    ),
                    timeout=self.config.timeout_seconds,
                )
            safety_violation = _shadow_safety_violation(result, provider=provider)
            if safety_violation:
                status = "skipped"
                readiness = False
                reason = safety_violation
                readiness_reasons = [safety_violation]
            else:
                readiness = bool(result.get("decision_ready"))
                if readiness:
                    proposal_outcome = await self._proposal_persister(
                        self.db,
                        str(user_id),
                        result,
                        source_run_id=run_id,
                    )
                    status = "success"
                else:
                    status = "skipped"
                    readiness_reasons = _readiness_reasons(result)
                    reason = readiness_reasons[0]
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except asyncio.TimeoutError:
            reason = "provider_shadow_timeout"
        except ValueError as exc:
            status = "skipped"
            readiness = False
            reason = _safe_reason(exc)
            readiness_reasons = [reason]
        except Exception as exc:  # noqa: BLE001 - isolate providers and fail closed
            reason = _safe_reason(exc)
        finally:
            _ACTIVE_PROVIDER_RUNS.discard(key)
            finished_wall = datetime.now(timezone.utc)
            event = {
                "provider": provider,
                "run_id": run_id,
                "tenant": _tenant_fingerprint(str(user_id)),
                "run_started": started_wall.isoformat(),
                "run_finished": finished_wall.isoformat(),
                "duration_ms": round((time.monotonic() - started_mono) * 1000, 3),
                "status": status,
                "readiness_result": readiness,
                "reason": reason,
                "readiness_reasons": readiness_reasons,
                "recommendations_count": (
                    int((result.get("summary") or {}).get("recommendations") or 0)
                    if status == "success"
                    else 0
                ),
                "proposals_created": (
                    int(proposal_outcome.get("created") or 0)
                    if status == "success"
                    else 0
                ),
                "proposals_deduplicated": (
                    int(proposal_outcome.get("deduplicated") or 0)
                    if status == "success"
                    else 0
                ),
                "non_executable_recommendations": (
                    len(proposal_outcome.get("non_executable_recommendations") or [])
                    if status == "success"
                    else 0
                ),
                "automatic_execution": False,
                "highest_confidence": (
                    _maximum_recommendation_value(result, "confidence")
                    if status == "success"
                    else None
                ),
                "highest_priority": (
                    _maximum_recommendation_value(result, "priority_score")
                    if status == "success"
                    else None
                ),
                "evidence_timestamp": result.get("evidence_timestamp"),
                "freshness_hours": (
                    (result.get("gates") or {}).get("freshness") or {}
                ).get("freshness_hours"),
                "overlap_prevented_count": self.overlap_prevented_count,
            }
            self._record(event)
        return event

    async def run_cycle(self) -> dict[str, Any]:
        """Run tenants sequentially and at most two providers per tenant."""
        if self._cycle_active:
            self.overlap_prevented_count += 1
            return {
                "status": "skipped",
                "reason": "scheduler_cycle_active",
                "overlap_prevented_count": self.overlap_prevented_count,
            }
        self._cycle_active = True
        started = time.monotonic()
        outcomes: list[dict[str, Any]] = []
        tenant_limit_reached = False
        try:
            tenants, tenant_limit_reached = await self._tenant_loader(
                self.db,
                max_tenants=self.config.max_tenants,
            )
            for tenant in tenants:
                user_id = str(tenant.get("user_id") or "")
                providers = [
                    provider
                    for provider in SUPPORTED_PROVIDERS
                    if provider in set(tenant.get("providers") or [])
                ]
                if not user_id or not providers:
                    continue
                tenant_outcomes = await asyncio.gather(
                    *(self.run_provider(user_id, provider) for provider in providers)
                )
                outcomes.extend(tenant_outcomes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - next cadence may recover
            logger.error(
                "decision_intelligence_phase5_scheduler_cycle_failed reason=%s",
                _safe_reason(exc),
            )
            return {
                "status": "failed",
                "reason": _safe_reason(exc),
                "outcomes": outcomes,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
            }
        finally:
            self._cycle_active = False
        return {
            "status": "success",
            "tenants": len(tenants),
            "providers": len(outcomes),
            "tenant_limit_reached": tenant_limit_reached,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "outcomes": outcomes,
            "overlap_prevented_count": self.overlap_prevented_count,
        }

    async def loop(self) -> None:
        await asyncio.sleep(self.config.initial_delay_seconds)
        while True:
            started = time.monotonic()
            await self.run_cycle()
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1.0, self.config.interval_seconds - elapsed))


def attach_phase5_shadow_scheduler(router: Any, db: Any) -> None:
    """Register one disabled-by-default scheduler through the Ads router."""
    if getattr(router, "_decision_intelligence_phase5_scheduler_attached", False):
        return
    setattr(router, "_decision_intelligence_phase5_scheduler_attached", True)

    from boot_runtime import wait_for_local_readiness

    state: dict[str, Any] = {"task": None, "scheduler": None}
    setattr(router, "_decision_intelligence_phase5_scheduler_state", state)

    async def _ready_loop(scheduler: Phase5ShadowScheduler) -> None:
        await wait_for_local_readiness()
        await scheduler.loop()

    @router.on_event("startup")
    async def _start_phase5_shadow_scheduler() -> None:
        config = load_scheduler_config()
        if not config.enabled:
            logger.info("Decision Intelligence Phase 5 shadow scheduler disabled")
            return
        task = state.get("task")
        if task is not None and not task.done():
            return
        scheduler = Phase5ShadowScheduler(db, config=config)
        state["scheduler"] = scheduler
        state["task"] = asyncio.create_task(
            _ready_loop(scheduler),
            name="decision-intelligence-phase5-shadow-scheduler",
        )
        logger.info(
            "Decision Intelligence Phase 5 shadow scheduler started "
            "interval_seconds=%s max_tenants=%s max_entities=%s",
            config.interval_seconds,
            config.max_tenants,
            config.max_entities,
        )

    @router.on_event("shutdown")
    async def _stop_phase5_shadow_scheduler() -> None:
        task = state.get("task")
        state["task"] = None
        state["scheduler"] = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


__all__ = [
    "Phase5SchedulerConfig",
    "Phase5ShadowScheduler",
    "attach_phase5_shadow_scheduler",
    "load_scheduled_tenants",
    "load_scheduler_config",
]
