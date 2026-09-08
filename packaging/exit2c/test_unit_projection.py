"""Offline unit reproduction using actual pure production projection functions.

AST loading avoids importing server, database clients, or provider integrations.
These documents are test inputs only; they never seed the HTTP acceptance DB.
"""
import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
import uuid

from order_fixture import ORDERS, order_fixture

ROOT = Path(__file__).resolve().parents[2]


def _load(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError("PROJECTION_FUNCTION_MISSING")
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + nodes,
                        type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)


def actual_functions():
    namespace = {"datetime": datetime, "timedelta": timedelta, "timezone": timezone, "uuid": uuid,
                 "RIYADH_TZ": timezone(timedelta(hours=3)),
                 "ORDER_OVERRIDE_FIELD": "supplier_export_spec_replacement_overrides",
                 "DEFAULT_ESTIMATED_DURATION_MINUTES": 2 * 24 * 60, "PIECE_STATUS_ASSIGNED": "assigned"}
    _load("backend/order_review_routes.py", {"_text", "_normalized"}, namespace)
    _load("backend/order_review_spec_replacements.py", {
        "canonical_spec_key", "_display_value", "_item_value", "extract_item_specs", "_snapshot_spec_rows",
        "_without_redundant_component", "split_legacy_replacement_text", "replacement_components",
        "replacement_override_map", "effective_spec_rows", "supplier_file_spec_fields"}, namespace)
    _load("backend/reviewed_preparation_batches.py", {"_card_field_projection"}, namespace)
    _load("backend/preparation_piece_barcode.py", {"preparation_piece_identity_key", "preparation_piece_id"}, namespace)
    _load("backend/tz_utils.py", {"riyadh_now_aware"}, namespace)
    _load("backend/preparation_piece_operations.py", {
        "_piece_id", "_positive_unit_indices", "_service_context_key", "build_piece_documents"}, namespace)
    return namespace


def synthetic_documents():
    """Return actual projected/built documents and independent source expectations."""
    functions = actual_functions()
    expected, allocations, pieces, batches, registries = {}, [], [], [], []
    employee = "exit2d-unit-employee"
    for order_index, number in enumerate(ORDERS):
        raw = order_fixture("raw-" + number)
        batch_id = "exit2d-unit-batch-" + str(order_index)
        registry = {"id": "exit2d-unit-registry-" + str(order_index), "batch_id": batch_id,
                    "user_id": "exit2d-unit-owner",
                    "file_number": "exit2d-unit-file-" + str(order_index), "file_title": "ملف اختبار",
                    "responsible_employee_id": employee, "responsible_employee_name": "موظف اختبار",
                    "client_request_id": "exit2d-unit-request-" + str(order_index), "status": "completed"}
        batch = {"id": batch_id, "user_id": "exit2d-unit-owner", "status": "ready", "lines": [],
                 "file_number": registry["file_number"], "responsible_employee_id": employee}
        for item in raw["items"]:
            # Expectations are captured from original fixture before any production projection.
            expected[(number, item["id"])] = {
                "quantity": item["quantity"], "options": {row["name"]: row["value"] for row in item["options"]},
                "product_id": item["product"]["id"]}
            fields = functions["supplier_file_spec_fields"](copy.deepcopy(item), {}, {})
            projected = functions["_card_field_projection"](fields, None)
            line = {"order_number": number, "order_item_id": item["id"], "quantity": item["quantity"],
                    "group_key": "exit2d-unit-group-" + item["id"],
                    "unit_indices": list(range(1, item["quantity"] + 1)), "product_id": item["product"]["id"],
                    "product_name": item["name"], "sku": item["product"]["sku"],
                    "file_spec_fields": fields, **projected}
            batch["lines"].append(line)
            for unit in line["unit_indices"]:
                allocations.append({"order_number": number, "order_item_id": item["id"], "unit_index": unit,
                                    "group_key": line["group_key"],
                                    "batch_id": batch_id, "status": "committed", "user_id": "exit2d-unit-owner"})
        pieces.extend(functions["build_piece_documents"](
            user_id="exit2d-unit-owner", registry=registry, batch=batch, services_by_product={},
            assigned_at=datetime(2026, 9, 8, tzinfo=timezone.utc)))
        batches.append(batch)
        registries.append(registry)
    files = [{"batch_id": "exit2d-unit-batch-" + str(index), "file_number": "exit2d-unit-file-" + str(index)}
             for index in range(len(ORDERS))]
    return {"expected": expected, "allocations": allocations, "pieces": pieces, "batches": batches, "files": files,
            "registries": registries, "employee": employee}


class UnitProjectionTests(unittest.TestCase):
    def test_old_all_options_predicate_rejects_actual_healthy_projection(self):
        data = synthetic_documents()
        rejected = 0
        for piece in data["pieces"]:
            options = data["expected"][(piece["order_number"], piece["order_item_id"])]["options"]
            old = all(piece["product_options_snapshot"].get(key) == value for key, value in options.items())
            rejected += not old
        self.assertTrue(rejected == 8, "OLD_PREDICATE_REPRODUCTION_FAILED")

    def test_actual_projection_preserves_all_specs_and_projects_only_extra_options(self):
        data = synthetic_documents()
        self.assertTrue(len(data["pieces"]) == len(data["allocations"]) == 8, "UNIT_COUNT_MISMATCH")
        for batch in data["batches"]:
            for line in batch["lines"]:
                expected = data["expected"][(line["order_number"], line["order_item_id"])]["options"]
                # Explicit fixture contract, not an expectation derived from output.
                fields = [{"spec_key": "color", "name": "اللون", "value": expected["اللون"],
                           "text": "اللون: " + expected["اللون"]},
                          {"spec_key": "النقش", "name": "النقش", "value": expected["النقش"],
                           "text": "النقش: " + expected["النقش"]}]
                self.assertTrue(line["file_spec_fields"] == fields, "FULL_SPEC_PROJECTION_MISMATCH")
                self.assertTrue(line["color"] == expected["اللون"], "COLOR_PROJECTION_MISMATCH")
                for piece in data["pieces"]:
                    if (piece["order_number"], piece["order_item_id"]) != (line["order_number"], line["order_item_id"]):
                        continue
                    self.assertTrue(piece["specifications_snapshot"] == fields, "PIECE_SPEC_SNAPSHOT_MISMATCH")
                    self.assertTrue(piece["product_options_snapshot"] == {"النقش": expected["النقش"]},
                                    "PIECE_EXTRA_OPTION_SNAPSHOT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
