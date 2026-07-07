"""Iter-2026-02.rev27 — Live-Write Gate invariants.

After order 270253311 (trace fcaa4216...) leaked REAL Qoyod invoice
#188 while the operator's settings said dry_run_mode=true and
selective_live_send_enabled=false, we enforce a STRICT unified
Live-Write Gate. The prior scoped-bypass semantics (SAS gate PASS →
real client regardless of global flags) is REVOKED.

New rule (user directive, verbatim):

  For a REAL Qoyod POST to occur, ALL of the following MUST be true:
    • selective_auto_send_enabled  = true
    • SAS gate eligible            = true
    • payment method in allow-list
    • selective_live_send_enabled  = true
    • production_writes_locked     = false
    • dry_run_mode                 = false
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")


# ── Helpers ────────────────────────────────────────────────────────
def _base_live_settings() -> dict:
    """A settings dict where LIVE writes ARE permitted."""
    return {
        "selective_auto_send_enabled":   True,
        "selective_live_send_enabled":   True,
        "production_writes_locked":      False,
        "dry_run_mode":                  False,
        "selective_auto_send_allowed_payment_methods": ["mada"],
    }


# ── Test 1: `_live_write_permitted` truth table ───────────────────
def test_1_live_write_permitted_all_gates_true():
    from integrations.qoyod.pipeline import _live_write_permitted
    ok, reason = _live_write_permitted(_base_live_settings())
    assert ok is True
    assert reason == "all_gates_permit_live_write"


def test_1b_dry_run_mode_true_blocks_live():
    from integrations.qoyod.pipeline import _live_write_permitted
    s = _base_live_settings(); s["dry_run_mode"] = True
    ok, reason = _live_write_permitted(s)
    assert ok is False and reason == "dry_run_mode_is_true"


def test_1c_live_send_disabled_blocks_live():
    from integrations.qoyod.pipeline import _live_write_permitted
    s = _base_live_settings(); s["selective_live_send_enabled"] = False
    ok, reason = _live_write_permitted(s)
    assert ok is False and reason == "selective_live_send_enabled_is_false"


def test_1d_production_writes_locked_blocks_live():
    from integrations.qoyod.pipeline import _live_write_permitted
    s = _base_live_settings(); s["production_writes_locked"] = True
    ok, reason = _live_write_permitted(s)
    assert ok is False and reason == "production_writes_locked_is_true"


def test_1e_sas_disabled_blocks_live():
    from integrations.qoyod.pipeline import _live_write_permitted
    s = _base_live_settings(); s["selective_auto_send_enabled"] = False
    ok, reason = _live_write_permitted(s)
    assert ok is False and reason == "selective_auto_send_enabled_is_false"


# ── Test 2: `_get_api_client` respects the gate ────────────────────
@pytest.mark.asyncio
async def test_2_get_api_client_returns_dry_when_dry_run_true_even_with_scoped():
    """The critical fix: `scoped_write_allowance=True` MUST NOT
    bypass `dry_run_mode=true`. This is what leaked invoice 188."""
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    s = _base_live_settings(); s["dry_run_mode"] = True
    client, is_dry = await pmod._get_api_client(
        MagicMock(), "main", s, scoped_write_allowance=True)
    assert isinstance(client, DryRunQoyodClient), (
        f"scoped bypass leaked a REAL client while dry_run_mode=true "
        f"— got {type(client).__name__}")
    assert is_dry is True


@pytest.mark.asyncio
async def test_2b_get_api_client_dry_when_live_send_disabled_with_scoped():
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    s = _base_live_settings(); s["selective_live_send_enabled"] = False
    client, is_dry = await pmod._get_api_client(
        MagicMock(), "main", s, scoped_write_allowance=True)
    assert isinstance(client, DryRunQoyodClient)
    assert is_dry is True


@pytest.mark.asyncio
async def test_2c_get_api_client_dry_when_locked_with_scoped():
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    s = _base_live_settings(); s["production_writes_locked"] = True
    client, is_dry = await pmod._get_api_client(
        MagicMock(), "main", s, scoped_write_allowance=True)
    assert isinstance(client, DryRunQoyodClient)
    assert is_dry is True


@pytest.mark.asyncio
async def test_2d_get_api_client_dry_when_scoped_missing():
    """Even with all gates permissive, no scoped ask → dry client."""
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    client, is_dry = await pmod._get_api_client(
        MagicMock(), "main", _base_live_settings(),
        scoped_write_allowance=False)
    assert isinstance(client, DryRunQoyodClient)
    assert is_dry is True


@pytest.mark.asyncio
async def test_2e_get_api_client_live_ONLY_when_all_gates_open_and_scoped():
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.api_client import QoyodAPIClient

    db = MagicMock()
    with patch.object(pmod, "get_api_key",
                      new=AsyncMock(return_value="test-key")):
        client, is_dry = await pmod._get_api_client(
            db, "main", _base_live_settings(),
            scoped_write_allowance=True)
    assert isinstance(client, QoyodAPIClient)
    assert is_dry is False
    assert client.write_lock_enabled is False


# ── Test 3-5: End-to-end pipeline honours dry-run for SAS-eligible ─
CUTOVER_ISO   = "2026-07-01T00:00:00+00:00"
AFTER_CUTOVER = "2026-07-05T10:00:00+00:00"


def _tabby_canonical():
    return {
        "order_id":              "MZN-tabby-01",
        "order_number":          "990001",
        "order_status":          "completed",
        "order_status_native":   "completed",
        "order_date":            AFTER_CUTOVER,
        "payment_method":        "mada",
        "payment_method_native": "mada",
        "currency":              "SAR",
        "subtotal":              226.94,
        "tax_amount":            34.04,
        "total_amount":          260.98,
        "items": [{
            "sku":         "SKU-T",
            "name":        "T",
            "quantity":    1,
            "unit_price":  226.94,
            "tax_amount":  34.04,
            "total":       260.98,
        }],
        "customer": {"name": "T", "phone": "+966500000000"},
    }


class _Coll:
    def __init__(self, docs): self._docs = list(docs)
    async def find_one(self, q, projection=None):
        for d in self._docs:
            if all(d.get(k) == v for k, v in q.items()): return dict(d)
        return None
    async def update_one(self, q, u, upsert=False):
        matched = modified = 0
        for d in self._docs:
            if all(d.get(k) == v for k, v in q.items()):
                matched = 1
                for k, v in (u.get("$set") or {}).items(): d[k] = v
                for k in (u.get("$unset") or {}): d.pop(k, None)
                for k, v in (u.get("$push") or {}).items():
                    arr = d.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        arr.extend(v["$each"])
                    else: arr.append(v)
                modified = 1; break
        return MagicMock(matched_count=matched, modified_count=modified)
    async def insert_one(self, d):
        self._docs.append(d); return MagicMock(inserted_id="x")
    def find(self, q=None, **kw):
        class _C:
            def __init__(self, ds): self.ds = ds
            def __aiter__(self):
                async def _g():
                    for d in self.ds: yield d
                return _g()
        return _C(list(self._docs))


def _dry_settings_full() -> dict:
    return {
        "user_id":                              "main",
        "selective_auto_send_enabled":          True,
        "selective_auto_send_cutover_at":       CUTOVER_ISO,
        "selective_auto_send_allowed_payment_methods":
            ["mada"],
        "selective_live_send_enabled":          False,  # ← Phase 2 style
        "production_writes_locked":             False,
        "dry_run_mode":                         True,   # ← operator kept dry
        "payment_method_mapping": [
            {"salla_method":            "mada",
             "qoyod_account_id":        "92",
             "qoyod_payment_method_id": "92"},
        ],
        "default_customer_id":      "230",
        "invoice_trigger_statuses": ["completed"],
        "invoice_date_source":      "send_date",
        "auto_receipt":             True,
        "capabilities":             {"create_receipts": True},
        "trigger_once_only":        True,
    }


@pytest.mark.asyncio
async def test_3_tabby_sas_eligible_dry_run_produces_no_real_writes():
    """The exact regression from order 270253311: tabby is SAS-eligible,
    dry_run_mode=true, selective_live_send=false. The pipeline MUST
    NOT create a real customer / invoice / payment. All ids must be
    DRY:*."""
    from integrations.qoyod import pipeline as pmod
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    row = {
        "id":                     "row-tabby-1",
        "user_id":                "main",
        "salla_order_number":     "990001",
        "trace_id":               "tr-tabby-1",
        "pipeline_stage":         "CUSTOMER_RESOLVED",
        "qoyod_customer_id":      "DRY:contact:test1",
        "canonical_payload":      _tabby_canonical(),
        "business_rules_decision": {
            "eligible": True, "invoice_date": AFTER_CUTOVER,
            "invoice_date_source": "salla",
            "triggered_by_status": "completed",
        },
        "pipeline_started_at":    AFTER_CUTOVER,
        "stage_history":          [],
    }
    db = MagicMock()
    db.qoyod_settings         = _Coll([_dry_settings_full()])
    db.integration_inbox      = _Coll([dict(row)])
    db.qoyod_invoices         = _Coll([])
    db.qoyod_invoice_payments = _Coll([])
    db.qoyod_write_lock_attempts = _Coll([])

    with patch.object(pmod, "get_api_key",
                      new=AsyncMock(return_value="key")):
        client, is_dry = await pmod._get_api_client(
            db, "main", _dry_settings_full(),
            scoped_write_allowance=True)

    # The single most important assertion — the exact fix.
    assert isinstance(client, DryRunQoyodClient), (
        f"regression: `scoped_write_allowance=True` returned a REAL "
        f"client while dry_run_mode=true — order 270253311 leak repro")
    assert is_dry is True


# ── Test 6: diagnostics detects live_write_gate_violation ──────────
@pytest.mark.asyncio
async def test_6_diagnostics_reports_live_write_gate_violation():
    """A row with real qoyod_invoice_id AND settings=dry_run_mode=true
    → diagnosis.live_write_gate_violation must be True with a clear
    reason. This is what will surface for order 270253311 after deploy.
    """
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics

    fake_row = {
        "id":              "row-270253311",
        "trace_id":        "fcaa42165b2a45818c7284a89d1d999c",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id":  "233",
        "qoyod_invoice_id":   "188",     # ← REAL id
        "canonical_payload":  {"payment_method": "mada"},
        "selective_auto_send_gate": {"eligible": True, "reason": "eligible"},
    }
    fake_settings = {
        "dry_run_mode":                 True,
        "selective_live_send_enabled":  False,
        "production_writes_locked":     False,
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=fake_row)
    db.qoyod_settings.find_one   = AsyncMock(return_value=fake_settings)

    out = await row_diagnostics(db, fake_row["trace_id"])

    assert out["ok"] is True
    assert out["diagnosis"]["qoyod_invoice_id_is_real"] is True
    assert out["diagnosis"]["live_write_gate_violation"] is True
    reason = out["diagnosis"]["live_write_violation_reason"]
    assert reason is not None
    assert "invoice" in reason
    assert "dry_run_mode=true" in reason
    assert "selective_live_send_enabled=false" in reason


@pytest.mark.asyncio
async def test_7_diagnostics_no_violation_when_ids_dry_and_flags_dry():
    """Sanity: DRY ids + dry settings → no violation."""
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    fake_row = {
        "id":              "row-dry",
        "trace_id":        "tr-dry",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id":  "DRY:contact:abc",
        "qoyod_invoice_id":   "DRY:invoice:def",
        "canonical_payload":  {"payment_method": "mada"},
        "selective_auto_send_gate": {"eligible": True, "reason": "eligible"},
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=fake_row)
    out = await row_diagnostics(db, "tr-dry")
    assert out["diagnosis"]["live_write_gate_violation"] is False
    assert out["diagnosis"]["live_write_violation_reason"] is None


@pytest.mark.asyncio
async def test_8_diagnostics_no_violation_when_all_gates_open_for_real_id():
    """Real ids + fully-permissive settings → no violation (this is
    the happy live path)."""
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    fake_row = {
        "id":              "row-live-ok",
        "trace_id":        "tr-live-ok",
        "pipeline_stage":  "COMPLETED",
        "qoyod_customer_id":       "233",
        "qoyod_invoice_id":        "188",
        "qoyod_invoice_payment_id": "159",
        "canonical_payload":       {"payment_method": "mada"},
        "selective_auto_send_gate": {"eligible": True, "reason": "eligible"},
    }
    fake_settings = {
        "dry_run_mode":                 False,
        "selective_live_send_enabled":  True,
        "production_writes_locked":     False,
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=fake_row)
    db.qoyod_settings.find_one   = AsyncMock(return_value=fake_settings)
    out = await row_diagnostics(db, "tr-live-ok")
    assert out["diagnosis"]["live_write_gate_violation"] is False
    assert out["diagnosis"]["qoyod_invoice_id_is_real"] is True


@pytest.mark.asyncio
async def test_9_diagnostics_detects_production_writes_locked_violation():
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    fake_row = {
        "id":              "row-lock-leak",
        "trace_id":        "tr-lock-leak",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_invoice_id":  "999",     # real
        "canonical_payload": {"payment_method": "mada"},
        "selective_auto_send_gate": {"eligible": True, "reason": "eligible"},
    }
    fake_settings = {
        "dry_run_mode":                 False,
        "selective_live_send_enabled":  True,
        "production_writes_locked":     True,   # ← locked but leaked
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=fake_row)
    db.qoyod_settings.find_one   = AsyncMock(return_value=fake_settings)
    out = await row_diagnostics(db, "tr-lock-leak")
    assert out["diagnosis"]["live_write_gate_violation"] is True
    assert "production_writes_locked=true" in \
        out["diagnosis"]["live_write_violation_reason"]
