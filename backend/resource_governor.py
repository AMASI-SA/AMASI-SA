"""Container-aware admission control for memory-intensive backend work.

The governor is deliberately independent from FastAPI and MongoDB so every
entry point (HTTP, scheduler, repair and backfill) can share the same policy.
It never cancels a task asynchronously; callers stop only at explicit safe
checkpoints after a provider page/day/entity/bulk batch.
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
        self._blocked = False
        self._active: dict[str, int] = {}
        self._pending: dict[str, int] = {}
        self._limits = {
            "snapchat": asyncio.Semaphore(_env_int("HEAVY_SNAPCHAT_CONCURRENCY", 1)),
            "ads": asyncio.Semaphore(_env_int("HEAVY_ADS_CONCURRENCY", 2)),
            "dashboard": asyncio.Semaphore(_env_int("HEAVY_DASHBOARD_CONCURRENCY", 2)),
            "startup": asyncio.Semaphore(_env_int("HEAVY_STARTUP_CONCURRENCY", 1)),
        }

    def decision(self) -> tuple[str, MemorySnapshot]:
        snapshot = memory_snapshot()
        ratio = snapshot.usage_ratio
        if ratio is None:  # fail open only when the container exposes no limit
            return "normal", snapshot
        if self._blocked and ratio >= self.resume_ratio:
            return "blocked", snapshot
        if ratio >= self.cancel_ratio:
            self._blocked = True
            return "cancel", snapshot
        if ratio >= self.block_ratio:
            self._blocked = True
            return "blocked", snapshot
        if ratio >= self.warning_ratio:
            return "warning", snapshot
        self._blocked = False
        return "normal", snapshot

    def safe_checkpoint(self) -> None:
        decision, _ = self.decision()
        if decision == "cancel":
            raise CooperativeCancellation("resource_pressure")

    @asynccontextmanager
    async def heavy(self, kind: str, *, task_name: str) -> AsyncIterator[MemorySnapshot]:
        semaphore, before = await self.acquire(kind, task_name=task_name)
        try:
            yield before
        finally:
            self.release(kind, semaphore)

    async def acquire(
        self, kind: str, *, task_name: str
    ) -> tuple[asyncio.Semaphore, MemorySnapshot]:
        decision, before = self.decision()
        if decision in {"blocked", "cancel"}:
            raise ResourcePressure("resource_pressure")
        semaphore = self._limits.get(kind, self._limits["startup"])
        self._pending[kind] = self._pending.get(kind, 0) + 1
        try:
            await semaphore.acquire()
        finally:
            self._pending[kind] = max(0, self._pending.get(kind, 1) - 1)
        decision, before = self.decision()
        if decision in {"blocked", "cancel"}:
            semaphore.release()
            raise ResourcePressure("resource_pressure")
        self._active[kind] = self._active.get(kind, 0) + 1
        return semaphore, before

    def release(self, kind: str, semaphore: asyncio.Semaphore) -> None:
        if self._active.get(kind, 0):
            self._active[kind] -= 1
        semaphore.release()

    def diagnostics(self) -> dict[str, Any]:
        decision, snapshot = self.decision()
        return {
            "memory": asdict(snapshot),
            "admission": decision,
            "active_heavy_tasks": dict(self._active),
            "pending_heavy_tasks": dict(self._pending),
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
            "cgroup_memory_peak": after.peak_bytes,
            "process_rss_before": self.before.process_rss_bytes,
            "process_rss_after": after.process_rss_bytes,
            "status": status,
            "reason": reason,
            **self.fields,
            **counts,
        }
        logger.info("resource_stage %s", json.dumps(payload, default=str, sort_keys=True))


__all__ = [
    "CooperativeCancellation", "MemorySnapshot", "ResourceGovernor",
    "ResourcePressure", "StageMetric", "governor", "memory_snapshot",
]
