"""Decision-policy regression tests; no production writes."""
import unittest

from operational_payment_event_policy import (
    Decision, Evidence, Kind, PaymentEvent, decide,
)


def event(**kwargs):
    defaults = dict(order_id="301344207", provider="tabby", amount="300.00",
                    kind=Kind.PAYMENT, evidence=Evidence.PROVIDER_SUCCESS,
                    transaction_id="txn-original")
    defaults.update(kwargs)
    return PaymentEvent(**defaults)


class TestOperationalPaymentEventPolicy(unittest.TestCase):
    def test_first_payment_and_different_provider_extra_are_distinct(self):
        first = decide(event())
        extra = decide(event(provider="tamara", amount="150.00",
                             transaction_id="txn-addition"))
        self.assertEqual(first.action, Decision.POST)
        self.assertEqual(extra.action, Decision.POST)
        self.assertEqual((first.balance_bucket, extra.balance_bucket), ("tabby", "tamara"))
        self.assertNotEqual(first.idempotency_key, extra.idempotency_key)

    def test_same_event_replay_has_stable_key(self):
        self.assertEqual(decide(event()).idempotency_key, decide(event()).idempotency_key)

    def test_status_only_cannot_prove_payment(self):
        result = decide(event(evidence=Evidence.ORDER_STATUS_ONLY))
        self.assertEqual(result.action, Decision.PENDING_EVIDENCE)

    def test_added_payment_matches_provider_statement(self):
        result = decide(event(provider="tamara", transaction_id="addition-01",
                              evidence=Evidence.MATCHED_PROVIDER_STATEMENT,
                              statement_match_verified=True))
        self.assertEqual(result.action, Decision.POST)

    def test_statement_without_strong_match_is_not_enough(self):
        result = decide(event(evidence=Evidence.MATCHED_PROVIDER_STATEMENT))
        self.assertEqual(result.action, Decision.PENDING_EVIDENCE)

    def test_refund_requires_accountant_or_verified_statement(self):
        kwargs = dict(kind=Kind.REFUND, refund_id="refund-1", transaction_id=None)
        self.assertEqual(decide(event(evidence=Evidence.UNVERIFIED, **kwargs)).action,
                         Decision.PENDING_ACCOUNTANT)
        self.assertEqual(decide(event(evidence=Evidence.ACCOUNTANT_APPROVED, **kwargs)).action,
                         Decision.POST)
        self.assertEqual(decide(event(evidence=Evidence.MATCHED_PROVIDER_STATEMENT,
                                      statement_match_verified=True, **kwargs)).action, Decision.POST)

    def test_cod_bank_delivered_but_not_accountant_approved(self):
        args = dict(kind=Kind.COD, provider="bank_inma", delivered=True,
                    cod_collection_method="bank", transaction_id="bank-receipt")
        pending = decide(event(evidence=Evidence.UNVERIFIED, **args))
        approved = decide(event(evidence=Evidence.ACCOUNTANT_APPROVED, **args))
        self.assertEqual(pending.action, Decision.PENDING_ACCOUNTANT)
        self.assertEqual(approved.action, Decision.POST)
        self.assertEqual(approved.balance_bucket, "bank")

    def test_cod_pos_and_cash_follow_different_policy(self):
        self.assertEqual(decide(event(kind=Kind.COD, delivered=True,
                                      cod_collection_method="pos",
                                      evidence=Evidence.ORDER_STATUS_ONLY)).action,
                         Decision.PENDING_ACCOUNTANT)
        cash = decide(event(kind=Kind.COD, delivered=True, cod_collection_method="cash",
                            evidence=Evidence.COURIER_CASH_COLLECTED))
        self.assertEqual(cash.action, Decision.POST)
        self.assertEqual(cash.balance_bucket, "courier_receivable")

    def test_cod_not_delivered_does_not_post(self):
        self.assertEqual(decide(event(kind=Kind.COD, delivered=False,
                                      cod_collection_method="cash",
                                      evidence=Evidence.COURIER_CASH_COLLECTED)).action,
                         Decision.PENDING_EVIDENCE)

    def test_missing_identifier_fails_closed(self):
        self.assertEqual(decide(event(transaction_id=None)).action, Decision.PENDING_EVIDENCE)

    def test_currency_and_precision_fail_closed(self):
        self.assertEqual(decide(event(currency="QAR")).action, Decision.PENDING_EVIDENCE)
        self.assertEqual(decide(event(amount="10.001")).action, Decision.REJECT)
        self.assertEqual(decide(event(amount="NaN")).action, Decision.REJECT)


if __name__ == "__main__":
    unittest.main()
