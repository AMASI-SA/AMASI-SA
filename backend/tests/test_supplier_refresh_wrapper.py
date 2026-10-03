"""Exercise the installed Production wrapper, not only the unwrapped route."""
import importlib
from unittest.mock import AsyncMock

import pytest
import supplier_receiving_routes as receiving
from product_field_cost_support import install_product_field_cost_support


@pytest.fixture
def installed_live_cost_support(monkeypatch):
    # Restore every installed wrapper so unrelated tests retain their own setup.
    names = ("product_v2_details_routes", "product_option_cost_routes",
             "order_option_cost_snapshot_routes", "product_v2_routes",
             "product_cost_setup_routes", "product_fulfillment_rules",
             "supplier_receiving_routes")
    for name in names:
        module = importlib.import_module(name)
        for key, value in list(vars(module).items()):
            if callable(value):
                monkeypatch.setattr(module, key, value)
    return install_product_field_cost_support


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, False, True])
async def test_installed_wrapper_forwards_refresh_flag_and_transaction(
    monkeypatch, installed_live_cost_support, mode
):
    original = AsyncMock(return_value=[])
    monkeypatch.setattr(receiving, "_recent_session_events", original)
    # AsyncMock dynamically manufactures attributes; set the install marker explicitly.
    original._mezan_live_invoice_costs = False
    installed_live_cost_support()
    db, transaction = object(), object()
    kwargs = {} if mode is None else {"refresh_product_services": mode}
    result = await receiving._recent_session_events(
        db, user_id="merchant", session_id="draft", limit=37,
        mongo_session=transaction, **kwargs
    )
    assert result == []
    original.assert_awaited_once_with(
        db, user_id="merchant", session_id="draft", limit=37,
        mongo_session=transaction, refresh_product_services=mode is True
    )
