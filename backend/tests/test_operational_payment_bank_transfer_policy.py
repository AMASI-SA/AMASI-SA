"""Bank transfer admission is an internal attestation, never Salla status proof."""
import unittest

from operational_payment_bank_transfer_policy import (
    BankTransferApproval, decide_bank_transfer_start,
)
from operational_payment_event_policy import Decision


def approval(**changes):
    data = dict(owner_id="owner-1", order_id="order-1",
                bank_account_id="alinma", amount="500.00",
                previous_status="awaiting_review", new_status="in_progress",
                approved_by="staff-1", approver_authorized=True,
                receipt_confirmed=True, trusted_internal_transition=True,
                transition_id="transition-1")
    data.update(changes)
    return BankTransferApproval(**data)


class BankTransferStartPolicyTests(unittest.TestCase):
    def test_valid_authorized_credit_attestation(self):
        result = decide_bank_transfer_start(approval())
        self.assertEqual(result.action, Decision.POST)
        self.assertEqual(result.balance_bucket, "alinma")
        self.assertEqual(result.idempotency_key,
                         "bank_transfer_first|owner-1|order-1")

    def test_different_transition_same_order_cannot_double_post(self):
        first = decide_bank_transfer_start(approval())
        second = decide_bank_transfer_start(approval(transition_id="transition-2"))
        self.assertEqual(first.idempotency_key, second.idempotency_key)

    def test_status_only_from_salla_cannot_post(self):
        for changes in (
            dict(trusted_internal_transition=False),
            dict(approver_authorized=False),
            dict(receipt_confirmed=False),
            dict(approved_by=""),
            dict(transition_id=""),
            dict(bank_account_id=""),
            dict(previous_status="waiting_payment"),
            dict(new_status="fulfilled"),
        ):
            with self.subTest(changes=changes):
                self.assertNotEqual(decide_bank_transfer_start(approval(**changes)).action,
                                    Decision.POST)

    def test_different_payment_methods_and_inaccurate_amounts_rejected(self):
        for changes in (
            dict(payment_method="mada"),
            dict(payment_method="cod"),
            dict(currency="QAR"),
            dict(amount="NaN"),
            dict(amount="0.00"),
            dict(amount="500.001"),
        ):
            with self.subTest(changes=changes):
                self.assertNotEqual(decide_bank_transfer_start(approval(**changes)).action,
                                    Decision.POST)


if __name__ == "__main__":
    unittest.main()
