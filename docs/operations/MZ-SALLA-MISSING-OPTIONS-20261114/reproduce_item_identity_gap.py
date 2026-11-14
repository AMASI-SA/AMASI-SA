"""Offline evidence for the deployed List Order Items compatibility gap.

Synthetic data only. This loads the exact pure production functions through
the AST so no database/provider/startup dependencies are installed or invoked.
It intentionally confirms the existing defect, not a fixed implementation.
Run from the repository root with Python 3.11+ and -B.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from report_option_recovery import attach_report_option_evidence, _has_options

items_spec = importlib.util.spec_from_file_location(
    "diagnostic_order_items_snapshot", BACKEND / "salla_integration/order_items_snapshot.py")
items_module = importlib.util.module_from_spec(items_spec)
items_spec.loader.exec_module(items_module)
CompleteOrderItems = items_module.CompleteOrderItems


def load_functions(path, names, namespace):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in names]
    assert {node.name for node in functions} == set(names)
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return hashlib.sha256(source.encode()).hexdigest()


sync = types.ModuleType("salla_integration.sync")
sync.Any = Any
sync.deepcopy = deepcopy
sync_hash = load_functions(BACKEND / "salla_integration/sync.py",
    {"_str", "_legacy_option_value", "_legacy_item_options"}, sync.__dict__)
sys.modules[sync.__name__] = sync
namespace = {"__package__": "salla_integration", "deepcopy": deepcopy,
             "attach_report_option_evidence": attach_report_option_evidence,
             "_has_options": _has_options}
recovery_hash = load_functions(BACKEND / "salla_integration/cost_option_recovery.py",
    {"raw_order", "candidate", "option_patch"}, namespace)

order = {
    "user_id": "synthetic-owner", "order_number": "synthetic-reference",
    "products": [{"order_item_id": "synthetic-line", "product_id": "synthetic-product",
                  "quantity": 1, "options": [], "unit_cost": 47}],
    "raw_by_source": {"salla_direct": {
        "id": "synthetic-native", "reference_id": "synthetic-reference",
        "items": [{"id": "synthetic-line", "product_id": "synthetic-product",
                   "quantity": 1, "options": [], "price": 123.45}]}},
}
selected = [{"product_option_id": "synthetic-size", "name": "Size",
             "value": {"id": "synthetic-selected", "name": "Example"}}]
documented_shape = [{"id": "synthetic-line", "sku": "SYNTHETIC-SKU",
                     "quantity": 1, "options": selected}]
before = deepcopy(order)
assert namespace["candidate"](order, {"synthetic-product"})

# The real complete-items contract accepts this documented response shape.
response = CompleteOrderItems(documented_shape, order_id="synthetic-native")
response.validate_for("synthetic-native")
assert namespace["option_patch"](order, response) is None

# Changing only the optional product identity lets the same choices through.
explicit_identity = CompleteOrderItems(
    [{**documented_shape[0], "product_id": "synthetic-product"}],
    order_id="synthetic-native")
patch = namespace["option_patch"](order, explicit_identity)
assert patch is not None
assert patch["products"][0]["options_raw"] == selected
assert patch["products"][0]["unit_cost"] == 47
assert patch["raw_by_source.salla_direct"]["items"][0]["price"] == 123.45
assert set(patch) == {"products", "raw_by_source.salla_direct"}
assert order == before

# Existing protection correctly refuses a contradictory product identity.
wrong_identity = [{**documented_shape[0], "product_id": "another-product"}]
assert namespace["option_patch"](order, wrong_identity) is None

print(json.dumps({
    "result": "existing_compatibility_gap_reproduced",
    "eligible_candidate": True,
    "complete_reader_accepts_missing_product_id": True,
    "missing_provider_product_id": "no_patch",
    "matching_provider_product_id": "options_restored_in_memory",
    "conflicting_provider_product_id": "refused",
    "input_unchanged": order == before,
    "database_calls": 0, "provider_calls": 0, "business_writes": 0,
    "source_sha256": {"cost_option_recovery.py": recovery_hash, "sync.py": sync_hash},
}, indent=2))
