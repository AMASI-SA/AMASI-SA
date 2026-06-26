"""Pre-Day 3 — Qoyod pipeline state machine tests.

Locks in the canonical state vocabulary, the allowed transitions
and the side-effects of `transition()`.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.state_machine import (
    HAPPY_PATH, FAILURE_STAGES, ALL_STAGES, TERMINAL_STAGES,
    FAILURE_TO_RESUME, ALLOWED_TRANSITIONS,
    SKIPPED, RETRYING,
    can_transition, is_valid_stage, resume_target,
    transition, initial_history_entry, InvalidTransition,
)


# ─────────────────────────────────────────────────────────────────────
# A) Vocabulary lock-in
# ─────────────────────────────────────────────────────────────────────
def test_happy_path_locked_to_ten_stages():
    assert HAPPY_PATH == (
        "NEW", "RECEIVED", "VALIDATED", "NORMALIZED", "RULES_APPLIED",
        "CUSTOMER_RESOLVED", "PRODUCT_RESOLVED",
        "INVOICE_CREATED", "RECEIPT_CREATED", "COMPLETED",
    )


def test_failure_stages_match_user_spec():
    # The user listed these failure tokens in the Pre-Day 3 + Day 3 briefs.
    # FAILED_NORMALIZATION was added at Day 3 so the inbox can record
    # a normalization-specific failure before falling into DEAD_LETTER.
    expected = {
        "FAILED_VALIDATION", "FAILED_NORMALIZATION",
        "FAILED_CUSTOMER", "FAILED_PRODUCT",
        "FAILED_INVOICE", "FAILED_RECEIPT",
        "DEAD_LETTER",
    }
    assert set(FAILURE_STAGES) == expected


def test_all_stages_includes_skipped_and_retrying():
    assert SKIPPED in ALL_STAGES
    assert RETRYING in ALL_STAGES


def test_terminal_stages_are_completed_skipped_deadletter_partial():
    assert TERMINAL_STAGES == frozenset({
        "COMPLETED", "SKIPPED", "DEAD_LETTER", "PARTIAL_FAILURE",
    })


def test_is_valid_stage_rejects_unknown():
    assert is_valid_stage("COMPLETED") is True
    assert is_valid_stage("completed") is False  # lowercase legacy is NOT canonical
    assert is_valid_stage("BOGUS") is False


# ─────────────────────────────────────────────────────────────────────
# B) Transition graph
# ─────────────────────────────────────────────────────────────────────
def test_happy_path_is_fully_connected():
    for i in range(len(HAPPY_PATH) - 1):
        assert can_transition(HAPPY_PATH[i], HAPPY_PATH[i + 1]), \
            f"missing happy edge {HAPPY_PATH[i]} → {HAPPY_PATH[i+1]}"


def test_happy_path_cannot_skip_stages():
    # NEW → VALIDATED (skipping RECEIVED) must be rejected.
    assert can_transition("NEW", "VALIDATED") is False
    assert can_transition("RECEIVED", "NORMALIZED") is False


def test_every_failure_stage_reachable_from_correct_step():
    for fail_stage, resume_from in FAILURE_TO_RESUME.items():
        assert can_transition(resume_from, fail_stage), \
            f"can't reach {fail_stage} from {resume_from}"


def test_failure_to_retrying_to_resume_loop():
    # FAILED_CUSTOMER → RETRYING → RULES_APPLIED  (resume target).
    assert can_transition("FAILED_CUSTOMER", "RETRYING")
    assert can_transition("RETRYING", "RULES_APPLIED")
    assert resume_target("FAILED_CUSTOMER") == "RULES_APPLIED"


def test_failure_can_be_killed_to_deadletter():
    for fail in FAILURE_TO_RESUME:
        assert can_transition(fail, "DEAD_LETTER")


def test_terminal_stages_have_no_outbound_edges():
    for terminal in TERMINAL_STAGES:
        outbound = [t for (f, t) in ALLOWED_TRANSITIONS if f == terminal]
        assert outbound == [], \
            f"terminal {terminal} has outbound edges: {outbound}"


def test_any_prefinal_stage_can_be_skipped():
    # Every happy-path stage *except* the final COMPLETED can transition
    # to SKIPPED (business rule decided not to send).
    for stage in HAPPY_PATH[:-1]:
        assert can_transition(stage, SKIPPED), \
            f"{stage} cannot transition to SKIPPED"
    # COMPLETED cannot be SKIPPED — it's already terminal.
    assert not can_transition("COMPLETED", SKIPPED)


# ─────────────────────────────────────────────────────────────────────
# C) transition() side-effects
# ─────────────────────────────────────────────────────────────────────
def test_transition_returns_set_and_push():
    patch = transition(from_stage="NEW", to_stage="RECEIVED",
                       actor="webhook")
    assert patch["$set"]["pipeline_stage"] == "RECEIVED"
    assert "updated_at" in patch["$set"]
    entry = patch["$push"]["stage_history"]
    assert entry["from_stage"] == "NEW"
    assert entry["to_stage"]   == "RECEIVED"
    assert entry["actor"]      == "webhook"


def test_transition_with_note_and_error_payload():
    patch = transition(
        from_stage="CUSTOMER_RESOLVED", to_stage="FAILED_PRODUCT",
        actor="worker",
        note="SKU missing in Qoyod",
        error={"code": "qoyod_not_found", "message": "sku=A-001"},
    )
    entry = patch["$push"]["stage_history"]
    assert entry["note"] == "SKU missing in Qoyod"
    assert entry["error"]["code"] == "qoyod_not_found"


def test_transition_rejects_invalid_edge():
    with pytest.raises(InvalidTransition):
        transition(from_stage="NEW", to_stage="COMPLETED", actor="x")
    with pytest.raises(InvalidTransition):
        transition(from_stage="COMPLETED", to_stage="RECEIVED", actor="x")


def test_transition_increments_attempts_only_on_retry_resume():
    # RETRYING → resume_from (RULES_APPLIED for FAILED_CUSTOMER) — bumps attempts.
    p = transition(from_stage=RETRYING, to_stage="RULES_APPLIED", actor="worker")
    assert p.get("$inc") == {"attempts": 1}

    # Normal happy-path transition does NOT bump attempts.
    p2 = transition(from_stage="NEW", to_stage="RECEIVED", actor="webhook")
    assert "$inc" not in p2

    # Going INTO retry doesn't bump either — attempts count "tries", not "retries".
    p3 = transition(from_stage="FAILED_CUSTOMER", to_stage=RETRYING, actor="user:1")
    assert "$inc" not in p3


def test_initial_history_entry_records_creation():
    e = initial_history_entry(actor="webhook", note="first receipt")
    assert e["from_stage"] is None
    assert e["to_stage"]   == "NEW"
    assert e["actor"]      == "webhook"
    assert e["note"]       == "first receipt"


def test_resume_target_unknown_failure_raises():
    with pytest.raises(ValueError):
        resume_target("FAILED_NOTHING")


# ─────────────────────────────────────────────────────────────────────
# C.bis) Audit Trail bookkeeping
# ─────────────────────────────────────────────────────────────────────
def test_audit_pipeline_started_at_set_on_first_hop_out_of_new():
    p = transition(from_stage="NEW", to_stage="RECEIVED", actor="webhook")
    assert "pipeline_started_at" in p["$set"]
    assert p["$set"]["pipeline_started_at"] == p["$set"]["updated_at"]


def test_audit_pipeline_started_at_not_set_on_other_transitions():
    p = transition(from_stage="RECEIVED", to_stage="VALIDATED", actor="w")
    assert "pipeline_started_at" not in p["$set"]


def test_audit_finished_at_on_terminal_stages():
    from datetime import datetime, timezone, timedelta
    # No existing_started_at supplied — duration field absent, finished_at set.
    p = transition(from_stage="RECEIPT_CREATED", to_stage="COMPLETED",
                   actor="worker")
    assert "pipeline_finished_at" in p["$set"]
    assert p["$set"]["pipeline_outcome"] == "COMPLETED"
    assert "pipeline_duration_ms" not in p["$set"]

    # With existing_started_at — duration computed.
    started = datetime.now(timezone.utc) - timedelta(seconds=12, milliseconds=500)
    p = transition(from_stage="RECEIPT_CREATED", to_stage="COMPLETED",
                   actor="worker", existing_started_at=started)
    assert "pipeline_duration_ms" in p["$set"]
    assert p["$set"]["pipeline_duration_ms"] >= 12_000
    # SKIPPED and DEAD_LETTER are also terminal.
    p2 = transition(from_stage="NORMALIZED", to_stage="SKIPPED",
                    actor="rules", existing_started_at=started)
    assert p2["$set"]["pipeline_outcome"] == "SKIPPED"
    assert "pipeline_finished_at" in p2["$set"]


def test_audit_last_success_stage_tracks_happy_path_only():
    p1 = transition(from_stage="NEW", to_stage="RECEIVED", actor="w")
    assert p1["$set"].get("last_success_stage") == "RECEIVED"
    p2 = transition(from_stage="RULES_APPLIED", to_stage="FAILED_CUSTOMER",
                    actor="w", error={"code": "x"})
    # Failure transitions DO NOT update last_success_stage.
    assert "last_success_stage" not in p2["$set"]


def test_audit_last_failed_stage_tracks_failures():
    p = transition(from_stage="CUSTOMER_RESOLVED", to_stage="FAILED_PRODUCT",
                   actor="w", error={"code": "missing_sku"})
    assert p["$set"]["last_failed_stage"] == "FAILED_PRODUCT"
    # Happy transitions DO NOT update last_failed_stage.
    p2 = transition(from_stage="NEW", to_stage="RECEIVED", actor="w")
    assert "last_failed_stage" not in p2["$set"]


# ─────────────────────────────────────────────────────────────────────
# D) End-to-end scenario walk (golden path + retry loop)
# ─────────────────────────────────────────────────────────────────────
def test_walks_full_happy_path_via_transition():
    # Simulate a row's pipeline_stage and assert every step is allowed.
    cur = "NEW"
    for nxt in HAPPY_PATH[1:]:
        p = transition(from_stage=cur, to_stage=nxt, actor="worker")
        assert p["$set"]["pipeline_stage"] == nxt
        cur = nxt
    assert cur == "COMPLETED"


def test_retry_loop_then_complete():
    # NEW → RECEIVED → VALIDATED → NORMALIZED → RULES_APPLIED →
    #   (work fails at customer step) → FAILED_CUSTOMER → RETRYING →
    #   RULES_APPLIED → CUSTOMER_RESOLVED → … → COMPLETED.
    cur = "NEW"
    for nxt in ("RECEIVED", "VALIDATED", "NORMALIZED", "RULES_APPLIED"):
        transition(from_stage=cur, to_stage=nxt, actor="worker")
        cur = nxt

    # Fail
    p = transition(from_stage="RULES_APPLIED", to_stage="FAILED_CUSTOMER",
                   actor="worker", error={"code": "qoyod_unauthorized"})
    assert p["$set"]["pipeline_stage"] == "FAILED_CUSTOMER"

    # Operator clicks Retry — push to RETRYING
    p = transition(from_stage="FAILED_CUSTOMER", to_stage=RETRYING,
                   actor="user:42", note="manual retry")
    assert p["$set"]["pipeline_stage"] == RETRYING

    # Worker picks it up and resumes from RULES_APPLIED.
    p = transition(from_stage=RETRYING, to_stage="RULES_APPLIED",
                   actor="worker")
    assert p["$inc"] == {"attempts": 1}

    # Continue happily.
    cur = "RULES_APPLIED"
    for nxt in ("CUSTOMER_RESOLVED", "PRODUCT_RESOLVED",
                "INVOICE_CREATED", "RECEIPT_CREATED", "COMPLETED"):
        transition(from_stage=cur, to_stage=nxt, actor="worker")
        cur = nxt
    assert cur == "COMPLETED"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
