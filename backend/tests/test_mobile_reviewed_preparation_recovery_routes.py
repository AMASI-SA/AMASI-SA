from datetime import datetime, timezone

import mobile_reviewed_preparation_recovery_routes as recovery


def test_stable_assigned_at_prefers_ready_timestamp():
    value = recovery._stable_assigned_at(
        {
            "ready_at": datetime(2026, 8, 6, 12, 30, tzinfo=timezone.utc),
            "created_at": datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
        },
        {},
    )

    assert value == "2026-08-06T12:30:00+00:00"


def test_stable_assigned_at_accepts_existing_iso_string():
    value = recovery._stable_assigned_at(
        {"ready_at": "2026-08-06T12:30:00+00:00"},
        {},
    )

    assert value == "2026-08-06T12:30:00+00:00"


def test_recovery_installs_safe_global_helpers(monkeypatch):
    monkeypatch.setattr(recovery.original, "_rollback_build", object())
    monkeypatch.setattr(recovery.original, "_record_planning_assignments", object())

    recovery.install_mobile_reviewed_preparation_recovery()

    assert recovery.original._rollback_build is recovery._safe_rollback_build
    assert (
        recovery.original._record_planning_assignments
        is recovery._stable_record_planning_assignments
    )
