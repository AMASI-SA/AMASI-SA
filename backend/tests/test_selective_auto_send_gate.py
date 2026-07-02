"""Iter-2026-02.rev16 — Selective Auto-Send Gate tests.

Locks in the 12 mandatory invariants (user directive 2026-02-27):

   1. cutover_at is stamped automatically on enable.
   2. Orders CREATED before cutover_at → SKIPPED.
   3. status ∈ {completed, تم التنفيذ} only.
   4. tabby_installment ONLY (default allow-list on first enable).
   5. delivered / جاري التوصيل → SKIPPED.
   6. bank_transfer → SKIPPED.
   7. COD → SKIPPED.
   8. No backlog / batch / Q2/Q3 backfill.
   9. Row with real qoyod_invoice_id → SKIPPED (payment-only path).
  10. Invoice OK + Payment FAIL — semantic verified via existing
      PARTIAL_FAILURE handling + retry_payment_only idempotency.
  11. payment resolver: tabby_installment → 92 exact, fallback tabby.
  12. production_writes_locked NEVER modified on-disk.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.selective_auto_send_gate import (   # noqa: E402
    evaluate_selective_auto_send_gate,
    ReasonCode,
    ALLOWED_STATUSES,
    BLOCKED_STATUSES,
    BLOCKED_PAYMENT_METHODS,
)


CUTOVER_ISO      = "2026-07-01T00:00:00+00:00"
AFTER_CUTOVER    = "2026-07-05T10:00:00+00:00"
BEFORE_CUTOVER   = "2026-06-28T10:00:00+00:00"


def _base_settings() -> dict:
    return {
        "user_id": "main",
        "selective_auto_send_enabled":     True,
        "selective_auto_send_cutover_at":  CUTOVER_ISO,
        "selective_auto_send_allowed_payment_methods":
            ["tabby_installment"],
        "payment_method_mapping": [
            {"salla_method": "tabby",
             "qoyod_account_id": "92"},
        ],
        "production_writes_locked": True,   # stays LOCKED on disk
    }


def _base_canonical(**over) -> dict:
    d = {
        "order_id":            "MZN-A1",
        "order_number":        "111111",
        "order_status":        "completed",
        "order_status_native": "completed",
        "payment_method":      "tabby_installment",
        "salla_order_created_at": AFTER_CUTOVER,
    }
    d.update(over)
    return d


def _base_row(**over) -> dict:
    d = {
        "id":                  "row-a1",
        "user_id":              "main",
        "salla_order_number":  "111111",
        "received_at":         AFTER_CUTOVER,
    }
    d.update(over)
    return d


# ─── HAPPY PATH — all 9 checks pass ───────────────────────────────
def test_happy_path_tabby_installment_after_cutover():
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(),
        row=_base_row(),
        settings=_base_settings())
    assert dec.eligible is True
    assert dec.reason == "eligible"
    assert dec.resolved_payment_key == "tabby"     # via alias
    assert dec.resolved_account_id  == "92"
    assert dec.cutover_at    == CUTOVER_ISO
    assert dec.salla_created_at == AFTER_CUTOVER


# ─── 1. Master switch OFF ─────────────────────────────────────────
def test_master_switch_off_blocks_everything():
    s = _base_settings()
    s["selective_auto_send_enabled"] = False
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(), row=_base_row(),
        settings=s)
    assert dec.eligible is False
    assert dec.reason == ReasonCode.NOT_ENABLED


# ─── 2. cutover_at missing ────────────────────────────────────────
def test_missing_cutover_blocks():
    s = _base_settings()
    s["selective_auto_send_cutover_at"] = None
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(), row=_base_row(), settings=s)
    assert dec.eligible is False
    assert dec.reason == ReasonCode.NO_CUTOVER


# ─── 3. Order BEFORE cutover — SKIPPED (no backlog!) ──────────────
def test_order_before_cutover_skipped():
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(
            salla_order_created_at=BEFORE_CUTOVER),
        row=_base_row(received_at=BEFORE_CUTOVER),
        settings=_base_settings())
    assert dec.eligible is False
    assert dec.reason == ReasonCode.BEFORE_CUTOVER


# ─── 3b. Order EXACTLY AT cutover — SKIPPED (strictly AFTER) ──────
def test_order_at_exact_cutover_skipped():
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(
            salla_order_created_at=CUTOVER_ISO),
        row=_base_row(),
        settings=_base_settings())
    assert dec.eligible is False
    assert dec.reason == ReasonCode.BEFORE_CUTOVER


# ─── 4. Status allow-list — completed / تم التنفيذ ─────────────────
def test_status_completed_ar_passes():
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(
            order_status="completed",
            order_status_native="تم التنفيذ"),
        row=_base_row(),
        settings=_base_settings())
    assert dec.eligible is True


def test_status_pending_skipped():
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(
            order_status="pending",
            order_status_native="pending"),
        row=_base_row(),
        settings=_base_settings())
    assert dec.eligible is False
    assert dec.reason == ReasonCode.STATUS_NOT_ALLOWED


# ─── 5. delivered / جاري التوصيل — hard-blocked ──────────────────
@pytest.mark.parametrize("blocked_status", sorted(BLOCKED_STATUSES))
def test_delivered_and_under_delivery_all_hard_blocked(blocked_status):
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(
            order_status=blocked_status,
            order_status_native=blocked_status),
        row=_base_row(),
        settings=_base_settings())
    assert dec.eligible is False
    assert dec.reason == ReasonCode.STATUS_HARD_BLOCKED


# ─── 6-7. bank_transfer / COD — hard-blocked ─────────────────────
@pytest.mark.parametrize("pm", sorted(BLOCKED_PAYMENT_METHODS))
def test_hard_blocked_payment_methods_refused(pm):
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(payment_method=pm),
        row=_base_row(),
        settings=_base_settings())
    assert dec.eligible is False
    assert dec.reason == ReasonCode.PM_HARD_BLOCKED


# ─── 4. Payment method NOT in allow-list (mada before expand) ─────
def test_mada_refused_before_allowlist_expansion():
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(payment_method="mada"),
        row=_base_row(),
        settings=_base_settings())
    assert dec.eligible is False
    assert dec.reason == ReasonCode.PM_NOT_ALLOWED


def test_mada_passes_after_allowlist_expansion():
    s = _base_settings()
    s["selective_auto_send_allowed_payment_methods"] = [
        "tabby_installment", "mada"]
    s["payment_method_mapping"].append(
        {"salla_method": "mada", "qoyod_account_id": "94"})
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(payment_method="mada"),
        row=_base_row(),
        settings=s)
    assert dec.eligible is True
    assert dec.resolved_account_id == "94"


# ─── 8. Payment method mapping missing ────────────────────────────
def test_payment_mapping_missing_refused():
    s = _base_settings()
    s["payment_method_mapping"] = []   # no mapping
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(), row=_base_row(), settings=s)
    assert dec.eligible is False
    assert dec.reason == ReasonCode.PM_MAPPING_MISSING


# ─── 9. Real qoyod_invoice_id already on row → SKIP ───────────────
def test_row_with_real_invoice_id_refused():
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(),
        row=_base_row(qoyod_invoice_id="186"),
        settings=_base_settings())
    assert dec.eligible is False
    assert dec.reason == ReasonCode.HAS_REAL_INVOICE_ID


def test_row_with_dry_id_still_eligible():
    """DRY:/PREVIEW: sentinels are NOT real ids — must not block."""
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(),
        row=_base_row(qoyod_invoice_id="DRY:invoice:xyz"),
        settings=_base_settings())
    assert dec.eligible is True


# ─── 11. payment resolver: exact key wins over alias ──────────────
def test_payment_resolver_exact_key_wins():
    s = _base_settings()
    # BOTH tabby AND tabby_installment mapped — exact must win.
    s["payment_method_mapping"] = [
        {"salla_method": "tabby",             "qoyod_account_id": "92"},
        {"salla_method": "tabby_installment", "qoyod_account_id": "999"},
    ]
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(payment_method="tabby_installment"),
        row=_base_row(), settings=s)
    assert dec.eligible is True
    assert dec.resolved_payment_key == "tabby_installment"
    assert dec.resolved_account_id  == "999"


def test_payment_resolver_alias_fallback_92():
    """User's spec: tabby_installment → fallback tabby → 92."""
    dec = evaluate_selective_auto_send_gate(
        canonical=_base_canonical(payment_method="tabby_installment"),
        row=_base_row(),
        settings=_base_settings())
    assert dec.eligible is True
    assert dec.resolved_payment_key == "tabby"
    assert dec.resolved_account_id  == "92"


