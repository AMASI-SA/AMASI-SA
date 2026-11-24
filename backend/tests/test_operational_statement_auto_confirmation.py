import unittest
from operational_statement_auto_confirmation import match_statement_to_native_event


def row(**kw):
    base = dict(event_type="sale", provider="tabby", native_currency="SAR",
                order_number="30001", principal_amount="100.00", event_reference="evt1", blockers=[])
    base.update(kw)
    return base


def proof(**kw):
    base = dict(order_number="30001", provider="tabby", currency="SAR",
                amount="100.00", kind="payment", status="captured", verified_source=True)
    base.update(kw)
    return base


class AutoStatementGateTests(unittest.TestCase):
    def test_exact_independent_match(self):
        x = match_statement_to_native_event(row(), {"evt1":proof()})
        self.assertTrue(x.eligible)
        self.assertEqual(x.reference, "evt1")

    def test_no_native_transaction_id_must_review(self):
        x = match_statement_to_native_event(row(event_reference=None), {})
        self.assertFalse(x.eligible)
        self.assertEqual(x.reason, "statement_event_identity_missing")

    def test_other_order_same_amount_cannot_match(self):
        x = match_statement_to_native_event(row(), {"evt1":proof(order_number="30002")})
        self.assertFalse(x.eligible)

    def test_untrusted_merchant_statement_only(self):
        x = match_statement_to_native_event(row(), {"evt1":proof(verified_source=False)})
        self.assertFalse(x.eligible)

    def test_refund_needs_refund_event_not_capture(self):
        x = match_statement_to_native_event(row(event_type="refund"), {"evt1":proof()})
        self.assertFalse(x.eligible)

    def test_refund_independent_execution(self):
        x = match_statement_to_native_event(row(event_type="refund"),
             {"evt1":proof(kind="refund", status="refunded")})
        self.assertTrue(x.eligible)

    def test_unbalanced_statement_blocked(self):
        x = match_statement_to_native_event(row(blockers=["statement_transfer_mismatch"]),
             {"evt1":proof()})
        self.assertFalse(x.eligible)

    def test_currency_and_amount_must_match(self):
        self.assertFalse(match_statement_to_native_event(row(native_currency="QAR"),
                         {"evt1":proof()}).eligible)
        self.assertFalse(match_statement_to_native_event(row(principal_amount="90.00"),
                         {"evt1":proof()}).eligible)

    def test_fee_is_not_additional_customer_payment(self):
        x = match_statement_to_native_event(row(event_type="settlement_fee"),
             {"evt1":proof()})
        self.assertFalse(x.eligible)


if __name__ == "__main__":
    unittest.main()
