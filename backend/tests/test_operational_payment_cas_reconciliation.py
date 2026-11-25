"""Staged evidence can only acknowledge already-counted operational facts."""
import copy
import unittest

from operational_payment_cas_reconciliation import reconcile_staged_payment_evidence


def observed(kind="payment", ref="tx-1", status="evidence_received",
             order="o1", provider="tabby", amount="300.00"):
    return {"key": "hash-1", "kind": kind, "native_ref": ref,
            "order_id": order, "provider": provider, "amount": amount,
            "currency": "SAR", "status": status}


def state(evidence=None, facts=None):
    return {"status":"active", "revision":7,
            "openings":{"tabby":{"amount":"2000.00"}},
            "movements":[{"id":"bank-x","amount":"50.00"}],
            "engine":{"facts": facts if facts is not None else {}},
            "payment_event_evidence_inbox":{"hash-1": evidence or observed()}}


class CanonicalEvidenceReconciliationTests(unittest.TestCase):
    def test_capture_exact_match_only_updates_evidence_status(self):
        s = state(facts={"capture:tx-1":{"order_id":"o1","provider":"tabby","amount":"300.00"}})
        previous = copy.deepcopy(s)
        result = reconcile_staged_payment_evidence(s)
        self.assertEqual(result, {"recognized":1,"pending":0,"conflict":0})
        self.assertEqual(s["payment_event_evidence_inbox"]["hash-1"]["status"],"already_recognized")
        self.assertEqual(s["movements"], previous["movements"])
        self.assertEqual(s["openings"], previous["openings"])
        self.assertEqual(s["engine"]["facts"], previous["engine"]["facts"])
        self.assertNotIn("obligations", s["engine"])

    def test_missing_capture_does_not_create_money(self):
        s = state()
        self.assertEqual(reconcile_staged_payment_evidence(s)["pending"], 1)
        self.assertEqual(s["engine"]["facts"], {})
        self.assertEqual(s["payment_event_evidence_inbox"]["hash-1"]["status"],"evidence_received")

    def test_different_order_same_reference_is_conflict(self):
        s = state(facts={"capture:tx-1":{"order_id":"other","provider":"tabby","amount":"300.00"}})
        self.assertEqual(reconcile_staged_payment_evidence(s)["conflict"], 1)
        self.assertEqual(s["payment_event_evidence_inbox"]["hash-1"]["status"],"conflict")

    def test_amount_mismatch_never_credited(self):
        s = state(facts={"capture:tx-1":{"order_id":"o1","provider":"tabby","amount":"200.00"}})
        self.assertEqual(reconcile_staged_payment_evidence(s)["conflict"], 1)

    def test_a_recognized_capture_disappearing_is_conflict(self):
        s = state(evidence=observed(status="already_recognized"))
        self.assertEqual(reconcile_staged_payment_evidence(s)["conflict"], 1)

    def test_refund_exact_source_approved_as_recognized(self):
        s=state(evidence=observed(kind="refund", ref="r-1", amount="50.00"),
                facts={"refund:tabby:r-1":{"order_id":"o1","amount":"50.00"}})
        self.assertEqual(reconcile_staged_payment_evidence(s)["recognized"],1)

    def test_cod_does_not_follow_provider_capture_path(self):
        s=state(evidence=observed(kind="cod"))
        self.assertEqual(reconcile_staged_payment_evidence(s)["pending"],1)
        self.assertEqual(s["payment_event_evidence_inbox"]["hash-1"]["status"],"evidence_received")

    def test_existing_conflict_remains_failed_without_reproof(self):
        s=state(evidence=observed(status="conflict"))
        self.assertEqual(reconcile_staged_payment_evidence(s)["conflict"],1)

    def test_old_conflict_cannot_silently_clear_after_fact_matches(self):
        s = state(evidence=observed(status="conflict"),
                  facts={"capture:tx-1":{"order_id":"o1","provider":"tabby","amount":"300.00"}})
        self.assertEqual(reconcile_staged_payment_evidence(s)["conflict"], 1)
        self.assertEqual(s["payment_event_evidence_inbox"]["hash-1"]["status"], "conflict")

    def test_bad_inbox_rejected(self):
        s=state()
        s["payment_event_evidence_inbox"]=[]
        with self.assertRaisesRegex(ValueError,"scope_invalid"):
            reconcile_staged_payment_evidence(s)


if __name__ == "__main__":
    unittest.main()
