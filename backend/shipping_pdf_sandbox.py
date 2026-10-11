"""Bounded disposable parser process; no native PDF parsing in the ASGI process."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import signal
import sys
from weakref import WeakKeyDictionary

WALL_SECONDS = 5
MAX_CONCURRENT = 2
_GATES = WeakKeyDictionary()
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


@asynccontextmanager
async def document_slot():
    loop = asyncio.get_running_loop()
    gate = _GATES.setdefault(loop, asyncio.Semaphore(MAX_CONCURRENT))
    if gate.locked():
        raise ParserError("shipping_document_parser_busy")
    await gate.acquire()  # No queue: only this event loop mutates gate.
    try:
        yield
    finally:
        gate.release()


async def verify_pdf(data, tracking):
    if sys.platform != "linux":
        raise ParserError("shipping_document_isolation_unavailable")
    if not isinstance(data, bytes) or not 0 < len(data) <= 2 * 1024 * 1024:
        raise ParserError("shipping_document_size_exceeded")
    process = None
    spawned = None
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
    except TimeoutError as exc:
        raise ParserError("shipping_document_parser_timeout") from exc
    except (OSError, BrokenPipeError, ConnectionError) as exc:
        raise ParserError("shipping_document_parser_failed") from exc
    finally:
        if process is None and spawned is not None:
            try:
                process = await asyncio.shield(spawned)
            except (OSError, asyncio.CancelledError):
                pass
        if process is not None:
            # Also terminate descendants; a cancelled/timed-out parse is never
            # reused or left executing beside the next request.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
