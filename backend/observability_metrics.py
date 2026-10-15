"""Bounded, process-local additions to diagnostics; off unless explicitly enabled.

No I/O in event callbacks. The existing one-second lag task refreshes the
optional kill file. Durations are seconds and buckets are non-cumulative.
"""
from __future__ import annotations

import bisect
import math
import os
import threading
import time
from pathlib import Path

BOUNDS = (.001, .005, .01, .025, .05, .1, .25, .5, 1., 2., 5., 10., 30., 60., 300.)
KINDS = ("snapchat", "ads", "dashboard", "startup", "other")
GROUPS = ("ready", "supplier_invoice", "shipping", "other")
NAMES = frozenset(
    ["event_loop.lag"] + [f"api.duration.{g}" for g in GROUPS]
    + [f"mongo.command.{c}.{o}" for c in ("find", "aggregate", "getMore", "write", "commitTransaction", "other") for o in ("ok", "error")]
    + [f"mongo.pool.wait.{o}" for o in ("ok", "error")]
    + [f"governor.wait.{gate}.{k}" for gate in ("kind", "global") for k in KINDS]
    + [f"governor.hold.{k}" for k in KINDS]
)
COUNTERS = frozenset(["rejected_metric"] + [f"api.status.{s}" for s in ("1xx", "2xx", "3xx", "4xx", "500", "502", "503", "504", "5xx", "cancelled")])


class Metrics:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._hist = {}
        self._counters = {}
        self._active = {}
        self._next = 0
        self._overflow = 0

    def observe(self, name: str, seconds: float) -> None:
        if not self.enabled:
            return
        if name not in NAMES or not math.isfinite(seconds) or seconds < 0:
            self.increment("rejected_metric")
            return
        with self._lock:
            row = self._hist.setdefault(name, [0, 0., [0] * (len(BOUNDS) + 1)])
            row[0] += 1
            row[1] += seconds
            row[2][bisect.bisect_left(BOUNDS, seconds)] += 1

    def increment(self, name: str) -> None:
        if not self.enabled:
            return
        if name not in COUNTERS:
            name = "rejected_metric"
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + 1

    def begin_request(self):
        if not self.enabled:
            return None
        with self._lock:
            if len(self._active) >= 2048:
                self._overflow += 1
                return None
            self._next += 1
            self._active[self._next] = time.monotonic()
            return self._next

    def end_request(self, token) -> None:
        if token is not None:
            with self._lock:
                self._active.pop(token, None)

    def snapshot(self):
        with self._lock:
            hist = {}
            for name, (count, total, buckets) in self._hist.items():
                row = dict(bounds=list(BOUNDS), buckets=list(buckets), count=count, sum=total)
                for label, fraction, minimum in (("p50", .5, 1), ("p95", .95, 20), ("p99", .99, 100)):
                    row[label] = None
                    if count >= minimum:
                        seen = 0
                        for index, bucket in enumerate(buckets):
                            seen += bucket
                            if seen >= math.ceil(count * fraction):
                                row[label] = BOUNDS[index] if index < len(BOUNDS) else None
                                break
                hist[name] = row
            oldest = max(0., time.monotonic() - min(self._active.values())) if self._active else 0.
            return dict(enabled=self.enabled, worker=dict(pid=os.getpid(), started_at=self.started_at,
                        cpu_seconds=time.process_time()), histograms=hist, counters=dict(self._counters),
                        api=dict(active=len(self._active), oldest_seconds=oldest, overflow=self._overflow))


metrics = Metrics(os.environ.get("OBS_METRICS_ENABLED", "").lower() == "true")
def _after_fork():
    # Preserve the singleton imported by listeners while clearing parent data.
    metrics.__init__(metrics.enabled)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)

_configured = metrics.enabled
_next_control_check = 0.


def refresh_control() -> None:
    """A configured, missing/unreadable/non-'enabled' file disables additions."""
    global _next_control_check
    now = time.monotonic()
    if now < _next_control_check:
        return
    _next_control_check = now + 30.
    enabled = _configured
    path = os.environ.get("OBS_CONTROL_FILE")
    if enabled and path:
        try:
            with Path(path).open("rb") as handle:
                enabled = handle.read(16).strip() == b"enabled"
        except OSError:
            enabled = False
    metrics.enabled = enabled


refresh_control()
