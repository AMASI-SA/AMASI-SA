"""Local accounting boundary regressions for confirmed Salla carrier changes.

These exercise real fee preparation and the post entry point with in-memory
collections. They prove rejection before financial writes, not Mongo transaction
isolation or successful posting; those remain covered by the replica P02 suite.
"""
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from mongomock_motor import AsyncMongoMockClient

import accounting_shipping_p02 as shipping
from accounting_public_errors import public_accounting_error


class CurrentShippingAccountingGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient()["current_shipping_guard"]
        self.owner = "SYN-OWNER"
        self.evidence = {
            "_id": "SYN-EVIDENCE",
            "id": "SYN-EVIDENCE",
            "user_id": self.owner,
            "order_number": "SYN-ORDER",
            "conflict": False,
            "delivery_source_text": "2026-09-21 10:00:00",
            "shipping_company": "iMile للتوصيل",
            "waybill": "SYN-WAYBILL",
            "shipping_cost_source": "24.07",
            "source": "salla_orders_export",
        }
        await self.db.mz2_salla_order_evidence.insert_one(deepcopy(self.evidence))
        self.rate = {
            "id": "SYN-RATE",
            "courier_id": "imile",
            "name": "iMile",
            "aliases_normalized": ["imile", "imile للتوصيل"],
            "effective_at": "2026-09-01T00:00:00+00:00",
            "revision": 1,
            "verification_status": "approved",
            "total_fee": "17.25",
            "evidence_ref": "SYN-RATE-EVIDENCE",
            "tax_treatment": "gross_expense_no_input_vat",
        }
        await self.db.mz2_shipping_rate_policies.insert_one({
            "_id": self.owner,
            "user_id": self.owner,
            "revision": 1,
            "versions": [deepcopy(self.rate)],
            "audit": [],
        })

    async def current(self, company="مندوب الرياض", **changes):
        observation = {
            "company_name": company,
            "company_code": "SYN-NEW-CARRIER",
            "company_logo": None,
            "method": None,
            "shipment_id": "SYN-SHIPMENT",
            "provider_updated_at": "2026-09-22T08:00:00+00:00",
            "source_kind": "order",
            "source_path": "shipping.company",
            "status": "pending",
            "tracking_number": None,
            "tracking_url": None,
            "label_url": None,
        }
        observation.update(changes.pop("observation", {}))
        row = {
            "user_id": self.owner,
            "order_number": self.evidence["order_number"],
            "shipping_company": company,
            "salla_shipping_current": observation,
            **changes,
        }
        await self.db.unified_orders.insert_one(row)
        return row

    async def prepare(self):
        return await shipping.prepare_courier_fee(
            self.db, owner=self.owner, evidence_id=self.evidence["id"],
        )

    async def test_changed_store_courier_blocks_old_import_without_mutating_evidence(self):
        await self.current()
        before = await self.db.mz2_salla_order_evidence.find_one({"id": self.evidence["id"]})
        with self.assertRaisesRegex(
            shipping.ShippingAccountingError,
            "shipping_current_carrier_conflict_review_required",
        ):
            await self.prepare()
        self.assertEqual(before, await self.db.mz2_salla_order_evidence.find_one({"id": self.evidence["id"]}))
        self.assertEqual(await self.db.mz2_shipping_accounting_events.count_documents({}), 0)

    async def test_known_aliases_match_without_mixing_numeric_carrier_code(self):
        await self.current("i-mile", observation={"company_code": "999999"})
        proposal = await self.prepare()
        self.assertEqual(proposal["state"], "eligible")
        self.assertEqual(proposal["facts"]["courier_id"], "imile")
        self.assertEqual(proposal["facts"]["total_fee"], "17.25")

    async def test_approved_policy_aliases_can_identify_custom_carrier(self):
        await self.db.mz2_salla_order_evidence.update_one(
            {"id": self.evidence["id"]}, {"$set": {"shipping_company": "SYN old label"}},
        )
        custom = {**self.rate, "courier_id": "syn-custom", "name": "SYN courier",
                  "aliases_normalized": ["syn old label", "syn new label"]}
        await self.db.mz2_shipping_rate_policies.update_one(
            {"_id": self.owner}, {"$set": {"versions": [custom]}},
        )
        await self.current("SYN new label")
        self.assertEqual((await self.prepare())["facts"]["courier_id"], "syn-custom")

    async def test_explicit_export_reference_wins_over_internal_number_collision(self):
        await self.db.mz2_salla_order_evidence.update_one(
            {"id": self.evidence["id"]}, {"$set": {"order_reference": "SYN-PUBLIC-REF"}},
        )
        await self.current("iMile", order_number=self.evidence["order_number"])
        await self.current(order_number="SYN-PUBLIC-REF", order_id=self.evidence["order_number"])
        with self.assertRaisesRegex(shipping.ShippingAccountingError, "shipping_current_carrier_conflict_review_required"):
            await self.prepare()

    async def test_internal_order_id_supported_when_export_has_no_public_reference(self):
        await self.current(order_number="SYN-PUBLIC-REF", order_id=self.evidence["order_number"])
        with self.assertRaisesRegex(shipping.ShippingAccountingError, "shipping_current_carrier_conflict_review_required"):
            await self.prepare()

    async def test_another_owners_observation_does_not_block(self):
        await self.current(user_id="SYN-OTHER-OWNER")
        self.assertEqual((await self.prepare())["state"], "eligible")

    async def test_missing_observation_uses_existing_behavior_and_ignores_raw_history(self):
        await self.db.unified_orders.insert_one({
            "user_id": self.owner, "order_number": self.evidence["order_number"],
            "shipping_company": "مندوب الرياض",
            "raw_by_source": {"salla_direct": {"shipments": [{"courier_name": "مندوب الرياض"}]}},
        })
        self.assertEqual((await self.prepare())["state"], "eligible")

    async def test_non_provider_observation_is_not_trusted(self):
        await self.current(observation={"source_kind": "excel"})
        self.assertEqual((await self.prepare())["state"], "eligible")

    async def test_numeric_name_does_not_get_compared_to_a_human_carrier_name(self):
        await self.current("12345")
        with self.assertRaisesRegex(shipping.ShippingAccountingError, "shipping_current_carrier_unresolved_review_required"):
            await self.prepare()

    async def test_confirmed_code_only_carrier_blocks_reusing_old_import_without_mutation(self):
        await self.current(None, observation={"company_name": None, "company_code": "999999"})
        before = await self.db.mz2_salla_order_evidence.find_one({"id": self.evidence["id"]})
        with self.assertRaisesRegex(shipping.ShippingAccountingError, "shipping_current_carrier_unresolved_review_required"):
            await self.prepare()
        self.assertEqual(before, await self.db.mz2_salla_order_evidence.find_one({"id": self.evidence["id"]}))
        self.assertEqual(await self.db.mz2_shipping_accounting_events.count_documents({}), 0)

    async def test_provider_observation_without_source_path_does_not_fabricate_conflict(self):
        await self.current(observation={"source_path": None})
        self.assertEqual((await self.prepare())["state"], "eligible")

    async def test_numeric_public_reference_matches_numeric_transport(self):
        await self.db.mz2_salla_order_evidence.update_one(
            {"id": self.evidence["id"]}, {"$set": {"order_reference": "275812811"}},
        )
        await self.current(order_number=275812811)
        with self.assertRaisesRegex(shipping.ShippingAccountingError, "shipping_current_carrier_conflict_review_required"):
            await self.prepare()

    async def test_public_review_codes_are_returned_without_exposing_source_data(self):
        for code in ("shipping_current_carrier_conflict_review_required",
                     "shipping_current_carrier_unresolved_review_required",
                     "shipping_current_order_ambiguous_review_required",
                     "shipping_current_shipment_cancelled_review_required"):
            self.assertEqual(public_accounting_error(shipping.ShippingAccountingError(code)), code)
        self.assertEqual(public_accounting_error(shipping.ShippingAccountingError("private source data")), "accounting_request_rejected")

    async def test_cancelled_current_shipment_requires_review_even_for_same_carrier(self):
        for status in ("cancelled", "canceled", "void", "deleted"):
            with self.subTest(status=status):
                await self.db.unified_orders.delete_many({})
                await self.current("iMile", observation={"status": status})
                with self.assertRaisesRegex(shipping.ShippingAccountingError, "shipping_current_shipment_cancelled_review_required"):
                    await self.prepare()

    async def test_ambiguous_order_identity_requires_review(self):
        await self.current("iMile")
        await self.current("مندوب الرياض")
        with self.assertRaisesRegex(shipping.ShippingAccountingError, "shipping_current_order_ambiguous_review_required"):
            await self.prepare()

    async def test_exact_already_posted_replay_preserves_original_facts(self):
        proposal = await self.prepare()
        posted = {
            "_id": proposal["event_id"], "user_id": self.owner,
            "kind": "courier_fee", "status": "posted",
            "facts": deepcopy(proposal["facts"]),
            "economic_hash": proposal["economic_hash"], "txn_group_id": "SYN-POSTED-GROUP",
        }
        await self.db.mz2_shipping_accounting_events.insert_one(posted)
        await self.current()
        replay = await self.prepare()
        self.assertEqual(replay["state"], "already_posted")
        self.assertEqual(replay["txn_group_id"], "SYN-POSTED-GROUP")
        self.assertEqual(await self.db.mz2_shipping_accounting_events.find_one({"_id": proposal["event_id"]}), posted)

    async def test_change_after_preview_is_rechecked_before_new_post_and_no_writes(self):
        self.assertEqual((await self.prepare())["state"], "eligible")
        await self.current()
        before = await self.db.mz2_salla_order_evidence.find_one({"id": self.evidence["id"]})

        async def local_owner(db, owner, callback):
            return await callback(db)

        with patch.object(shipping, "atomic_owner", new=local_owner), \
             patch.object(shipping, "require_p02_shipping_financial_writes", new_callable=AsyncMock) as gate, \
             patch.object(shipping, "read_mz2_write_balances", new_callable=AsyncMock) as balances, \
             patch.object(shipping, "post_txn_group", new_callable=AsyncMock,
                          return_value={"txn_group_id": "SYN-UNEXPECTED-GROUP"}) as ledger:
            with self.assertRaisesRegex(shipping.ShippingAccountingError, "shipping_current_carrier_conflict_review_required"):
                await shipping.post_courier_fee(
                    self.db, owner=self.owner, actor={"id": self.owner}, evidence_id=self.evidence["id"],
                )
            gate.assert_not_awaited()
            balances.assert_not_awaited()
            ledger.assert_not_awaited()
        self.assertEqual(before, await self.db.mz2_salla_order_evidence.find_one({"id": self.evidence["id"]}))
        self.assertEqual(await self.db.mz2_shipping_accounting_events.count_documents({}), 0)
        self.assertEqual(await self.db.transactions.count_documents({}), 0)


if __name__ == "__main__":
    unittest.main()
