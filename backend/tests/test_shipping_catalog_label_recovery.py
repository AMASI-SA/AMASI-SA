"""Catalogue renames are display changes, not permission to change a choice."""
from copy import deepcopy
import unittest

from order_review_business_snapshot import build_snapshot
from component_catalog_label_recovery import restored_catalog_labels


class CatalogueLabelProofTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"id": "order", "items": [{
            "id": "line", "product_id": "product", "quantity": 1,
            "options": [{"id": "option", "product_option_id": "catalog-option",
                         "name": "المنتج", "type": "radio",
                         "value": {"id": "choice", "name": "بلوفر + ترنج",
                                   "option_value": "بلوفر + ترنج",
                                   "price": {"amount": 29, "currency": "SAR"}}}],
        }]}
        self.operation = {"_id": "approval", "user_id": "owner", "order_number": "order",
                          "revision": 4, "workflow_fingerprint": "workflow"}
        self.operation["business_snapshot"] = build_snapshot(
            {"raw_by_source": {"salla_direct": self.raw}}, {}, {},
            identity=deepcopy(self.operation), schema_version=2)
        self.current = deepcopy(self.raw)
        self.current["items"][0]["options"][0]["value"].update(
            name="سويتر + ترنج", option_value="سويتر + ترنج")

    def test_renamed_same_choice_restores_exact_approved_input_without_mutation(self):
        before = deepcopy(self.current)
        self.assertEqual(restored_catalog_labels(self.current, self.operation), self.raw)
        self.assertEqual(self.current, before)

    def test_other_product_names_do_not_need_label_recovery(self):
        self.assertIsNone(restored_catalog_labels(self.raw, self.operation))

    def test_changed_selection_price_or_unknown_option_data_is_not_rename(self):
        mutations = [
            lambda o: o["value"].update(id="different"),
            lambda o: o.update(id="different"),
            lambda o: o.update(product_option_id="different"),
            lambda o: o["value"]["price"].update(amount=30),
            lambda o: o["value"]["price"].update(currency="USD"),
            lambda o: o.update(type="text"),
            lambda o: o.update(extra="new"),
            lambda o: o["value"].update(option_value="conflicting alias"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                current = deepcopy(self.current)
                mutate(current["items"][0]["options"][0])
                self.assertIsNone(restored_catalog_labels(current, self.operation))

    def test_missing_or_rebound_approval_fails_closed(self):
        for key, value in [("business_snapshot", {}), ("_id", "other"),
                           ("order_number", "other"), ("user_id", "other")]:
            with self.subTest(key=key):
                op = deepcopy(self.operation)
                op[key] = value
                self.assertIsNone(restored_catalog_labels(self.current, op))

    def test_corrupt_evidence_and_changed_shape_fail_closed(self):
        op = deepcopy(self.operation)
        op["business_snapshot"]["integrity_hash"] = "invalid"
        self.assertIsNone(restored_catalog_labels(self.current, op))
        for current in [{"items": []}, {"items": [{"id": "other", "options": []}]}]:
            self.assertIsNone(restored_catalog_labels(current, self.operation))
