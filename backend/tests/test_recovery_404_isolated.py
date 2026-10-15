"""Offline contract tests: stdlib only; no env, DB, HTTP, or production imports.

Run from repository root: python backend/tests/test_recovery_404_isolated.py
"""
import asyncio
from dataclasses import replace
from datetime import date
import importlib.util
from pathlib import Path
import sys
import unittest

MODULE = Path(__file__).resolve().parents[1] / "integrations/qoyod_manual/recovery_404.py"
spec = importlib.util.spec_from_file_location("isolated_recovery", MODULE)
r = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r
spec.loader.exec_module(r)

SCOPE = r.Scope(("100", "101", "102"), ("102",), date(2026, 7, 1),
                date(2026, 9, 19), "reviewed-release")
FACTS = r.Facts("100", date(2026, 8, 4), "completed", True, False, True,
                "120.00", "120.00", "SAR", True, 404, "GET", "/products", True, True)
INVOICE = r.Invoice("100", "test-invoice", "120.00", "120.00", "0.00", "SAR")


class FakePorts:
    """Boundary fake; shared claims simulate durable atomic store across workers."""
    def __init__(self):
        self.active = True
        self.current_facts = FACTS
        self.invoices = ()
        self.claims = set()
        self.results = []
        self.sends = 0
        self.repairs = 0
        self.marker = None
        self.timeout = False
        self.complete = True
        self.fresh = True
        self.revoke_on_claim = False
        self.fact_reads = 0
        self.cancel_after_claim = False
        self.fail_finish = False

    async def authorized(self, fingerprint):
        return self.active and fingerprint == SCOPE.fingerprint

    async def facts(self, reference):
        self.fact_reads += 1
        if self.cancel_after_claim and self.claims:
            return replace(self.current_facts, status="cancelled")
        return self.current_facts

    async def observe(self, reference):
        return r.Observation(self.invoices, self.complete, self.fresh,
                             self.marker, bool(self.marker))

    async def claim_once(self, reference, fingerprint):
        if reference in self.claims:
            return False
        self.claims.add(reference)
        if self.revoke_on_claim:
            self.active = False
        return True

    async def has_claim(self, reference):
        return reference in self.claims

    async def send_guarded(self, reference):
        self.sends += 1
        self.invoices = (INVOICE,)
        await asyncio.sleep(0)
        if self.timeout:
            raise TimeoutError("provider may have committed")

    async def reconcile_marker(self, reference, invoice_id):
        self.repairs += 1
        self.marker = invoice_id

    async def finish(self, outcome):
        if self.fail_finish:
            raise OSError("storage unavailable")
        self.results.append(outcome)

    async def pause(self, reason):
        self.active = False


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_has_no_reads_or_writes(self):
        p = FakePorts(); p.active = False
        self.assertEqual((await r.recover_one(SCOPE, "100", p)).state, "disabled")
        self.assertEqual((p.fact_reads, p.sends, p.repairs), (0, 0, 0))

    async def test_closed_scope_and_completed_exclusion(self):
        for ref in ("999", "102"):
            p = FakePorts()
            self.assertEqual((await r.recover_one(SCOPE, ref, p)).state, "excluded")
            self.assertEqual(p.fact_reads, 0)

    async def test_changed_failure_and_deferred_classes_never_send(self):
        changes = [dict(is_cod=True), dict(skus_complete=False),
                   dict(status="cancelled"), dict(status="refunded"),
                   dict(payment_eligible=False), dict(fresh_salla=False),
                   dict(failure_status=500), dict(failure_endpoint="/invoices"),
                   dict(failure_method="POST"), dict(failure_precedes_release=False),
                   dict(quarantine_open=False), dict(currency="USD"),
                   dict(order_date=date(2026, 6, 30)),
                   dict(order_date=date(2026, 9, 20)),
                   dict(expected_invoice_total="119.98")]
        for change in changes:
            with self.subTest(change=change):
                p = FakePorts(); p.current_facts = replace(FACTS, **change)
                self.assertEqual((await r.recover_one(SCOPE, "100", p)).state, "blocked")
                self.assertEqual(p.sends, 0)
                self.assertFalse(p.claims)

    async def test_unknown_or_stale_provider_absence_is_not_permission(self):
        for key in ("complete", "fresh"):
            p = FakePorts(); setattr(p, key, False)
            result = await r.recover_one(SCOPE, "100", p)
            self.assertEqual(result.reason, "provider_reference_unknown")
            self.assertEqual(p.sends, 0)

    async def test_only_fully_verified_invoice_resolves_existing(self):
        p = FakePorts(); p.invoices = (INVOICE,)
        result = await r.recover_one(SCOPE, "100", p)
        self.assertEqual(result.state, "verified_existing")
        self.assertEqual((p.sends, p.repairs), (0, 1))

    async def test_duplicates_amount_and_payment_block_without_writes(self):
        cases = [((INVOICE, INVOICE), "duplicate_provider_invoices"),
                 ((replace(INVOICE, total="80.00", paid="80.00"),), "provider_amount_mismatch"),
                 ((replace(INVOICE, paid="100", remaining="20"),), "provider_settlement_incomplete"),
                 ((replace(INVOICE, reference="999"),), "provider_reference_mismatch")]
        for invoices, reason in cases:
            p = FakePorts(); p.invoices = invoices
            self.assertEqual((await r.recover_one(SCOPE, "100", p)).reason, reason)
            self.assertEqual((p.sends, p.repairs), (0, 0))

    async def test_new_send_requires_readback_and_marker(self):
        p = FakePorts()
        result = await r.recover_one(SCOPE, "100", p)
        self.assertEqual(result.state, "verified_sent")
        self.assertEqual((p.sends, p.repairs), (1, 1))
        self.assertEqual(result.invoice_id, INVOICE.invoice_id)

    async def test_timeout_after_commit_cannot_send_again_after_restart(self):
        p = FakePorts(); p.timeout = True
        result = await r.recover_one(SCOPE, "100", p)
        self.assertEqual(result.state, "unknown")
        self.assertFalse(p.active)
        # Restart preserves the claim, even if a new operator activation occurs.
        restarted = FakePorts(); restarted.claims = p.claims
        result = await r.recover_one(SCOPE, "100", restarted)
        self.assertEqual(result.reason, "prior_attempt_requires_reconciliation")
        self.assertEqual(restarted.sends, 0)

    async def test_parallel_workers_only_one_send(self):
        p = FakePorts()
        results = await asyncio.gather(r.recover_one(SCOPE, "100", p),
                                       r.recover_one(SCOPE, "100", p))
        self.assertEqual(p.sends, 1)
        self.assertEqual(sum(x.state == "verified_sent" for x in results), 1)

    async def test_paused_timeout_can_be_audited_without_any_write(self):
        p = FakePorts(); p.active = False; p.invoices = (INVOICE,)
        p.marker = INVOICE.invoice_id
        result = await r.audit_one(SCOPE, "100", p)
        self.assertEqual(result.state, "verified_audit")
        self.assertEqual((p.sends, p.repairs, len(p.claims)), (0, 0, 0))

    async def test_audit_absence_does_not_authorize_resend(self):
        p = FakePorts(); p.active = False
        p.claims.add("100")
        result = await r.audit_one(
            SCOPE, "100", p, allow_preclaim_requeue=True
        )
        self.assertEqual(result.reason, "submitted_invoice_not_found_do_not_retry")
        self.assertEqual(p.sends, 0)

    async def test_pre_send_read_failure_returns_to_pending_after_complete_audit(self):
        p = FakePorts(); p.active = False
        result = await r.audit_one(
            SCOPE, "100", p, allow_preclaim_requeue=True
        )
        self.assertEqual((result.state, result.reason),
                         ("pending", "pre_send_read_recovered"))
        self.assertEqual((p.sends, p.repairs, len(p.claims)), (0, 0, 0))

    async def test_sent_marker_does_not_override_wrong_paid_invoice_total(self):
        p = FakePorts(); p.marker = INVOICE.invoice_id
        p.invoices = (replace(INVOICE, total="70.00", paid="70.00"),)
        result = await r.audit_one(SCOPE, "100", p)
        self.assertEqual(result.reason, "provider_amount_mismatch")
        self.assertEqual(result.state, "review")

    async def test_revocation_and_live_cancellation_after_claim(self):
        for flag in ("revoke_on_claim", "cancel_after_claim"):
            p = FakePorts(); setattr(p, flag, True)
            result = await r.recover_one(SCOPE, "100", p)
            self.assertEqual(result.state, "blocked")
            self.assertEqual(p.sends, 0)

    async def test_marker_readback_failure_does_not_resolve(self):
        p = FakePorts()
        async def fail_marker(*args):
            pass
        p.reconcile_marker = fail_marker
        self.assertEqual((await r.recover_one(SCOPE, "100", p)).reason,
                         "mezan_marker_unverified")
        self.assertFalse(p.active)

    async def test_persistence_failure_disarms(self):
        p = FakePorts(); p.fail_finish = True
        with self.assertRaises(OSError):
            await r.recover_one(SCOPE, "100", p)
        self.assertFalse(p.active)
        self.assertIn("100", p.claims)

    async def test_unknown_stops_batch_and_invalid_limit_refused(self):
        p = FakePorts(); p.timeout = True
        results = await r.recover_batch(SCOPE, ("100", "101"), p, limit=2)
        self.assertEqual(len(results), 1)
        with self.assertRaises(ValueError):
            await r.recover_batch(SCOPE, ("100",), p, limit=6)

    def test_scope_identity_changes_when_scope_or_release_changes(self):
        self.assertNotEqual(SCOPE.fingerprint,
                            replace(SCOPE, excluded=()).fingerprint)
        self.assertNotEqual(SCOPE.fingerprint,
                            replace(SCOPE, release_identity="other").fingerprint)

    def test_invalid_money_fails_closed(self):
        for amount in ("NaN", "Infinity", "-1", "not-money"):
            with self.assertRaises(r.EvidenceError):
                r.money(amount)


if __name__ == "__main__":
    unittest.main(verbosity=2)
