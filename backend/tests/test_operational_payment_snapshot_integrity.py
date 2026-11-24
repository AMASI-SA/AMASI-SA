"""No-write diagnostics for canonical operational captures and collections."""
import copy
import unittest
from operational_payment_snapshot_integrity import audit_payment_snapshot


def state():
    return {
        "revision": 9,
        "engine": {"as_of": "2026-11-24T19:00:00+03:00", "facts": {
            "capture:tabby-1": {"order_id": "o1", "provider": "tabby", "amount": "300.00"},
        }},
        "customer_collections": [
            {"id": "c1", "status": "confirmed", "order_id": "o2", "order_number": "20002",
             "destination_type": "provider", "destination_id": "tamara", "reference": "tamara-2",
             "currency": "SAR", "amount": "150.00"}
        ],
    }


class IntegrityTests(unittest.TestCase):
    def test_snapshot_is_read_only_and_unmatched_reported(self):
        original = state()
        copy_of_original = copy.deepcopy(original)
        result = audit_payment_snapshot(original)
        self.assertEqual(original, copy_of_original)
        self.assertEqual(result["counts"]["canonical_capture_facts"], 1)
        self.assertEqual(result["counts"]["collection_source_unmatched"], 1)
        self.assertFalse(result["safe_for_automatic_posting"])

    def test_exact_capture_matches_existing_collection(self):
        original = state()
        original["engine"]["facts"]["capture:tamara-2"] = {
            "order_id": "o2", "provider": "tamara", "amount": "150.00"}
        result = audit_payment_snapshot(original)
        self.assertEqual(result["counts"]["collections_source_matched"], 1)
        self.assertNotIn("collection_source_unmatched", result["counts"])

    def test_same_reference_different_order_conflict(self):
        original = state()
        original["customer_collections"].append({
            **original["customer_collections"][0], "id": "c2",
            "order_id": "o3", "order_number": "20003"})
        result = audit_payment_snapshot(original)
        self.assertEqual(result["counts"]["collection_reference_conflict"], 1)

    def test_duplicate_collection_id(self):
        original = state()
        original["customer_collections"].append(copy.deepcopy(original["customer_collections"][0]))
        result = audit_payment_snapshot(original)
        self.assertEqual(result["counts"]["collection_identity_invalid"], 1)

    def test_status_not_confirmed_is_not_receipt(self):
        original = state()
        original["customer_collections"][0]["status"] = "pending"
        result = audit_payment_snapshot(original)
        self.assertEqual(result["counts"]["collection_not_confirmed"], 1)
        self.assertNotIn("collection_source_unmatched", result["counts"])

    def test_invalid_capture_does_not_create_payment(self):
        original = state()
        original["engine"]["facts"]["capture:tabby-1"]["amount"] = "NaN"
        result = audit_payment_snapshot(original)
        self.assertEqual(result["counts"]["capture_fact_invalid"], 1)
        self.assertFalse(result["safe_for_automatic_posting"])

    def test_issue_cap_and_privacy(self):
        original = state()
        original["customer_collections"].append({
            **original["customer_collections"][0], "id": "c2", "reference": "tamara-3"})
        result = audit_payment_snapshot(original, max_issues=1)
        self.assertTrue(result["issues_truncated"])
        self.assertEqual(len(result["issues"]), 1)
        self.assertNotIn("customer_email", str(result))

    def test_payment_evidence_inbox_counts_without_changing_balances(self):
        original = state()
        original["payment_event_evidence_inbox"] = {
            "ref1": {"status": "evidence_received"},
            "ref2": {"status": "already_recognized"},
        }
        snapshot = copy.deepcopy(original)
        result = audit_payment_snapshot(original)
        self.assertEqual(original, snapshot)
        self.assertEqual(result["counts"]["payment_evidence_pending_projection"], 1)
        self.assertEqual(result["counts"]["payment_evidence_already_recognized"], 1)
        self.assertFalse(result["safe_for_automatic_posting"])

    def test_invalid_scope_fails_closed(self):
        with self.assertRaises(ValueError):
            audit_payment_snapshot({"engine": {}, "customer_collections": "not a list"})
        with self.assertRaises(ValueError):
            audit_payment_snapshot(state(), max_issues=-1)


if __name__ == "__main__":
    unittest.main()
