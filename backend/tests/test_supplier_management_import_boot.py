"""Regression coverage for the supplier management wrapper import contract."""

from __future__ import annotations

import importlib
import sys
import types


def test_supplier_management_wrapper_reexports_audit(monkeypatch):
    fulfillment = types.ModuleType("fulfillment_v2_routes")

    async def _actor_context(*_args, **_kwargs):
        return {"merchant_id": "merchant", "actor_id": "actor", "permissions": set()}

    def _require_permission(*_args, **_kwargs):
        return None

    fulfillment._actor_context = _actor_context
    fulfillment._require_permission = _require_permission
    monkeypatch.setitem(sys.modules, "fulfillment_v2_routes", fulfillment)

    product_costs = types.ModuleType("product_option_cost_routes")
    product_costs.RESOURCES = "product_resources_v2"
    monkeypatch.setitem(sys.modules, "product_option_cost_routes", product_costs)

    sys.modules.pop("mezan_supplier_management_routes", None)
    routes = importlib.import_module("mezan_supplier_management_routes")

    from mezan_supplier_management_routes import _audit

    assert callable(_audit)
    assert _audit is routes._BASE._audit
