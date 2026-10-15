from __future__ import annotations

from integrations.qoyod.candidate_orders import UNIFIED_CANDIDATE_AUTO_FLAG
from integrations.qoyod_manual import auto_send


def _armed_settings(**overrides):
    value = {
        "enabled": True,
        "auto_send": True,
        "auto_send_desired": True,
        "auto_receipt": True,
        "dry_run_mode": False,
        "legacy_pipeline_frozen": True,
        "invoice_trigger_statuses": ["completed", "delivering", "delivered"],
        "trigger_once_only": True,
        "plan_b_auto_send_armed_at": "2026-09-14T10:00:00+00:00",
        "plan_b_auto_send_orders_user_id": "orders-owner",
        "plan_b_auto_send_actor": "owner@example.test",
        "plan_b_auto_send_canary_run_id": "canary-1",
        UNIFIED_CANDIDATE_AUTO_FLAG: True,
        "capabilities": {
            "create_customers": True,
            "create_products": True,
            "create_invoices": True,
            "create_receipts": True,
        },
    }
    value.update(overrides)
    return value


def test_credential_pause_preserves_previous_operator_intent():
    patch = auto_send.credential_pause_patch(
        _armed_settings(),
        reason="credentials_invalid_or_expired",
        now_iso="2026-09-14T11:00:00+00:00",
        error={"code": "qoyod_unauthorized", "status_code": 401},
    )

    assert patch["auto_send_desired"] is True
    assert patch["enabled"] is False
    assert patch["auto_send"] is False
    assert patch[UNIFIED_CANDIDATE_AUTO_FLAG] is False
    assert patch["plan_b_auto_send_armed_at"] is None
    assert patch["plan_b_auto_send_disabled_reason"] == (
        "credentials_invalid_or_expired"
    )
    assert patch["plan_b_auto_send_last_error"] == {
        "code": "qoyod_unauthorized",
        "status_code": 401,
    }


def test_legacy_armed_state_is_migrated_to_durable_intent():
    legacy = _armed_settings()
    legacy.pop("auto_send_desired")

    patch = auto_send.credential_pause_patch(
        legacy,
        reason="credentials_removed",
        now_iso="2026-09-14T11:00:00+00:00",
    )

    assert patch["auto_send_desired"] is True


def test_explicit_operator_opt_out_is_never_rearmed_by_credential_save():
    plan = auto_send.credential_recovery_plan(
        _armed_settings(
            enabled=False,
            auto_send=False,
            auto_send_desired=False,
            plan_b_auto_send_armed_at=None,
            **{UNIFIED_CANDIDATE_AUTO_FLAG: False},
        ),
        readiness_issues=[],
        orders_user_id="orders-owner",
        actor="owner@example.test",
        canary_run_id="canary-1",
        now_iso="2026-09-14T12:00:00+00:00",
    )

    assert plan == {
        "requested": False,
        "resumed": False,
        "reason": "operator_intent_disabled",
        "issues": [],
        "settings_patch": {},
    }


def test_credential_recovery_stays_paused_when_readiness_fails():
    plan = auto_send.credential_recovery_plan(
        _armed_settings(
            enabled=False,
            auto_send=False,
            plan_b_auto_send_armed_at=None,
            plan_b_auto_send_disabled_reason="credentials_removed",
            **{UNIFIED_CANDIDATE_AUTO_FLAG: False},
        ),
        readiness_issues=[
            {"code": "credentials_invalid", "message": "invalid token"},
        ],
        orders_user_id="orders-owner",
        actor="owner@example.test",
        canary_run_id="canary-1",
        now_iso="2026-09-14T12:00:00+00:00",
    )

    assert plan["requested"] is True
    assert plan["resumed"] is False
    assert plan["reason"] == "readiness_failed"
    assert plan["issues"][0]["code"] == "credentials_invalid"
    assert plan["settings_patch"] == {}


def test_verified_credential_rearms_only_the_previous_operator_intent():
    paused = _armed_settings(
        enabled=False,
        auto_send=False,
        plan_b_auto_send_armed_at=None,
        plan_b_auto_send_disabled_reason="credentials_invalid_or_expired",
        **{UNIFIED_CANDIDATE_AUTO_FLAG: False},
    )

    plan = auto_send.credential_recovery_plan(
        paused,
        readiness_issues=[],
        orders_user_id="orders-owner",
        actor="owner@example.test",
        canary_run_id="canary-2",
        now_iso="2026-09-14T12:00:00+00:00",
    )

    patch = plan["settings_patch"]
    assert plan["requested"] is True
    assert plan["resumed"] is True
    assert plan["reason"] == "credentials_verified"
    assert patch["auto_send_desired"] is True
    assert patch["enabled"] is True
    assert patch["auto_send"] is True
    assert patch["dry_run_mode"] is False
    assert patch[UNIFIED_CANDIDATE_AUTO_FLAG] is True
    assert patch["plan_b_auto_send_armed_at"] == "2026-09-14T12:00:00+00:00"
    assert patch["plan_b_auto_send_orders_user_id"] == "orders-owner"
    assert patch["plan_b_auto_send_canary_run_id"] == "canary-2"
    assert patch["plan_b_auto_send_disabled_reason"] is None
    assert patch["plan_b_auto_send_last_error"] is None


def test_status_snapshot_distinguishes_desire_pause_and_runtime_arming():
    paused = _armed_settings(
        enabled=False,
        auto_send=False,
        plan_b_auto_send_armed_at=None,
        plan_b_auto_send_disabled_reason="credentials_invalid_or_expired",
        **{UNIFIED_CANDIDATE_AUTO_FLAG: False},
    )

    status = auto_send.status_snapshot(paused)

    assert status["desired"] is True
    assert status["requested"] is False
    assert status["armed"] is False
    assert status["credential_paused"] is True
    assert status["resume_pending"] is True
