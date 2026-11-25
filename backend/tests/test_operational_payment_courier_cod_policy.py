"""External courier COD: verified shipment + cash custody or accountant confirmation."""
import unittest

from operational_payment_courier_cod_policy import (
    CourierCODDelivery, decide_courier_cod_delivery,
)
from operational_payment_event_policy import Decision


def delivered(**changes):
    data = dict(owner_id="owner-1", order_id="order-1",
                courier_id="smsa", shipment_id="ship-1",
                delivery_evidence_id="proof-1", amount="300.00",
                delivered=True, verified_carrier_delivery=True,
                collection_method="cash", collected_confirmed=True,
                collection_evidence_id="col-1")
    data.update(changes)
    return CourierCODDelivery(**data)


class CourierCODDecisionTests(unittest.TestCase):
    def test_cash_courier_receivable_after_verified_collection(self):
        result = decide_courier_cod_delivery(delivered())
        self.assertEqual(result.action, Decision.POST)
        self.assertEqual(result.balance_bucket, "courier_receivable")
        self.assertEqual(result.idempotency_key, "courier_cod_first|owner-1|order-1")

    def test_replays_and_changed_collection_retain_same_order_key(self):
        first = decide_courier_cod_delivery(delivered())
        repeated = decide_courier_cod_delivery(delivered(
            delivery_evidence_id="proof-2", collection_evidence_id="col-2"))
        self.assertEqual(first.idempotency_key, repeated.idempotency_key)

    def test_status_alone_or_missing_collection_cannot_post(self):
        cases = (
            dict(delivered=False), dict(verified_carrier_delivery=False),
            dict(delivery_evidence_id=""), dict(shipment_id=""),
            dict(courier_id=""), dict(collected_confirmed=False),
            dict(collection_evidence_id=""), dict(payment_method="mada"),
            dict(currency="QAR"), dict(amount="300.001"),
            dict(amount="NaN"), dict(amount="0"),
        )
        for change in cases:
            with self.subTest(change=change):
                self.assertNotEqual(decide_courier_cod_delivery(delivered(**change)).action,
                                    Decision.POST)

    def test_network_or_bank_pending_until_accountant_or_exact_match(self):
        for method, bucket in (("pos", "courier_receivable"), ("bank", "courier_receivable")):
            with self.subTest(method=method):
                pending = decide_courier_cod_delivery(delivered(collection_method=method))
                self.assertEqual(pending.action, Decision.PENDING_ACCOUNTANT)
                approved = decide_courier_cod_delivery(delivered(
                    collection_method=method, accountant_approved=True))
                self.assertEqual(approved.action, Decision.POST)
                self.assertEqual(approved.balance_bucket, bucket)
                matched = decide_courier_cod_delivery(delivered(
                    collection_method=method, independently_matched=True))
                self.assertEqual(matched.action, Decision.POST)
                self.assertEqual(matched.balance_bucket, bucket)

    def test_collection_without_delivered_proof_stays_pending_even_if_accountant_approved(self):
        result = decide_courier_cod_delivery(delivered(
            verified_carrier_delivery=False, accountant_approved=True))
        self.assertEqual(result.action, Decision.PENDING_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
