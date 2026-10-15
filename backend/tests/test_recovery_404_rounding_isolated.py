"""Offline evidence-only isolation tests; no provider calls or credentials."""
from dataclasses import replace
from datetime import date
import unittest

from test_recovery_404_isolated import r, FakePorts, FACTS, SCOPE


class RoundingAuditTests(unittest.IsolatedAsyncioTestCase):
    def ports(self):
        p = FakePorts()
        p.active = False
        p.claims.add("100")
        p.current_facts = replace(FACTS, total="161.11", expected_invoice_total="161.11")
        p.invoices = (r.Invoice("100", "6472", "161.12", "161.11", "0.01", "SAR"),)
        p.marker = "6472"
        return p

    def no_writes(self, p):
        self.assertEqual((p.sends, p.repairs), (0, 0))
        self.assertEqual(p.claims, {"100"})
        self.assertFalse(p.active)

    async def test_proven_rounding_is_incomplete_and_read_only(self):
        for marker in (None, "6472"):
            p = self.ports(); p.marker = marker
            result = await r.audit_one(SCOPE, "100", p)
            self.assertEqual(result.state, "rounding_review")
            self.assertEqual(result.reason, "existing_invoice_rounding_requires_settlement")
            self.assertEqual((result.invoice_id, result.invoice_total, result.paid_amount,
                              result.remaining, result.salla_total),
                             ("6472", "161.12", "161.11", "0.01", "161.11"))
            self.no_writes(p)

    async def test_ineligible_or_stale_salla_cannot_isolate(self):
        for change in ({"is_cod": True}, {"skus_complete": False},
                       {"fresh_salla": False}, {"status": "cancelled"},
                       {"status": "refunded"}, {"payment_eligible": False},
                       {"order_date": date(2026, 6, 30)},
                       {"order_date": date(2026, 9, 20)},
                       {"reference": "999"}, {"currency": "USD"},
                       {"total": "160.11"}, {"total": "NaN"}):
            with self.subTest(change=change):
                p = self.ports(); p.current_facts = replace(p.current_facts, **change)
                self.assertEqual((await r.audit_one(SCOPE, "100", p)).state, "review")
                self.no_writes(p)

    async def test_invoice_evidence_must_be_unique_complete_fresh_and_consistent(self):
        cases = [{"reference": "999"}, {"invoice_id": ""}, {"currency": "USD"},
                 {"total": "161.13"}, {"paid": "161.10"}, {"remaining": "0.02"},
                 {"remaining": "0.00"}, {"paid": None}, {"total": "NaN"}]
        for change in cases:
            with self.subTest(change=change):
                p = self.ports(); p.invoices = (replace(p.invoices[0], **change),)
                self.assertEqual((await r.audit_one(SCOPE, "100", p)).state, "review")
                self.no_writes(p)
        for kind in ("duplicate", "absent", "stale", "partial", "wrong_marker"):
            with self.subTest(kind=kind):
                p = self.ports()
                if kind == "duplicate": p.invoices *= 2
                if kind == "absent": p.invoices = ()
                if kind == "stale": p.fresh = False
                if kind == "partial": p.complete = False
                if kind == "wrong_marker": p.marker = "other-invoice"
                self.assertEqual((await r.audit_one(SCOPE, "100", p)).state, "review")
                self.no_writes(p)

    async def test_recovery_isolates_only_after_independent_fresh_audit(self):
        p = self.ports(); p.active = True
        observations = []
        observe = p.observe

        async def counted(reference):
            observations.append(reference)
            return await observe(reference)

        p.observe = counted
        result = await r.recover_one(SCOPE, "100", p)
        self.assertEqual(result.state, "rounding_review")
        self.assertEqual((p.fact_reads, len(observations)), (2, 2))
        self.assertEqual((p.sends, p.repairs), (0, 0))
        self.assertEqual(p.claims, {"100"})
        self.assertTrue(p.active)

    async def test_recovery_pauses_when_independent_audit_no_longer_proves_rounding(self):
        for kind in ("stale", "duplicate", "unknown", "changed_payment", "stale_salla"):
            with self.subTest(kind=kind):
                p = self.ports(); p.active = True
                observe = p.observe
                reads = 0

                async def changed(reference):
                    nonlocal reads
                    reads += 1
                    if reads == 2:
                        if kind == "unknown": raise TimeoutError("unknown result")
                        if kind == "stale": p.fresh = False
                        if kind == "duplicate": p.invoices *= 2
                        if kind == "changed_payment":
                            p.invoices = (replace(p.invoices[0], paid="161.10"),)
                    return await observe(reference)

                p.observe = changed
                if kind == "stale_salla":
                    facts = p.facts

                    async def stale(reference):
                        result = await facts(reference)
                        return replace(result, fresh_salla=False) if p.fact_reads == 2 else result

                    p.facts = stale
                result = await r.recover_one(SCOPE, "100", p)
                self.assertEqual(result.state, "blocked")
                self.assertEqual(result.reason, "provider_settlement_incomplete")
                self.no_writes(p)

    async def test_known_post_send_rounding_isolated_but_timeout_still_pauses(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                p = self.ports(); p.active = True
                invoice = p.invoices[0]
                p.invoices = (); p.claims.clear(); p.marker = None

                async def send(reference):
                    p.sends += 1
                    p.invoices = (invoice,)
                    p.marker = invoice.invoice_id
                    if timeout:
                        raise TimeoutError("provider may have committed")

                p.send_guarded = send
                result = await r.recover_one(SCOPE, "100", p)
                self.assertEqual(result.state, "unknown" if timeout else "rounding_review")
                self.assertEqual(p.active, not timeout)
                self.assertEqual((p.sends, p.repairs), (1, 0))
                self.assertEqual(p.claims, {"100"})
                self.assertEqual(p.fact_reads, 2 if timeout else 3)

    async def test_excluded_order_cannot_be_rounding_isolated(self):
        p = self.ports()
        p.current_facts = replace(p.current_facts, reference="102")
        p.invoices = (replace(p.invoices[0], reference="102"),)
        self.assertEqual((await r.audit_one(SCOPE, "102", p)).state, "review")
        self.no_writes(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
