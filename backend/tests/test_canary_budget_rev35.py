"""Iter-2026-02.rev35 — Live-Canary order budget (max_orders=1).

Runs against the REAL local Mongo (MONGO_URL via conftest) under an
isolated throw-away tenant. Covers:
  1. Arm gating: token refusal, hard cap max_orders=1, re-arm refusal
     when slots are used unless force_reset.
  2. Atomic idempotent reservation: not-armed → refuse, first order
     reserves, SAME order re-reserves (invoice+payment = one slot),
     second order refuses.
  3. pipeline._get_api_client raises CanaryBudgetHold (and persists
     the row flag) instead of minting a live client when the budget
     refuses — and NEVER falls back to a DryRun client on that path.
  4. rev32_hardening write-time check: canary window + unreserved
     order → Rev32Violation(canary_budget_violation); reserved order
     passes the budget layer.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.canary_budget import (
    ARM_CONFIRM_TOKEN,
    BUDGET_COLLECTION,
    CanaryBudgetHold,
    CanaryBudgetRefused,
    arm_canary_budget,
    get_canary_budget,
    is_order_reserved,
    reserve_canary_budget,
)

TENANT = f"test-canarybudget-{uuid4().hex[:8]}"

_COLLECTIONS = (BUDGET_COLLECTION, "integration_inbox",
                "qoyod_settings", "qoyod_kill_switch_log",
                "qoyod_live_send_audit", "qoyod_credentials")


async def _store_test_api_key(db):
    from integrations.qoyod.credentials import save_api_key
    await save_api_key(db, TENANT, "test-key-123")


@pytest_asyncio.fixture()
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    for coll in _COLLECTIONS:
        await database[coll].delete_many({"user_id": TENANT})
    yield database
    for coll in _COLLECTIONS:
        await database[coll].delete_many({"user_id": TENANT})
    client.close()


# ── 1. Arm gating ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_arm_refuses_wrong_token_and_hard_cap(db):
    with pytest.raises(CanaryBudgetRefused) as exc:
        await arm_canary_budget(
            db, user_id=TENANT, confirm_token="WRONG")
    assert exc.value.code == "confirm_token_mismatch"

    with pytest.raises(CanaryBudgetRefused) as exc:
        await arm_canary_budget(
            db, user_id=TENANT, confirm_token=ARM_CONFIRM_TOKEN,
            max_orders=2)
    assert exc.value.code == "max_orders_out_of_bounds"

    status = await get_canary_budget(db, user_id=TENANT)
    assert status["armed"] is False


@pytest.mark.asyncio
async def test_arm_ok_then_rearm_needs_force_reset(db):
    out = await arm_canary_budget(
        db, user_id=TENANT, confirm_token=ARM_CONFIRM_TOKEN, actor="t")
    assert out["outcome"] == "ARMED" and out["max_orders"] == 1

    ok, reason = await reserve_canary_budget(
        db, user_id=TENANT, order_number="1001")
    assert (ok, reason) == (True, "reserved")

    # Re-arm with a used slot → refused without force_reset.
    with pytest.raises(CanaryBudgetRefused) as exc:
        await arm_canary_budget(
            db, user_id=TENANT, confirm_token=ARM_CONFIRM_TOKEN)
    assert exc.value.code == "budget_already_used"

    out = await arm_canary_budget(
        db, user_id=TENANT, confirm_token=ARM_CONFIRM_TOKEN,
        force_reset=True, actor="t")
    assert out["outcome"] == "ARMED"
    status = await get_canary_budget(db, user_id=TENANT)
    assert status["used"] == 0 and status["remaining"] == 1


# ── 2. Reservation semantics ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_reserve_fail_closed_and_idempotent(db):
    # Not armed → refuse.
    ok, reason = await reserve_canary_budget(
        db, user_id=TENANT, order_number="2001")
    assert (ok, reason) == (False, "canary_budget_not_armed")

    await arm_canary_budget(
        db, user_id=TENANT, confirm_token=ARM_CONFIRM_TOKEN)

    # Missing order context → refuse (fail-closed).
    ok, reason = await reserve_canary_budget(
        db, user_id=TENANT, order_number=None)
    assert (ok, reason) == (False, "missing_order_number")

    # First order reserves the single slot.
    ok, reason = await reserve_canary_budget(
        db, user_id=TENANT, order_number="2001")
    assert (ok, reason) == (True, "reserved")
    # SAME order again (payment after invoice) → idempotent allow.
    ok, reason = await reserve_canary_budget(
        db, user_id=TENANT, order_number="2001")
    assert (ok, reason) == (True, "already_reserved")
    # A SECOND order → exhausted.
    ok, reason = await reserve_canary_budget(
        db, user_id=TENANT, order_number="2002")
    assert (ok, reason) == (False, "canary_budget_exhausted")

    assert await is_order_reserved(
        db, user_id=TENANT, order_number="2001") is True
    assert await is_order_reserved(
        db, user_id=TENANT, order_number="2002") is False
    status = await get_canary_budget(db, user_id=TENANT)
    assert status["order_numbers"] == ["2001"]


# ── 3. pipeline._get_api_client hold path ────────────────────────────
_LIVE_SETTINGS = {
    "dry_run_mode":                False,
    "selective_live_send_enabled": True,
    "production_writes_locked":    False,
    "selective_auto_send_enabled": True,
    "selective_auto_send_allowed_payment_methods": ["mada"],
}


async def _seed_live_row(db, row_id: str, order_no: str):
    """Row shaped so the rev32 stale-worker check passes."""
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    await db.integration_inbox.insert_one({
        "user_id": TENANT, "id": row_id,
        "connector_key": "salla-test",
        "idempotency_key": f"idem-{row_id}",
        "salla_order_number": order_no,
        "trace_id": f"trace-{row_id}",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "sas_worker_trace": {"worker_pipeline_sha": _compute_pipeline_sha()},
        "selective_auto_send_gate": {"eligible": True, "reason": "test"},
        "canonical_payload": {"payment_method": "mada"},
    })


@pytest.mark.asyncio
async def test_get_api_client_holds_when_budget_not_armed(db):
    from integrations.qoyod.pipeline import _get_api_client
    # Tenant needs an api key so we reach the budget layer.
    await _store_test_api_key(db)
    await db.qoyod_settings.update_one(
        {"user_id": TENANT},
        {"$set": {**_LIVE_SETTINGS}},
        upsert=True)
    await _seed_live_row(db, "row-hold-1", "3001")

    with pytest.raises(CanaryBudgetHold) as exc:
        await _get_api_client(
            db, TENANT, {**_LIVE_SETTINGS},
            scoped_write_allowance=True, row_id="row-hold-1")
    assert exc.value.reason == "canary_budget_not_armed"
    assert exc.value.order_number == "3001"
    # Row flag persisted for RCA.
    row = await db.integration_inbox.find_one(
        {"user_id": TENANT, "id": "row-hold-1"})
    assert row["canary_budget_hold"]["reason"] == "canary_budget_not_armed"
    # Row untouched otherwise — same stage, no dead-letter.
    assert row["pipeline_stage"] == "CUSTOMER_RESOLVED"
    assert row.get("dead_lettered_at") is None


@pytest.mark.asyncio
async def test_get_api_client_mints_live_client_when_reserved(db):
    from integrations.qoyod.pipeline import _get_api_client
    from integrations.qoyod.invoice_builder import DryRunQoyodClient
    await _store_test_api_key(db)
    await db.qoyod_settings.update_one(
        {"user_id": TENANT},
        {"$set": {**_LIVE_SETTINGS}},
        upsert=True)
    await _seed_live_row(db, "row-live-1", "3002")
    await arm_canary_budget(
        db, user_id=TENANT, confirm_token=ARM_CONFIRM_TOKEN)

    client, is_dry = await _get_api_client(
        db, TENANT, {**_LIVE_SETTINGS},
        scoped_write_allowance=True, row_id="row-live-1")
    assert is_dry is False
    assert client is not None
    assert not isinstance(client, DryRunQoyodClient)
    # Slot consumed.
    status = await get_canary_budget(db, user_id=TENANT)
    assert status["order_numbers"] == ["3002"]

    # A SECOND order on the same tenant → hold.
    await _seed_live_row(db, "row-hold-2", "3003")
    with pytest.raises(CanaryBudgetHold) as exc:
        await _get_api_client(
            db, TENANT, {**_LIVE_SETTINGS},
            scoped_write_allowance=True, row_id="row-hold-2")
    assert exc.value.reason == "canary_budget_exhausted"


# ── 4. rev32_hardening write-time budget layer ───────────────────────
@pytest.mark.asyncio
async def test_write_time_budget_violation_for_unreserved_order(db):
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    await db.qoyod_settings.update_one(
        {"user_id": TENANT}, {"$set": {**_LIVE_SETTINGS}}, upsert=True)
    await _seed_live_row(db, "row-w-1", "4001")
    await arm_canary_budget(
        db, user_id=TENANT, confirm_token=ARM_CONFIRM_TOKEN)
    # Budget armed but order 4001 NOT reserved → violation.
    with pytest.raises(Rev32Violation) as exc:
        await assert_final_write_permitted(
            db, "row-w-1", action="create_invoice",
            payment_method="mada", user_id=TENANT)
    assert exc.value.violation_type == "canary_budget_violation"
    row = await db.integration_inbox.find_one(
        {"user_id": TENANT, "id": "row-w-1"})
    assert "canary_budget_violation" in (row.get("rev32_flags") or {})


@pytest.mark.asyncio
async def test_write_time_budget_passes_for_reserved_order(db):
    from integrations.qoyod.rev32_hardening import (
        assert_final_write_permitted,
    )
    await db.qoyod_settings.update_one(
        {"user_id": TENANT}, {"$set": {**_LIVE_SETTINGS}}, upsert=True)
    await _seed_live_row(db, "row-w-2", "4002")
    await arm_canary_budget(
        db, user_id=TENANT, confirm_token=ARM_CONFIRM_TOKEN)
    ok, _ = await reserve_canary_budget(
        db, user_id=TENANT, order_number="4002")
    assert ok
    # Reserved → the budget layer passes; the full guard chain
    # completes without raising for this well-formed row.
    await assert_final_write_permitted(
        db, "row-w-2", action="create_invoice",
        payment_method="mada", user_id=TENANT)
