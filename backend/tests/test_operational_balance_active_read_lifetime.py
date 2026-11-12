"""Active balance panels cancel saved-state reads when their request disconnects."""
import asyncio

from fastapi import FastAPI
import pytest

from operational_balance_routes import make_operational_balance_router
from test_operational_balance_historical_preview_lifetime import request


@pytest.mark.parametrize("path", [
    "/context", "/entities/supplier", "/accounts/bank/bank", "/movements", "/audit",
    "/inventory-purchases", "/inventory-purchases/entry/supplier/invoice",
    "/supplier-adjustments/entry/supplier/invoice", "/customer-returns", "/customer-exchanges",
    "/customer-returns/entry/order", "/customer-exchanges/entry/order",
])
def test_departing_panel_cancels_saved_state_read_without_writers(monkeypatch, path):
    import operational_balance_routes as routes
    import operational_balance_sources as sources
    import operational_balance_exchanges as exchanges
    import operational_customer_returns as returns
    import operational_financial_activity as activity

    async def scenario():
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def read(*_args, **_kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        async def scope(*_args, **_kwargs):
            return {"id": "owner", "role": "owner"}, "owner", "web"

        async def empty(*_args, **_kwargs):
            return []

        async def entity(*_args, **_kwargs):
            return {"kind": "bank", "id": "bank", "currency": "SAR"}

        async def account_scope(*_args, **_kwargs):
            return None

        monkeypatch.setattr(routes, "scope", scope)
        monkeypatch.setattr(routes, "read", read)
        monkeypatch.setattr(routes, "entity", entity)
        monkeypatch.setattr(sources, "entities", empty)
        monkeypatch.setattr(returns, "order_view", empty)
        monkeypatch.setattr(exchanges, "exchange_order", empty)
        monkeypatch.setattr(activity, "account_scope", account_scope)

        async def principal():
            return {"id": "owner"}

        app = FastAPI()
        # A deliberately capability-free db makes any unexpected writer fail.
        app.include_router(make_operational_balance_router(object(), principal), prefix="/api")
        task, queue, sent = request(app, path)
        try:
            await asyncio.wait_for(entered.wait(), 2)
            queue.put_nowait({"type": "http.disconnect"})
            await asyncio.wait_for(task, 2)
            assert stopped.is_set()
            assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 499
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(scenario())
