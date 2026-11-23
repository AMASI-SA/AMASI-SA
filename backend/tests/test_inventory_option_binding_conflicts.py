from copy import deepcopy
import unittest

from inventory_option_binding_conflicts import informational_option_service_conflicts


class InformationalOptionServiceConflictsTest(unittest.TestCase):
    def test_service_binding_uses_stable_option_id_not_shared_display_name(self):
        bindings = [
            {"option_id": "gift", "option_name": "النص", "value_id": "yes",
             "mode": "resource", "resource_id": "card-print"},
            {"option_id": "engraving", "option_name": "النص", "value_id": "filled",
             "mode": "resource", "resource_id": "engrave"},
        ]
        resources = {
            "card-print": {"kind": "service", "name": "طباعة الكرت"},
            "engrave": {"kind": "service", "name": "نقش المنتج"},
        }
        self.assertEqual(informational_option_service_conflicts(
            informational_option_ids={"gift"}, bindings=bindings, resources_by_id=resources,
        ), [{"option_id": "gift", "value_id": "yes", "resource_id": "card-print",
             "resource_name": "طباعة الكرت"}])

    def test_both_filled_and_legacy_option_level_service_bindings_conflict(self):
        bindings = [
            {"option_id": "gift-text", "value_id": value_id,
             "mode": "resource", "resource_id": "svc"}
            for value_id in ("filled", "__option__")
        ]
        conflicts = informational_option_service_conflicts(
            informational_option_ids={"gift-text"}, bindings=bindings,
            resources_by_id={"svc": {"kind": "service", "name": "طباعة"}},
        )
        self.assertEqual([row["value_id"] for row in conflicts], ["__option__", "filled"])

    def test_existing_stopped_service_still_conflicts_without_preparation_flag(self):
        # Existing recipe links remain executable when a resource is stopped;
        # supplier service inheritance does not require requires_preparation.
        conflicts = informational_option_service_conflicts(
            informational_option_ids={"wrap"},
            bindings=[{"option_id": "wrap", "value_id": "yes", "mode": "resource",
                       "resource_id": "svc"}],
            resources_by_id={"svc": {"kind": " Service ", "name": "تغليف",
                                     "status": "inactive", "requires_preparation": False}},
        )
        self.assertEqual([row["resource_id"] for row in conflicts], ["svc"])

    def test_components_and_direct_prices_are_not_supplier_service_conflicts(self):
        bindings = [
            {"option_id": "wrap", "value_id": "paper", "mode": "resource",
             "resource_id": "paper", "quantity": 2},
            {"option_id": "wrap", "value_id": "bag", "mode": "resource",
             "resource_id": "bag"},
            {"option_id": "wrap", "value_id": "priced", "mode": "direct",
             "resource_id": "old-service-id", "direct_amount": 7},
        ]
        resources = {
            "paper": {"kind": "stock_component", "unit_cost": 3, "requires_preparation": True},
            "bag": {"kind": "component", "unit_cost": 5},
            "old-service-id": {"kind": "service"},
        }
        before = deepcopy((bindings, resources))
        self.assertEqual(informational_option_service_conflicts(
            informational_option_ids={"wrap"}, bindings=bindings, resources_by_id=resources,
        ), [])
        self.assertEqual((bindings, resources), before)

    def test_every_bound_value_is_checked_without_a_customer_selection(self):
        conflicts = informational_option_service_conflicts(
            informational_option_ids={42},
            bindings=[{"option_id": "42", "value_id": value_id, "mode": "resource",
                       "resource_id": "svc"} for value_id in ("yes", "no")],
            resources_by_id={"svc": {"kind": "service"}},
        )
        self.assertEqual([row["value_id"] for row in conflicts], ["no", "yes"])
        self.assertTrue(all(row["resource_name"] == "svc" for row in conflicts))

    def test_repeated_rows_do_not_duplicate_conflicts_or_mutate_inputs(self):
        binding = {"option_id": "gift", "value_id": "yes", "mode": "resource",
                   "resource_id": "svc", "quantity": 3}
        bindings = [binding, deepcopy(binding)]
        resources = {"svc": {"kind": "service", "name": "طباعة", "unit_cost": 8}}
        before = deepcopy((bindings, resources))
        conflicts = informational_option_service_conflicts(
            informational_option_ids={"gift"}, bindings=bindings, resources_by_id=resources,
        )
        self.assertEqual(len(conflicts), 1)
        self.assertEqual((bindings, resources), before)

    def test_no_informational_options_leave_existing_service_links_untouched(self):
        self.assertEqual(informational_option_service_conflicts(
            informational_option_ids=set(),
            bindings=[{"option_id": "engrave", "value_id": "filled", "mode": "resource",
                       "resource_id": "svc"}],
            resources_by_id={"svc": {"kind": "service"}},
        ), [])


if __name__ == "__main__":
    unittest.main()
