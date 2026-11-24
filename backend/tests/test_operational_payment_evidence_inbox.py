"""Fail-closed, no-money CAS payment evidence staging tests."""
import copy
import unittest
from operational_payment_event_policy import Evidence, Kind, PaymentEvent
from operational_payment_evidence_inbox import (
    event_record, queue_evidence_in_state, INBOX_KEY,
)


def payment(**kw):
    data = dict(order_id="o-1", provider="tabby", amount="300.00",
                kind=Kind.PAYMENT, evidence=Evidence.PROVIDER_SUCCESS,
                transaction_id="tx-1")
    data.update(kw)
    return PaymentEvent(**data)


def record(event=None, **kw):
    return event_record(event or payment(), owner="merchant-1",
                        source=kw.get("source", "salla_native_transactions"),
                        source_ref=kw.get("source_ref", "native-1"))


class EvidenceInboxTests(unittest.TestCase):
    def test_verified_capture_staged_without_balance_changes(self):
        state = {"status": "active", "engine": {"facts": {"capture:x": {"amount": "999.00"}}},
                 "movements": [], "openings": {"bank": "200.00"}}
        original = copy.deepcopy(state)
        result = queue_evidence_in_state(state, record(), "2026-11-24T18:00:00Z")
        self.assertEqual(result["status"], "evidence_received")
        self.assertEqual(state["engine"], original["engine"])
        self.assertEqual(state["movements"], original["movements"])
        self.assertEqual(state["openings"], original["openings"])
        self.assertEqual(state[INBOX_KEY][result["key"]]["status"], "evidence_received")

    def test_replay_is_idempotent(self):
        state = {"status": "active"}
        a = queue_evidence_in_state(state, record(), "2026-11-24T18:00:00Z")
        b = queue_evidence_in_state(state, record(), "2026-11-24T18:01:00Z")
        self.assertEqual(a["key"], b["key"])
        self.assertEqual(b["status"], "already_received")
        self.assertEqual(len(state[INBOX_KEY]), 1)

    def test_different_source_same_proof_keeps_single_event(self):
        state = {"status": "active"}
        first = record()
        second = record(source="provider_statement_independent_match", source_ref="statement-1")
        queue_evidence_in_state(state, first, "2026-11-24T18:00:00Z")
        queue_evidence_in_state(state, second, "2026-11-24T18:01:00Z")
        self.assertEqual(len(state[INBOX_KEY]), 1)
        self.assertEqual(len(next(iter(state[INBOX_KEY].values()))["sources"]), 2)

    def test_reused_transaction_on_different_order_fails(self):
        state = {"status": "active"}
        queue_evidence_in_state(state, record(), "2026-11-24T18:00:00Z")
        wrong = record(payment(order_id="o-2"))
        with self.assertRaisesRegex(ValueError, "identity_conflict"):
            queue_evidence_in_state(state, wrong, "2026-11-24T18:02:00Z")
        self.assertEqual(len(state[INBOX_KEY]), 1)

    def test_different_installments_not_conflated(self):
        state = {"status": "active"}
        queue_evidence_in_state(state, record(), "2026-11-24T18:00:00Z")
        another = record(payment(transaction_id="tx-2", amount="150.00"))
        queue_evidence_in_state(state, another, "2026-11-24T18:02:00Z")
        self.assertEqual(len(state[INBOX_KEY]), 2)

    def test_existing_canonical_capture_is_not_new_money(self):
        state = {"status": "active", "engine": {"facts": {
            "capture:tx-1": {"order_id": "o-1", "provider": "tabby", "amount": "300.00"}
        }}}
        result = queue_evidence_in_state(state, record(), "2026-11-24T18:00:00Z")
        self.assertEqual(result["status"], "already_recognized")
        self.assertEqual(state[INBOX_KEY][result["key"]]["status"], "already_recognized")

    def test_conflicting_canonical_capture_rejected(self):
        state = {"status": "active", "engine": {"facts": {
            "capture:tx-1": {"order_id": "o-2", "provider": "tabby", "amount": "300.00"}
        }}}
        with self.assertRaisesRegex(ValueError, "canonical_conflict"):
            queue_evidence_in_state(state, record(), "2026-11-24T18:00:00Z")

    def test_unverified_payment_cannot_enter(self):
        with self.assertRaisesRegex(ValueError, "payment_event_not_confirmed"):
            record(payment(evidence=Evidence.ORDER_STATUS_ONLY))

    def test_refund_not_approved_cannot_enter(self):
        with self.assertRaisesRegex(ValueError, "payment_event_not_confirmed"):
            record(payment(kind=Kind.REFUND, refund_id="refund1",
                           evidence=Evidence.UNVERIFIED))

    def test_delivery_bank_pending_cannot_enter(self):
        with self.assertRaisesRegex(ValueError, "payment_event_not_confirmed"):
            record(payment(kind=Kind.COD, delivered=True, cod_collection_method="bank",
                           evidence=Evidence.ORDER_STATUS_ONLY))

    def test_untrusted_source_rejected(self):
        with self.assertRaisesRegex(ValueError, "untrusted_payment_event_source"):
            record(source="browser_form")

    def test_inactive_workspace_rejected(self):
        with self.assertRaisesRegex(ValueError, "workspace_not_active"):
            queue_evidence_in_state({"status": "draft"}, record(),
                                    "2026-11-24T18:00:00Z")


if __name__ == "__main__":
    unittest.main()
