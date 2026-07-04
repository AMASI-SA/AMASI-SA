"""Iter-2026-02.rev31 — Tabby-only Live Canary tests.

Verifies the purpose-built admin endpoint that flips EXACTLY three
flags and nothing else (`dry_run_mode=False`,
`production_writes_locked=False`, `selective_live_send_enabled=True`)
under a strict precondition check.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "/app/backend")


# ── helpers ──────────────────────────────────────────────────────
def _mk_db(settings_doc: dict) -> MagicMock:
    """Build a MagicMock DB whose `qoyod_settings` returns `settings_doc`
    on `find_one` and captures the `update_one($set)` patch for
    assertion. The returned mock's `qoyod_settings.captured_patch` will
    hold the last `$set` written."""
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=dict(settings_doc))
    captured = {"patch": None, "filter": None, "upsert": None}
    async def _update_one(f, u, upsert=False):
        captured["filter"] = f
        captured["patch"]  = (u or {}).get("$set")
        captured["upsert"] = upsert
        return MagicMock(matched_count=1, modified_count=1)
    coll.update_one = _update_one
    coll.captured = captured
    db = MagicMock()
    db.qoyod_settings = coll
    return db


def _good_settings() -> dict:
    """The exact settings shape the operator described as "ready
    for Tabby live canary" (rev30 dry cycle passed twice)."""
    return {
        "user_id":                                   "main",
        "auto_send":                                 False,
        "selective_auto_send_enabled":               True,
        "selective_auto_send_allowed_payment_methods": ["tabby_installment"],
        "auto_receipt":                              True,
        "capabilities":                              {"create_receipts": True},
        # Current fail-closed posture.
        "dry_run_mode":                              True,
        "production_writes_locked":                  True,
        "selective_live_send_enabled":               False,
    }


# ── Test 1: rev31 marker present in build ────────────────────────
def test_1_rev31_marker_registered_and_present():
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )
    assert "rev31_tabby_live_canary" in REQUIRED_MARKERS
    r = build_diagnostics_report()
    m = r["marker_check"]["markers"]["rev31_tabby_live_canary"]
    assert m["present"] is True
    assert m["count"] >= 1
    assert r["acceptance"]["code_matches_expected"] is True


# ── Test 2: happy path flips exactly three flags ─────────────────
@pytest.mark.asyncio
async def test_2_enable_flips_exactly_three_flags():
    from integrations.qoyod.live_canary import enable_tabby_live_canary
    db = _mk_db(_good_settings())
    out = await enable_tabby_live_canary(
        db, user_id="main",
        confirm_token="ENABLE_TABBY_LIVE_CANARY",
        actor="tester@example.com")
    assert out["ok"] is True
    assert out["outcome"] == "ENABLED"
    assert out["dry_run_mode"] is False
    assert out["production_writes_locked"] is False
    assert out["selective_live_send_enabled"] is True
    assert out["auto_send_still_off"] is True
    assert out["allowed_payment_methods"] == ["tabby_installment"]
    # DB write includes only the flags + audit trail — NOT any
    # payment_method_mapping / SAS toggle / auto_send.
    patch = db.qoyod_settings.captured["patch"]
    assert patch["dry_run_mode"] is False
    assert patch["production_writes_locked"] is False
    assert patch["selective_live_send_enabled"] is True
    assert "payment_method_mapping" not in patch
    assert "auto_send" not in patch
    assert "selective_auto_send_enabled" not in patch
    assert "selective_auto_send_allowed_payment_methods" not in patch
    assert "auto_receipt" not in patch
    # Audit fields.
    assert patch["tabby_live_canary_enabled_by"] == "tester@example.com"
    assert patch.get("tabby_live_canary_enabled_at")


# ── Test 3: wrong confirm token → refused ────────────────────────
@pytest.mark.asyncio
async def test_3_wrong_confirm_token_refused():
    from integrations.qoyod.live_canary import (
        enable_tabby_live_canary, LiveCanaryRefused,
    )
    db = _mk_db(_good_settings())
    with pytest.raises(LiveCanaryRefused) as excinfo:
        await enable_tabby_live_canary(
            db, user_id="main", confirm_token="wrong_token")
    assert excinfo.value.code == "confirm_token_mismatch"
    # No DB write occurred.
    assert db.qoyod_settings.captured["patch"] is None


# ── Test 4: auto_send=True → refused ─────────────────────────────
@pytest.mark.asyncio
async def test_4_auto_send_on_refused():
    from integrations.qoyod.live_canary import (
        enable_tabby_live_canary, LiveCanaryRefused,
    )
    s = _good_settings()
    s["auto_send"] = True
    db = _mk_db(s)
    with pytest.raises(LiveCanaryRefused) as excinfo:
        await enable_tabby_live_canary(
            db, user_id="main",
            confirm_token="ENABLE_TABBY_LIVE_CANARY")
    assert excinfo.value.code == "auto_send_is_on"
    assert db.qoyod_settings.captured["patch"] is None


# ── Test 5: SAS disabled → refused ───────────────────────────────
@pytest.mark.asyncio
async def test_5_sas_disabled_refused():
    from integrations.qoyod.live_canary import (
        enable_tabby_live_canary, LiveCanaryRefused,
    )
    s = _good_settings()
    s["selective_auto_send_enabled"] = False
    db = _mk_db(s)
    with pytest.raises(LiveCanaryRefused) as excinfo:
        await enable_tabby_live_canary(
            db, user_id="main",
            confirm_token="ENABLE_TABBY_LIVE_CANARY")
    assert excinfo.value.code == "selective_auto_send_disabled"


