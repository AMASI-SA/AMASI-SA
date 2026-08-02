from integrations_control_center.snapchat_operational_error_resolution import (
    resolve_snapchat_stale_operational_state,
)


def _snapshot(**overrides):
    value = {
        "provider": "snapchat_ads",
        "connection_status": "connected",
        "data_quality": "complete",
        "last_sync_at": "2026-08-02T20:10:00+00:00",
        "latest_error": {
            "code": "snapchat_provider_http_400",
            "message": "read-only request failed",
            "occurred_at": "2026-08-02T20:05:00+00:00",
        },
    }
    value.update(overrides)
    return value


def test_old_operational_error_is_hidden_after_newer_complete_sync():
    snapshot, health = resolve_snapchat_stale_operational_state(
        _snapshot(),
        {
            "health_status": "unhealthy",
            "checked_at": "2026-08-02T20:05:00+00:00",
            "health_score": 20,
        },
    )

    assert snapshot["latest_error"] is None
    assert health is None


def test_newer_operational_error_remains_visible():
    snapshot, health = resolve_snapchat_stale_operational_state(
        _snapshot(
            latest_error={
                "code": "snapchat_provider_http_400",
                "occurred_at": "2026-08-02T20:15:00+00:00",
            }
        ),
        None,
    )

    assert snapshot["latest_error"]["code"] == "snapchat_provider_http_400"
    assert health is None


def test_partial_sync_does_not_hide_current_error():
    snapshot, _ = resolve_snapchat_stale_operational_state(
        _snapshot(data_quality="partial"),
        None,
    )

    assert snapshot["latest_error"] is not None


def test_authorization_error_is_never_hidden_by_data_timestamp():
    snapshot, _ = resolve_snapchat_stale_operational_state(
        _snapshot(
            latest_error={
                "code": "snapchat_needs_reauth",
                "occurred_at": "2026-08-02T20:05:00+00:00",
            }
        ),
        None,
    )

    assert snapshot["latest_error"]["code"] == "snapchat_needs_reauth"


def test_other_provider_is_unchanged():
    original = _snapshot(provider="meta_ads")
    snapshot, health = resolve_snapchat_stale_operational_state(
        original,
        {"health_status": "unhealthy", "checked_at": "2026-08-02T20:05:00+00:00"},
    )

    assert snapshot["latest_error"] == original["latest_error"]
    assert health is not None
