"""Bounded disposable parser process; no native PDF parsing in the ASGI process."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import signal
import sys
from weakref import WeakKeyDictionary

WALL_SECONDS = 5
CLEANUP_SECONDS = 1
MAX_CONCURRENT = 2
_GATES = WeakKeyDictionary()
_SLOT = ContextVar("shipping_pdf_slot", default=None)
_SUPERVISORS = set()  # Strong ownership until a late child has been killed/reaped.
WORKER = Path(__file__).with_name("shipping_pdf_worker.py")
ALLOWED = {
    "shipping_document_not_pdf", "shipping_document_pdf_rejected",
    "shipping_document_awb_unproven", "shipping_document_size_exceeded",
    "shipping_document_identity_missing", "shipping_document_parser_failed",
}


class ParserError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Slot:
    def __init__(self, gate):
        self.gate = gate
        self.pending = set()
        self.exited = False
        self.released = False

    def release_if_clean(self):
        if self.exited and not self.pending and not self.released:
            self.released = True
            self.gate.release()


def _supervise(coroutine, slot):
    task = asyncio.create_task(coroutine)
    _SUPERVISORS.add(task)
    if slot is not None:
        slot.pending.add(task)
    def finished(done):
        _SUPERVISORS.discard(done)
        # A failed/cancelled supervisor cannot prove cleanup. Quarantine its
        # capacity for this process lifetime instead of admitting more children.
        clean = not done.cancelled() and done.exception() is None
        if slot is not None and clean:
            slot.pending.discard(done)
            slot.release_if_clean()
    task.add_done_callback(finished)
    return task


@asynccontextmanager
async def document_slot():
    loop = asyncio.get_running_loop()
    gate = _GATES.setdefault(loop, asyncio.Semaphore(MAX_CONCURRENT))
    if gate.locked():
        raise ParserError("shipping_document_parser_busy")
    await gate.acquire()  # No queue: only this event loop mutates gate.
    slot = _Slot(gate)
    token = _SLOT.set(slot)
    try:
        yield
    finally:
        _SLOT.reset(token)
        slot.exited = True
        slot.release_if_clean()


async def verify_pdf(data, tracking):
    if sys.platform != "linux":
        raise ParserError("shipping_document_isolation_unavailable")
    if not isinstance(data, bytes) or not 0 < len(data) <= 2 * 1024 * 1024:
        raise ParserError("shipping_document_size_exceeded")
    process = None
    spawned = None
    verified = False
    async def run():
        nonlocal process, spawned
        spawned = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-I", str(WORKER), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            cwd=str(WORKER.parent), start_new_session=True,
        ))
        process = await asyncio.shield(spawned)
        process.stdin.write(json.dumps({"tracking": tracking}).encode() + b"\n" + data)
        await process.stdin.drain()
        process.stdin.close()
        result = await process.stdout.read(129)
        await process.wait()
        if process.returncode != 0:
            raise ParserError("shipping_document_parser_failed")
        if result != b"OK":
            code = result.decode("ascii", errors="replace")
            raise ParserError(code if code in ALLOWED else "shipping_document_parser_failed")
    try:
        await asyncio.wait_for(run(), WALL_SECONDS)
        verified = True
    except TimeoutError as exc:
        raise ParserError("shipping_document_parser_timeout") from exc
    except (OSError, BrokenPipeError, ConnectionError) as exc:
        raise ParserError("shipping_document_parser_failed") from exc
    finally:
        if spawned is not None:
            async def cleanup():
                child = process
                if child is None:
                    try:
                        child = await asyncio.shield(spawned)
                    except OSError:
                        return  # Creation failed before returning a child.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await child.wait()
            # Cleanup is owned independently of request cancellation. A stuck
            # spawn/reap never extends the request's cleanup grace indefinitely,
            # and never releases its admission slot until cleanup is proved.
            supervisor = _supervise(cleanup(), _SLOT.get())
            done, _ = await asyncio.wait({supervisor}, timeout=CLEANUP_SECONDS)
            if (not done or supervisor.cancelled() or supervisor.exception() is not None) and verified:
                raise ParserError("shipping_document_parser_failed")
