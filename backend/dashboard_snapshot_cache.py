"""Small process-local saved dashboard results; never caches source cohorts.

The router owns an instance, so separate applications/databases cannot share a
result. Immutable JSON bytes bound retained payload memory and give every caller
an independent object. Concurrent refreshes share work, including explicit ones.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Hashable

from fastapi.encoders import jsonable_encoder


class DashboardSnapshotBusy(RuntimeError):
    """The bounded set of distinct dashboard computations is already full."""


class DashboardSnapshotTimeout(TimeoutError):
    """A saved-result computation exceeded its bounded execution time."""


@dataclass(frozen=True)
class _Entry:
    expires_at: float
    payload: bytes


class DashboardSnapshotCache:
    def __init__(
        self,
        *,
        max_entries: int = 32,
        max_bytes: int = 8 * 1024 * 1024,
        max_entry_bytes: int = 2 * 1024 * 1024,
        max_inflight: int = 8,
        build_timeout_seconds: float = 45.0,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self.max_inflight = max_inflight
        self.build_timeout_seconds = build_timeout_seconds
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Task[bytes]] = {}
        self._bytes = 0

    def _remove(self, key: Hashable) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry.payload)

    def _expire(self) -> None:
        now = self._monotonic()
        for key, entry in list(self._entries.items()):
            if entry.expires_at <= now:
                self._remove(key)

    @staticmethod
    def _observe_completion(task: asyncio.Task[bytes]) -> None:
        # A disconnected waiter must not cancel another waiter's computation,
        # nor leave a failed task's exception unobserved.
        if not task.cancelled():
            task.exception()

    async def get(
        self,
        key: Hashable,
        *,
        ttl_seconds: int,
        build: Callable[[], Awaitable[dict[str, Any]]],
        refresh: bool = False,
    ) -> dict[str, Any]:
        self._expire()
        task = self._inflight.get(key)
        if task is None:
            entry = self._entries.get(key)
            if entry is not None and not refresh:
                self._entries.move_to_end(key)
                return json.loads(entry.payload)
            if len(self._inflight) >= self.max_inflight:
                raise DashboardSnapshotBusy("dashboard_snapshot_capacity")
            # No await occurs between lookup and assignment: one event loop
            # cannot admit two producers for the same key.
            task = asyncio.create_task(
                self._build(key, ttl_seconds=ttl_seconds, build=build),
                name="dashboard-card-snapshot",
            )
            self._inflight[key] = task
            task.add_done_callback(self._observe_completion)
        return json.loads(await asyncio.shield(task))

    async def _build(
        self,
        key: Hashable,
        *,
        ttl_seconds: int,
        build: Callable[[], Awaitable[dict[str, Any]]],
    ) -> bytes:
        try:
            try:
                result = await asyncio.wait_for(build(), timeout=self.build_timeout_seconds)
            except asyncio.TimeoutError as exc:
                raise DashboardSnapshotTimeout("dashboard_snapshot_timeout") from exc
            generated_at = self._now().astimezone(timezone.utc)
            value = {
                **result,
                "snapshot": {
                    "generated_at": generated_at.isoformat(),
                    "next_refresh_at": (generated_at + timedelta(seconds=ttl_seconds)).isoformat(),
                    "refresh_interval_seconds": ttl_seconds,
                },
            }
            payload = json.dumps(
                jsonable_encoder(value), ensure_ascii=False, separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            # Oversized results can still be served without retaining them.
            self._remove(key)
            if (
                self.max_entries > 0
                and len(payload) <= min(self.max_entry_bytes, self.max_bytes)
            ):
                self._entries[key] = _Entry(self._monotonic() + ttl_seconds, payload)
                self._bytes += len(payload)
                while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                    self._remove(next(iter(self._entries)))
            return payload
        finally:
            if self._inflight.get(key) is asyncio.current_task():
                self._inflight.pop(key, None)
