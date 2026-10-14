"""Real disposable Linux processes and OS limits; no carrier/network calls."""
import asyncio
import os
from pathlib import Path
import sys
import time
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pymupdf
import pytest

import shipping_pdf_sandbox as sandbox
import shipping_print_document as documents
from test_shipping_phase1_policy import scenario, OWNER, NUMBER

VERIFY = documents.verify_and_store
LINUX = pytest.mark.skipif(sys.platform != "linux", reason="Actual Linux rlimits require Linux; CI must run these")


def pdf(pages=1, tracking="AWB-1", encrypted=False, complex=False):
    with pymupdf.open() as doc:
        for index in range(pages):
            page = doc.new_page()
            page.insert_text((40, 40), f"Shipment {tracking} page {index + 1}")
            if complex:
                shape = page.new_shape()
                for n in range(1500):
                    x, y = 20 + n % 500, 60 + n % 650
                    shape.draw_line((x, y), (x + 10, y + 10))
                shape.finish(color=(0.1, 0.2, 0.3))
                shape.commit()
                for row in range(25):
                    page.insert_text((40, 100 + row * 20), "Synthetic shipping document " * 3)
        kwargs = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "owner_pw": "owner", "user_pw": "user"} if encrypted else {}
        return doc.tobytes(deflate=True, **kwargs)


def probe(tmp_path, monkeypatch, body):
    worker = tmp_path / "probe.py"
    module_dir = str(Path(sandbox.__file__).parent)
    worker.write_text("import sys, os, time, json\n" +
        f"sys.path.insert(0, {module_dir!r})\n" +
        "from shipping_pdf_worker import apply_limits\napply_limits()\n" + body, encoding="utf-8")
    monkeypatch.setattr(sandbox, "WORKER", worker)
    return worker


@LINUX
@pytest.mark.asyncio
async def test_valid_complex_eight_page_pdf_uses_real_child():
    blob = pdf(pages=8, complex=True)
    assert len(blob) < 2 * 1024 * 1024
    assert await sandbox.verify_pdf(blob, "AWB-1") is None


@LINUX
@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [
    ("nine_pages", "shipping_document_pdf_rejected"),
    ("encrypted", "shipping_document_pdf_rejected"),
    ("malformed", "shipping_document_pdf_rejected"),
    ("html", "shipping_document_not_pdf"),
    ("wrong_awb", "shipping_document_awb_unproven"),
    ("prefix_awb", "shipping_document_awb_unproven"),
    ("suffix_awb", "shipping_document_awb_unproven"),
])
async def test_real_parser_retains_identity_and_format_rejections(kind, expected):
    blobs = {"nine_pages": lambda: pdf(pages=9), "encrypted": lambda: pdf(encrypted=True),
        "malformed": lambda: b"%PDF-1.7\ninvalid objects and xref\n%%EOF",
        "html": lambda: b"<html>login</html>", "wrong_awb": lambda: pdf(tracking="AWB-2"),
        "prefix_awb": lambda: pdf(tracking="PREFIX-AWB-1"), "suffix_awb": lambda: pdf(tracking="AWB-1-SUFFIX")}
    with pytest.raises(sandbox.ParserError) as error:
        await sandbox.verify_pdf(blobs[kind](), "AWB-1")
    assert error.value.code == expected


@LINUX
@pytest.mark.asyncio
async def test_os_address_space_limit_actually_refuses_large_allocation(tmp_path, monkeypatch):
    probe(tmp_path, monkeypatch,
        "import resource\n" +
        "limits = {'as': resource.getrlimit(resource.RLIMIT_AS), 'cpu': resource.getrlimit(resource.RLIMIT_CPU)}\n"
        "try:\n    huge = bytearray(600 * 1024 * 1024)\nexcept MemoryError:\n    limits['refused'] = True\n"
        "else:\n    limits['refused'] = False\n"
        "assert limits['as'] == (512 * 1024 * 1024,) * 2\n"
        "assert limits['cpu'] == (2, 2)\nassert limits['refused']\nsys.stdout.write('OK')\n")
    await sandbox.verify_pdf(b"synthetic input", "AWB-1")