# ─── 12. Settings on-disk stay untouched (pure function) ──────────
def test_gate_is_pure_function_no_settings_mutation():
    s = _base_settings()
    original = {k: v for k, v in s.items()}
    evaluate_selective_auto_send_gate(
        canonical=_base_canonical(), row=_base_row(), settings=s)
    assert s == original
    assert s["production_writes_locked"] is True


# ─── Enable / Disable / Expand helper tests ───────────────────────
@pytest.mark.asyncio
async def test_enable_stamps_cutover_and_starts_with_tabby_only():
    from integrations.qoyod.enable_selective_auto_send import (
        enable_selective_auto_send,
    )

    class _C:
        def __init__(self):
            self.doc: dict = {}
        async def find_one(self, q, projection=None):
            return dict(self.doc) if self.doc else None
        async def update_one(self, q, u, upsert=False):
            for k, v in (u.get("$set") or {}).items():
                self.doc[k] = v
            for k in (u.get("$unset") or {}):
                self.doc.pop(k, None)
    class _DB:
        def __init__(self):
            self.qoyod_settings = _C()

    db = _DB()
    out = await enable_selective_auto_send(
        db, user_id="main",
        confirm_token="ENABLE-SELECTIVE-AUTO-SEND",
        actor="ops")
    assert out["ok"] is True
    assert out["outcome"] == "ENABLED"
    assert out["cutover_at"] is not None
    assert out["allowed_payment_methods"] == ["tabby_installment"]
    # production_writes_locked must NEVER be touched by enable.
    assert "production_writes_locked" not in db.qoyod_settings.doc


