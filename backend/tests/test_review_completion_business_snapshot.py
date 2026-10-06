"""Offline synthetic contracts, not production payload replay."""
from copy import deepcopy
from decimal import Decimal
import unittest

from fastapi import HTTPException
from order_review_business_snapshot import build_snapshot, compare_snapshots, diagnostic, verify_snapshot


class BusinessSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"id": 1, "items": [{"product": {"id": 2}, "sku": "A", "quantity": 1,
                    "options": [{"id": 3, "value": "blue"}], "customer_selections": ["AB"]}],
                    "shipping": {"company": "Carrier", "method": "door"},
                    "customer": {"name": "Buyer", "phone": "050"}, "payment_method": "cod"}
        self.order = {"order_id": "1", "items": []}
        self.acceptance = {"config_version": 1}

    def build(self, raw=None, acceptance=None):
        return build_snapshot({"raw_by_source": {"salla_direct": self.raw if raw is None else raw}},
                              self.order, self.acceptance if acceptance is None else acceptance,
                              identity={"operation_id": "op", "revision": 1})

    def test_alias_equivalence_and_immutable_inputs(self):
        original = deepcopy(self.raw)
        before = self.build()
        after = deepcopy(self.raw)
        after["shipping"]["company_name"] = after["shipping"]["company"]
        current = self.build(after)
        self.assertTrue(compare_snapshots(before, current)["equal"])
        self.assertNotEqual(before["source_hash"], current["source_hash"])
        self.assertEqual(self.raw, original)
        self.raw["items"][0]["quantity"] = 99
        self.assertTrue(verify_snapshot(before))

    def test_business_mutations(self):
        before = self.build()
        changes = [("items", 0, "product", {"id": 8}), ("items", 0, "quantity", 2),
                   ("items", 0, "sku", "B"), ("items", 0, "options", ["red"]),
                   ("items", 0, "customer_selections", ["CD"]),
                   ("shipping", "company", "Other"), ("customer", "phone", "051"),
                   ("payment_method", "bank")]
        for path in changes:
            with self.subTest(path=path):
                raw = deepcopy(self.raw)
                target = raw
                for key in path[:-2]:
                    target = target[key]
                target[path[-2]] = path[-1]
                result = compare_snapshots(before, self.build(raw))
                self.assertFalse(result["equal"])
                self.assertEqual(result["code"], "review_completion_source_changed")

    def test_unknown_root_nested_and_status_fail_closed(self):
        before = self.build()
        for section in (None, "shipping", "status"):
            raw = deepcopy(self.raw)
            target = raw if section is None else raw.setdefault(section, {})
            target["new_private_field"] = "secret"
            with self.subTest(section=section):
                after = self.build(raw)
                self.assertEqual(compare_snapshots(before, after)["code"], "unknown_source_change")
                self.assertNotIn("secret", str(diagnostic(before, after)))
                self.assertNotIn("new_private_field", str(diagnostic(before, after)))

    def test_status_transition_only_permitted_with_separate_guards(self):
        before = self.build()
        after = deepcopy(self.raw)
        after["status"] = {"slug": "under_review", "customized": {"name": "reviewed", "id": 4}}
        after["updated_at"] = "2030-01-01"
        self.assertTrue(compare_snapshots(before, self.build(after))["equal"])

    def test_unknown_shapes_cannot_hide_in_transport_fields(self):
        before = self.build()
        for key in ("event", "updated_at", "status"):
            after = deepcopy(self.raw)
            after[key] = {"slug": {"unknown_business_fact": 1}}
            self.assertEqual(compare_snapshots(before, self.build(after))["code"], "unknown_source_change")

    def test_missing_null_empty_and_types_are_distinct(self):
        before = self.build()
        for value in (None, "", [], {}):
            raw = deepcopy(self.raw)
            raw["notes"] = value
            self.assertFalse(compare_snapshots(before, self.build(raw))["equal"])
        for value in (True, "1"):
            raw = deepcopy(self.raw)
            raw["items"][0]["quantity"] = value
            self.assertFalse(compare_snapshots(before, self.build(raw))["equal"])

    def test_exact_decimal_preserves_beyond_float_precision(self):
        self.raw["paid_amount"] = Decimal("1.1234567890123456789012345678901")
        before = self.build()
        self.raw["paid_amount"] = Decimal("1.1234567890123456789012345678902")
        self.assertFalse(compare_snapshots(before, self.build())["equal"])

    def test_conflicting_alias_and_null_alias_fail_closed(self):
        for value in ("Other", None, ""):
            raw = deepcopy(self.raw)
            raw["shipping"]["company_name"] = value
            with self.assertRaises(HTTPException) as exc:
                self.build(raw)
            self.assertEqual(exc.exception.status_code, 409)

    def test_acceptance_change_specific_code(self):
        result = compare_snapshots(self.build(), self.build(acceptance={"config_version": 2}))
        self.assertEqual(result["code"], "component_acceptance_changed")

    def test_documented_dto_projection_changes_are_not_approval_changes(self):
        self.order.update(status_native="pending", is_new=True, timeline=[], engine_updated_at="old",
                          completed_at=None, source={"provider": "salla", "source_order_id": "1",
                                                     "source_event": "order.created", "received_at": "old"})
        before = self.build()
        self.order.update(status_native="reviewed", is_new=False, timeline=[{"status": "reviewed"}],
                          engine_updated_at="new", completed_at="new")
        self.order["source"].update(source_event="order.updated", received_at="new")
        self.assertTrue(compare_snapshots(before, self.build())["equal"])
        self.order["source"]["new_metadata"] = "new"
        self.assertEqual(compare_snapshots(before, self.build())["code"], "unknown_source_change")

    def test_documented_dto_options_are_business_changes(self):
        self.order["items"] = [{"order_item_id": "1", "options_raw": [{"id": 3, "value": "blue"}]}]
        before = self.build()
        self.order["items"][0]["options_raw"][0]["value"] = "red"
        self.assertEqual(compare_snapshots(before, self.build())["code"], "review_completion_source_changed")

    def test_integrity_and_version_tamper_rejected(self):
        before = self.build()
        for key, value in (("schema_version", 99), ("canonical_hash", "x"), ("unknown_field_fingerprints", {})):
            after = deepcopy(before)
            after[key] = value
            if after == before:
                after["facts"]["identity"] = ["null"]
            self.assertFalse(verify_snapshot(after))
            self.assertFalse(compare_snapshots(before, after)["equal"])

    def test_unproven_aliases_empty_ids_and_array_order_not_erased(self):
        before = self.build()
        changes = []
        method = deepcopy(self.raw)
        method["shipping"]["shipping_method"] = method["shipping"].pop("method")
        changes.append(method)
        product = deepcopy(self.raw)
        product["items"][0]["product_id"] = 2
        changes.append(product)
        for raw in changes:
            self.assertFalse(compare_snapshots(before, self.build(raw))["equal"])
        for value in (None, "", {}, [], False):
            raw = deepcopy(self.raw)
            raw["items"][0]["product"]["id"] = value
            approved = self.build(raw)
            raw["items"][0]["product_id"] = value
            self.assertFalse(compare_snapshots(approved, self.build(raw))["equal"])
        raw = deepcopy(self.raw)
        raw["items"][0]["options"] = ["first", "second"]
        approved = self.build(raw)
        raw["items"][0]["options"].reverse()
        self.assertFalse(compare_snapshots(approved, self.build(raw))["equal"])

    def test_exact_address_duplicate_only_and_company_metadata_preserved(self):
        self.raw["shipping_address"] = {"city": "Synthetic city"}
        before = self.build()
        raw = deepcopy(self.raw)
        raw["shipping"]["address"] = deepcopy(raw["shipping_address"])
        self.assertTrue(compare_snapshots(before, self.build(raw))["equal"])
        raw["shipping"]["address"]["city"] = "Other"
        with self.assertRaises(HTTPException):
            self.build(raw)
        self.raw["shipping"]["company"] = {"name": "Carrier", "id": 1}
        before = self.build()
        raw = deepcopy(self.raw)
        raw["shipping"]["company_name"] = "Carrier"
        self.assertTrue(compare_snapshots(before, self.build(raw))["equal"])
        raw["shipping"]["company"]["id"] = 2
        self.assertFalse(compare_snapshots(before, self.build(raw))["equal"])
