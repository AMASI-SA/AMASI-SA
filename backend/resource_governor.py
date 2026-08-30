"""Container-aware admission control for memory-intensive backend work.

The governor is deliberately independent from FastAPI and MongoDB so every
entry point (HTTP, scheduler, repair and backfill) can share the same policy.
It never cancels a task asynchronously. Until staging/commit gates exist,
authoritative publishers may stop only before their first authoritative write.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from collections import deque
from typing import Any, AsyncIterator

try:  # Unix containers expose ru_maxrss; local Windows tests do not.
    import resource  # type: ignore
except ImportError:  # pragma: no cover - exercised on Windows CI/dev
    resource = None  # type: ignore

logger = logging.getLogger(__name__)

CGROUP_ROOT = Path(os.environ.get("MEZAN_CGROUP_ROOT", "/sys/fs/cgroup"))


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MemorySnapshot:
    current_bytes: int | None
    max_bytes: int | None
    peak_bytes: int | None
    events: dict[str, int]
    process_rss_bytes: int | None
    process_uss_bytes: int | None
    process_peak_rss_bytes: int | None
    usage_ratio: float | None


@dataclass(frozen=True)
class AdmissionToken:
    kind: str
    kind_semaphore: asyncio.Semaphore
    weight: int


class WeightedSemaphore:
    """FIFO weighted capacity gate with atomic reservations.

    A waiter either reserves its full weight or no capacity at all.  This
    avoids the classic deadlock where two weight-2 jobs each hold one permit
    from a capacity-2 semaphore while waiting for their second permit.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("weighted semaphore capacity must be positive")
        self.capacity = capacity
        self.available = capacity
        self._condition = asyncio.Condition()
        self._waiters: deque[object] = deque()

    @property
    def in_use(self) -> int:
        return self.capacity - self.available

    @property
    def pending(self) -> int:
        return len(self._waiters)

    async def acquire(self, weight: int) -> None:
        if not 1 <= weight <= self.capacity:
            raise ValueError("weight must be between 1 and capacity")
        ticket = object()
        async with self._condition:
            self._waiters.append(ticket)
            try:
                await self._condition.wait_for(
                    lambda: self._waiters[0] is ticket
                    and self.available >= weight
                )
                self.available -= weight
                self._waiters.popleft()
                self._condition.notify_all()
            except BaseException:
                try:
                    self._waiters.remove(ticket)
                except ValueError:
                    pass
                self._condition.notify_all()
                raise

    async def release(self, weight: int) -> None:
        if weight < 1:
            raise ValueError("weight must be positive")
        async with self._condition:
            if self.available + weight > self.capacity:
                raise ValueError("weighted semaphore released too many permits")
            self.available += weight
            self._condition.notify_all()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _read_limit(path: Path) -> int | None:
    value = _read_text(path)
    if not value or value == "max":
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _read_events(path: Path) -> dict[str, int]:
    events: dict[str, int] = {}
    for line in (_read_text(path) or "").splitlines():
        key, _, raw = line.partition(" ")
        try:
            events[key] = int(raw)
        except ValueError:
            continue
    return events


def _process_memory() -> tuple[int | None, int | None]:
    rss = uss = None
    status = _read_text(Path("/proc/self/status")) or ""
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            try:
                rss = int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                pass
    # smaps_rollup is cheap and exposes Private_* as a useful USS estimate.
    smaps = _read_text(Path("/proc/self/smaps_rollup")) or ""
    private_kb = 0
    found_private = False
    for line in smaps.splitlines():
        if line.startswith(("Private_Clean:", "Private_Dirty:")):
            try:
                private_kb += int(line.split()[1])
                found_private = True
            except (IndexError, ValueError):
                pass
    if found_private:
        uss = private_kb * 1024
    return rss, uss


