from types import SimpleNamespace

from mongo_observability import MongoMetrics


def test_command_metrics_keep_shape_but_not_filter_values():
    metrics = MongoMetrics()
    metrics.command_started(SimpleNamespace(
        command_name="find",
        command={"find": "unified_orders", "filter": {"phone": "+966-secret"}},
    ))
    metrics.command_succeeded(SimpleNamespace(duration_micros=12_000))

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
