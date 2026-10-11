"""Bounded, process-local additions to diagnostics; off unless explicitly enabled.

No I/O in event callbacks. Explicit control-file capability starts a separate
watcher after worker startup. Durations are seconds and buckets non-cumulative.
"""
from __future__ import annotations

import bisect
import math
import os
import stat
import threading
import time
from pathlib import PurePosixPath

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

    def set_enabled(self, enabled: bool) -> None:
        # Return acknowledges disabling: earlier callbacks have finished;
        # later callbacks recheck under this same existing lock.
        with self._lock:
            self.enabled = enabled

    def observe(self, name: str, seconds: float) -> None:
        if not self.enabled:
            return
        if name not in NAMES or not math.isfinite(seconds) or seconds < 0:
            self.increment("rejected_metric")
            return
        with self._lock:
            if not self.enabled:
                return
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
            if not self.enabled:
                return
            self._counters[name] = self._counters.get(name, 0) + 1

    def begin_request(self):
        if not self.enabled:
            return None
        with self._lock:
            if not self.enabled:
                return None
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


def _read_control(path: str) -> bool | None:
    """Read an owner-private regular file through trusted directory FDs.

    None means invalid/unavailable; it cannot rearm a stall latch. Non-POSIX
    platforms fail closed without opening anything.
    """
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        return None
    parts = PurePosixPath(path).parts
    if not parts or parts[0] != "/" or len(parts) < 3 or ".." in parts:
        return None
    if parts[1] == "app":
        return None
    directory = None
    descriptor = None
    try:
        uid = os.geteuid()
        directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        for index, part in enumerate(parts[1:-1], 1):
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     dir_fd=directory)
            os.close(directory)
            directory = next_directory
            info = os.fstat(directory)
            if index == len(parts) - 2:
                if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
                    return None
            elif info.st_uid not in (0, uid) or (
                info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            ):
                return None
        # Nonblocking open prevents a FIFO replacement hanging before fstat.
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=directory)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                or info.st_nlink != 1 or info.st_size > 16):
            return None
        value = os.read(descriptor, 17).strip()
        return {b"enabled": True, b"disabled": False}.get(value)
    except (OSError, ValueError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


class ControlFileWatcher:
    """Explicit worker-local capability, not a request/event-loop file read."""

    def __init__(self, path: str, target: Metrics, *, poll_seconds=1.0, stall_seconds=5.0):
        self._lifecycle = threading.Lock()
        self.path = path
        self.target = target
        self.poll_seconds = poll_seconds
        self.stall_seconds = stall_seconds
        self.latched = False
        self.thread = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat = time.monotonic()

    def heartbeat(self) -> None:
        self._heartbeat = time.monotonic()

    def poll(self) -> None:
        # Serialize application with stop, so an earlier read cannot enable
        # instrumentation after stop has acknowledged disabling.
        with self._lock:
            if self._stop.is_set():
                return
            command = _read_control(self.path)
            if time.monotonic() - self._heartbeat >= self.stall_seconds:
                self.latched = True
            elif command is False:
                self.latched = False
            self.target.set_enabled(command is True and not self.latched)

    def _run(self, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                self.poll()
                if stop.wait(self.poll_seconds):
                    return
        except Exception:
            # Do not leave recording enabled if the controller itself fails.
            # No exception text/path is logged; explicit rearm remains required.
            self.latched = True
        finally:
            self.target.set_enabled(False)

    def start(self) -> None:
        with self._lifecycle:
            with self._lock:
                if self.thread is not None and self.thread.is_alive():
                    return
                self._stop = threading.Event()
                self.heartbeat()
                self.thread = threading.Thread(target=self._run, args=(self._stop,),
                                               name="observability-control", daemon=True)
                self.thread.start()

    def stop(self) -> None:
        with self._lifecycle:
            with self._lock:
                self._stop.set()
                self.target.set_enabled(False)
                thread = self.thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)


_configured = os.environ.get("OBS_METRICS_ENABLED", "").lower() == "true"
_control_path = os.environ.get("OBS_CONTROL_FILE", "")
metrics = Metrics(_configured and not _control_path)
_control = ControlFileWatcher(_control_path, metrics) if _control_path else None


def start_control() -> None:
    if _control is not None:
        _control.start()


def stop_control() -> None:
    if _control is not None:
        _control.stop()


def control_heartbeat() -> None:
    if _control is not None:
        _control.heartbeat()


def _after_fork():
    global _control
    # No inherited thread, lock or dynamic activation. Preserve the singleton
    # imported by callbacks, rebuilding its process-local state and locks.
    metrics.__init__(_configured and not _control_path)
    _control = ControlFileWatcher(_control_path, metrics) if _control_path else None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
