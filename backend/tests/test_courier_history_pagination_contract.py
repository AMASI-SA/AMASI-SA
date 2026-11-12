"""Recovery must request a supported page size without weakening completeness."""
from copy import deepcopy

import pytest

from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as shipping
from salla_integration.service import SallaError
from test_courier_history_identity_recovery import KEY, OWNER, PATH, history_route


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [PATH, PATH.removesuffix("refresh") + "complete-and-refresh"])
@pytest.mark.parametrize("scoped_available", [True, False])
async def test_recovery_lists_use_supported_page_size(history_route, monkeypatch, endpoint, scoped_available):
    db, client, state = history_route
    if endpoint.endswith("complete-and-refresh"):
        employee = "synthetic-pagination-employee"
        await db.users.insert_one({"id": employee, "created_by": OWNER, "role": "employee"})
        await db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": OWNER, "user_id": employee,
            "enabled": True, "permissions": ["app.page.assembly_shipping"]})
        state["actor"] = {"id": OWNER, "_session_client": "amasi_mobile",
                          "_mobile_actor_id": employee, "_mobile_owner_id": OWNER}
    if not scoped_available:
        state["unavailable"].add("/orders/42/shipments")
    provider = compat.call_salla
    requested_pages = []

    async def bounded_provider(database, owner, method, path, **kwargs):
        if path in {"/orders/42/shipments", "/shipments", "/orders/42/histories"}:
            params = deepcopy(kwargs.get("params") or {})
            requested_pages.append((path, params))
            # Salla's published list contract caps per_page at 60. This is a
            # boundary model, not a claim about a captured provider rejection.
            if type(params.get("per_page")) is not int or not 1 <= params["per_page"] <= 60:
                raise SallaError("synthetic unsupported page size", status_code=422)
        return await provider(database, owner, method, path, **kwargs)

    monkeypatch.setattr(compat, "call_salla", bounded_provider)
    monkeypatch.setattr(shipping, "call_salla", bounded_provider)
    before = await db.unified_orders.find_one(KEY)
    response = await client.post(endpoint)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] is True and result["print_state"] == "ready"
    assert result["label_type"] == "store_courier"
    assert all(result.get(key) is None for key in ("shipment_id", "tracking_number", "shipping_number", "label_url"))
    assert "ARCHIVED-MUST-NOT-PRINT" not in response.text and "archived.pdf" not in response.text
    assert ("/orders/42/histories", {"page": 1, "per_page": 50}) in requested_pages
    selected = "/orders/42/shipments" if scoped_available else "/shipments"
    assert sum(path == selected for path, _ in requested_pages) == 2
    for path, params in requested_pages:
        assert params["page"] == 1 and params["per_page"] == 50
        assert (params.get("order_id") == "42") if path == "/shipments" else ("order_id" not in params)
    assert all(method == "GET" for method, _ in state["calls"])
    assert await db.unified_orders.find_one(KEY) == before
