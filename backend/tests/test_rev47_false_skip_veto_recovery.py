"""rev47 — False SKIPPED-history veto recovery (user approval 2026-07).

Pins (prod RCA: order 270939808, trace ad0c8807…):
  • rev33(X) write-veto now EXEMPTS a historical SKIPPED entry ONLY
    when it is (a) transient-classified (rev44 rule) AND (b) resumed
    via the audited SKIPPED → RETRYING hop. Everything else stays an
    absolute veto (fatal skips, unknown notes, unresumed, cancelled).
  • New reviewed pattern `false_skip_history_veto_2026_07_07` is
    MANUAL-ONLY: auto-requeue never touches it.
  • Manual requeue clears dead_lettered_at (audited) and parks the row
    at SKIPPED(transient hold) — worker can never auto-send it.
  • pattern_check is READ-ONLY and proves exclusivity.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.dead_letter_requeue import (
    KNOWN_FIXED_PATTERNS, _false_skip_history_veto_matcher,
    auto_requeue_known_fixed, find_requeue_candidates, match_pattern,
    pattern_check, requeue_one,
)
from integrations.qoyod.rev32_hardening import (
    Rev32Violation, assert_final_write_permitted,
    skip_reason_from_history_note, skipped_history_entry_exempt,
)
from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
from integrations.qoyod.send_eligibility_ssot import (
    evaluate_order_for_qoyod_send,
)

TENANT = f"test-r47-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_settings", "qoyod_products_mapping",
         "qoyod_invoices", "qoyod_canary_budget",
         "rev32_kill_switch_events")


@pytest_asyncio.fixture()
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    for c in COLLS:
        await database[c].delete_many({"user_id": TENANT})
    yield database
    for c in COLLS:
        await database[c].delete_many({"user_id": TENANT})
    client.close()


def _h(frm, to, note=None, actor="worker"):
    e = {"from_stage": frm, "to_stage": to,
         "at": datetime.now(timezone.utc), "actor": actor}
    if note:
        e["note"] = note
    return e


_SAS_SKIP_NOTE = "selective_auto_send_gate: payment_method_not_in_allow_list"
_VETO_ERROR = {
    "code": "rev32_guard_blocked",
    "violation_type": "post_skipped_history_write_violation",
    "message": "row was marked SKIPPED at least once in stage_history",
}


def _prod_replica_row(order, *, stage="DEAD_LETTER",
                      dead_lettered=True, history=None,
                      status="completed", status_native="تم التنفيذ",
                      pm="credit_card", error=None):
    """Replica of prod order 270939808's failure shape."""
    if history is None:
        history = [
            _h(None, "NEW"), _h("NEW", "RECEIVED"),
            _h("RECEIVED", "VALIDATED"), _h("VALIDATED", "NORMALIZED"),
            _h("NORMALIZED", "SKIPPED", note=_SAS_SKIP_NOTE),
            _h("SKIPPED", "RETRYING", actor="mada_canary:operator"),
            _h("RETRYING", "NORMALIZED", actor="mada_canary:operator"),
            _h("NORMALIZED", "SKIPPED", note=_SAS_SKIP_NOTE),
            _h("SKIPPED", "RETRYING", actor="mada_canary:operator"),
            _h("RETRYING", "NORMALIZED", actor="mada_canary:operator"),
            _h("NORMALIZED", "RULES_APPLIED"),
            _h("RULES_APPLIED", "FAILED_CUSTOMER"),
            _h("FAILED_CUSTOMER", "DEAD_LETTER"),
        ]
    row = {
        "user_id": TENANT, "id": f"{TENANT}-row-{order}",
        "trace_id": f"{TENANT}-tr-{order}",
        "idempotency_key": f"idem-{order}",
        "connector_key": "salla",
        "salla_order_number": str(order), "salla_order_id": str(order),
        "pipeline_stage": stage,
        "last_failed_stage": "FAILED_CUSTOMER",
        "pipeline_error": dict(error if error is not None else _VETO_ERROR),
        "skip_class": "transient",
        "skip_class_reason": "payment_method_not_in_allow_list",
        "qoyod_customer_id": None,
        "pipeline_started_at": datetime.now(timezone.utc),
        "received_at": datetime.now(timezone.utc),
        "stage_history": history,
        "sas_worker_trace": {
            "worker_pipeline_sha": _compute_pipeline_sha()},
        "selective_auto_send_gate": {"eligible": True,
                                     "reason": "eligible"},
        "canonical_payload": {
            "order_id": str(order), "order_number": str(order),
            "order_date": "2026-07-07T10:00:00Z",
            "order_status": status,
            "order_status_native": status_native,
            "payment_method": pm,
            "customer": {"name": "ع", "phone": "0500000001"},
            "items": [{"sku": "SKU-R47", "name": "منتج", "quantity": 1,
                       "unit_price": 100.0, "total": 115.0,
                       "tax_amount": 15.0, "discount_amount": 0.0}],
            "subtotal": 100.0, "tax_amount": 15.0,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 115.0,
        },
    }
    if dead_lettered:
        row["dead_lettered_at"] = datetime.now(timezone.utc)
    return row


