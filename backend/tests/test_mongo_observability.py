from types import SimpleNamespace

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring

from mongo_observability import MongoMetrics


def test_command_metrics_keep_shape_but_not_filter_values():
    metrics = MongoMetrics()
    metrics.started(SimpleNamespace(
        command_name="find",
        command={"find": "unified_orders", "filter": {"phone": "+966-secret"}},
    ))
    metrics.succeeded(SimpleNamespace(duration_micros=12_000))

    snapshot = metrics.snapshot()
    assert snapshot["recent_query_shapes"] == ["find:unified_orders"]
    assert "+966-secret" not in repr(snapshot)
    assert snapshot["operation_duration_ms"]["p50"] == 12.0


def test_pool_counts_and_wait_are_bounded(monkeypatch):
    metrics = MongoMetrics()
    times = iter([1.0, 1.025])
    monkeypatch.setattr("mongo_observability.time.monotonic", lambda: next(times))
    metrics.connection_created(SimpleNamespace())
    metrics.connection_check_out_started(SimpleNamespace())
    metrics.connection_checked_out(SimpleNamespace())
    snapshot = metrics.snapshot()
    assert snapshot["active_connections"] == 1
    assert snapshot["checked_out_connections"] == 1
    assert snapshot["checkout_wait_ms"]["p50"] == 25.0
    metrics.connection_checked_in(SimpleNamespace())
    metrics.connection_closed(SimpleNamespace())
    assert metrics.snapshot()["active_connections"] == 0


def test_checkout_failures_are_classified_not_all_timeouts(monkeypatch):
    metrics = MongoMetrics()
    monkeypatch.setattr("mongo_observability.time.monotonic", lambda: 2.0)
    for reason in ("timeout", "poolClosed", "connectionError", "mystery"):
        metrics.connection_check_out_started(SimpleNamespace())
        metrics.connection_check_out_failed(SimpleNamespace(reason=reason))
    snapshot = metrics.snapshot()
    assert snapshot["checkout_timeouts"] == 1
    assert snapshot["checkout_failures"] == {
        "timeout": 1, "pool_closed": 1, "connection_error": 1, "other": 1,
    }


def test_command_listener_uses_pymongo_contract_and_is_motor_compatible():
    metrics = MongoMetrics()
    assert isinstance(metrics, monitoring.CommandListener)
    client = AsyncIOMotorClient(
        "mongodb://127.0.0.1:27017", connect=False, event_listeners=[metrics]
    )
    assert client.delegate._event_listeners.enabled_for_commands
    metrics.started(SimpleNamespace(command_name="ping", command={"ping": 1}))
    metrics.failed(SimpleNamespace(duration_micros=2_000, failure="timeout"))
    snapshot = metrics.snapshot()
    assert snapshot["recent_query_shapes"] == ["ping:1"]
    assert snapshot["operation_timeouts"] == 1
    client.close()