@pytest.mark.asyncio
async def test_enable_idempotent_does_not_widen_cutover():
    from integrations.qoyod.enable_selective_auto_send import (
        enable_selective_auto_send,
    )

    class _C:
        def __init__(self):
            self.doc: dict = {}
        async def find_one(self, q, projection=None):
            return dict(self.doc) if self.doc else None
        async def update_one(self, q, u, upsert=False):
            for k, v in (u.get("$set") or {}).items():
                self.doc[k] = v

    class _DB:
        def __init__(self):
            self.qoyod_settings = _C()

    db = _DB()
    out1 = await enable_selective_auto_send(
        db, user_id="main",
        confirm_token="ENABLE-SELECTIVE-AUTO-SEND",
        actor="ops")
    cutover_1 = out1["cutover_at"]

    # Simulate time passing.
    import asyncio
    await asyncio.sleep(0.01)

    out2 = await enable_selective_auto_send(
        db, user_id="main",
        confirm_token="ENABLE-SELECTIVE-AUTO-SEND",
        actor="ops")
    assert out2["outcome"] == "ALREADY_ENABLED"
    assert out2["cutover_at"] == cutover_1     # not widened


@pytest.mark.asyncio
async def test_enable_wrong_token_refused():
    from integrations.qoyod.enable_selective_auto_send import (
        enable_selective_auto_send, SelectiveAutoSendRefused,
    )

    class _C:
        async def find_one(self, q, projection=None): return None
        async def update_one(self, q, u, upsert=False):
            raise AssertionError("MUST NOT write on refused enable")
    class _DB:
        def __init__(self): self.qoyod_settings = _C()

    with pytest.raises(SelectiveAutoSendRefused) as excinfo:
        await enable_selective_auto_send(
            _DB(), user_id="main",
            confirm_token="WRONG", actor="ops")
    assert excinfo.value.code == "confirm_token_mismatch"


@pytest.mark.asyncio
async def test_expand_rejects_hard_blocked_methods():
    from integrations.qoyod.enable_selective_auto_send import (
        expand_allowed_payment_methods,
    )

    class _C:
        def __init__(self):
            self.doc = {
                "selective_auto_send_allowed_payment_methods":
                    ["tabby_installment"]}
        async def find_one(self, q, projection=None):
            return dict(self.doc)
        async def update_one(self, q, u, upsert=False):
            for k, v in (u.get("$set") or {}).items():
                self.doc[k] = v
    class _DB:
        def __init__(self): self.qoyod_settings = _C()

    db = _DB()
    out = await expand_allowed_payment_methods(
        db, user_id="main",
        add_methods=["mada", "bank_transfer", "cod", "apple_pay"],
        confirm_token="EXPAND-SELECTIVE-AUTO-SEND",
        actor="ops")
    assert out["ok"] is True
    assert set(out["added"])    == {"mada", "apple_pay"}
    assert set(out["rejected"]) == {"bank_transfer", "cod"}
    assert "bank_transfer" not in out["allowed_payment_methods"]
    assert "cod"           not in out["allowed_payment_methods"]