def memory_snapshot(root: Path | None = None) -> MemorySnapshot:
    root = root or CGROUP_ROOT
    current = _read_limit(root / "memory.current")
    maximum = _read_limit(root / "memory.max")
    peak = _read_limit(root / "memory.peak")
    rss, uss = _process_memory()
    try:
        peak_rss = (
            int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
            if resource is not None else None
        )
    except (ValueError, OSError):
        peak_rss = None
    ratio = current / maximum if current is not None and maximum else None
    return MemorySnapshot(
        current_bytes=current,
        max_bytes=maximum,
        peak_bytes=peak,
        events=_read_events(root / "memory.events"),
        process_rss_bytes=rss,
        process_uss_bytes=uss,
        process_peak_rss_bytes=peak_rss,
        usage_ratio=ratio,
    )


class ResourcePressure(RuntimeError):
    """A new heavy job was refused before it allocated large resources."""


class CooperativeCancellation(RuntimeError):
    """A running job reached a safe checkpoint under critical pressure."""


class ResourceGovernor:
    def __init__(self) -> None:
        self.warning_ratio = _env_float("HEAVY_MEMORY_WARNING_RATIO", 0.70)
        self.block_ratio = _env_float("HEAVY_MEMORY_BLOCK_RATIO", 0.80)
        self.cancel_ratio = _env_float("HEAVY_MEMORY_CANCEL_RATIO", 0.85)
        self.resume_ratio = _env_float("HEAVY_MEMORY_RESUME_RATIO", 0.70)
        if not (
            0 <= self.resume_ratio <= self.warning_ratio
            < self.block_ratio < self.cancel_ratio <= 1
        ):
            raise ValueError(
                "invalid heavy-memory thresholds: require "
                "0 <= resume <= warning < block < cancel <= 1"
            )
        self._blocked = False
        self._active: dict[str, int] = {}
        self._pending: dict[str, int] = {}
        self._limits = {
            "snapchat": asyncio.Semaphore(_env_int("HEAVY_SNAPCHAT_CONCURRENCY", 1)),
            "ads": asyncio.Semaphore(_env_int("HEAVY_ADS_CONCURRENCY", 2)),
            "dashboard": asyncio.Semaphore(_env_int("HEAVY_DASHBOARD_CONCURRENCY", 2)),
            "startup": asyncio.Semaphore(_env_int("HEAVY_STARTUP_CONCURRENCY", 1)),
        }
        self._global_capacity = _env_int("HEAVY_GLOBAL_CAPACITY", 2)
        self._global = WeightedSemaphore(self._global_capacity)
        self._weights = {
            "snapchat": _env_int("HEAVY_SNAPCHAT_WEIGHT", 1),
            "ads": _env_int("HEAVY_ADS_WEIGHT", 1),
            "dashboard": _env_int("HEAVY_DASHBOARD_WEIGHT", 2),
            "startup": _env_int("HEAVY_STARTUP_WEIGHT", 2),
        }
        if any(weight > self._global_capacity for weight in self._weights.values()):
            raise ValueError("heavy task weight cannot exceed global capacity")

    def decision(self) -> tuple[str, MemorySnapshot]:
        snapshot = memory_snapshot()
        ratio = snapshot.usage_ratio
        if ratio is None:  # fail open only when the container exposes no limit
            return "normal", snapshot
        if ratio >= self.cancel_ratio:
            self._blocked = True
            return "cancel", snapshot
        if self._blocked and ratio >= self.resume_ratio:
            return "blocked", snapshot
        if ratio >= self.block_ratio:
            self._blocked = True
            return "blocked", snapshot
        if ratio >= self.warning_ratio:
            return "warning", snapshot
        self._blocked = False
        return "normal", snapshot

    def peek(self) -> tuple[str, MemorySnapshot]:
        """Return the current policy result without mutating hysteresis state."""
        snapshot = memory_snapshot()
        ratio = snapshot.usage_ratio
        if ratio is None:
            return "normal", snapshot
        if ratio >= self.cancel_ratio:
            return "cancel", snapshot
        if self._blocked and ratio >= self.resume_ratio:
            return "blocked", snapshot
        if ratio >= self.block_ratio:
            return "blocked", snapshot
        if ratio >= self.warning_ratio:
            return "warning", snapshot
        return "normal", snapshot

    def safe_checkpoint(self) -> None:
        decision, _ = self.decision()
        if decision == "cancel":
            raise CooperativeCancellation("resource_pressure")

    @asynccontextmanager
    async def heavy(self, kind: str, *, task_name: str) -> AsyncIterator[MemorySnapshot]:
        token, before = await self.acquire(kind, task_name=task_name)
        try:
            yield before
        finally:
            await self.release(token)

    async def acquire(
        self, kind: str, *, task_name: str
    ) -> tuple[AdmissionToken, MemorySnapshot]:
        decision, before = self.decision()
        if decision in {"blocked", "cancel"}:
            raise ResourcePressure("resource_pressure")
        semaphore = self._limits.get(kind, self._limits["startup"])
        weight = self._weights.get(kind, self._weights["startup"])
        self._pending[kind] = self._pending.get(kind, 0) + 1
        global_acquired = False
        kind_acquired = False
        try:
            # Per-kind first prevents same-kind waiters from reserving global
            # capacity while queued behind (for example) Snapchat's limit 1.
            await semaphore.acquire()
            kind_acquired = True
            await self._global.acquire(weight)
            global_acquired = True
        except BaseException:
            if global_acquired:
                await self._global.release(weight)
            if kind_acquired:
                semaphore.release()
            raise
        finally:
            self._pending[kind] = max(0, self._pending.get(kind, 1) - 1)
        decision, before = self.decision()
        if decision in {"blocked", "cancel"}:
            semaphore.release()
            await self._global.release(weight)
            raise ResourcePressure("resource_pressure")
        self._active[kind] = self._active.get(kind, 0) + 1
        return AdmissionToken(kind, semaphore, weight), before

    async def release(self, token: AdmissionToken) -> None:
        if self._active.get(token.kind, 0):
            self._active[token.kind] -= 1
        token.kind_semaphore.release()
        await self._global.release(token.weight)

    def diagnostics(self) -> dict[str, Any]:
        decision, snapshot = self.peek()
        return {
            "memory": asdict(snapshot),
            "admission": decision,
            "active_heavy_tasks": dict(self._active),
            "pending_heavy_tasks": dict(self._pending),
            "global_heavy_capacity": self._global_capacity,
            "global_heavy_in_use": self._global.in_use,
            "global_heavy_reserved": self._global.in_use,
            "global_heavy_available": self._global.available,
            "global_heavy_waiters": self._global.pending,
            "thresholds": {
                "warning": self.warning_ratio,
                "block": self.block_ratio,
                "cancel": self.cancel_ratio,
                "resume": self.resume_ratio,
            },
        }