@LINUX
@pytest.mark.asyncio
async def test_actual_cpu_limit_kills_busy_child_before_wall_limit(tmp_path, monkeypatch):
    probe(tmp_path, monkeypatch, "while True:\n    pass\n")
    start = time.monotonic()
    with pytest.raises(sandbox.ParserError) as error:
        await sandbox.verify_pdf(b"synthetic input", "AWB-1")
    assert error.value.code == "shipping_document_parser_failed"
    assert 1 <= time.monotonic() - start < sandbox.WALL_SECONDS


@LINUX
@pytest.mark.asyncio
async def test_actual_wall_timeout_keeps_event_loop_responsive(tmp_path, monkeypatch):
    probe(tmp_path, monkeypatch, "time.sleep(30)\n")
    ticks = []
    async def heartbeat():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)
    beat = asyncio.create_task(heartbeat())
    start = time.monotonic()
    try:
        with pytest.raises(sandbox.ParserError) as error:
            await sandbox.verify_pdf(b"synthetic input", "AWB-1")
        assert error.value.code == "shipping_document_parser_timeout"
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
    elapsed = time.monotonic() - start
    assert sandbox.WALL_SECONDS <= elapsed < sandbox.WALL_SECONDS + 2
    assert len(ticks) > 30
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < 1