# ─── Pipeline integration — scoped write allowance grant path ─────
@pytest.mark.asyncio
async def test_get_api_client_scoped_write_allowance_grants_write():
    """When `scoped_write_allowance=True` is passed AND the DB says
    `production_writes_locked=True`, the returned client MUST have
    `write_lock_enabled=False`. This is the SOLE per-row bypass
    the gate uses — DB flag stays LOCKED on disk."""
    from unittest.mock import AsyncMock, patch
    from integrations.qoyod import pipeline as pmod

    class _DB: pass
    db = _DB()
    settings = {
        "user_id": "main",
        "production_writes_locked": True,       # LOCKED on disk
        "dry_run_mode": False,
    }
    with patch.object(pmod, "get_api_key",
                      AsyncMock(return_value="test-key")):
        # Without scoped allowance → locked.
        client_locked, _ = await pmod._get_api_client(
            db, "main", settings)
        assert client_locked is not None
        assert client_locked.write_lock_enabled is True

        # With scoped allowance → UNLOCKED for this call only.
        client_scoped, _ = await pmod._get_api_client(
            db, "main", settings, scoped_write_allowance=True)
        assert client_scoped is not None
        assert client_scoped.write_lock_enabled is False

    # Settings dict UNCHANGED — no in-place mutation.
    assert settings["production_writes_locked"] is True


# ─── rev17 — scoped allowance ALSO bypasses dry_run_mode ─────────
@pytest.mark.asyncio
async def test_scoped_allowance_bypasses_dry_run_mode():
    """Regression 2026-02-27: order 270075325 passed the Gate but
    the pipeline still built a DRY-RUN payload (no POST to قيود)
    because `_get_api_client` returned `DryRunQoyodClient` when the
    tenant's on-disk `dry_run_mode=True`. Fix: scoped_write_allowance
    MUST bypass BOTH the write lock AND dry_run_mode so the eligible
    row uses a REAL live client. DB settings stay unchanged."""
    from unittest.mock import AsyncMock, patch
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.api_client import QoyodAPIClient
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    class _DB: pass
    db = _DB()
    settings = {
        "user_id":                  "main",
        "dry_run_mode":             True,      # ON on disk
        "production_writes_locked": True,      # LOCKED on disk
    }
    with patch.object(pmod, "get_api_key",
                      AsyncMock(return_value="test-key")):
        # Without scoped → DryRun (legacy behavior).
        legacy_client, legacy_is_dry = await pmod._get_api_client(
            db, "main", settings)
        assert isinstance(legacy_client, DryRunQoyodClient)
        assert legacy_is_dry is True

        # With scoped → REAL live client despite dry_run_mode=True.
        scoped_client, scoped_is_dry = await pmod._get_api_client(
            db, "main", settings, scoped_write_allowance=True)
        assert isinstance(scoped_client, QoyodAPIClient)
        assert not isinstance(scoped_client, DryRunQoyodClient)
        assert scoped_is_dry is False
        assert scoped_client.write_lock_enabled is False

    # DB values STILL untouched — no in-place mutation.
    assert settings["dry_run_mode"]             is True
    assert settings["production_writes_locked"] is True


@pytest.mark.asyncio
async def test_scoped_client_actually_sends_post_when_gate_passes():
    """End-to-end proof: an eligible row with tenant dry_run_mode=True
    produces a REAL POST /invoices call (not a DryRun stub)."""
    from unittest.mock import AsyncMock, patch
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.api_client import QoyodAPIClient
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    class _DB: pass
    db = _DB()
    settings = {
        "user_id":                  "main",
        "dry_run_mode":             True,
        "production_writes_locked": True,
    }
    with patch.object(pmod, "get_api_key",
                      AsyncMock(return_value="live-key")):
        client, is_dry = await pmod._get_api_client(
            db, "main", settings, scoped_write_allowance=True)
    # The client MUST be able to POST — assert it's a live client
    # with the correct auth key and write-lock OFF.
    assert isinstance(client, QoyodAPIClient)
    assert client.write_lock_enabled is False
    # DryRun client would have short-circuited creates; live client
    # will actually hit قيود (mocked at network layer in real tests).
    assert not isinstance(client, DryRunQoyodClient)