_LIVE_SETTINGS = {
    "user_id": TENANT,
    "dry_run_mode": False,
    "production_writes_locked": False,
    "selective_live_send_enabled": True,
    "selective_auto_send_enabled": True,
    "selective_auto_send_cutover_at": "2026-07-01T00:00:00Z",
    "selective_auto_send_allowed_payment_methods": ["credit_card"],
    "payment_method_mapping": [
        {"salla_method": "credit_card", "qoyod_account_id": "77"}],
}


async def _seed_live_guard_env(db, order):
    await db.qoyod_settings.insert_one(dict(_LIVE_SETTINGS))
    await db.qoyod_canary_budget.insert_one({
        "user_id": TENANT, "max_orders": 1,
        "order_numbers": [str(order)],
        "pinned_order_number": str(order),
        "armed_at": datetime.now(timezone.utc), "armed_by": "test",
    })


# ── 1. Note parser ───────────────────────────────────────────────────
def test_note_parser():
    assert skip_reason_from_history_note(_SAS_SKIP_NOTE) \
        == "payment_method_not_in_allow_list"
    assert skip_reason_from_history_note(
        "selective_auto_send_gate re-eval failed: status_hard_blocked"
    ) == "status_hard_blocked"
    assert skip_reason_from_history_note(
        "business_rule: not_in_trigger_statuses"
    ) == "not_in_trigger_statuses"
    assert skip_reason_from_history_note(
        "rev33.2 canary_scope_skip: pm='mada' outside allowlist"
    ) == "canary_scope_skip_pm_not_in_allowlist"
    assert skip_reason_from_history_note(
        "manual_recovery_hold: dead_letter_false_veto_recovery_hold"
    ) == "dead_letter_false_veto_recovery_hold"
    # Unknown/legacy formats → None (fail-closed veto stays).
    assert skip_reason_from_history_note(
        "duplicate blocked: real invoice 193 already exists") is None
    assert skip_reason_from_history_note(
        "pre_activation_skipped: row received_at < go_live") is None
    assert skip_reason_from_history_note(None) is None
    assert skip_reason_from_history_note("") is None


