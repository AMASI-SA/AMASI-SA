"""Pure contract tests: no database, network, or event emitter."""
import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from order_change_event import (
    ChangeActor, OptionSnapshot, OrderChangeEvent, UnitIdentity, UnitSnapshot,
    build_control_event,
)


class OrderChangeEventTests(unittest.TestCase):
    def payload(self, **changes):
        result = dict(
            order_number="ORDER-1", change_id="change-1", idempotency_key="retry-1",
            change_type="hold", actor=ChangeActor(actor_id="employee-1"),
            reason="Customer requested a pause", timestamp=datetime(2026, 10, 6, tzinfo=timezone.utc),
            old_fulfillment_stage="in_progress", revision=3, generation=2,
            old_units=(UnitSnapshot(order_item_id="line-1", unit_index=1, generation=2),),
            affected_employees=("employee-1", "employee-2"), required_action="stop",
        )
        result.update(changes)
        return result

    def test_controls_build_serializable_payload_without_writes(self):
        for kind, action in (("hold", "stop"), ("resume", "reload")):
            data = self.payload(change_type=kind)
            data.pop("required_action")
            event = build_control_event(**data)
            self.assertEqual(event.required_action, action)
            self.assertEqual(event.financial_impact, "pending_contract")
            self.assertIs(event.salla_mutation_enabled, False)
            self.assertEqual(event.affected_employees, ("employee-1", "employee-2"))
            self.assertEqual(OrderChangeEvent.model_validate_json(event.model_dump_json()), event)

    def test_required_audit_fields_and_aware_time(self):
        for field in ("reason", "order_number", "change_id", "idempotency_key", "old_fulfillment_stage"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                OrderChangeEvent(**self.payload(**{field: "   "}))
        with self.assertRaises(ValidationError):
            ChangeActor(actor_id=" ")
        with self.assertRaises(ValidationError):
            OrderChangeEvent(**self.payload(timestamp=datetime(2026, 10, 6)))

    def test_extra_fields_and_sensitive_capabilities_fail_closed(self):
        for changes in ({"sku": "not-an-identity"}, {"financial_impact": "posted"},
                        {"salla_mutation_enabled": True}, {"salla_mutation_enabled": 0}, {"revision": True},
                        {"generation": -1}, {"generation": " "}, {"generation": True}, {"required_action": "reload"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                OrderChangeEvent(**self.payload(**changes))
        with self.assertRaises(ValidationError):
            UnitIdentity(order_item_id="line", unit_index=1, generation=2, sku="x")
        with self.assertRaises(ValidationError):
            UnitIdentity(order_item_id="line", unit_index=0, generation=2)

    def test_nested_payload_is_immutable(self):
        event = OrderChangeEvent(**self.payload(old_options=(OptionSnapshot(key="engraving", value="Customer text"),)))
        for obj, field, value in ((event, "reason", "rewrite"), (event.actor, "actor_id", "another"),
                                  (event.old_units[0], "unit_index", 1),
                                  (event.old_options[0], "value", "changed")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                setattr(obj, field, value)
        self.assertIsInstance(event.old_units, tuple)
        self.assertIsInstance(event.old_options, tuple)
        employees = ["employee-1"]
        event = OrderChangeEvent(**self.payload(affected_employees=employees))
        employees.append("employee-2")
        self.assertEqual(event.affected_employees, ("employee-1",))

    def test_units_use_line_index_generation_and_reject_duplicates(self):
        first = UnitSnapshot(order_item_id="line-1", unit_index=1, generation=2)
        second = UnitSnapshot(order_item_id="line-1", unit_index=2, generation=2)
        self.assertEqual(len(OrderChangeEvent(**self.payload(old_units=(first, second))).old_units), 2)
        with self.assertRaises(ValidationError):
            OrderChangeEvent(**self.payload(old_units=(first, first)))
        with self.assertRaises(ValidationError):
            OrderChangeEvent(**self.payload(affected_employees=("employee-1", "employee-1")))

    def test_commercial_contracts_require_units_but_cannot_enable_mutations(self):
        unit = UnitSnapshot(order_item_id="line-1", unit_index=1, generation=2)
        for kind in ("add_product", "edit_options", "cancel_product"):
            with self.subTest(kind=kind):
                event = OrderChangeEvent(**self.payload(
                    change_type=kind, required_action="preparation_reconcile", new_units=(unit,),
                    old_options=(OptionSnapshot(key="engraving", value="before"),),
                    new_options=(OptionSnapshot(key="engraving", value="after"),),
                ))
                self.assertFalse(event.salla_mutation_enabled)
                with self.assertRaises(ValidationError):
                    OrderChangeEvent(**self.payload(change_type=kind, required_action="preparation_reconcile", old_units=()))
        args = self.payload(change_type="add_product")
        args.pop("required_action")
        with self.assertRaises(ValueError):
            build_control_event(**args)

    def test_replacement_retains_old_unit_and_reciprocal_links(self):
        old_id = UnitIdentity(order_item_id="line-A", unit_index=1, generation=1)
        new_id = UnitIdentity(order_item_id="line-B", unit_index=1, generation=2)
        old = UnitSnapshot(**old_id.model_dump(), replaced_by=new_id)
        new = UnitSnapshot(**new_id.model_dump(), replaces=old_id)
        event = OrderChangeEvent(**self.payload(change_type="replace_product", required_action="preparation_reconcile", old_units=(old,), new_units=(new,)))
        self.assertEqual(event.old_units[0].order_item_id, "line-A")
        self.assertEqual(event.new_units[0].replaces, old_id)
        self.assertEqual(event.old_units[0].generation, 1)
        self.assertEqual(event.new_units[0].generation, 2)
        with self.assertRaises(ValidationError):
            event.new_units[0].replaces.generation = 9
        for changes in ({"old_units": ()}, {"new_units": ()},
                        {"old_units": (UnitSnapshot(**old_id.model_dump()),)},
                        {"new_units": (UnitSnapshot(**new_id.model_dump()),)},
                        {"new_units": (old,)}):
            data = event.model_dump()
            data.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                OrderChangeEvent(**data)

    def test_commercial_numeric_generation_rejects_future_history_and_stale_new_units(self):
        future = UnitSnapshot(order_item_id="future", unit_index=1, generation=3)
        stale = UnitSnapshot(order_item_id="old", unit_index=1, generation=1)
        with self.assertRaises(ValidationError):
            OrderChangeEvent(**self.payload(change_type="cancel_product", required_action="preparation_reconcile", old_units=(future,)))
        with self.assertRaises(ValidationError):
            OrderChangeEvent(**self.payload(change_type="add_product", required_action="preparation_reconcile", new_units=(stale,)))

    def test_controls_keep_mixed_unit_generations_and_hash_fence(self):
        units = [UnitSnapshot(order_item_id="line", unit_index=1, generation=1),
                 UnitSnapshot(order_item_id="line", unit_index=1, generation=2)]
        for kind in ("hold", "resume"):
            for generation in (1, "generation-fence-sha256"):
                args = self.payload(change_type=kind, generation=generation, old_units=units, new_units=units)
                args.pop("required_action")
                event = build_control_event(**args)
                self.assertEqual(event.generation, generation)
                self.assertEqual([unit.generation for unit in event.old_units], [1, 2])
                self.assertEqual(OrderChangeEvent.model_validate_json(event.model_dump_json()), event)


if __name__ == "__main__":
    unittest.main()