governor = ResourceGovernor()


class StageMetric:
    """One bounded structured log per stage, never per provider row."""

    def __init__(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        self.fields = fields
        self.started_wall = time.time()
        self.started_mono = time.monotonic()
        self.before = memory_snapshot()

    def finish(self, *, status: str, reason: str | None = None, **counts: Any) -> None:
        after = memory_snapshot()
        payload = {
            "event": "heavy_stage",
            "stage": self.stage,
            "started_at": self.started_wall,
            "finished_at": time.time(),
            "duration_ms": round((time.monotonic() - self.started_mono) * 1000, 2),
            "cgroup_memory_before": self.before.current_bytes,
            "cgroup_memory_after": after.current_bytes,
            "cgroup_lifetime_peak_bytes": after.peak_bytes,
            "process_rss_before": self.before.process_rss_bytes,
            "process_rss_after": after.process_rss_bytes,
            "status": status,
            "reason": reason,
            **self.fields,
            **counts,
        }
        logger.info("resource_stage %s", json.dumps(payload, default=str, sort_keys=True))


__all__ = [
    "AdmissionToken", "CooperativeCancellation", "MemorySnapshot", "ResourceGovernor",
    "ResourcePressure", "StageMetric", "governor", "memory_snapshot",
    "WeightedSemaphore",
]
