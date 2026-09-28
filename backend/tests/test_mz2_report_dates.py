"""Accounting day boundaries and authoritative date validation."""
from datetime import datetime, timezone
import unittest
from accounting_report_dates import accounting_instant, report_cutoff

class ReportDateTests(unittest.TestCase):
    def test_august_end_is_exclusive_saudi_midnight(self):
        self.assertEqual(report_cutoff("2026-08-31"), datetime(2026, 8, 31, 21, tzinfo=timezone.utc))
        self.assertLess(accounting_instant({"metadata": {"accounting_at": "2026-08-31T23:30:00+03:00"}}), report_cutoff("2026-08-31"))
        self.assertEqual(accounting_instant({"metadata": {"recognized_at": "2026-09-01"}}), report_cutoff("2026-08-31"))

    def test_authoritative_invalid_value_never_falls_back_to_audit(self):
        for value in ("bad", "2026-08-31T20:00:00", "", 12, None):
            with self.assertRaises(ValueError):
                accounting_instant({"metadata": {"accounting_at": value}, "posted_at": "2026-09-20T12:00:00Z"})
        with self.assertRaises(ValueError): report_cutoff("2026-8-1")
        with self.assertRaises(ValueError): accounting_instant({})

    def test_existing_recognition_and_settlement_date_precede_audit(self):
        for key in ("recognized_at", "statement_date"):
            self.assertEqual(accounting_instant({"metadata": {key: "2026-08-31"}, "posted_at": "2026-09-20T12:00:00Z"}), datetime(2026, 8, 30, 21, tzinfo=timezone.utc))
