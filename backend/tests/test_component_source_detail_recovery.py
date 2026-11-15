"""Contract regressions for settled-order detail enrichment; synthetic data only."""
from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from component_option_currency_recovery import PROOF_KIND, REASON, prove_option_price_currency_change
from fulfillment_v2_routes import component_order_fingerprint
from order_engine.mapper import map_salla_order
from order_engine.models import OrderCollectionBalanceDTO
from order_review_business_snapshot import _hash, _tree, build_snapshot
from salla_payment_collection import settled_payment_snapshot


RATE = "12.07292044"
ACCEPTED_CLOCK = "2026-11-02T10:00:00.000000+00:00"
IDENTITY_FIELDS = ("_id", "user_id", "order_number", "revision", "workflow_fingerprint")
BULLET_DISABLED = {
    "is_bullet_delivery": False, "bullet_countdown": 0, "on_demand_countdown": 0,
    "on_demand_delivery": False, "on_demand_timer": False,
}


def money(amount, currency="KWD"):
    return {"amount": amount, "currency": currency}


def clock(day):
    return {"date": f"2026-11-{day:02d} 10:00:00.000000", "timezone": "UTC", "timezone_type": 3}


def detail_fixture():
    approved = {
        "id": 701, "reference_id": "synthetic-detail-order", "currency": "KWD",
        "status": {"slug": "in_progress"}, "date": clock(1), "updated_at": clock(1),
        "payment_method": "credit_card", "payment_status": "paid", "read": False,
        "pending_payment_ends_at": 123456,
        "features": {"shippable": True, "digitalable": False, "has_suspicious_alert": False},
        "payment_actions": {
            "remaining_action": {"paid_amount": 12.42, "remaining_amount": None,
                                 "has_remaining_amount": False, "checkout_url": ""},
            "refund_action": {"paid_amount": 12.42, "refund_amount": 0, "refunded_transactions": []},
        },
        "amounts": {"total": money(12.42), "sub_total": money(10.8),
                    "tax": {"percent": 15, "amount": money(1.62)}, "discounts": []},
        "exchange_rate": {"base_currency": "SAR", "exchange_currency": "KWD", "rate": RATE},
        "items": [],
    }
    for index, option_amount in enumerate((0.828, 1.242, 0.828)):
        thumbnail = f"https://example.test/catalog/{index + 1}.png"
        approved["items"].append({
            "id": 1001 + index, "product_id": 2001 + index, "quantity": 1,
            "name": f"Synthetic item {index + 1}", "sku": f"SYNTH-{index + 1}",
            "product_type": "service", "product_thumbnail": thumbnail,
            "amounts": {"price_without_tax": money(3.6), "total": money(4.14),
                        "total_discount": money(0), "tax": {"percent": "15", "amount": money(0.54)}},
            "product": {"id": 2001 + index, "name": f"Synthetic item {index + 1}",
                        "sku": f"SYNTH-{index + 1}", "type": "service", "thumbnail": thumbnail,
                        "price": money(2.5), "regular_price": money(3.5),
                        "sale_price": money(0 if index != 1 else 2)},
            "options": [{"id": 3001 + index, "name": "Size", "type": "radio",
                         "value": {"id": 4001 + index, "name": "Selected size", "price": money(option_amount)}},
                        {"id": 5001 + index, "name": "Personalization", "type": "text",
                         "value": {"id": 6001 + index, "name": "Synthetic name", "price": money(0)}}],
        })
    operation = {"_id": "synthetic-detail-approval", "user_id": "owner",
                 "order_number": "synthetic-detail-order", "revision": 1,
                 "workflow_fingerprint": "synthetic-workflow", "state": "completed",
                 "completion_mode": "mezan_local_v1"}
    freeze(approved, operation)
    current = settled_payment_snapshot(approved)
    current.update(updated_at=clock(2), read=True, pending_payment_ends_at=0,
                   actions={"cancellable": False, "reorderable": False, "payable": False})
    current["features"].update(bullet_delivery=deepcopy(BULLET_DISABLED), pay_as_company=False)
    for index, item in enumerate(current["items"]):
        item.update(files=[], codes=[], reservations=[], digital_edit_rules=[],
                    is_digital_code_product=False, thumbnail=item["product_thumbnail"])
        # Catalog reference amounts deliberately do not use the order FX rate.
        item["product"].update(price=money(25, "SAR"), regular_price=money(37, "SAR"),
                               sale_price=money(0 if index != 1 else 20, "SAR"))
        item["options"][0]["value"]["price"] = money(15 if index == 1 else 10, "SAR")
        item["options"][1]["value"]["price"] = money(0, "SAR")
    previous = {"accepted": True, "retry_required": False, "cancelled": False,
                "state": "legacy_operational_pre_g47", "source_updated_at": ACCEPTED_CLOCK,
                "source_fingerprint": component_order_fingerprint(map_salla_order(approved))}
    return approved, operation, current, previous


