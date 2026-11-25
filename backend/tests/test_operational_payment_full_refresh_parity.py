"""Inspect financial parity before/direct/full-refresh without writing funds."""
import copy
import unittest

from operational_payment_full_refresh_parity import compare_first_provider_capture
from operational_payment_direct_provider import apply_first_provider_capture
from test_operational_payment_direct_provider import state, normalized, STAMP


def snapshots():
    before=state()
    direct=copy.deepcopy(before)
    apply_first_provider_capture(direct,order_id="order1",order_number="10001",
                                 normalized=normalized(),as_of=STAMP)
    refreshed=copy.deepcopy(direct)  # Synthetic equality fixture, NOT actual UAT
    return before,direct,refreshed


def compare(before,direct,refreshed):
    return compare_first_provider_capture(
        before,direct,refreshed,order_id="order1",
        provider="tabby",reference="native1",
    )


class FullRefreshParityTests(unittest.TestCase):
    def test_synthetic_identical_canonical_claim_is_only_preliminary_match(self):
        before,direct,refreshed=snapshots()
        originals=copy.deepcopy((before,direct,refreshed))
        result=compare(before,direct,refreshed)
        self.assertEqual(result["status"],"match")
        self.assertEqual(result["issues"],[])
        self.assertFalse(result["release_verified"])
        self.assertEqual((before,direct,refreshed),originals)

    def test_full_refresh_fee_difference_is_never_silent(self):
        before,direct,refreshed=snapshots()
        refreshed["engine"]["provider_reports"]["provider:order1"]["estimated_fees"]="99.99"
        result=compare(before,direct,refreshed)
        self.assertEqual(result["status"],"conflict")
        self.assertIn("payment_parity_fee_estimated_fees_delta",result["issues"])

    def test_full_refresh_date_change_is_conflict(self):
        before,direct,refreshed=snapshots()
        refreshed["engine"]["obligations"]["provider:order1"]["business_date"]="2026-11-25"
        self.assertIn("payment_parity_claim_business_date_delta",
                      compare(before,direct,refreshed)["issues"])

    def test_missing_full_refresh_capture_is_conflict(self):
        before,direct,refreshed=snapshots()
        refreshed["engine"]["facts"].pop("capture:native1")
        self.assertIn("payment_parity_refresh_capture_missing_or_changed",
                      compare(before,direct,refreshed)["issues"])

    def test_missing_full_refresh_fee_report_is_conflict(self):
        before,direct,refreshed=snapshots()
        refreshed["engine"]["provider_reports"].pop("provider:order1")
        self.assertIn("payment_parity_refresh_fee_projection_missing",
                      compare(before,direct,refreshed)["issues"])

    def test_recalculation_must_not_add_bank_movement_for_payment(self):
        before,direct,refreshed=snapshots()
        refreshed["movements"].append({"id":"duplicate-receipt","amount":"300.00"})
        self.assertIn("payment_parity_refresh_bank_or_opening_changed",
                      compare(before,direct,refreshed)["issues"])

    def test_existing_prior_claim_not_classified_new(self):
        before,direct,refreshed=snapshots()
        before["engine"]["obligations"]["provider:order1"]={
            "party_id":"tabby","confirmed":"300.00",
        }
        self.assertIn("payment_parity_not_first_capture",
                      compare(before,direct,refreshed)["issues"])

    def test_fee_policy_identity_must_match(self):
        before,direct,refreshed=snapshots()
        refreshed["engine"]["provider_reports"]["provider:order1"]["policy_id"]="different-policy"
        self.assertIn("payment_parity_fee_policy_delta",
                      compare(before,direct,refreshed)["issues"])


if __name__=="__main__":
    unittest.main()
