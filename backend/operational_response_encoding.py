"""One owned saved-response serializer per process; no source or finance I/O."""
import asyncio
from threading import Lock
from weakref import WeakKeyDictionary, ref

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from demand_read_diagnostics import phase

from operational_financial_activity_runtime import preparation_checkpoint, prepare_in_thread


_GATES = WeakKeyDictionary()
_GATES_LOCK = Lock()
_PROCESS_ENCODER = Lock()


def _loop_gate():
    # Weak values avoid retaining a closed loop through Lock._loop after a
    # contended test/runtime. Active callers/waiters hold the gate strongly.
    loop = asyncio.get_running_loop()
    with _GATES_LOCK:
        saved = _GATES.get(loop)
        gate = saved() if saved is not None else None
        if gate is None:
            gate = asyncio.Lock()
            _GATES[loop] = ref(gate)
        return gate


def _encode(value):
    # Ordinary HTTP processes use one loop. This second fence also prevents
    # simultaneous large allocations if an embedding runs multiple loops.
    while not _PROCESS_ENCODER.acquire(timeout=.05):
        preparation_checkpoint()
    try:
        preparation_checkpoint()
        with phase("response_encode_cpu_ms", cpu=True):
            content = jsonable_encoder(value)
            preparation_checkpoint()
            result = JSONResponse(content=content, headers={'Cache-Control': 'no-store'})
        preparation_checkpoint()
        return result
    finally:
        _PROCESS_ENCODER.release()


async def encode_saved_response(value):
    """Queued readers cancel immediately; running work joins before returning.

    Uses the existing encoder/response without changing number, date, Unicode,
    header or byte semantics. Authorization belongs to the caller after this
    awaited preparation; a completed encoding never grants access by itself.
    """
    async with _loop_gate():
        preparation_checkpoint()
        result = await prepare_in_thread(_encode, value)
        preparation_checkpoint()
        return result