# ── 2. Exemption helper matrix ───────────────────────────────────────
def test_exemption_requires_transient_and_resumed():
    row = _prod_replica_row("1001")
    entry = _h("NORMALIZED", "SKIPPED", note=_SAS_SKIP_NOTE)
    resumed = _h("SKIPPED", "RETRYING")
    assert skipped_history_entry_exempt(entry, resumed, row) is True
    # Not resumed (no next / wrong next) → veto.
    assert skipped_history_entry_exempt(entry, None, row) is False
    assert skipped_history_entry_exempt(
        entry, _h("RETRYING", "NORMALIZED"), row) is False
    # Fatal/unknown note → veto even when resumed.
    fatal = _h("PRODUCT_RESOLVED", "SKIPPED",
               note="duplicate blocked: real invoice 193 already exists")
    assert skipped_history_entry_exempt(fatal, resumed, row) is False
    # Cancelled-like order status → veto (classify_skip rule).
    cancelled = _prod_replica_row("1002", status="cancelled",
                                  status_native="ملغي")
    assert skipped_history_entry_exempt(entry, resumed, cancelled) is False


# ── 3. Guard: transient+resumed history passes end-to-end ────────────
@pytest.mark.asyncio
async def test_guard_exempts_transient_resumed_skips(db):
    order = "270939808"
    row = _prod_replica_row(order, stage="RULES_APPLIED",
                            dead_lettered=False)
    row["stage_history"] = row["stage_history"][:-2]  # drop FAILED/DL
    row["pipeline_error"] = None
    row["last_failed_stage"] = None
    await db.integration_inbox.insert_one(dict(row))
    await _seed_live_guard_env(db, order)
    # Must NOT raise — the two transient+resumed SKIPPED entries are
    # exempt; all other guard layers are satisfied.
    await assert_final_write_permitted(
        db, row["id"], action="create_customer",
        payment_method="credit_card", user_id=TENANT)
    # Kill switch must NOT have fired.
    settings = await db.qoyod_settings.find_one({"user_id": TENANT})
    assert not settings.get("kill_switch_triggered")


@pytest.mark.asyncio
async def test_guard_vetoes_unresumed_transient_skip(db):
    row = _prod_replica_row("2001", stage="RULES_APPLIED",
                            dead_lettered=False)
    row["stage_history"] = [
        _h("VALIDATED", "NORMALIZED"),
        _h("NORMALIZED", "SKIPPED", note=_SAS_SKIP_NOTE),
        # resurrected WITHOUT the audited RETRYING hop → veto.
        _h("SKIPPED", "NORMALIZED"),
        _h("NORMALIZED", "RULES_APPLIED"),
    ]
    await db.integration_inbox.insert_one(dict(row))
    await _seed_live_guard_env(db, "2001")
    with pytest.raises(Rev32Violation) as ex:
        await assert_final_write_permitted(
            db, row["id"], action="create_customer",
            payment_method="credit_card", user_id=TENANT)
    assert ex.value.violation_type == "post_skipped_history_write_violation"


@pytest.mark.asyncio
async def test_guard_vetoes_fatal_and_unknown_notes(db):
    for order, note in (
        ("2002", "duplicate blocked: real invoice 193 already exists"),
        ("2003", "some legacy unparseable note"),
        ("2004", None),
    ):
        row = _prod_replica_row(order, stage="RULES_APPLIED",
                                dead_lettered=False)
        row["stage_history"] = [
            _h("VALIDATED", "NORMALIZED"),
            _h("NORMALIZED", "SKIPPED", note=note),
            _h("SKIPPED", "RETRYING"),
            _h("RETRYING", "NORMALIZED"),
            _h("NORMALIZED", "RULES_APPLIED"),
        ]
        await db.integration_inbox.insert_one(dict(row))
    await _seed_live_guard_env(db, "2002")
    for order in ("2002", "2003", "2004"):
        with pytest.raises(Rev32Violation) as ex:
            await assert_final_write_permitted(
                db, f"{TENANT}-row-{order}", action="create_customer",
                payment_method="credit_card", user_id=TENANT)
        assert ex.value.violation_type \
            == "post_skipped_history_write_violation"


