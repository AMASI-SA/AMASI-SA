"""Bounded Salla scheduler health is diagnostic-only and tenant-neutral."""
import copy
import unittest
from datetime import datetime, timezone

from operational_salla_schedule_diagnostics import diagnose_salla_daily_cycle

NOW=datetime(2026,11,24,21,55,tzinfo=timezone.utc)


def row(**overrides):
    data={
        "schedule_version":"daily_10_riyadh_update_v1",
        "status":"completed",
        "started_at":"2026-11-24T07:00:00+00:00",
        "last_success_at":"2026-11-24T07:22:00+00:00",
        "completed_slot":"2026-11-24",
        "attempts":1,
        "last_result":{"errors_count":0,"incomplete_count":0,
                       "truncated":False,"coverage_complete":True},
    }
    data.update(overrides)
    return data


class ScheduleDiagnosticsTests(unittest.TestCase):
    def test_complete_does_not_claim_financial_posting_safety(self):
        record=row()
        original=copy.deepcopy(record)
        result=diagnose_salla_daily_cycle(record,now=NOW)
        self.assertEqual(result["state"],"completed")
        self.assertFalse(result["eligible_for_financial_posting"])
        self.assertEqual(record,original)
        self.assertEqual(result["coverage"]["errors_count"],0)

    def test_failed_daily_run_keeps_last_success_without_false_recovery(self):
        result=diagnose_salla_daily_cycle(
            row(status="failed",last_error_type="TimeoutError",
                last_result={"errors_count":2,"incomplete_count":3,
                             "truncated":False,"coverage_complete":False}),now=NOW)
        self.assertEqual(result["state"],"failed")
        self.assertEqual(result["last_error_class"],"TimeoutError")
        self.assertEqual(result["coverage"]["incomplete_count"],3)
        self.assertIsNotNone(result["last_success_at"])

    def test_scheduled_partial_is_failed_not_successful(self):
        result=diagnose_salla_daily_cycle(row(status="partial"),now=NOW)
        self.assertEqual(result["state"],"failed")

    def test_partial_due_to_missing_modified_since_is_not_outage_or_success(self):
        result=diagnose_salla_daily_cycle(row(
            status="partial",
            last_result={
                "errors_count":0, "incomplete_count":0, "truncated":False,
                "coverage_complete":False,
                "coverage_reason":"provider_modified_since_filter_unavailable",
                "discovery":"recent_creations_and_verified_local_source_activity",
                "raw_order_ids":["PRIVATE"],
            }),now=NOW)
        self.assertEqual(result["state"],"partial_coverage")
        self.assertEqual(result["reason"],"salla_modified_history_coverage_unprovable")
        self.assertEqual(result["coverage_limitation"],
                         "provider_modified_since_filter_unavailable")
        self.assertFalse(result["eligible_for_financial_posting"])
        self.assertNotIn("PRIVATE",str(result))

    def test_unknown_provider_error_does_not_claim_coverage_limitation(self):
        result=diagnose_salla_daily_cycle(row(
            status="failed",
            last_result={
                "errors_count":7, "incomplete_count":4, "truncated":False,
                "coverage_complete":False, "coverage_reason":"sensitive_private_error",
                "discovery":"another",
            }),now=NOW)
        self.assertEqual(result["state"],"failed")
        self.assertIsNone(result["coverage_limitation"])

    def test_unknown_exception_details_are_never_returned(self):
        record=row(status="failed",last_error_type="secret://salla-token",
                   log_id="private-log", merchant_id="private", last_result={
                       "errors_count":1,"incomplete_count":0,
                       "truncated":False,"coverage_complete":False,
                       "failed_order_ids":["customer-1"],"secret":"raw"})
        result=diagnose_salla_daily_cycle(record,now=NOW)
        self.assertIsNone(result["last_error_class"])
        self.assertNotIn("private",str(result))
        self.assertNotIn("secret",str(result))
        self.assertNotIn("customer",str(result))
        self.assertNotIn("token",str(result))
        self.assertNotIn("log_id",result)

    def test_missing_or_bad_record_does_not_invent_status(self):
        self.assertEqual(diagnose_salla_daily_cycle(None,now=NOW)["state"],"unknown")
        self.assertEqual(diagnose_salla_daily_cycle(row(schedule_version="legacy"),now=NOW)["state"],"unknown")
        self.assertEqual(diagnose_salla_daily_cycle(row(status="unknown"),now=NOW)["state"],"unknown")

    def test_missing_coverage_never_claims_completed(self):
        result=diagnose_salla_daily_cycle(row(last_result={"errors_count":0}),now=NOW)
        self.assertEqual(result["state"],"unknown")
        self.assertFalse(result["eligible_for_financial_posting"])

    def test_running_and_retry_are_visible_not_started_as_new_run(self):
        result=diagnose_salla_daily_cycle(row(status="running",attempts=2),now=NOW)
        self.assertEqual(result["state"],"running")
        self.assertEqual(result["attempts"],2)

    def test_malformed_numeric_fields_sanitized(self):
        record=row(last_result={"errors_count":True,"incomplete_count":-5,
                                "truncated":"false","coverage_complete":"yes"})
        result=diagnose_salla_daily_cycle(record,now=NOW)
        self.assertEqual(result["state"],"unknown")
        self.assertIsNone(result["coverage"]["errors_count"])
        self.assertIsNone(result["coverage"]["incomplete_count"])

    def test_invalid_datetime_is_not_repeated_or_guessed(self):
        result=diagnose_salla_daily_cycle(row(started_at="private-key",last_success_at="unknown"),now=NOW)
        self.assertIsNone(result["started_at"])
        self.assertIsNone(result["last_success_at"])
        self.assertEqual(result["state"],"unknown")


if __name__=="__main__":
    unittest.main()