@LINUX
@pytest.mark.asyncio
async def test_cancellation_kills_and_reaps_actual_process(tmp_path, monkeypatch):
    probe(tmp_path, monkeypatch, "time.sleep(30)\n")
    original = asyncio.create_subprocess_exec
    children = []
    async def capture(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    task = asyncio.create_task(sandbox.verify_pdf(b"synthetic input", "AWB-1"))
    for _ in range(100):
        if children:
            break
        await asyncio.sleep(0.01)
    assert children
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


@LINUX
@pytest.mark.asyncio
async def test_cancellation_during_spawn_reaps_child_before_releasing_caller(tmp_path, monkeypatch):
    probe(tmp_path, monkeypatch, "time.sleep(30)\n")
    original = asyncio.create_subprocess_exec
    created, release = asyncio.Event(), asyncio.Event()
    children = []
    async def delayed_spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        created.set()
        await release.wait()
        return child
    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(sandbox.verify_pdf(b"synthetic input", "AWB-1"))
    await asyncio.wait_for(created.wait(), 2)
    task.cancel()
    await asyncio.sleep(0.05)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert children[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("double_cancel", [False, True])
async def test_stalled_spawn_has_bounded_request_and_quarantines_slot_until_reaped(monkeypatch, double_cancel):
    # Scheduler regression uses a synthetic late Process on every platform;
    # separate Linux tests above prove actual kill/reap and OS resource limits.
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox, "WALL_SECONDS", 0.1)
    monkeypatch.setattr(sandbox, "CLEANUP_SECONDS", 0.05)
    created, release = asyncio.Event(), asyncio.Event()
    child = SimpleNamespace(pid=424242, wait=AsyncMock(return_value=0))
    kills = []
    monkeypatch.setattr(sandbox.os, "killpg", lambda pid, sig: kills.append(pid), raising=False)
    monkeypatch.setattr(sandbox.signal, "SIGKILL", 9, raising=False)
    async def delayed_spawn(*args, **kwargs):
        created.set()
        await release.wait()
        return child
    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    async def request():
        async with sandbox.document_slot():
            await sandbox.verify_pdf(b"synthetic", "AWB-1")
    async with sandbox.document_slot():
        task = asyncio.create_task(request())
        await asyncio.wait_for(created.wait(), 1)
        started = time.monotonic()
        if double_cancel:
            task.cancel()
            for _ in range(100):
                if sandbox._SUPERVISORS:
                    break
                await asyncio.sleep(0.001)
            assert sandbox._SUPERVISORS
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        else:
            with pytest.raises(sandbox.ParserError) as error:
                await asyncio.wait_for(task, 1)
            assert error.value.code == "shipping_document_parser_timeout"
        assert time.monotonic() - started < 0.8
        assert sandbox._SUPERVISORS
        with pytest.raises(sandbox.ParserError) as error:
            async with sandbox.document_slot():
                pytest.fail("Late child must keep its capacity quarantined")
        assert error.value.code == "shipping_document_parser_busy"
        assert kills == []
        supervisors = tuple(sandbox._SUPERVISORS)
        release.set()
        await asyncio.wait_for(asyncio.gather(*supervisors), 1)
        await asyncio.sleep(0)  # Run completion callbacks that release quarantine.
        assert kills == [child.pid]
        child.wait.assert_awaited_once()
        assert not sandbox._SUPERVISORS
        async with sandbox.document_slot():
            pass


@pytest.mark.asyncio
async def test_unproved_cleanup_rejects_even_successful_parser_and_retains_capacity(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox, "CLEANUP_SECONDS", 0.05)
    monkeypatch.setattr(sandbox.os, "killpg", lambda pid, sig: None, raising=False)
    monkeypatch.setattr(sandbox.signal, "SIGKILL", 9, raising=False)
    release = asyncio.Event()
    calls = 0
    async def wait():
        nonlocal calls
        calls += 1
        if calls > 1:
            await release.wait()
        return 0
    child = SimpleNamespace(pid=424242, returncode=0, wait=wait,
        stdin=SimpleNamespace(write=lambda data: None, drain=AsyncMock(), close=lambda: None),
        stdout=SimpleNamespace(read=AsyncMock(return_value=b"OK")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=child))
    async with sandbox.document_slot():
        with pytest.raises(sandbox.ParserError) as error:
            async with sandbox.document_slot():
                await sandbox.verify_pdf(b"synthetic", "AWB-1")
        assert error.value.code == "shipping_document_parser_failed"
        with pytest.raises(sandbox.ParserError):
            async with sandbox.document_slot():
                pytest.fail("Unproved cleanup must quarantine parser capacity")
        supervisors = tuple(sandbox._SUPERVISORS)
        assert supervisors
        release.set()
        await asyncio.wait_for(asyncio.gather(*supervisors), 1)
        await asyncio.sleep(0)
        async with sandbox.document_slot():
            pass


@pytest.mark.asyncio
async def test_document_concurrency_has_two_slots_and_no_waiting_queue():
    async with sandbox.document_slot():
        async with sandbox.document_slot():
            with pytest.raises(sandbox.ParserError) as error:
                async with sandbox.document_slot():
                    pytest.fail("A third document must not enter")
            assert error.value.code == "shipping_document_parser_busy"
    async with sandbox.document_slot():
        pass  # Both slots released after refusal.


@pytest.mark.asyncio
async def test_unsupported_platform_fails_closed_before_spawning(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "unsupported-test-os")
    spawn = AsyncMock(side_effect=AssertionError("must not spawn without resource isolation"))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(sandbox.ParserError) as error:
        await sandbox.verify_pdf(b"synthetic input", "AWB-1")
    assert error.value.code == "shipping_document_isolation_unavailable"
    spawn.assert_not_called()


@LINUX
@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code", [("os._exit(42)", "shipping_document_parser_failed"),
    ("time.sleep(30)", "shipping_document_parser_timeout")])
async def test_parser_crash_or_timeout_cannot_store_or_publish_ready(scenario, monkeypatch, tmp_path, failure, code):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    import fulfillment_v2_routes as routes
    db, state = scenario
    probe(tmp_path, monkeypatch, failure + "\n")
    monkeypatch.setattr(documents, "verify_and_store", VERIFY)
    monkeypatch.setattr(documents, "_download", AsyncMock(return_value=pdf()))
    async def user():
        return {"id": OWNER, "role": "owner"}
    app = FastAPI()
    app.include_router(routes.make_fulfillment_v2_router(db, user))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://example.test") as client:
        response = await client.post(f"/fulfillment-v2/completed/{NUMBER}/carrier-label/refresh")
    assert response.status_code == 503
    assert response.json()["detail"]["reason_code"] == code
    assert response.json().get("ready") is not True
    assert await db[documents.COLLECTION].count_documents({}) == 0
    workflow = await db.order_review_workflows.find_one({"user_id": OWNER})
    assert workflow["carrier_label_ready"] is False
    assert not workflow.get("carrier_label_url")
    assert all(method == "GET" for method, _ in state["calls"])