@pytest.mark.asyncio
async def test_guard_vetoes_cancelled_like_status(db):
    row = _prod_replica_row("2005", stage="RULES_APPLIED",
                            dead_lettered=False,
                            status="cancelled", status_native="ملغي")
    row["stage_history"] = [
        _h("VALIDATED", "NORMALIZED"),
        _h("NORMALIZED", "SKIPPED", note=_SAS_SKIP_NOTE),
        _h("SKIPPED", "RETRYING"),
        _h("RETRYING", "NORMALIZED"),
        _h("NORMALIZED", "RULES_APPLIED"),
    ]
    await db.integration_inbox.insert_one(dict(row))
    await _seed_live_guard_env(db, "2005")
    with pytest.raises(Rev32Violation) as ex:
        await assert_final_write_permitted(
            db, row["id"], action="create_customer",
            payment_method="credit_card", user_id=TENANT)
    assert ex.value.violation_type == "post_skipped_history_write_violation"


# ── 4. Pattern matcher exactness ─────────────────────────────────────
def test_matcher_matches_only_the_false_veto():
    assert _false_skip_history_veto_matcher(dict(_VETO_ERROR)) is True
    assert _false_skip_history_veto_matcher({
        "code": "rev32_guard_blocked",
        "violation_type": "canary_budget_violation"}) is False
    assert _false_skip_history_veto_matcher({
        "code": "qoyod_validation_error",
        "details": {"contact_name": ["Can't be blank"]}}) is False
    assert _false_skip_history_veto_matcher(None) is False
    assert _false_skip_history_veto_matcher({}) is False


def test_registry_contains_reviewed_manual_only_pattern():
    pat = next(p for p in KNOWN_FIXED_PATTERNS
               if p["id"] == "false_skip_history_veto_2026_07_07")
    assert pat["manual_only"] is True
    assert pat["clear_dead_letter_evidence"] is True
    assert pat["hold_in_skipped"] is True
    assert pat["applies_to_failed_stages"] == frozenset(
        {"FAILED_CUSTOMER"})


def test_match_pattern_excludes_manual_only_by_default():
    row = _prod_replica_row("3001")
    assert match_pattern(row) is None
    got = match_pattern(row, include_manual_only=True)
    assert got and got["id"] == "false_skip_history_veto_2026_07_07"


# ── 5. Auto-requeue NEVER touches the manual-only pattern ────────────
@pytest.mark.asyncio
async def test_auto_requeue_ignores_manual_only(db):
    await db.integration_inbox.insert_one(_prod_replica_row("4001"))
    cands = await find_requeue_candidates(db, user_id=TENANT)
    assert cands == []
    out = await auto_requeue_known_fixed(db, user_id=TENANT)
    assert out["requeued"] == 0
    saved = await db.integration_inbox.find_one({"id": f"{TENANT}-row-4001"})
    assert saved["pipeline_stage"] == "DEAD_LETTER"
    assert saved.get("dead_lettered_at") is not None


# ── 6. Manual requeue-one → SKIPPED(transient hold), stamp cleared ───
@pytest.mark.asyncio
async def test_requeue_one_recovers_to_skipped_hold(db):
    await db.integration_inbox.insert_one(_prod_replica_row("270939808"))
    out = await requeue_one(db, user_id=TENANT, row_id=f"{TENANT}-row-270939808",
                            actor="operator:test")
    assert out["ok"] is True
    res = out["result"]
    assert res["final_stage"] == "SKIPPED"
    assert res["held_in_skipped"] is True
    saved = await db.integration_inbox.find_one({"id": f"{TENANT}-row-270939808"})
    assert saved["pipeline_stage"] == "SKIPPED"
    assert saved["skip_class"] == "transient"
    assert saved["skip_class_reason"] \
        == "dead_letter_false_veto_recovery_hold"
    assert saved.get("dead_lettered_at") is None
    assert saved["dead_letter_cleared_pattern"] \
        == "false_skip_history_veto_2026_07_07"
    assert saved["dead_letter_cleared_by"] == "operator:test"
    tail = [ (e.get("from_stage"), e.get("to_stage"))
             for e in saved["stage_history"][-3:] ]
    assert tail == [("DEAD_LETTER", "RETRYING"),
                    ("RETRYING", "NORMALIZED"),
                    ("NORMALIZED", "SKIPPED")]


