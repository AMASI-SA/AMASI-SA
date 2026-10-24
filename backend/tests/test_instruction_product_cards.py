import base64
import unittest
from copy import deepcopy
from instruction_product_cards import product_cards

class ProductCardTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = "a" * 32, "b" * 32
        self.items = [{"order_item_id": "i1", "name": "same", "options_normalized": {"size": "current"}}, {"order_item_id": "i2", "name": "same"}, {"order_item_id": "i3", "name": "unassigned"}]
        self.pieces = [{"piece_id": self.a, "order_item_id": "i1", "product_options_snapshot": {"size": "M", "name": "A"}, "image_url": "a.png"}, {"piece_id": self.b, "order_item_id": "i2", "product_options_snapshot": {"size": "S", "name": "B"}}]
        self.instruction = {"scope": "order", "proposed_changes": [{"order_item_id": "i1", "field": "size", "previous": "M", "requested": "L"}]}

    def test_employee_only_sees_authorized_piece_and_exact_before_requested(self):
        original = deepcopy(self.pieces)
        cards = product_cards(self.instruction, [{"piece_id": self.a}], self.pieces, self.items)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["before_options"], {"size": "M", "name": "A"})
        self.assertEqual(card["requested_options"], {"size": "L", "name": "A"})
        self.assertEqual(card["barcode_value"], "MEZAN-PIECE:" + self.a)
        self.assertTrue(base64.b64decode(card["barcode_image"].split(",")[1]).startswith(b"\x89PNG"))
        self.assertEqual(self.pieces, original)

    def test_creator_whole_order_includes_unassigned_without_inventing_barcode(self):
        cards = product_cards(self.instruction, [], self.pieces, self.items, creator=True)
        self.assertEqual(len(cards), 3)
        self.assertEqual(cards[1]["changed_fields"], [])
        self.assertEqual(cards[2]["barcode_value"], "")
        self.assertEqual(cards[2]["baseline_source"], "current_order")

    def test_item_and_piece_scope_never_include_other_items(self):
        self.instruction.update(scope="item", target_ids=["i1"])
        self.assertEqual(len(product_cards(self.instruction, [], self.pieces, self.items, creator=True)), 1)
        self.instruction.update(scope="piece", target_ids=[self.b])
        cards = product_cards(self.instruction, [], self.pieces, self.items, creator=True)
        self.assertEqual([card["id"] for card in cards], [self.b])
        self.assertEqual(cards[0]["changed_fields"], [])
        self.assertEqual(product_cards(self.instruction, [{"piece_id": self.a}], self.pieces, self.items), [])

    def test_repeated_task_stages_do_not_duplicate_cards(self):
        tasks = [{"piece_id": self.a}, {"piece_id": self.a}]
        self.assertEqual(len(product_cards(self.instruction, tasks, self.pieces, self.items)), 1)

if __name__ == "__main__": unittest.main()
