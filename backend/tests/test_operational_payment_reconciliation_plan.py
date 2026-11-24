"""Three-phase operational payment reconciliation is read-only and fail-closed."""
import unittest
from operational_payment_event_policy import Kind, Evidence, PaymentEvent
from operational_payment_reconciliation_plan import ReconciliationRow, plan, exception_summary


def item(order="1001", provider="tabby", transaction="t1", amount="300.00",
         kind=Kind.PAYMENT, evidence=Evidence.PROVIDER_SUCCESS, **kw):
    return PaymentEvent(order_id=order, provider=provider, amount=amount,
                        kind=kind, evidence=evidence, transaction_id=transaction, **kw)


class ReconciliationPlanTests(unittest.TestCase):
    def test_initial_and_added_different_providers(self):
        result = plan([ReconciliationRow(item(), 1),
                       ReconciliationRow(item(provider="tamara", amount="150.00", transaction="t2"), 2)])
        self.assertEqual([x.status for x in result], ["eligible_candidate", "eligible_candidate"])
        self.assertNotEqual(result[0].idempotency_key, result[1].idempotency_key)

    def test_unverified_addition_is_pending(self):
        result = plan([ReconciliationRow(item(evidence=Evidence.UNVERIFIED), 2)])
        self.assertEqual(result[0].status, "pending_evidence")

    def test_matched_statement_addition_eligible(self):
        result = plan([ReconciliationRow(item(evidence=Evidence.MATCHED_PROVIDER_STATEMENT,
                                               statement_match_verified=True), 2)])
        self.assertEqual(result[0].status, "eligible_candidate")

    def test_refund_confirmation_needed(self):
        result = plan([ReconciliationRow(item(kind=Kind.REFUND, refund_id="refund1",
                                             evidence=Evidence.ORDER_STATUS_ONLY), 2)])
        self.assertEqual(result[0].status, "pending_accountant")

    def test_cod_delivery_bank_not_confirmed(self):
        result = plan([ReconciliationRow(item(kind=Kind.COD, delivered=True,
                                             cod_collection_method="bank",
                                             evidence=Evidence.ORDER_STATUS_ONLY), 2)])
        self.assertEqual(result[0].status, "pending_accountant")

    def test_cod_delivery_accountant_confirmed(self):
        result = plan([ReconciliationRow(item(kind=Kind.COD, delivered=True,
                                             cod_collection_method="bank",
                                             evidence=Evidence.ACCOUNTANT_APPROVED), 2)])
        self.assertEqual(result[0].status, "eligible_candidate")

    def test_exact_existing_effect_is_not_reposted(self):
        effect = {"order_id":"1001", "provider":"tabby","currency":"SAR",
                  "amount":"300.00","kind":"payment"}
        result = plan([ReconciliationRow(item(), 1, existing_effect=effect)])
        self.assertEqual(result[0].status, "already_posted")

    def test_different_existing_amount_is_conflict(self):
        effect = {"order_id":"1001", "provider":"tabby","currency":"SAR",
                  "amount":"500.00","kind":"payment"}
        result = plan([ReconciliationRow(item(), 1, existing_effect=effect)])
        self.assertEqual(result[0].status, "conflict")

    def test_cross_order_same_transaction_is_conflict(self):
        result = plan([ReconciliationRow(item(), 1),
                       ReconciliationRow(item(order="1002"), 1)])
        self.assertEqual([x.status for x in result],
                         ["eligible_candidate", "conflict"])

    def test_repeated_same_event_is_not_twice(self):
        result = plan([ReconciliationRow(item(), 1), ReconciliationRow(item(), 1)])
        self.assertEqual([x.status for x in result],
                         ["eligible_candidate", "already_seen"])

    def test_historical_requires_dry_run(self):
        result = plan([ReconciliationRow(item(), 3)])
        self.assertEqual(result[0].status, "backfill_candidate")

    def test_historical_opening_overlap_blocked(self):
        result = plan([ReconciliationRow(item(), 3, included_in_opening=True)])
        self.assertEqual(result[0].status, "requires_reconciliation")

    def test_daily_exception_counts_are_private(self):
        actions = plan([
            ReconciliationRow(item(), 1),
            ReconciliationRow(item(transaction="missing", evidence=Evidence.UNVERIFIED), 2),
            ReconciliationRow(item(transaction="old"), 3, included_in_opening=True),
        ])
        result = exception_summary(actions)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["requires_review"], 2)
        self.assertEqual(result["by_phase"][1]["eligible_candidate"], 1)
        self.assertNotIn("1001", str(result))
        self.assertNotIn("300.00", str(result))

    def test_invalid_phase_rejected(self):
        with self.assertRaises(ValueError):
            plan([ReconciliationRow(item(), 4)])


if __name__ == "__main__":
    unittest.main()