@pytest.mark.asyncio
async def test_requeue_one_refuses_generic_dead_letter(db):
    row = _prod_replica_row("4002", error={
        "code": "qoyod_api_error", "message": "totally different"})
    await db.integration_inbox.insert_one(row)
    out = await requeue_one(db, user_id=TENANT, row_id=f"{TENANT}-row-4002")
    assert out["ok"] is False
    assert out["reason"] == "no_known_fix_pattern_matches"


@pytest.mark.asyncio
async def test_requeue_one_refuses_when_history_has_fatal_skip(db):
    row = _prod_replica_row("4003")
    row["stage_history"].insert(
        4, _h("NORMALIZED", "SKIPPED",
              note="duplicate blocked: real invoice 99 already exists"))
    row["stage_history"].insert(5, _h("SKIPPED", "RETRYING"))
    await db.integration_inbox.insert_one(row)
    out = await requeue_one(db, user_id=TENANT, row_id=f"{TENANT}-row-4003")
    assert out["ok"] is False
    assert out["result"]["reason"] \
        == "historical_skip_not_transient_or_not_resumed"
    saved = await db.integration_inbox.find_one({"id": f"{TENANT}-row-4003"})
    assert saved["pipeline_stage"] == "DEAD_LETTER"
    assert saved.get("dead_lettered_at") is not None


# ── 7. SSOT diagnosis is GREEN after recovery ────────────────────────
@pytest.mark.asyncio
async def test_ssot_ready_after_recovery(db):
    order = "270939808"
    await db.integration_inbox.insert_one(_prod_replica_row(order))
    await db.qoyod_settings.insert_one(dict(_LIVE_SETTINGS))
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-R47", "qoyod_product_id": "9"})
    out = await requeue_one(db, user_id=TENANT, row_id=f"{TENANT}-row-{order}")
    assert out["ok"] is True
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number=order)
    assert ev["skipped_dead_letter_check"]["passed"] is True
    assert ev["stage_check"]["passed"] is True
    assert ev["ready_to_send"] is True
    assert ev["blockers"] == []


# ── 8. Guard passes after recovery + audited one-shot resume ─────────
@pytest.mark.asyncio
async def test_guard_passes_after_recovery_and_audited_resume(db):
    order = "270939808"
    await db.integration_inbox.insert_one(_prod_replica_row(order))
    await _seed_live_guard_env(db, order)
    out = await requeue_one(db, user_id=TENANT, row_id=f"{TENANT}-row-{order}")
    assert out["ok"] is True
    # Simulate the audited canary one-shot resume + RULES_APPLIED.
    resume = [_h("SKIPPED", "RETRYING", actor="mada_canary:operator"),
              _h("RETRYING", "NORMALIZED", actor="mada_canary:operator"),
              _h("NORMALIZED", "RULES_APPLIED")]
    await db.integration_inbox.update_one(
        {"id": f"{TENANT}-row-{order}"},
        {"$set": {"pipeline_stage": "RULES_APPLIED"},
         "$push": {"stage_history": {"$each": resume}}})
    await assert_final_write_permitted(
        db, f"{TENANT}-row-{order}", action="create_customer",
        payment_method="credit_card", user_id=TENANT)
    settings = await db.qoyod_settings.find_one({"user_id": TENANT})
    assert not settings.get("kill_switch_triggered")


