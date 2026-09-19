"""A completed concurrent audit invalidates a previously read activation snapshot."""
import unittest
from unittest.mock import patch

import test_recovery_404_integration as fixture

c, TARGET = fixture.c, fixture.TARGET


class ActivationAuditRace(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.Integration.asyncSetUp
    asyncTearDown = fixture.Integration.asyncTearDown
    prepare = fixture.Integration.prepare

    async def exercise_race(self, operation, *, strip_lease_guard=False):
        doc = await self.prepare()
        await c.pause(self.db)
        await self.db.qoyod_404_outcomes.update_one(
            {"_id": f"{c.CAMPAIGN}:{TARGET}"},
            {"$set": {"state": "rounding_review", "reason": "existing_invoice_rounding_requires_settlement"}})
        collection = self.db.qoyod_404_campaigns
        original_update = collection.update_one
        raced = False

        async def concurrent_audit(query, update, *args, **kwargs):
            nonlocal raced
            fields = update.get("$set", {})
            final_mutation = (fields.get("state") == "active" if operation == "activate"
                              else "release_reviewed_by" in fields)
            if final_mutation and not raced:
                raced = True
                # Full real audit completes between outcome validation and CAS.
                # The formerly isolated invoice is no longer proved present.
                await c.audit_pending(self.db, self.factory)
            if final_mutation and strip_lease_guard:
                # Regression control: reproduce the previous CAS query only.
                query = {key: value for key, value in query.items() if key != "lease_token"}
            return await original_update(query, update, *args, **kwargs)

        error = None
        # mongomock-motor creates a new wrapper on attribute access. Pin it so
        # service calls and this deterministic interleaving share one wrapper.
        with patch.object(self.db, "qoyod_404_campaigns", collection), \
                patch.object(collection, "update_one", side_effect=concurrent_audit):
            try:
                if operation == "activate":
                    await c.activate(self.db, doc["fingerprint"], "store", "store", "test-release")
                else:
                    await c.review_release(self.db, doc["fingerprint"], "store", "store", "new-release")
            except ValueError as exc:
                error = exc
        self.assertTrue(raced)
        if strip_lease_guard:
            self.assertIsNone(error, "old CAS accepts stale evidence, proving the regression")
        else:
            self.assertIsInstance(error, ValueError)
        current = await c.report(self.db)
        self.assertEqual(current["state"], "active" if strip_lease_guard and operation == "activate" else "paused")
        self.assertEqual(current["release_identity"], "new-release" if strip_lease_guard and operation == "review-release" else "test-release")
        self.assertFalse(current["can_activate"])
        row = next(row for row in current["results"] if row["reference"] == TARGET)
        self.assertEqual(row["state"], "review")
        self.assertEqual(row["reason"], "submitted_invoice_not_found_do_not_retry")
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))

    async def test_activation_rejects_completed_concurrent_audit(self):
        await self.exercise_race("activate")

    async def test_release_review_rejects_completed_concurrent_audit(self):
        await self.exercise_race("review-release")

    async def test_old_activation_query_accepts_stale_evidence_regression_control(self):
        await self.exercise_race("activate", strip_lease_guard=True)

    async def test_old_release_review_query_accepts_stale_evidence_regression_control(self):
        await self.exercise_race("review-release", strip_lease_guard=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
