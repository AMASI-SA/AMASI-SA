"""Bounded PyMongo pool/command metrics without query values or documents."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from pymongo import monitoring


def _percentile(values: deque[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return round(ordered[index], 2)


class MongoMetrics(monitoring.ConnectionPoolListener, monitoring.CommandListener):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active_connections = 0
        self.checked_out_connections = 0
        self.checkout_timeouts = 0
        self.checkout_failures = {
            "timeout": 0,
            "pool_closed": 0,
            "connection_error": 0,
            "other": 0,
        }
        self.operation_timeouts = 0
        self.checkout_wait_ms: deque[float] = deque(maxlen=1024)
        self.operation_ms: deque[float] = deque(maxlen=1024)
        self.query_shapes: deque[str] = deque(maxlen=32)
        self._checkout_started: dict[int, float] = {}

    # Pool lifecycle methods are required by the listener ABC.
    def pool_created(self, event): pass
    def pool_ready(self, event): pass
    def pool_cleared(self, event): pass
    def pool_closed(self, event): pass
    def connection_created(self, event):
        with self._lock: self.active_connections += 1
    def connection_ready(self, event): pass
    def connection_closed(self, event):
        with self._lock: self.active_connections = max(0, self.active_connections - 1)
    def connection_check_out_started(self, event):
        with self._lock: self._checkout_started[threading.get_ident()] = time.monotonic()
    def connection_check_out_failed(self, event):
        with self._lock:
            started = self._checkout_started.pop(threading.get_ident(), None)
            if started is not None:
                self.checkout_wait_ms.append((time.monotonic() - started) * 1000)
            reason = str(getattr(event, "reason", "")).lower()
            if "timeout" in reason:
                kind = "timeout"
                self.checkout_timeouts += 1
            elif "poolclosed" in reason or "pool_closed" in reason or "closed" in reason:
                kind = "pool_closed"
            elif "connection" in reason:
                kind = "connection_error"
            else:
                kind = "other"
            self.checkout_failures[kind] += 1
    def connection_checked_out(self, event):
        with self._lock:
            started = self._checkout_started.pop(threading.get_ident(), None)
            if started is not None:
                self.checkout_wait_ms.append((time.monotonic() - started) * 1000)
            self.checked_out_connections += 1
    def connection_checked_in(self, event):
        with self._lock:
            self.checked_out_connections = max(0, self.checked_out_connections - 1)

    def started(self, event):
        # Shape is command + collection only. Never retain filters or values.
        collection = str(event.command.get(event.command_name) or "")[:64]
        with self._lock: self.query_shapes.append(f"{event.command_name}:{collection}")
    def succeeded(self, event):
        with self._lock: self.operation_ms.append(event.duration_micros / 1000)
    def failed(self, event):
        message = str(getattr(event, "failure", "")).lower()
        with self._lock:
            self.operation_ms.append(event.duration_micros / 1000)
            if "timeout" in message or "timed out" in message:
                self.operation_timeouts += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_connections": self.active_connections,
                "checked_out_connections": self.checked_out_connections,
                "checkout_wait_ms": {
                    "p50": _percentile(self.checkout_wait_ms, 0.50),
                    "p95": _percentile(self.checkout_wait_ms, 0.95),
                    "p99": _percentile(self.checkout_wait_ms, 0.99),
                },
                "checkout_timeouts": self.checkout_timeouts,
                "checkout_failures": dict(self.checkout_failures),
                "operation_duration_ms": {
                    "p50": _percentile(self.operation_ms, 0.50),
                    "p95": _percentile(self.operation_ms, 0.95),
                    "p99": _percentile(self.operation_ms, 0.99),
                },
                "operation_timeouts": self.operation_timeouts,
                "recent_query_shapes": list(dict.fromkeys(self.query_shapes))[-16:],
            }


mongo_metrics = MongoMetrics()

__all__ = ["MongoMetrics", "mongo_metrics"]
