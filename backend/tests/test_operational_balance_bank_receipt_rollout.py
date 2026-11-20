from copy import deepcopy
import unittest

from operational_bank_receipt_rollout import (
    MARKER, initialize_admission, needs_historical_review, posted_review_required, recorded_bank_credits,
)


START = "2026-11-01T00:00:00+03:00"
OLD_ORDER = "2026-11-10T15:00:00+03:00"
ACTIVATION = "2026-11-20T18:00:00+03:00"
NEW = "2026-11-20T18:01:00+03:00"


class ReceiptRolloutTests(unittest.TestCase):
    def setUp(self):
        self.state = {"status": "active", "owner_id": "owner", "started_at": START, "engine": {}}

    def test_first_committed_refresh_boundary_is_frozen_and_no_read_changes_it(self):
        original = deepcopy(self.state)
        self.assertTrue(needs_historical_review(self.state, order_created_at=OLD_ORDER))
        self.assertEqual(original, self.state)
        initialize_admission(self.state, ACTIVATION)
        saved = deepcopy(self.state)
        initialize_admission(self.state, NEW)
        self.assertEqual(self.state, saved)
        for created_at in (OLD_ORDER, ACTIVATION):
            self.assertTrue(needs_historical_review(self.state, order_created_at=created_at))
        self.assertFalse(needs_historical_review(self.state, order_created_at=NEW))
        self.assertTrue(needs_historical_review(self.state, order_created_at=OLD_ORDER, reached_at=ACTIVATION))
        self.assertFalse(needs_historical_review(self.state, order_created_at=OLD_ORDER, reached_at=NEW))

    def test_newly_started_store_and_corrupt_or_cross_owner_markers(self):
        initialize_admission(self.state, START)
        self.assertFalse(needs_historical_review(self.state, order_created_at=OLD_ORDER))
        saved = deepcopy(self.state)
        for key, value in [("owner_id", "other"), ("version", True), ("started_at", NEW),
                           ("activated_at", "invalid")]:
            state = deepcopy(saved)
            state["engine"][MARKER][key] = value
            before = deepcopy(state)
            initialize_admission(state, NEW)
            self.assertEqual(before, state)
            self.assertTrue(needs_historical_review(state, order_created_at=NEW))
        self.assertTrue(needs_historical_review(self.state, order_created_at=NEW, reached_at=OLD_ORDER))

    def test_existing_money_is_reviewed_without_mutation_and_ordinary_payments_are_excluded(self):
        entry = {"automatic_order_bank": True, "direction": "incoming", "party_type": "bank",
                 "order_number": "example-1", "occurred_at": ACTIVATION, "amount": "30.00"}
        self.state["movements"] = [entry, {**entry, "order_number": "manual", "automatic_order_bank": False},
                                   {**entry, "order_number": "other", "party_type": "provider"},
                                   {**entry, "order_number": "out", "direction": "outgoing"}]
        original = deepcopy(self.state)
        found = recorded_bank_credits(self.state)
        self.assertEqual(set(found), {"example-1"})
        self.assertTrue(posted_review_required(found["example-1"], reached=False))
        self.assertTrue(posted_review_required(entry, reached=True, reached_at=NEW))
        self.assertFalse(posted_review_required(entry, reached=True, reached_at=OLD_ORDER))
        self.assertFalse(posted_review_required(None, reached=False))
        self.assertEqual(self.state, original)