def freeze(raw, operation):
    operation["business_snapshot"] = build_snapshot(
        {"raw_by_source": {"salla_direct": raw}}, map_salla_order(raw), {},
        identity={key: operation.get(key) for key in IDENTITY_FIELDS},
        schema_version=2, normalization_version=3,
    )


class SettledDetailProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.approved, self.operation, self.current, self.previous = detail_fixture()
        self.find_one = AsyncMock(side_effect=lambda query: deepcopy(self.operation)
                                  if all(self.operation.get(key) == value for key, value in query.items()) else None)
        self.db = SimpleNamespace(order_review_completion_operations=SimpleNamespace(find_one=self.find_one))
        self.workflows = {"synthetic-detail-order": {"review_completion_operation_id": self.operation["_id"]}}

    async def prove(self, *, raw=None, order=None, previous=None):
        raw = self.current if raw is None else raw
        order = map_salla_order(raw) if order is None else order
        previous = self.previous if previous is None else previous
        with patch("review_local_policy.load_local_review_workflows", AsyncMock(return_value=self.workflows)):
            return await prove_option_price_currency_change(
                self.db, user_id="owner", order_number="synthetic-detail-order",
                raw=raw, order=order, previous=previous,
            )

    async def test_settled_detail_enrichment_preserves_the_approved_contract_without_writes(self):
        before = deepcopy((self.approved, self.operation, self.current, self.previous))
        result = await self.prove()
        self.assertIsNotNone(result)
        self.assertEqual(result["reason"], REASON)
        self.assertEqual(result["proof_kind"], PROOF_KIND)
        self.assertEqual(result["approved_fingerprint"], self.previous["source_fingerprint"])
        self.assertEqual(result["detail_enrichment"]["version"], "settled_order_detail_v1")
        audit = json.dumps(result["detail_enrichment"])
        for private_value in ("synthetic-detail-order", "Synthetic name", "https://example.test"):
            self.assertNotIn(private_value, audit)
        self.assertEqual((self.approved, self.operation, self.current, self.previous), before)
        self.find_one.assert_awaited_once()

    async def test_catalog_reference_drift_does_not_relax_the_option_exchange_proof(self):
        old_catalog = Decimal(str(self.approved["items"][0]["product"]["price"]["amount"]))
        current_catalog = Decimal(str(self.current["items"][0]["product"]["price"]["amount"]))
        self.assertGreater(abs(current_catalog - old_catalog * Decimal(RATE)), Decimal("1"))
        self.assertIsNotNone(await self.prove())
        self.current["items"][0]["options"][0]["value"]["price"]["amount"] = 10.01
        self.assertIsNone(await self.prove())

    async def test_enrichment_can_be_proven_when_catalog_prices_remain_approved(self):
        for item, original in zip(self.current["items"], self.approved["items"]):
            item["product"] = deepcopy(original["product"])
        self.assertIsNotNone(await self.prove())

    async def test_accepted_clock_and_settled_expiry_use_explicit_semantic_projections(self):
        unknown = self.operation["business_snapshot"]["unknown_field_fingerprints"]
        self.assertNotEqual(unknown["/source/updated_at"], _hash(_tree(self.current["updated_at"])))
        self.assertNotEqual(unknown["/source/pending_payment_ends_at"], _hash(_tree(0)))
        result = await self.prove()
        self.assertIsNotNone(result)
        categories = result["detail_enrichment"]["categories"]
        self.assertIn("previously_accepted_source_clock", categories)
        self.assertIn("inactive_payment_link_expiry", categories)

    async def test_existing_source_balance_display_projection_remains_supported(self):
        order = map_salla_order(self.current)
        payment = order.payment.model_copy(update={
            "source_balance": OrderCollectionBalanceDTO(paid="12.42", remaining="0.00")})
        self.assertIsNotNone(await self.prove(order=order.model_copy(update={"payment": payment})))

    async def test_contract_money_changes_are_rejected_at_one_native_minor_unit(self):
        mutations = {
            "order_total": lambda raw: raw["amounts"]["total"].update(amount=12.421),
            "order_tax": lambda raw: raw["amounts"]["tax"]["amount"].update(amount=1.621),
            "line_price": lambda raw: raw["items"][0]["amounts"]["price_without_tax"].update(amount=3.601),
            "line_total": lambda raw: raw["items"][0]["amounts"]["total"].update(amount=4.141),
            "line_discount": lambda raw: raw["items"][0]["amounts"]["total_discount"].update(amount=0.001),
            "line_tax": lambda raw: raw["items"][0]["amounts"]["tax"]["amount"].update(amount=0.541),
            "line_currency": lambda raw: raw["items"][0]["amounts"]["total"].update(currency="SAR"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = deepcopy(self.current)
                mutate(raw)
                self.assertIsNone(await self.prove(raw=raw))

    async def test_mapper_fallbacks_do_not_replace_explicit_approved_line_money(self):
        fallbacks = {
            "price_without_tax": ("price", 3.6),
            "total": ("total", 4.14),
            "total_discount": ("discount", 0),
            "tax": ("tax", 0.54),
        }
        for missing, (fallback, value) in fallbacks.items():
            with self.subTest(missing=missing):
                self.approved, self.operation, self.current, self.previous = detail_fixture()
                for raw in (self.approved, self.current):
                    raw["items"][0]["amounts"].pop(missing)
                    raw["items"][0][fallback] = value
                freeze(self.approved, self.operation)
                self.previous["source_fingerprint"] = component_order_fingerprint(map_salla_order(self.approved))
                old_item = map_salla_order(self.approved).items[0]
                new_item = map_salla_order(self.current).items[0]
                self.assertEqual((old_item.unit_price, old_item.total, old_item.discount, old_item.tax_reported_by_source),
                                 (new_item.unit_price, new_item.total, new_item.discount, new_item.tax_reported_by_source))
                self.assertIsNone(await self.prove())

    async def test_canonical_financial_facts_are_checked_independently_of_raw_source(self):
        original = map_salla_order(self.current)
        for field, value in (("total", 12.421), ("subtotal", 10.801), ("total_sar", 150.01)):
            with self.subTest(total_field=field):
                order = original.model_copy(update={"totals": original.totals.model_copy(update={field: value})})
                self.assertIsNone(await self.prove(order=order))
        for field, value in (("paid_amount", 12.419), ("remaining_amount", 0.001),
                             ("method", "bank_transfer"), ("collection_status", "partial")):
            with self.subTest(payment_field=field):
                order = original.model_copy(update={"payment": original.payment.model_copy(update={field: value})})
                self.assertIsNone(await self.prove(order=order))
        for field, value in (("unit_price", 3.601), ("discount", 0.001), ("total", 4.141)):
            with self.subTest(item_field=field):
                items = [original.items[0].model_copy(update={field: value}), *original.items[1:]]
                self.assertIsNone(await self.prove(order=original.model_copy(update={"items": items})))

    async def test_catalog_prices_require_precise_numeric_currency_envelopes(self):
        invalid_prices = [money(value, "SAR") for value in (True, False, "25", None, -1, 25.001,
                                                             float("inf"), float("nan"))]
        invalid_prices += [money(25, code) for code in ("KWD", "USD", "sar", "")]
        invalid_prices += [{"amount": 25, "currency": "SAR", "new_metadata": False}, {"amount": 25}]
        for invalid in invalid_prices:
            with self.subTest(price=invalid):
                raw = deepcopy(self.current)
                raw["items"][0]["product"]["price"] = invalid
                self.assertIsNone(await self.prove(raw=raw))

    async def test_canonical_item_identity_and_customization_are_not_replaced_from_raw(self):
        original = map_salla_order(self.current)
        for field, value in (("name", "Changed item"), ("sku", "OTHER-SKU"),
                             ("image_url", "https://example.test/other.png"),
                             ("custom_fields", [{"name": "Name", "value": "Other name"}])):
            with self.subTest(item_field=field):
                items = [original.items[0].model_copy(update={field: value}), *original.items[1:]]
                self.assertIsNone(await self.prove(order=original.model_copy(update={"items": items})))

    async def test_catalog_prices_cannot_cross_between_free_and_nonzero(self):
        for index, kind, amount in ((0, "price", 0), (0, "sale_price", 1), (1, "sale_price", 0)):
            with self.subTest(index=index, kind=kind):
                raw = deepcopy(self.current)
                raw["items"][index]["product"][kind]["amount"] = amount
                self.assertIsNone(await self.prove(raw=raw))

    async def test_product_selection_customization_and_unknown_fields_remain_guarded(self):
        mutations = {
            "product_identity": lambda raw: raw["items"][0]["product"].update(id=9999),
            "quantity": lambda raw: raw["items"][0].update(quantity=2),
            "variant": lambda raw: raw["items"][0].update(variant_id=999),
            "choice": lambda raw: raw["items"][0]["options"][0]["value"].update(id=999),
            "custom_name": lambda raw: raw["items"][0]["options"][1]["value"].update(name="Other name"),
            "unknown_root": lambda raw: raw.update(unfamiliar=False),
            "unknown_product": lambda raw: raw["items"][0]["product"].update(unfamiliar=False),
            "unknown_option": lambda raw: raw["items"][0]["options"][0].update(unfamiliar=False),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = deepcopy(self.current)
                mutate(raw)
                self.assertIsNone(await self.prove(raw=raw))

    async def test_empty_delivery_enrichment_never_hides_content_or_type_changes(self):
        for field in ("files", "codes", "reservations", "digital_edit_rules"):
            for value in (["new content"], {}, None):
                with self.subTest(field=field, value=value):
                    raw = deepcopy(self.current)
                    raw["items"][0][field] = value
                    self.assertIsNone(await self.prove(raw=raw))
        for value in (True, 0, "false", None):
            with self.subTest(digital_flag=value):
                raw = deepcopy(self.current)
                raw["items"][0]["is_digital_code_product"] = value
                self.assertIsNone(await self.prove(raw=raw))

    async def test_display_defaults_and_image_aliases_are_narrowly_bound(self):
        mutations = {
            "existing_feature": lambda raw: raw["features"].update(shippable=False),
            "unknown_feature": lambda raw: raw["features"].update(new_delivery_flag=False),
            "company_enabled": lambda raw: raw["features"].update(pay_as_company=True),
            "company_numeric": lambda raw: raw["features"].update(pay_as_company=0),
            "bullet_enabled": lambda raw: raw["features"]["bullet_delivery"].update(is_bullet_delivery=True),
            "bullet_numeric_bool": lambda raw: raw["features"]["bullet_delivery"].update(is_bullet_delivery=0),
            "bullet_countdown_bool": lambda raw: raw["features"]["bullet_delivery"].update(bullet_countdown=False),
            "payable": lambda raw: raw["actions"].update(payable=True),
            "action_numeric": lambda raw: raw["actions"].update(reorderable=0),
            "unknown_action": lambda raw: raw["actions"].update(new_action=False),
            "other_thumbnail": lambda raw: raw["items"][0].update(thumbnail="https://example.test/different.png"),
            "read_type": lambda raw: raw.update(read=1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = deepcopy(self.current)
                mutate(raw)
                self.assertIsNone(await self.prove(raw=raw))

    async def test_payment_aliases_must_equal_the_settled_native_evidence(self):
        conflicts = {"paid_amount": 12.419, "remaining_amount": 0.001,
                     "has_remaining_amount": True, "payment_collection_status": "partial",
                     "payment_checkout_url": "https://example.test/new-payment",
                     "checkout_url": "https://example.test/new-payment"}
        for field, value in conflicts.items():
            with self.subTest(field=field):
                raw = deepcopy(self.current)
                raw[field] = value
                self.assertIsNone(await self.prove(raw=raw))
        for field, value in (("refund_amount", 0.001), ("refunded_transactions", [{"amount": 0.001}])):
            with self.subTest(refund_field=field):
                raw = deepcopy(self.current)
                raw["payment_actions"]["refund_action"][field] = value
                self.assertIsNone(await self.prove(raw=raw))
        raw = deepcopy(self.current)
        raw["payment_actions"]["remaining_action"]["paid_amount"] = 12.419
        self.assertIsNone(await self.prove(raw=raw))

    async def test_settled_dto_cannot_hide_native_refund_or_balance_conflicts(self):
        mutations = {
            "pending_refund": lambda raw: raw.update(pending_refund_amount=0.1),
            "refunded": lambda raw: raw["payment_actions"]["refund_action"].update(refund_amount=0.1),
            "refund_transactions": lambda raw: raw["payment_actions"]["refund_action"].update(
                refunded_transactions=[{"amount": 0.1}]),
            "independent_remaining": lambda raw: raw.update(payment={"remaining_amount": 0.1}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                self.approved, self.operation, self.current, self.previous = detail_fixture()
                for raw in (self.approved, self.current):
                    mutate(raw)
                freeze(self.approved, self.operation)
                self.previous["source_fingerprint"] = component_order_fingerprint(map_salla_order(self.approved))
                # The mapper's promoted root balance still looks paid. That
                # display result must not erase contradictory native evidence.
                self.assertEqual(map_salla_order(self.current).payment.collection_status, "paid")
                self.assertIsNone(await self.prove())

    async def test_payment_waiting_state_or_open_collection_link_is_not_inactive_expiry(self):
        mutations = {
            "pending_flag": lambda raw: raw.update(is_pending_payment=True),
            "waiting_status": lambda raw: raw["status"].update(slug="awaiting_payment"),
            "payment_status": lambda raw: raw.update(payment_status="pending"),
            "native_checkout": lambda raw: raw["payment_actions"]["remaining_action"].update(
                checkout_url="https://example.test/active-checkout"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                raw = deepcopy(self.current)
                mutate(raw)
                self.assertIsNone(await self.prove(raw=raw))

    async def test_inactive_expiry_cannot_be_inferred_from_nonzero_or_boolean_values(self):
        for value in (1, -1, False, True, "0", None, {}, []):
            with self.subTest(expiry=value):
                raw = deepcopy(self.current)
                raw["pending_payment_ends_at"] = value
                self.assertIsNone(await self.prove(raw=raw))

    async def test_source_clock_requires_its_exact_shape_and_accepted_instant(self):
        invalid_clocks = [
            {**clock(2), "unfamiliar": False},
            {**clock(2), "timezone": "Not/AZone"},
            {**clock(2), "timezone_type": "3"},
            {**clock(2), "timezone_type": 3.0},
            {**clock(2), "timezone_type": True},
            {**clock(2), "date": "not a timestamp"},
            {**clock(2), "date": "2026-11-02T10:00:00"},
            {**clock(2), "date": "2026-11-02 10:00:00.000000+00:00"},
            {**clock(2), "date": "2026-11-02T13:00:00+03:00"},
            clock(3),
        ]
        for value in invalid_clocks:
            with self.subTest(clock=value):
                raw = deepcopy(self.current)
                raw["updated_at"] = value
                self.assertIsNone(await self.prove(raw=raw))
        for field, value in (("source_updated_at", "2026-11-01T10:00:00.000000+00:00"),
                             ("accepted", False), ("retry_required", True), ("cancelled", True)):
            with self.subTest(previous_field=field):
                self.assertIsNone(await self.prove(previous={**self.previous, field: value}))


if __name__ == "__main__":
    unittest.main()
