"""The saved incident evidence is incomplete; refusing it is the contract."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("offline_recovery_analysis", ROOT / "scripts/review_completion_recovery_analyze.py")
analyzer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyzer)


class OfflineEvidenceTests(unittest.TestCase):
    def test_nine_saved_hashes_cannot_authorize_recovery(self):
        evidence = json.loads((ROOT / "docs/operations/review-business-snapshot/CASE_ANALYSIS.json").read_text())
        result = analyzer.analyze(evidence)
        self.assertEqual(result["counts"], {"total": 9, "safe_to_resume": 0,
                         "requires_review": 9, "insufficient_evidence": 9})
        self.assertTrue(all(row["operation_id"] is None for row in result["cases"]))
        self.assertFalse(result["production_replay"])

    def test_duplicate_evidence_does_not_select_an_arbitrary_snapshot(self):
        with self.assertRaises(ValueError):
            analyzer.analyze({"cases": [{"order_number": "291967952"}] * 2})

    def test_outside_allowlist_and_malformed_complete_evidence_refuse(self):
        cases = [{"order_number": "outside"}, {"order_number": "291967952",
                 **{key: {} for key in analyzer.EVIDENCE_FIELDS}}]
        result = analyzer.analyze({"cases": cases})
        self.assertEqual(result["counts"]["safe_to_resume"], 0)
