"""Direct financial posting requires independently confirmed refresh health."""
import unittest
from operational_payment_refresh_health_gate import RefreshHealthHold, require_healthy_source

STAMP = "2026-11-24T21:44:00+03:00"
ASOF = "2026-11-24T20:27:00+03:00"

def state():
    return {"owner_id":"merchant1", "engine":{"as_of":ASOF}}

def health(*,outcome="success",started="2026-11-24T20:20:00+03:00",
           last_success=ASOF,owner="merchant1"):
    return {"owner_id":owner, "last_success_at":last_success,
            "last_attempt":{"outcome":outcome,"started_at":started,
                            "finished_at":"2026-11-24T20:27:00+03:00"}}

class FinancialHealthTests(unittest.TestCase):
    def test_matching_success(self):
        self.assertTrue(require_healthy_source(state(),health(),at=STAMP))
    def test_latest_timeout_blocks_despite_recent_saved_balance(self):
        with self.assertRaisesRegex(RefreshHealthHold,"newer_attempt_failed"):
            require_healthy_source(state(),
                health(outcome="failed",started="2026-11-24T21:35:00+03:00"),
                at=STAMP)
    def test_latest_unknown_blocks(self):
        with self.assertRaises(RefreshHealthHold):
            require_healthy_source(state(),
                health(outcome="unknown",started="2026-11-24T21:35:00+03:00"),
                at=STAMP)
    def test_latest_running_blocks(self):
        with self.assertRaisesRegex(RefreshHealthHold,"attempt_running"):
            require_healthy_source(state(),health(outcome="running"),at=STAMP)
    def test_old_failure_before_confirmed_newer_snapshot(self):
        self.assertTrue(require_healthy_source(state(),health(
            outcome="failed",started="2026-11-24T20:00:00+03:00"),at=STAMP))
    def test_missing_health_fails_closed(self):
        with self.assertRaisesRegex(RefreshHealthHold,"health_missing"):
            require_healthy_source(state(),None,at=STAMP)
    def test_health_owner_mismatch_fails(self):
        with self.assertRaisesRegex(RefreshHealthHold,"owner_conflict"):
            require_healthy_source(state(),health(owner="another"),at=STAMP)
    def test_saved_as_of_must_match_success_receipt(self):
        with self.assertRaisesRegex(RefreshHealthHold,"receipt_mismatch"):
            require_healthy_source(state(),health(last_success="2026-11-24T20:26:00+03:00"),at=STAMP)
    def test_no_future_aggregate(self):
        snapshot=state()
        snapshot["engine"]["as_of"]="2026-11-24T23:00:00+03:00"
        with self.assertRaisesRegex(RefreshHealthHold,"future_snapshot"):
            require_healthy_source(snapshot,health(),at=STAMP)

if __name__=="__main__":
    unittest.main()