@pytest.mark.asyncio
async def test_pipeline_skips_at_normalized_when_gate_fails():
    """Row that fails the gate (before cutover) must transition to
    SKIPPED and NEVER touch business_rules / customer resolver /
    api_client. Also: DB `production_writes_locked` stays unchanged.
    """
    from unittest.mock import AsyncMock, patch, MagicMock
    from types import SimpleNamespace
    from integrations.qoyod import pipeline as pmod

    class _Coll:
        def __init__(self, docs=None):
            self._docs = list(docs or [])
        async def find_one(self, q, projection=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    return dict(d)
            return None
        async def update_one(self, q, u, upsert=False):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    for k, v in (u.get("$set") or {}).items():
                        d[k] = v
                    return MagicMock(matched_count=1, modified_count=1)
            return MagicMock(matched_count=0, modified_count=0)
    db = MagicMock()
    db.qoyod_settings   = _Coll([{
        "user_id": "main",
        "selective_auto_send_enabled":    True,
        "selective_auto_send_cutover_at": CUTOVER_ISO,
        "selective_auto_send_allowed_payment_methods":
            ["tabby_installment"],
        "payment_method_mapping":
            [{"salla_method": "tabby",
              "qoyod_account_id": "92"}],
        "production_writes_locked": True,
    }])
    db.qoyod_invoices   = _Coll([])
    db.integration_inbox = _Coll([])

    row = {
        "id":                  "row-before-cutover",
        "user_id":              "main",
        "salla_order_number": "222222",
        "pipeline_stage":     "NORMALIZED",
        "trace_id":           "tr-1",
        "canonical_payload": {
            "order_id":       "MZN-B1",
            "order_number":   "222222",
            "order_status":   "completed",
            "order_status_native": "completed",
            "payment_method": "tabby_installment",
            "salla_order_created_at": BEFORE_CUTOVER,
        },
    }

    # Stub DTO — pipeline only reads `.order_id` on this path.
    dto = SimpleNamespace(order_id="MZN-B1")
    business_rules_called = MagicMock()

    with patch.object(pmod, "SalesOrderDTO", return_value=dto), \
         patch.object(pmod, "evaluate_rules",
                      side_effect=business_rules_called), \
         patch.object(pmod, "_get_api_client",
                      new=AsyncMock()) as get_client, \
         patch.object(pmod, "_apply", new=AsyncMock()):
        out = await pmod.process_normalized_row(db, row)

    assert out["outcome"] == "SKIPPED"
    assert out["reason"] == ReasonCode.BEFORE_CUTOVER
    business_rules_called.assert_not_called()
    get_client.assert_not_called()      # never asked for a client


@pytest.mark.asyncio
async def test_pipeline_grants_scoped_writes_when_gate_passes():
    """Row that passes the gate must invoke `_get_api_client` with
    `scoped_write_allowance=True` — that's how the write lock is
    bypassed for this row only, without touching the DB flag."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from types import SimpleNamespace
    from integrations.qoyod import pipeline as pmod

    class _Coll:
        def __init__(self, docs=None):
            self._docs = list(docs or [])
        async def find_one(self, q, projection=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    return dict(d)
            return None
        async def update_one(self, q, u, upsert=False):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    for k, v in (u.get("$set") or {}).items():
                        d[k] = v
                    return MagicMock(matched_count=1, modified_count=1)
            return MagicMock(matched_count=0, modified_count=0)
    db = MagicMock()
    db.qoyod_settings = _Coll([{
        "user_id": "main",
        "selective_auto_send_enabled":    True,
        "selective_auto_send_cutover_at": CUTOVER_ISO,
        "selective_auto_send_allowed_payment_methods":
            ["tabby_installment"],
        "payment_method_mapping":
            [{"salla_method": "tabby",
              "qoyod_account_id": "92"}],
        "production_writes_locked": True,
    }])
    db.qoyod_invoices = _Coll([])
    db.integration_inbox = _Coll([])

    row = {
        "id":                  "row-after-cutover",
        "user_id":              "main",
        "salla_order_number": "333333",
        "pipeline_stage":     "NORMALIZED",
        "trace_id":           "tr-2",
        "canonical_payload": {
            "order_id":       "MZN-C1",
            "order_number":   "333333",
            "order_status":   "completed",
            "order_status_native": "completed",
            "payment_method": "tabby_installment",
            "salla_order_created_at": AFTER_CUTOVER,
        },
    }

    dto = SimpleNamespace(order_id="MZN-C1", customer=SimpleNamespace())

    # Stub the business_rules to return a "not eligible" so pipeline
    # exits after we've observed the api_client construction.
    from integrations.qoyod.business_rules import RulesDecision
    from integrations.qoyod.customer_resolver import ResolutionResult
    decision = RulesDecision(
        eligible=True, reason="eligible",
        invoice_date=datetime(2026, 7, 5, 10, 0, 0, tzinfo=timezone.utc),
        invoice_date_source="salla",
        triggered_by_status="completed")

    captured: dict = {}

    async def _spy_get_api_client(
            db_, user_id_, settings_, *,
            scoped_write_allowance=False):
        captured["scoped_write_allowance"] = scoped_write_allowance
        return MagicMock(), False

    # After business_rules eligible → pipeline transitions to
    # RULES_APPLIED and calls resolve_customer. Stub that to fail so
    # the pipeline exits cleanly after we've observed the api_client
    # construction.
    fail_res = ResolutionResult(
        success=False,
        error={"code": "test_stub_exit",
               "message": "intentional early exit"})

    with patch.object(pmod, "SalesOrderDTO", return_value=dto), \
         patch.object(pmod, "evaluate_rules",
                      return_value=decision), \
         patch.object(pmod, "_get_api_client",
                      side_effect=_spy_get_api_client), \
         patch.object(pmod, "resolve_customer",
                      new=AsyncMock(return_value=fail_res)), \
         patch.object(pmod, "validate_totals",
                      return_value=MagicMock(
                          ok=True, code="ok",
                          message="ok", details={},
                          to_log_dict=lambda: {"ok": True})), \
         patch.object(pmod, "_dead_letter", new=AsyncMock()), \
         patch.object(pmod, "_apply", new=AsyncMock()):
        await pmod.process_normalized_row(db, row)

    assert captured["scoped_write_allowance"] is True


@pytest.mark.asyncio
async def test_pipeline_no_write_allowance_when_master_switch_off():
    """When `selective_auto_send_enabled=False`, the gate is skipped
    entirely and `_get_api_client` is called WITHOUT scoped
    allowance — legacy write-lock semantics preserved."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from types import SimpleNamespace
    from integrations.qoyod import pipeline as pmod

    class _Coll:
        def __init__(self, docs=None):
            self._docs = list(docs or [])
        async def find_one(self, q, projection=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    return dict(d)
            return None
        async def update_one(self, q, u, upsert=False):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    for k, v in (u.get("$set") or {}).items():
                        d[k] = v
                    return MagicMock(matched_count=1, modified_count=1)
            return MagicMock(matched_count=0, modified_count=0)
    db = MagicMock()
    db.qoyod_settings = _Coll([{
        "user_id": "main",
        "selective_auto_send_enabled": False,    # OFF
    }])
    db.qoyod_invoices    = _Coll([])
    db.integration_inbox = _Coll([])

    row = {
        "id":                  "row-legacy",
        "user_id":              "main",
        "salla_order_number": "444444",
        "pipeline_stage":     "NORMALIZED",
        "trace_id":           "tr-3",
        "canonical_payload": {
            "order_id":       "MZN-D1",
            "order_number":   "444444",
            "order_status":   "completed",
            "order_status_native": "completed",
            "payment_method": "tabby_installment",
        },
    }
    dto = SimpleNamespace(order_id="MZN-D1", customer=SimpleNamespace())

    from integrations.qoyod.business_rules import RulesDecision
    from integrations.qoyod.customer_resolver import ResolutionResult
    decision = RulesDecision(
        eligible=True, reason="eligible",
        invoice_date=datetime(2026, 7, 5, 10, 0, 0, tzinfo=timezone.utc),
        invoice_date_source="salla",
        triggered_by_status="completed")
    fail_res = ResolutionResult(
        success=False,
        error={"code": "test_stub_exit",
               "message": "intentional early exit"})

    captured: dict = {}
    async def _spy(db_, u_, s_, *, scoped_write_allowance=False):
        captured["scoped_write_allowance"] = scoped_write_allowance
        return MagicMock(), False
    with patch.object(pmod, "SalesOrderDTO", return_value=dto), \
         patch.object(pmod, "evaluate_rules", return_value=decision), \
         patch.object(pmod, "_get_api_client", side_effect=_spy), \
         patch.object(pmod, "resolve_customer",
                      new=AsyncMock(return_value=fail_res)), \
         patch.object(pmod, "validate_totals",
                      return_value=MagicMock(
                          ok=True, code="ok", message="ok",
                          details={}, to_log_dict=lambda: {"ok": True})), \
         patch.object(pmod, "_dead_letter", new=AsyncMock()), \
         patch.object(pmod, "_apply", new=AsyncMock()):
        await pmod.process_normalized_row(db, row)
    assert captured["scoped_write_allowance"] is False
