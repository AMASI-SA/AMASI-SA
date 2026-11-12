"""The explicit shipping preview owns reads; approval remains a separate writer."""
import asyncio
import json
from threading import Event

from fastapi import APIRouter, FastAPI

from operational_completed_shipping_routes import attach_completed_shipping_routes
from operational_financial_activity_runtime import preparation_checkpoint, prepare_in_thread


def test_disconnect_cancels_and_joins_completion_preview_cpu_without_starting_approval(monkeypatch):
    import operational_completed_shipping_service as service

    async def scenario():
        entered, stopped = Event(), Event()
        approved = []

        def calculation():
            entered.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()

        async def preview(*args, **kwargs):
            return await prepare_in_thread(calculation)

        async def approve(*args, **kwargs):
            approved.append(True)

        async def principal():
            return {"id": "owner"}

        async def scope(*args):
            return {"id": "owner"}, "owner", "web"

        monkeypatch.setattr(service, "preview", preview)
        monkeypatch.setattr(service, "approve", approve)
        app, router = FastAPI(), APIRouter()
        attach_completed_shipping_routes(router, None, principal, scope, lambda permission: principal)
        app.include_router(router)
        messages, sent = asyncio.Queue(), []
        messages.put_nowait({"type": "http.request", "body": json.dumps({"order_number": "10"}).encode(), "more_body": False})

        async def receive():
            return await messages.get()

        async def send(message):
            sent.append(message)

        task = asyncio.create_task(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/completed-shipping-fees/preview",
            "raw_path": b"/completed-shipping-fees/preview", "query_string": b"",
            "headers": [(b"content-type", b"application/json")], "client": ("127.0.0.1", 1000),
            "server": ("isolated", 80), "root_path": ""}, receive, send))
        assert await asyncio.to_thread(entered.wait, 2)
        messages.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)
        assert stopped.is_set() and not approved
        assert next(message["status"] for message in sent if message["type"] == "http.response.start") == 499
        assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]

    asyncio.run(scenario())
