"""Iter-293.4-rev5 — Pipeline must honour the per-order approval bypass.

The Bug
───────
The operator ran `one_shot_reprocess` for order 269571122 with the
correct approval phrase. `one_shot_reprocess` built a QoyodAPIClient
with `write_lock_enabled=False` (correctly granting the per-order
bypass) and called `process_customer_resolved_row`. But the pipeline
short-circuited at `is_locked(settings)` BEFORE invoking the
api_client, parking the row at `LOCKED_AWAITING_APPROVAL` and never
sending the invoice. Result the operator saw:

    HTTP 200, ok=false, outcome=LOCKED_AWAITING_APPROVAL,
    qoyod_invoice_id=undefined, per_order_approval=undefined,
    invoice request body=undefined.

The Contract Pinned Here (SUPERSEDED by rev33 — see below)
────────────────────────
`_writes_blocked` (pure helper) still honours the supplied client as
the source of truth — those unit tests are unchanged.

rev33 UPDATE (after invoice-188 leak, user decree):
A caller-supplied LIVE api_client can NO LONGER bypass the settings
gates. `process_customer_resolved_row` re-checks
`_live_write_permitted(settings)` + the SAS gate, and when either
denies, the supplied client is FORCIBLY replaced with
`DryRunQoyodClient`. Fail-closed: no real POST ever happens while
`production_writes_locked=True`, regardless of what client the
caller constructed.

What's covered
──────────────
1. `_writes_blocked` pure-helper contract (unchanged).
2. Unlocked api_client + locked settings → rev33 forces DryRun:
   the real client's create_invoice is NEVER called; row completes
   with a DRY: invoice id only.
3. Locked api_client + locked settings → same fail-closed forcing:
   no real POST attempted.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")
os.environ.setdefault("QOYOD_API_BASE", "https://api.qoyod.test")

from integrations.qoyod.pipeline import _writes_blocked  # noqa: E402
from integrations.qoyod.api_client import QoyodAPIClient  # noqa: E402
from integrations.qoyod.invoice_builder import DryRunQoyodClient  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
class TestWritesBlockedHelper:
    """Pure logic — no DB, no async."""

    def test_unlocked_api_client_overrides_locked_settings(self):
        """The per-order approval grant materialises as an UNLOCKED
        QoyodAPIClient. Pipeline must trust THAT signal."""
        unlocked = QoyodAPIClient("fake-key", write_lock_enabled=False)
        assert _writes_blocked(unlocked,
                               {"production_writes_locked": True}) is False

    def test_locked_api_client_blocks_even_if_settings_say_unlocked(self):
        """Defense-in-depth: if someone hands us a locked client we
        honour it even when settings say writes are allowed."""
        locked = QoyodAPIClient("fake-key", write_lock_enabled=True)
        assert _writes_blocked(locked,
                               {"production_writes_locked": False}) is True

    def test_no_client_falls_back_to_settings(self):
        """Defensive fallback — preserves existing webhook entry-points
        that may call the pipeline before constructing a client."""
        assert _writes_blocked(None,
                               {"production_writes_locked": True}) is True
        assert _writes_blocked(None,
                               {"production_writes_locked": False}) is False

    def test_dry_run_client_never_blocks(self):
        """DryRunQoyodClient has no `write_lock_enabled` attribute.
        Helper must treat its absence as False (DryRun never writes
        to Qoyod anyway)."""
        dry = DryRunQoyodClient()
        assert _writes_blocked(dry,
                               {"production_writes_locked": True}) is False

    def test_api_client_property_is_read_only(self):
        """The public property is intentionally not writable — operators
        MUST construct a fresh client when changing lock state, never
        mutate an existing one."""
        c = QoyodAPIClient("fake-key", write_lock_enabled=True)
        assert c.write_lock_enabled is True
        with pytest.raises(AttributeError):
            c.write_lock_enabled = False  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────
# Integration-ish: drive the real `process_customer_resolved_row` with
# an unlocked api_client + locked settings and verify the pipeline
# reaches `create_invoice` (the proof we are NOT short-circuiting).
# ─────────────────────────────────────────────────────────────────────
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                return dict(r)
        return None
    async def insert_one(self, doc):
        self.rows.append(dict(doc))

        class _R:
            inserted_id = doc.get("id")
        return _R()

    async def update_one(self, q, upd, **_):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()
                   if not isinstance(v, dict)):
                r.update(upd.get("$set") or {})

                class _R:
                    matched_count = 1
                return _R()

        class _R:
            matched_count = 0
        return _R()


class _DB:
    def __init__(self):
        # Settings: GLOBAL LOCK IS ON.
        self.qoyod_settings = _Coll()
        self.qoyod_settings.rows.append({
            "user_id":                  "main",
            "production_writes_locked": True,
            "dry_run_mode":             False,
            "invoice_trigger_statuses": ["completed"],
            "auto_receipt":             True,
            # Iter-001k — Selective Send guard now runs first. Master
            # gate opened + trigger status opted-in for these legacy
            # per-order-unlock tests. Not production settings.
            "selective_live_send_enabled": True,
            "qoyod_enabled_invoice_trigger_statuses":
                ["completed", "تم التنفيذ",
                 "delivered", "shipping"],
            "qoyod_sync_start_date": "2020-01-01",
        })
        self.integration_inbox          = _Coll()
        self.qoyod_invoices             = _Coll()
        self.qoyod_invoice_payments     = _Coll()
        self.qoyod_write_lock_attempts  = _Coll()
        self.qoyod_per_order_approvals  = _Coll()
        self.qoyod_products_mapping     = _Coll()
        self.qoyod_customers_mapping    = _Coll()
        self.qoyod_credentials          = _Coll()
        self.qoyod_payment_method_mappings = _Coll()
        self.qoyod_branches             = _Coll()


def _customer_resolved_row():
    """A row ready for `process_customer_resolved_row`, with the
    DTO-shaped canonical_payload the pipeline expects."""
    return {
        "id":                  "row-269571122",
        "user_id":              "main",
        "trace_id":             "a8931309a65e47d3b6cfd39129f9f750",
        "salla_order_number":   "269571122",
        "salla_order_id":       "269571122",
        "pipeline_stage":       "CUSTOMER_RESOLVED",
        "qoyod_customer_id":    "REAL-1234",
        "business_rules_decision": {
            "eligible":             True,
            "triggered_by_status":  "completed",
            "invoice_date_source":  "trigger_status_date",
            "invoice_date":         "2026-02-27T10:00:00+00:00",
        },
        # rev29c/rev29d — Every CUSTOMER_RESOLVED row must carry a
        # persisted SAS gate; the preflight rejects any that don't.
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible",
            "resolved_payment_key": "cash_on_delivery",
        },
        "selective_auto_send_gate_at":     "2026-02-27T10:00:01+00:00",
        "selective_auto_send_gate_source": "sas_enabled_at_worker",
        "canonical_payload": {
            "order_id":      "269571122",
            "order_number":  "269571122",
            # Iter-001k — order_date required for sync-cutoff check.
            "order_date":    "2026-07-05",
            "order_status":  "completed",
            "customer": {
                "name": "Test", "phone": "0500000000",
                "email": "t@x.com",
            },
            "items": [{
                "sku":         "AMS10002",
                "name":        "بضاعة 1",
                "qty":         1,
                "unit_price":  186.0,
                "line_total":  186.0,
            }],
            "subtotal":      186.0,
            "tax_amount":    27.78,
            "total_amount":  213.78,
            "shipping_amount": 0.0,
            "discount_amount": 0.0,
            "payment_method": "cod",
        },
    }


@pytest.mark.asyncio
class TestPipelineHonoursPerOrderUnlock:

    async def test_unlocked_client_forced_dry_when_settings_locked(self):
        """rev33 fail-closed contract. With
        settings.production_writes_locked=True, a supplied UNLOCKED
        api_client MUST be replaced with DryRunQoyodClient — the real
        client's create_invoice is NEVER called and the row completes
        with a DRY: invoice id only. (Pre-rev33 this test asserted the
        opposite: the per-order bypass. That contract was revoked by
        user decree after the invoice-188 leak.)
        """
        db = _DB()
        row = _customer_resolved_row()
        db.integration_inbox.rows.append(row)

        unlocked_client = MagicMock()
        unlocked_client.write_lock_enabled = False
        unlocked_client.create_invoice = AsyncMock(
            return_value={"invoice": {"id": "QID-1", "number": "INV-1"}})
        unlocked_client.create_invoice_payment = AsyncMock(
            return_value={"invoice_payment": {"id": "PAY-1"}})

        from integrations.qoyod.pipeline import process_customer_resolved_row

        # Stub the resolvers / preflight so we don't need a full DB.
        from integrations.qoyod.product_resolver import (
            ProductsResolutionResult, ProductResolutionItem)
        prod_ok = ProductsResolutionResult(
            success=True,
            items=[ProductResolutionItem(sku="AMS10002",
                                         qoyod_product_id="21",
                                         created_new=False)],
        )
        with patch(
            "integrations.qoyod.pipeline.resolve_products",
            new_callable=AsyncMock, return_value=prod_ok,
        ), patch(
            "integrations.qoyod.pipeline.preflight_run",
            return_value=MagicMock(
                passed=True, to_log_dict=lambda: {"passed": True}),
        ), patch(
            "integrations.qoyod.pipeline.build_invoice_payload",
            return_value={
                "invoice": {
                    "contact_id":  "REAL-1234",
                    "issue_date":  "2026-02-27",
                    "line_items": [{
                        "product_id": "21",
                        "quantity":   1,
                        "unit_price": 186.0,
                        "description": "AMS10002",
                    }],
                    "reference":   "269571122",
                },
                "_diagnostics": {"pricing_mode": "exact_match",
                                 "difference":   0.0},
            },
        ):
            result = await process_customer_resolved_row(
                db, row, api_client=unlocked_client)

        # rev33 fail-closed proof: the REAL client was never invoked.
        assert not unlocked_client.create_invoice.called, (
            "rev33 violated: settings.production_writes_locked=True "
            "but the caller-supplied live client reached "
            "create_invoice. "
            f"actual result={result!r}")
        assert not unlocked_client.create_invoice_payment.called
        # The row completed in forced dry-run mode.
        assert result.get("outcome") == "COMPLETED"
        assert result.get("dry_run") is True
        assert str(result.get("qoyod_invoice_id", "")).startswith("DRY:")
        # Row state reflects the dry completion.
        final = await db.integration_inbox.find_one({"id": row["id"]})
        assert final["pipeline_stage"] == "COMPLETED"
        assert str(final.get("qoyod_invoice_id", "")).startswith("DRY:")

    async def test_locked_client_forced_dry_no_real_write(self):
        """rev33 — a locked client + locked settings likewise gets
        replaced with DryRunQoyodClient. No real POST is ever
        attempted; the row completes dry instead of raising
        QoyodWriteLockedError."""
        db = _DB()
        row = _customer_resolved_row()
        db.integration_inbox.rows.append(row)

        locked_client = MagicMock()
        locked_client.write_lock_enabled = True
        locked_client.create_invoice = AsyncMock(
            side_effect=AssertionError(
                "create_invoice must NOT be called when the client "
                "is locked"))

        from integrations.qoyod.pipeline import process_customer_resolved_row
        from integrations.qoyod.product_resolver import (
            ProductsResolutionResult, ProductResolutionItem)
        prod_ok = ProductsResolutionResult(
            success=True,
            items=[ProductResolutionItem(sku="AMS10002",
                                         qoyod_product_id="21",
                                         created_new=False)],
        )
        with patch(
            "integrations.qoyod.pipeline.resolve_products",
            new_callable=AsyncMock, return_value=prod_ok,
        ), patch(
            "integrations.qoyod.pipeline.preflight_run",
            return_value=MagicMock(
                passed=True, to_log_dict=lambda: {"passed": True}),
        ), patch(
            "integrations.qoyod.pipeline.build_invoice_payload",
            return_value={
                "invoice": {"contact_id": "REAL-1234",
                            "line_items": [{"product_id": "21",
                                            "quantity": 1,
                                            "unit_price": 186.0}]},
                "_diagnostics": {"pricing_mode": "exact_match",
                                 "difference":   0.0},
            },
        ):
            result = await process_customer_resolved_row(
                db, row, api_client=locked_client)

        # No real POST was attempted (rev33 forced DryRun).
        assert not locked_client.create_invoice.called
        assert result.get("outcome") == "COMPLETED"
        assert result.get("dry_run") is True
        assert str(result.get("qoyod_invoice_id", "")).startswith("DRY:")
        # No blocked-write audit row — the locked client was never used.
        assert len(db.qoyod_write_lock_attempts.rows) == 0
