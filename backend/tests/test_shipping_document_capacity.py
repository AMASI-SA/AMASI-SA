"""Lifecycle admission: bounded blobs, delayed IO, cancellation and slow ASGI send."""
import asyncio
import json
import os
from pathlib import Path
import tracemalloc
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, APIRouter

import shipping_print_document as documents
import shipping_pdf_sandbox as sandbox


@pytest.mark.asyncio
async def test_slow_storage_cannot_admit_third_download(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def insert(row):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await release.wait()

    collection = SimpleNamespace(create_index=AsyncMock(), insert_one=insert)
    db = {documents.COLLECTION: collection}
    download = AsyncMock(return_value=b"x" * documents.MAX_BYTES)
    monkeypatch.setattr(documents, "_download", download)
    monkeypatch.setattr(sandbox, "verify_pdf", AsyncMock())
    snapshot = {"ready": True, "tracking_number": "TEST-AWB", "shipment_id": "test",
                "label_url": "https://synthetic.invalid/test.pdf"}
    tasks = [asyncio.create_task(documents.verify_and_store(db, "test", str(n), snapshot)) for n in range(2)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(documents.DocumentError) as exc:
            await asyncio.wait_for(documents.verify_and_store(db, "test", "third", snapshot), .2)
        assert exc.value.code == "shipping_document_parser_busy"
        assert download.await_count == 2
    finally:
        release.set()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_detached_database_io_retains_capacity_until_real_completion(cancel):
    entered, release = asyncio.Event(), asyncio.Event()

    async def database_io():
        entered.set()
        await release.wait()
        return b"x" * documents.MAX_BYTES

    async def operation():
        async with sandbox.document_slot():
            return await sandbox.retained_io(database_io(), None if cancel else .01)

    task = asyncio.create_task(operation())
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    try:
        async with sandbox.document_slot():
            with pytest.raises(sandbox.ParserError):
                async with sandbox.document_slot():
                    pytest.fail("IO still owns its slot")
    finally:
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
    async with sandbox.document_slot(), sandbox.document_slot():
        pass


def document_app():
    from shipping_document_capacity import DocumentCapacityRoute, BoundedPDFResponse
    app = FastAPI()
    router = APIRouter(route_class=DocumentCapacityRoute)
    calls = []

    @router.get("/carrier-label/document/{token}")
    async def document(token: str):
        calls.append(token)
        return BoundedPDFResponse(b"x" * documents.MAX_BYTES, media_type="application/pdf")

    app.include_router(router)
    return app, calls


async def request(app, token, send):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "method": "GET",
             "path": "/carrier-label/document/" + token, "root_path": "",
             "query_string": b"", "headers": [], "http_version": "1.1",
             "scheme": "http", "server": ("test", 80), "client": ("test", 1)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(scope, receive, send)


@pytest.mark.asyncio
async def test_slow_clients_bound_allocations_through_last_send_and_recover():
    app, calls = document_app()
    entered, release = asyncio.Event(), asyncio.Event()
    bodies = 0

    async def slow_send(message):
        nonlocal bodies
        if message["type"] == "http.response.body":
            bodies += 1
            if bodies == 2:
                entered.set()
            await release.wait()

    tasks = [asyncio.create_task(request(app, str(n), slow_send)) for n in range(2)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        messages = []
        async def record(message):
            messages.append(message)
        await asyncio.gather(*(request(app, "overflow", record) for _ in range(100)))
        assert len(calls) == 2  # No handler execution / blob allocation for refusals.
        assert all(m["status"] == 503 for m in messages if m["type"] == "http.response.start")
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        await request(app, "after-cancellation", record)
        assert len(calls) == 3
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_slow_send_timeout_never_appends_error_or_success(monkeypatch):
    import shipping_document_capacity as capacity
    monkeypatch.setattr(capacity, "REQUEST_SECONDS", .01)
    app, _ = document_app()
    messages = []

    async def stalled(message):
        messages.append(message["type"])
        if message["type"] == "http.response.body":
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await request(app, "test", stalled)
    assert messages == ["http.response.start", "http.response.body"]
    async with sandbox.document_slot(), sandbox.document_slot():
        pass


@pytest.mark.asyncio
async def test_pdf_chunks_preserve_bytes_and_last_send_holds_capacity():
    app, _ = document_app()
    entered, release = asyncio.Event(), asyncio.Event()
    chunks = []

    async def send(message):
        if message["type"] == "http.response.body":
            assert len(message["body"]) <= 64 * 1024
            chunks.append(message["body"])
            if not message.get("more_body", False):
                entered.set()
                await release.wait()

    task = asyncio.create_task(request(app, "test", send))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        async with sandbox.document_slot():
            with pytest.raises(sandbox.ParserError):
                async with sandbox.document_slot():
                    pytest.fail("last send still owns capacity")
        assert b"".join(chunks) == b"x" * documents.MAX_BYTES
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_two_thousand_concurrent_requests_have_bounded_pdf_payloads():
    app, calls = document_app()
    entered, release = asyncio.Event(), asyncio.Event()
    bodies = 0
    rejected = 0

    async def slow_send(message):
        nonlocal bodies
        if message["type"] == "http.response.body":
            bodies += 1
            if bodies == 2:
                entered.set()
            await release.wait()

    async def discard(message):
        nonlocal rejected
        if message["type"] == "http.response.start":
            assert message["status"] == 503
            rejected += 1

    tracemalloc.start()
    held = [asyncio.create_task(request(app, str(n), slow_send)) for n in range(2)]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.gather(*(request(app, "overflow", discard) for _ in range(1998)))
        current, peak = tracemalloc.get_traced_memory()
        assert len(calls) == 2
        assert rejected == 1998
        # Python allocation regression bound for this synthetic ASGI workload;
        # not a native RSS/cgroup or production ingress memory guarantee.
        assert peak < 32 * 1024 * 1024
        report = {"requests": 2000, "admitted_pdf_payloads": 2, "rejected_before_handler": rejected,
                  "unique_payload_bytes": 2 * documents.MAX_BYTES,
                  "python_traced_current_bytes": current, "python_traced_peak_bytes": peak,
                  "scope": "synthetic slow ASGI send; excludes native allocator, transport and proxy"}
        if os.environ.get("SHIPPING_CAPACITY_REPORT"):
            Path(os.environ["SHIPPING_CAPACITY_REPORT"]).write_text(json.dumps(report, indent=2))
    finally:
        release.set()
        await asyncio.gather(*held, return_exceptions=True)
        tracemalloc.stop()
