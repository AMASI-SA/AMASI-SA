"""Pure proof checks; all order/choice identifiers are synthetic."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from order_review_business_snapshot import build_snapshot, _tree
from component_option_currency_recovery import (
    PROOF_KIND, REASON, prove_option_price_currency_change, restore_option_price_currencies,
)


RATE = "12.07292044"
IDENTITY_FIELDS = ("_id", "user_id", "order_number", "revision", "workflow_fingerprint")


def fixture():
    raw = {
        "id": 701, "reference_id": "synthetic-order", "currency": "KWD",
        "status": {"slug": "in_progress"},
        "created_at": "2026-11-01T10:00:00+00:00",
        "date": {"date": "2026-11-01T10:00:00+00:00", "updated": "2026-11-01T10:00:00+00:00"},
        "payment_method": "credit_card", "payment_status": "paid", "paid_amount": 12.42,
        "amounts": {
            "total": {"amount": 12.42, "currency": "KWD"},
            "sub_total": {"amount": 10.8, "currency": "KWD"},
            "tax": {"percent": 15, "amount": {"amount": 1.62, "currency": "KWD"}},
            "discounts": [],
        },
        "exchange_rate": {"base_currency": "SAR", "exchange_currency": "KWD", "rate": RATE},
        "items": [],
    }
    for index, (amount, count) in enumerate(((0.828, 4), (1.242, 3), (0.828, 3))):
        raw["items"].append({
            "id": index + 1001, "product_id": index + 2001, "quantity": 1,
            "amounts": {"total": {"amount": 4.14, "currency": "KWD"}},
            "options": [{
                "id": index * 10 + choice + 1, "name": "Option", "type": "radio",
                "value": {"id": index * 10 + choice + 101, "name": "Choice",
                          "price": {"amount": amount if choice == 0 else 0, "currency": "KWD"}},
            } for choice in range(count)],
        })
    operation = {
        "_id": "synthetic-approval", "user_id": "owner", "order_number": "synthetic-order",
        "revision": 1, "workflow_fingerprint": "synthetic-workflow",
        "state": "completed", "completion_mode": "mezan_local_v1",
    }
    freeze(raw, operation)
    current = deepcopy(raw)
    for index, item in enumerate(current["items"]):
        for choice, option in enumerate(item["options"]):
            option["value"]["price"] = {"amount": (15 if index == 1 else 10) if choice == 0 else 0,
                                         "currency": "SAR"}
    return raw, operation, current


def freeze(raw, operation, *, schema_version=2, normalization_version=3):
    operation["business_snapshot"] = build_snapshot(
        {"raw_by_source": {"salla_direct": raw}}, {}, {},
        identity={key: operation.get(key) for key in IDENTITY_FIELDS},
        schema_version=schema_version, normalization_version=normalization_version,
    )


def price(raw, item=0, option=0):
    return raw["items"][item]["options"][option]["value"]["price"]


class OptionCurrencyProofTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.operation, self.current = fixture()

    def test_three_nonzero_and_seven_zero_options_restore_without_mutation(self):
        before_current, before_operation = deepcopy(self.current), deepcopy(self.operation)
        restored = restore_option_price_currencies(self.current, self.operation)
        self.assertEqual(restored, self.raw)
        self.assertEqual(self.current, before_current)
        self.assertEqual(self.operation, before_operation)
        # Decimal values in provider data would serialize as strings in the
        # component fingerprint. Restored numeric facts must remain JSON numbers.
        self.assertEqual(json.dumps(restored, sort_keys=True), json.dumps(self.raw, sort_keys=True))

    def test_both_sar_minor_units_represent_the_same_approved_kwd_minor_unit(self):
        for amount in (14.99, 15):
            with self.subTest(amount=amount):
                price(self.current, 1)["amount"] = amount
                self.assertEqual(restore_option_price_currencies(self.current, self.operation), self.raw)

    def test_adjacent_native_minor_units_are_rejected(self):
        for item, amount in ((1, 14.98), (1, 15.01), (0, 9.99), (0, 10.01)):
            with self.subTest(item=item, amount=amount):
                current = deepcopy(self.current)
                price(current, item)["amount"] = amount
                self.assertIsNone(restore_option_price_currencies(current, self.operation))

    def test_same_currency_amount_change_and_other_currency_pairs_are_rejected(self):
        for currency, amount in (("KWD", 0.829), ("AED", 10), ("USD", 10), ("sar", 10), ("", 10)):
            with self.subTest(currency=currency, amount=amount):
                current = deepcopy(self.current)
                price(current).update(currency=currency, amount=amount)
                self.assertIsNone(restore_option_price_currencies(current, self.operation))

    def test_fractional_halalah_and_fractional_fils_are_not_rounded_away(self):
        price(self.current, 1)["amount"] = 14.999
        self.assertIsNone(restore_option_price_currencies(self.current, self.operation))
        raw, operation, current = fixture()
        price(raw, 1)["amount"] = 1.2424
        freeze(raw, operation)
        self.assertIsNone(restore_option_price_currencies(current, operation))

    def test_exact_half_fils_boundaries_are_rejected(self):
        raw, operation, current = fixture()
        raw["exchange_rate"]["rate"] = "20"
        freeze(raw, operation)
        current = deepcopy(raw)
        for amount in (24.83, 24.85):
            with self.subTest(amount=amount):
                price(current, 1).update(amount=amount, currency="SAR")
                self.assertIsNone(restore_option_price_currencies(current, operation))
        price(current, 1)["amount"] = 24.84
        self.assertEqual(restore_option_price_currencies(current, operation), raw)

    def test_nonzero_price_requires_real_numeric_positive_finite_values(self):
        for value in (True, False, "10", None, [], {}, float("nan"), float("inf"), -10, 0):
            with self.subTest(value=value):
                current = deepcopy(self.current)
                price(current)["amount"] = value
                self.assertIsNone(restore_option_price_currencies(current, self.operation))

    def test_zero_price_type_and_currency_contract_is_retained(self):
        for value, currency in (("0", "SAR"), (False, "SAR"), (0.01, "SAR"), (0, "sar"), (0, "")):
            with self.subTest(value=value, currency=currency):
                current = deepcopy(self.current)
                price(current, option=1).update(amount=value, currency=currency)
                self.assertIsNone(restore_option_price_currencies(current, self.operation))

    def test_unapproved_rate_or_missing_rate_is_rejected(self):
        for exchange in (None, {}, {"base_currency": "SAR", "exchange_currency": "KWD", "rate": "12.08"}):
            with self.subTest(exchange=exchange):
                current = deepcopy(self.current)
                if exchange is None:
                    current.pop("exchange_rate")
                else:
                    current["exchange_rate"] = exchange
                self.assertIsNone(restore_option_price_currencies(current, self.operation))

    def test_invalid_frozen_exchange_contract_is_rejected(self):
        for key, value in (("rate", "0"), ("rate", "-1"), ("rate", "NaN"), ("rate", "Infinity"),
                           ("rate", True), ("rate", None), ("rate", "1E999999"),
                           ("base_currency", "KWD"), ("exchange_currency", "SAR")):
            with self.subTest(key=key, value=value):
                raw, operation, current = fixture()
                raw["exchange_rate"][key] = value
                current["exchange_rate"][key] = value
                freeze(raw, operation)
                self.assertIsNone(restore_option_price_currencies(current, operation))

    def test_exchange_envelope_type_change_is_not_silently_normalized(self):
        self.current["exchange_rate"]["rate"] = float(RATE)
        self.assertIsNone(restore_option_price_currencies(self.current, self.operation))

    def test_missing_original_fx_cannot_be_added_as_new_evidence(self):
        self.raw.pop("exchange_rate")
        freeze(self.raw, self.operation)
        self.assertIsNone(restore_option_price_currencies(self.current, self.operation))

    def test_original_order_currency_must_be_kwd(self):
        for currency in (None, "SAR", "AED"):
            with self.subTest(currency=currency):
                raw, operation, current = fixture()
                if currency is None:
                    raw.pop("currency"); current.pop("currency")
                    raw["amounts"]["total"].pop("currency"); current["amounts"]["total"].pop("currency")
                else:
                    raw["currency"] = current["currency"] = currency
                    raw["amounts"]["total"]["currency"] = current["amounts"]["total"]["currency"] = currency
                freeze(raw, operation)
                self.assertIsNone(restore_option_price_currencies(current, operation))

    def test_conflicting_order_currency_aliases_are_not_proof(self):
        for location in ("root", "amounts"):
            with self.subTest(location=location):
                raw, operation, current = fixture()
                for item in (raw, current):
                    (item if location == "root" else item["amounts"])["currency"] = "SAR"
                freeze(raw, operation)
                self.assertIsNone(restore_option_price_currencies(current, operation))

    def test_every_financial_fact_is_preserved(self):
        mutations = {
            "total": lambda raw: raw["amounts"]["total"].update(amount=12.43),
            "tax": lambda raw: raw["amounts"]["tax"]["amount"].update(amount=1.63),
            "discount": lambda raw: raw["amounts"].update(discounts=[{"discount": 1}]),
            "line_total": lambda raw: raw["items"][0]["amounts"]["total"].update(amount=4.15),
            "paid": lambda raw: raw.update(paid_amount=12.41),
            "root_currency": lambda raw: raw.update(currency="SAR"),
            "line_currency": lambda raw: raw["items"][0]["amounts"]["total"].update(currency="SAR"),
            "quantity": lambda raw: raw["items"][0].update(quantity=2),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                current = deepcopy(self.current)
                mutate(current)
                self.assertIsNone(restore_option_price_currencies(current, self.operation))

    def test_option_identity_selection_and_unknown_metadata_are_preserved(self):
        mutations = {
            "line_id": lambda raw: raw["items"][0].update(id=999),
            "option_id": lambda raw: raw["items"][0]["options"][0].update(id=999),
            "selection_id": lambda raw: raw["items"][0]["options"][0]["value"].update(id=999),
            "selection_name": lambda raw: raw["items"][0]["options"][0]["value"].update(name="Changed choice"),
            "price_metadata": lambda raw: price(raw).update(unfamiliar=True),
            "option_metadata": lambda raw: raw["items"][0]["options"][0].update(unfamiliar=True),
            "root_metadata": lambda raw: raw.update(unfamiliar={"business": True}),
            "status_metadata": lambda raw: raw["status"].update(unfamiliar=True),
            "transport_metadata": lambda raw: raw.update(event={"unfamiliar": True}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                current = deepcopy(self.current)
                mutate(current)
                self.assertIsNone(restore_option_price_currencies(current, self.operation))

    def test_shapes_counts_and_missing_ids_are_not_repaired(self):
        mutations = (
            lambda raw: raw.update(items={}),
            lambda raw: raw["items"].pop(),
            lambda raw: raw["items"][0].pop("id"),
            lambda raw: raw["items"][0].update(options={}),
            lambda raw: raw["items"][0]["options"].pop(),
            lambda raw: raw["items"][0]["options"][0].pop("id"),
            lambda raw: raw["items"][0]["options"][0].update(value=[]),
            lambda raw: raw["items"][0]["options"][0]["value"].update(price={"amount": {"amount": 10}, "currency": "SAR"}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                current = deepcopy(self.current); mutate(current)
                self.assertIsNone(restore_option_price_currencies(current, self.operation))

    def test_missing_corrupt_and_rebound_snapshot_are_rejected(self):
        for key, value in (("business_snapshot", {}), ("_id", "other"), ("user_id", "other"),
                           ("order_number", "other"), ("revision", 2), ("workflow_fingerprint", "other")):
            with self.subTest(key=key):
                operation = deepcopy(self.operation); operation[key] = value
                self.assertIsNone(restore_option_price_currencies(self.current, operation))
        operation = deepcopy(self.operation)
        operation["business_snapshot"]["integrity_hash"] = "corrupt"
        self.assertIsNone(restore_option_price_currencies(self.current, operation))

    def test_existing_transport_normalization_and_snapshot_version_are_reused(self):
        for schema_version in (1, 2):
            for normalization_version in (2, 3):
                with self.subTest(schema_version=schema_version, normalization_version=normalization_version):
                    freeze(self.raw, self.operation, schema_version=schema_version, normalization_version=normalization_version)
                    current = deepcopy(self.current)
                    current["updated_at"] = "2026-11-02T10:00:00+00:00"
                    current["date"]["updated"] = "2026-11-02T10:00:00+00:00"
                    restored = restore_option_price_currencies(current, self.operation)
                    self.assertIsNotNone(restored)
                    self.assertEqual(restored["items"], self.raw["items"])

    def test_original_representation_is_accepted_without_making_new_numbers(self):
        restored = restore_option_price_currencies(self.raw, self.operation)
        self.assertEqual(restored, self.raw)
        self.assertEqual(_tree(restored), _tree(self.raw))

    def test_invalid_input_containers_fail_closed(self):
        for invalid in (None, [], "raw", 1):
            with self.subTest(invalid=invalid):
                self.assertIsNone(restore_option_price_currencies(invalid, self.operation))
                self.assertIsNone(restore_option_price_currencies(self.current, invalid))


class OptionCurrencyLineageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from fulfillment_v2_routes import component_order_fingerprint
        from order_engine.mapper import map_salla_order

        self.raw, self.operation, self.current = fixture()
        self.fingerprint = lambda raw: component_order_fingerprint(map_salla_order(raw))
        self.map_order = map_salla_order
        self.previous = {"source_fingerprint": self.fingerprint(self.raw)}
        self.find_one = AsyncMock(side_effect=lambda query: deepcopy(self.operation)
                                  if all(self.operation.get(key) == value for key, value in query.items()) else None)
        self.db = SimpleNamespace(order_review_completion_operations=SimpleNamespace(find_one=self.find_one))
        self.workflows = {"synthetic-order": {"review_completion_operation_id": self.operation["_id"]}}

    async def prove(self, *, raw=None, previous=None):
        raw = self.current if raw is None else raw
        with patch("review_local_policy.load_local_review_workflows", AsyncMock(return_value=self.workflows)):
            return await prove_option_price_currency_change(
                self.db, user_id="owner", order_number="synthetic-order", raw=raw,
                order=self.map_order(raw), previous=self.previous if previous is None else previous,
            )

    async def test_completed_approval_produces_read_only_auditable_proof(self):
        before = deepcopy((self.current, self.operation, self.previous))
        proof = await self.prove()
        self.assertEqual(proof["reason"], REASON)
        self.assertEqual(proof["proof_kind"], PROOF_KIND)
        self.assertEqual(proof["exchange_rate_to_sar"], RATE)
        self.assertEqual(proof["exchange_rate_fingerprint"],
                         self.operation["business_snapshot"]["unknown_field_fingerprints"]["/source/exchange_rate"])
        self.assertEqual(proof["approved_fingerprint"], self.fingerprint(self.raw))
        self.assertEqual(proof["previous_fingerprint"], self.previous["source_fingerprint"])
        self.assertEqual((self.current, self.operation, self.previous), before)
        self.find_one.assert_awaited_once_with({
            "_id": "synthetic-approval", "user_id": "owner", "order_number": "synthetic-order",
            "state": "completed", "completion_mode": "mezan_local_v1",
        })

    async def test_repeat_changes_and_return_to_original_retain_approval_lineage(self):
        proof = await self.prove()
        current = deepcopy(self.current)
        previous = self.previous
        for next_raw in (self.raw, current, self.raw):
            previous = {
                "source_fingerprint": self.fingerprint(current),
                "option_price_currency_revalidation": {**proof, "current_fingerprint": self.fingerprint(current)},
            }
            proof = await self.prove(raw=next_raw, previous=previous)
            self.assertIsNotNone(proof)
            self.assertEqual(proof["approved_fingerprint"], self.fingerprint(self.raw))
            current = deepcopy(next_raw)

    async def test_valid_zero_price_lineage_can_start_nonzero_representation_proof(self):
        zero_view = deepcopy(self.raw)
        for item in zero_view["items"]:
            for option in item["options"]:
                if option["value"]["price"]["amount"] == 0:
                    option["value"]["price"]["currency"] = "SAR"
        current_fingerprint = self.fingerprint(zero_view)
        previous = {
            "source_fingerprint": current_fingerprint,
            "zero_price_currency_revalidation": {
                "operation_id": self.operation["_id"],
                "approval_integrity_hash": self.operation["business_snapshot"]["integrity_hash"],
                "approved_fingerprint": self.fingerprint(self.raw), "current_fingerprint": current_fingerprint,
                "reason": "zero_option_price_currency_only",
            },
        }
        proof = await self.prove(previous=previous)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["approved_fingerprint"], self.fingerprint(self.raw))
        previous["zero_price_currency_revalidation"]["approval_integrity_hash"] = "changed"
        self.assertIsNone(await self.prove(previous=previous))

    async def test_broken_lineage_fields_are_all_rejected(self):
        proof = await self.prove()
        current_fingerprint = self.fingerprint(self.current)
        audit = {**proof, "current_fingerprint": current_fingerprint}
        for field in ("operation_id", "approval_integrity_hash", "approved_fingerprint", "current_fingerprint",
                      "reason", "proof_kind", "exchange_rate_to_sar", "exchange_rate_fingerprint"):
            with self.subTest(field=field):
                changed = {**audit, field: "changed"}
                previous = {"source_fingerprint": current_fingerprint, "option_price_currency_revalidation": changed}
                self.assertIsNone(await self.prove(raw=self.raw, previous=previous))
        for invalid in ({}, [], None, "invalid"):
            with self.subTest(invalid=invalid):
                previous = {"source_fingerprint": current_fingerprint, "option_price_currency_revalidation": invalid}
                self.assertIsNone(await self.prove(raw=self.raw, previous=previous))

    async def test_new_lineage_supersedes_retained_stale_zero_audit(self):
        proof = await self.prove()
        current_fingerprint = self.fingerprint(self.current)
        previous = {
            "source_fingerprint": current_fingerprint,
            "option_price_currency_revalidation": {**proof, "current_fingerprint": current_fingerprint},
            "zero_price_currency_revalidation": {"current_fingerprint": "stale"},
        }
        self.assertIsNotNone(await self.prove(raw=self.raw, previous=previous))

    async def test_missing_rebound_or_superseded_approval_cannot_authorize(self):
        original = deepcopy(self.operation)
        for field, value in (("_id", "other"), ("user_id", "other"), ("order_number", "other"),
                             ("state", "prepared"), ("completion_mode", "other"), ("superseded_by", "new")):
            with self.subTest(field=field):
                self.operation = {**deepcopy(original), field: value}
                self.assertIsNone(await self.prove())
        self.operation = original
        self.workflows = {}
        self.assertIsNone(await self.prove())

    async def test_arbitrary_or_missing_previous_fingerprint_cannot_be_resealed(self):
        self.assertIsNone(await self.prove(previous={"source_fingerprint": "unrelated"}))
        self.assertIsNone(await self.prove(previous={}))
        self.assertIsNone(await self.prove(raw=self.raw))


if __name__ == "__main__":
    unittest.main()
