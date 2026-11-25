"""Event-scoped Salla proof can never turn partial historical coverage into global success."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from operational_salla_event_source_gate import (
    FLAG, EventSourceHold, require_verified_event_source,
)

NOW = datetime(2026, 11, 24, 18, 44, tzinfo=timezone.utc)


def record(**kwargs):
    data = {
        "schedule_version": "daily_10_riyadh_update_v1", "status": "partial",
        "completed_slot": "2026-11-24",
        "last_result": {
            "errors_count": 0, "incomplete_count": 0,
            "truncated": False, "coverage_complete": False,
            "coverage_reason": "provider_modified_since_filter_unavailable",
            "discovery": "recent_creations_and_verified_local_source_activity",
        },
    }
    data.update(kwargs)
    return data


def admission(row=None, *, bound=True, when=NOW):
    return require_verified_event_source(row or record(), now=when,
                                         event_proof_bound=bound)


class EventScopedAdmissionTests(unittest.TestCase):
    def test_default_flag_off(self):
        with patch.dict(os.environ, {FLAG:"0"}):
            with self.assertRaisesRegex(EventSourceHold, "gate_disabled"):
                admission()

    def test_partial_history_only_with_exact_signed_event_proof(self):
        with patch.dict(os.environ, {FLAG:"1"}):
            result = admission()
        self.assertEqual(result["basis"],"new_verified_salla_event_only")
        self.assertFalse(result["historical_coverage_complete"])
        self.assertEqual(result["permission"],"first_payment_canonical_cas_only")

    def test_no_signed_or_native_source_proof_denied(self):
        with patch.dict(os.environ, {FLAG:"1"}):
            with self.assertRaisesRegex(EventSourceHold,"proof_unverified"):
                admission(bound=False)

    def test_missing_modified_since_reason_not_a_waiver(self):
        with patch.dict(os.environ, {FLAG:"1"}):
            bad=record()
            bad["last_result"]["coverage_reason"]="undocumented_reason"
            with self.assertRaisesRegex(EventSourceHold,"technical_incomplete"):
                admission(bad)

    def test_actual_provider_error_not_treated_as_history_only(self):
        with patch.dict(os.environ, {FLAG:"1"}):
            for field,value in [
                ("errors_count",1),("incomplete_count",2),
                ("truncated",True),("coverage_complete",True),
                ("discovery","unknown"),
            ]:
                bad=record()
                bad["last_result"][field]=value
                with self.assertRaisesRegex(EventSourceHold,"technical_incomplete"):
                    admission(bad)

    def test_failed_or_running_schedule_denied(self):
        with patch.dict(os.environ, {FLAG:"1"}):
            for status in ("failed","running","incomplete","completed"):
                with self.assertRaisesRegex(EventSourceHold,"daily_status_unverified"):
                    admission(record(status=status))

    def test_stale_daily_slot_denied(self):
        with patch.dict(os.environ, {FLAG:"1"}):
            with self.assertRaisesRegex(EventSourceHold,"daily_slot_unverified"):
                admission(record(completed_slot="2026-11-23"))

    def test_morning_uses_yesterday_cycle(self):
        at=datetime(2026,11,24,6,0,tzinfo=timezone.utc) # 09:00 Riyadh
        with patch.dict(os.environ,{FLAG:"1"}):
            self.assertFalse(admission(
                record(completed_slot="2026-11-23"),when=at)["historical_coverage_complete"])

    def test_morning_does_not_claim_new_day_complete(self):
        at=datetime(2026,11,24,6,0,tzinfo=timezone.utc)
        with patch.dict(os.environ,{FLAG:"1"}):
            with self.assertRaisesRegex(EventSourceHold,"daily_slot_unverified"):
                admission(record(completed_slot="2026-11-24"),when=at)

    def test_owner_source_outage_cannot_claim_success_by_missing_record(self):
        with patch.dict(os.environ,{FLAG:"1"}):
            with self.assertRaises(EventSourceHold):
                admission({"status":"partial"})


if __name__=="__main__":
    unittest.main()
