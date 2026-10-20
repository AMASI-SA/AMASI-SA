from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from assembly_option_change_notice import (
    annotate_piece_options, assembly_option_notices, option_changes_by_item,
)
from order_review_business_snapshot import build_snapshot


class OptionNoticeTests(unittest.TestCase):
    def setUp(self):
        self.op = {"_id": "approval", "user_id": "owner", "order_number": "order",
                   "revision": 1, "workflow_fingerprint": "workflow"}
        approved = {"items": [{"order_item_id": "line", "options_raw": [
            {"id": 12, "name": "Old field", "value": "Old value"},
            {"id": 13, "name": "Size", "value": "M"}]}]}
        self.op["business_snapshot"] = build_snapshot(
            {"raw_by_source": {"salla_direct": {"items": []}}}, approved, {},
            identity=deepcopy(self.op), schema_version=2)
        self.order = SimpleNamespace(order_number="order", items=[SimpleNamespace(
            order_item_id="line", options_raw=[
                {"id": 12, "name": "New field", "value": "New value"},
                {"id": 13, "name": "Size", "value": "M"}])])

    def test_both_field_name_and_value_are_reported_for_exact_option(self):
        self.assertEqual(option_changes_by_item(self.order, self.op), {"line": [{
            "previous_name": "Old field", "previous_value": "Old value",
            "current_name": "New field", "current_value": "New value"}]})

    def test_unknown_or_rebound_identity_has_no_invented_old_value(self):
        self.order.items[0].options_raw[0]["id"] = 999
        self.assertEqual(option_changes_by_item(self.order, self.op), {})

    def test_new_order_approved_with_new_values_has_no_notice(self):
        approved = {"items": [{"order_item_id": "line",
                              "options_raw": deepcopy(self.order.items[0].options_raw)}]}
        identity = {key: self.op.get(key) for key in (
            "_id", "user_id", "order_number", "revision", "workflow_fingerprint")}
        self.op["business_snapshot"] = build_snapshot(
            {"raw_by_source": {"salla_direct": {"items": []}}}, approved, {},
            identity=identity, schema_version=2)
        self.assertEqual(option_changes_by_item(self.order, self.op), {})
        self.op["user_id"] = "other"
        self.assertEqual(option_changes_by_item(self.order, self.op), {})

    def test_annotation_keeps_ready_and_source_immutable_and_removes_duplicate_display(self):
        rows = [{"order_item_id": "line", "assembly_ready": True, "can_mark_ready": True,
                 "specifications": [{"name": "New field", "value": "New value"},
                                    {"name": "Old field", "value": "Old value"},
                                    {"name": "Size", "value": "M"}]}]
        before = deepcopy(rows)
        after = annotate_piece_options(rows, option_changes_by_item(self.order, self.op))
        self.assertEqual(rows, before)
        self.assertTrue(after[0]["assembly_ready"])
        self.assertTrue(after[0]["can_mark_ready"])
        self.assertEqual(len(after[0]["specifications"]), 2)
        self.assertIn("change", after[0]["specifications"][0])
        self.assertNotIn("change", after[0]["specifications"][1])

    def test_operational_cards_and_missing_current_option_are_unchanged(self):
        rows = [{"order_item_id": "line", "is_operational_item": True,
                 "specifications": [{"name": "New field", "value": "New value"}]}]
        self.assertEqual(annotate_piece_options(rows, option_changes_by_item(self.order, self.op)), rows)


class NoticeReadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_is_scoped_and_superseded_approval_cannot_display_changes(self):
        collection = SimpleNamespace(find_one=AsyncMock(return_value={"superseded_by": "later"}))
        db = SimpleNamespace(order_review_completion_operations=collection)
        rows = [{"order_item_id": "line", "specifications": []}]
        result = await assembly_option_notices(
            db, user_id="owner", workflow={"completion_mode": "mezan_local_v1",
                                         "review_completion_operation_id": "approval"},
            order=SimpleNamespace(order_number="order"), rows=rows)
        self.assertEqual(result, rows)
        collection.find_one.assert_awaited_once_with({
            "_id": "approval", "user_id": "owner", "order_number": "order",
            "state": "completed", "completion_mode": "mezan_local_v1"})