# ── 9. pattern_check (READ-ONLY, exclusivity) ────────────────────────
@pytest.mark.asyncio
async def test_pattern_check_exclusive_match(db):
    order = "270939808"
    await db.integration_inbox.insert_one(_prod_replica_row(order))
    # A generic DEAD_LETTER row (different error) must not count.
    await db.integration_inbox.insert_one(_prod_replica_row(
        "5001", error={"code": "qoyod_api_error", "message": "x"}))
    out = await pattern_check(db, user_id=TENANT, order_number=order)
    assert out["ok"] and out["found"]
    assert out["pattern_matches"] is True
    assert out["matched_pattern_id"] \
        == "false_skip_history_veto_2026_07_07"
    assert out["pattern_is_manual_only"] is True
    assert out["all_historical_skips_transient_and_resumed"] is True
    assert out["other_matches_count"] == 0
    assert out["safe_to_requeue"] is True
    # No writes happened.
    saved = await db.integration_inbox.find_one({"id": f"{TENANT}-row-{order}"})
    assert saved["pipeline_stage"] == "DEAD_LETTER"


@pytest.mark.asyncio
async def test_pattern_check_flags_other_matches(db):
    order = "270939808"
    await db.integration_inbox.insert_one(_prod_replica_row(order))
    await db.integration_inbox.insert_one(_prod_replica_row("5002"))
    out = await pattern_check(db, user_id=TENANT, order_number=order)
    assert out["other_matches_count"] == 1
    assert out["other_dead_letter_rows_matching_pattern"][0][
        "order_number"] == "5002"
    assert out["safe_to_requeue"] is False


@pytest.mark.asyncio
async def test_pattern_check_not_found(db):
    out = await pattern_check(db, user_id=TENANT, order_number="999999")
    assert out["ok"] is False
    assert out["found"] is False


# ── 10. rev47.1 — build diagnostics module markers ──────────────────
def test_build_diagnostics_module_markers_present():
    from integrations.qoyod.sas_build_diagnostics import (
        build_diagnostics_report,
    )
    r = build_diagnostics_report()
    mm = r["module_marker_check"]
    assert mm["all_module_markers_present"] is True
    expected = {"rev44_transient_skip",
                "rev45_customer_pending_resolution",
                "rev46_credit_card_canary_scope",
                "rev46_1_payment_account_mapping_check",
                "rev47_skip_history_exemption",
                "rev47_manual_only_recovery_pattern"}
    assert set(mm["markers"]) == expected
    for v in mm["markers"].values():
        assert v["present"] and v["sha256_first16"]
    acc = r["acceptance"]
    assert acc["module_markers_ok"] is True
    assert acc["code_matches_expected"] == (
        acc["pipeline_markers_ok"] and acc["module_markers_ok"])