# ── Test 6: allow-list not exactly tabby → refused ───────────────
@pytest.mark.asyncio
async def test_6_allowlist_extras_refused():
    from integrations.qoyod.live_canary import (
        enable_tabby_live_canary, LiveCanaryRefused,
    )
    s = _good_settings()
    s["selective_auto_send_allowed_payment_methods"] = [
        "tabby_installment", "mada"]  # mada slipped in
    db = _mk_db(s)
    with pytest.raises(LiveCanaryRefused) as excinfo:
        await enable_tabby_live_canary(
            db, user_id="main",
            confirm_token="ENABLE_TABBY_LIVE_CANARY")
    assert excinfo.value.code == "allowlist_not_exactly_tabby"


@pytest.mark.asyncio
async def test_6b_allowlist_empty_refused():
    from integrations.qoyod.live_canary import (
        enable_tabby_live_canary, LiveCanaryRefused,
    )
    s = _good_settings()
    s["selective_auto_send_allowed_payment_methods"] = []
    db = _mk_db(s)
    with pytest.raises(LiveCanaryRefused) as excinfo:
        await enable_tabby_live_canary(
            db, user_id="main",
            confirm_token="ENABLE_TABBY_LIVE_CANARY")
    assert excinfo.value.code == "allowlist_not_exactly_tabby"


# ── Test 7: auto_receipt=False → refused ─────────────────────────
@pytest.mark.asyncio
async def test_7_auto_receipt_off_refused():
    from integrations.qoyod.live_canary import (
        enable_tabby_live_canary, LiveCanaryRefused,
    )
    s = _good_settings()
    s["auto_receipt"] = False
    db = _mk_db(s)
    with pytest.raises(LiveCanaryRefused) as excinfo:
        await enable_tabby_live_canary(
            db, user_id="main",
            confirm_token="ENABLE_TABBY_LIVE_CANARY")
    assert excinfo.value.code == "auto_receipt_disabled"


# ── Test 8: capabilities.create_receipts=False → refused ─────────
@pytest.mark.asyncio
async def test_8_create_receipts_capability_off_refused():
    from integrations.qoyod.live_canary import (
        enable_tabby_live_canary, LiveCanaryRefused,
    )
    s = _good_settings()
    s["capabilities"] = {"create_receipts": False}
    db = _mk_db(s)
    with pytest.raises(LiveCanaryRefused) as excinfo:
        await enable_tabby_live_canary(
            db, user_id="main",
            confirm_token="ENABLE_TABBY_LIVE_CANARY")
    assert excinfo.value.code == "capability_create_receipts_disabled"


# ── Test 9: idempotent already-enabled → no-op ───────────────────
@pytest.mark.asyncio
async def test_9_idempotent_already_enabled_returns_no_op():
    from integrations.qoyod.live_canary import enable_tabby_live_canary
    s = _good_settings()
    # Simulate the canary is ALREADY active.
    s["dry_run_mode"] = False
    s["production_writes_locked"] = False
    s["selective_live_send_enabled"] = True
    db = _mk_db(s)
    out = await enable_tabby_live_canary(
        db, user_id="main",
        confirm_token="ENABLE_TABBY_LIVE_CANARY")
    assert out["outcome"] == "ALREADY_ENABLED"
    # No DB write on the idempotent path.
    assert db.qoyod_settings.captured["patch"] is None


# ── Test 10: disable — always succeeds, restores fail-closed ─────
@pytest.mark.asyncio
async def test_10_disable_restores_fail_closed_posture():
    from integrations.qoyod.live_canary import disable_tabby_live_canary
    # Any current settings — even the canary-active state.
    db = _mk_db({
        "user_id": "main",
        "dry_run_mode": False,
        "production_writes_locked": False,
        "selective_live_send_enabled": True,
    })
    out = await disable_tabby_live_canary(
        db, user_id="main",
        confirm_token="DISABLE_TABBY_LIVE_CANARY",
        actor="tester@example.com",
        reason="rollback drill")
    assert out["ok"] is True
    assert out["outcome"] == "DISABLED"
    assert out["dry_run_mode"] is True
    assert out["production_writes_locked"] is True
    assert out["selective_live_send_enabled"] is False
    patch = db.qoyod_settings.captured["patch"]
    assert patch["dry_run_mode"] is True
    assert patch["production_writes_locked"] is True
    assert patch["selective_live_send_enabled"] is False
    assert patch["tabby_live_canary_disabled_by"] == "tester@example.com"
    assert patch["tabby_live_canary_disabled_reason"] == "rollback drill"


# ── Test 11: disable — wrong confirm token → refused ─────────────
@pytest.mark.asyncio
async def test_11_disable_wrong_confirm_token_refused():
    from integrations.qoyod.live_canary import (
        disable_tabby_live_canary, LiveCanaryRefused,
    )
    db = _mk_db({"user_id": "main"})
    with pytest.raises(LiveCanaryRefused) as excinfo:
        await disable_tabby_live_canary(
            db, user_id="main", confirm_token="ENABLE_TABBY_LIVE_CANARY")
    assert excinfo.value.code == "confirm_token_mismatch"
    assert db.qoyod_settings.captured["patch"] is None


# ── Test 12: source-side proof — endpoint wired in routes.py ─────
def test_12_endpoint_wired_in_routes():
    import inspect
    from integrations.qoyod import routes as rmod
    src = inspect.getsource(rmod)
    assert '/admin/live-canary/enable-tabby' in src
    assert '/admin/live-canary/disable-tabby' in src
    assert 'enable_tabby_live_canary' in src
    assert 'disable_tabby_live_canary' in src
    # Body models are declared with extra="forbid".
    assert 'class EnableTabbyLiveCanaryBody' in src
    assert 'class DisableTabbyLiveCanaryBody' in src
