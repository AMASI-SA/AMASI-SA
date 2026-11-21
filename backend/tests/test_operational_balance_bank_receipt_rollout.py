from copy import deepcopy
import unittest

from operational_bank_receipt_rollout import (
    FOUR_STATUS_MARKER, MARKER, initialize_admission, needs_historical_review, recorded_bank_credits,
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

    def test_recorded_money_index_ignores_manual_or_unrelated_payments(self):
        entry = {"automatic_order_bank": True, "direction": "incoming", "party_type": "bank",
                 "order_number": "example-1", "occurred_at": ACTIVATION, "amount": "30.00"}
        self.state["movements"] = [entry, {**entry, "order_number": "manual", "automatic_order_bank": False},
                                   {**entry, "order_number": "other", "party_type": "provider"},
                                   {**entry, "order_number": "out", "direction": "outgoing"}]
        original = deepcopy(self.state)
        found = recorded_bank_credits(self.state)
        self.assertEqual(set(found), {"example-1"})
        self.assertEqual(self.state, original)

    def test_four_status_policy_activated_separately_for_preexisting_store(self):
        initialize_admission(self.state, ACTIVATION)
        del self.state["engine"][FOUR_STATUS_MARKER]
        initialize_admission(self.state, NEW)
        self.assertEqual(self.state["engine"][FOUR_STATUS_MARKER]["activated_at"], "2026-11-20T15:01:00+00:00")
        self.assertNotEqual(self.state["engine"][MARKER], self.state["engine"][FOUR_STATUS_MARKER])
        self.assertTrue(needs_historical_review(self.state, order_created_at=OLD_ORDER, marker_key=FOUR_STATUS_MARKER))