# ── 11. rev47.2 — worker must not steal a one-shot-claimed row ───────
# USER SPEC (2026-07): stored allowed_payment_methods=["mada"], canary
# overlay=["credit_card"]; send-diagnosis must be READY_TO_SEND_ONCE
# and the one-shot send must NOT become SKIPPED via
# payment_method_not_in_allow_list (prod RCA 19:30:12 UTC — the 5s
# background worker drained the row with STORED settings mid-send).
@pytest.mark.asyncio
async def test_worker_cannot_steal_claimed_row_stored_mada(db):
    from integrations.qoyod.invoice_builder import DryRunQoyodClient
    from integrations.qoyod.mada_canary_send import _ScopedDB
    from integrations.qoyod.one_shot_reprocess import _reset_row_to_stage
    from integrations.qoyod.pipeline import (
        process_normalized_row, process_pending_customer_resolved,
        process_pending_normalized,
    )
    from integrations.qoyod.send_diagnosis import build_send_diagnosis

    order = "270939808"
    await db.integration_inbox.insert_one(_prod_replica_row(order))
    stored = dict(_LIVE_SETTINGS)
    stored.update({
        # prod-like stored posture: kill switch fired, canary scope
        # NOT in the stored allow-list (worker sees mada only).
        "selective_auto_send_allowed_payment_methods": ["mada"],
        "production_writes_locked": True,
        "selective_live_send_enabled": False,
        "kill_switch_triggered": True,
    })
    await db.qoyod_settings.insert_one(stored)
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-R47", "qoyod_product_id": "9"})
    await db.qoyod_canary_budget.insert_one({
        "user_id": TENANT, "max_orders": 1, "order_numbers": [],
        "pinned_order_number": order,
        "armed_at": datetime.now(timezone.utc), "armed_by": "test"})

    # Recovery (requeue-one) → SKIPPED hold.
    out = await requeue_one(db, user_id=TENANT,
                            row_id=f"{TENANT}-row-{order}")
    assert out["ok"] is True

    # (a) USER SPEC: diagnosis is READY even though stored=["mada"].
    diag = await build_send_diagnosis(db, user_id=TENANT,
                                      order_number=order)
    assert diag["verdict"] == "READY_TO_SEND_ONCE"
    assert diag["all_blockers"] == []

    # (b) One-shot reset (exactly what the canary send does) → the
    # row lands at NORMALIZED carrying the rev47.2 claim.
    scoped = _ScopedDB(db)
    row = await db.integration_inbox.find_one(
        {"id": f"{TENANT}-row-{order}"})
    await _reset_row_to_stage(scoped, row, resume_stage="NORMALIZED",
                              actor="mada_canary:operator")
    row = await db.integration_inbox.find_one(
        {"id": f"{TENANT}-row-{order}"})
    assert row["pipeline_stage"] == "NORMALIZED"
    assert row["one_shot_claim_until"] is not None
    assert row["one_shot_claim_actor"] == "mada_canary:operator"

    # (c) Background worker drain (STORED settings, mada-only) must
    # NOT see the claimed row — no steal, no false skip.
    drained = await process_pending_normalized(db, TENANT)
    assert drained.get("processed", 0) == 0
    row = await db.integration_inbox.find_one(
        {"id": f"{TENANT}-row-{order}"})
    assert row["pipeline_stage"] == "NORMALIZED"  # untouched

    # (d) The in-request scoped processing (canary overlay,
    # credit_card allowed) proceeds — NOT skipped.
    res = await process_normalized_row(scoped, row,
                                       api_client=DryRunQoyodClient())
    assert res.get("outcome") != "SKIPPED"
    row = await db.integration_inbox.find_one(
        {"id": f"{TENANT}-row-{order}"})
    assert row["pipeline_stage"] == "CUSTOMER_RESOLVED"

    # (e) The claim also shields the CUSTOMER_RESOLVED drain window.
    drained2 = await process_pending_customer_resolved(db, TENANT)
    assert drained2.get("processed", 0) == 0
    row = await db.integration_inbox.find_one(
        {"id": f"{TENANT}-row-{order}"})
    assert row["pipeline_stage"] == "CUSTOMER_RESOLVED"


@pytest.mark.asyncio
async def test_expired_claim_is_visible_to_worker_again(db):
    from datetime import timedelta
    from integrations.qoyod.pipeline import process_pending_normalized

    order = "6001"
    row = _prod_replica_row(order, stage="NORMALIZED",
                            dead_lettered=False)
    row["stage_history"] = row["stage_history"][:4]
    row["pipeline_error"] = None
    row["last_failed_stage"] = None
    row["one_shot_claim_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=5))
    await db.integration_inbox.insert_one(row)
    stored = dict(_LIVE_SETTINGS)
    stored["selective_auto_send_allowed_payment_methods"] = ["mada"]
    await db.qoyod_settings.insert_one(stored)

    drained = await process_pending_normalized(db, TENANT)
    assert drained.get("processed") == 1  # expired claim → drained
    fresh = await db.integration_inbox.find_one(
        {"id": f"{TENANT}-row-{order}"})
    # stored allow-list is mada-only → credit_card row transiently
    # skipped by the WORKER (fail-closed behaviour preserved).
    assert fresh["pipeline_stage"] == "SKIPPED"
    assert fresh["skip_class"] == "transient"
