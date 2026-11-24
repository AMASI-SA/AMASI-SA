"""Read complete capture envelopes without buffering an unbounded expansion."""
import asyncio
import inspect

from motor.core import AgnosticBaseCursor

from operational_demand_refresh import calculation_checkpoint
from operational_financial_activity_runtime import join_task
from operational_provider_capture_batches import expand_batches


MAX_ROWS = 20_000
MOTOR_BATCH_SIZE = 4


async def read_capture_rows(cursor, *, max_rows):
    """Keep the existing row cap and own active Motor I/O through cleanup.

    A cursor row can contain up to 300 financial observations. Stream actual
    Motor documents in small batches and enforce the remaining expanded budget
    before retaining each envelope. Cancellation joins the active driver read
    and then closes the cursor, matching the canonical source-reader lifetime.
    """
    if type(max_rows) is not int or not 0 <= max_rows <= MAX_ROWS:
        raise ValueError("operational_source_scope_too_large")
    calculation_checkpoint()
    if not isinstance(cursor, AgnosticBaseCursor):
        to_list = getattr(cursor, "to_list", None)
        if not inspect.iscoroutinefunction(to_list):
            raise ValueError("operational_source_cursor_unsupported")
        # Offline coroutine readers own their awaited work directly. A Motor
        # Future-returning proxy must never enter this fallback.
        rows = await to_list(max_rows + 1)
        calculation_checkpoint()
        if type(rows) is not list:
            raise ValueError("operational_source_read_invalid")
        return expand_batches(rows, max_rows=max_rows)
    if not all(callable(getattr(cursor, name, None)) for name in ("__anext__", "close", "batch_size")):
        raise ValueError("operational_source_cursor_unsupported")
    cursor = cursor.batch_size(MOTOR_BATCH_SIZE)
    stop = asyncio.Event()

    async def consume():
        retained, documents = [], 0
        try:
            while not stop.is_set():
                calculation_checkpoint()
                try:
                    row = await cursor.__anext__()
                except StopAsyncIteration:
                    break
                if stop.is_set():
                    break
                calculation_checkpoint()
                expanded = expand_batches([row], max_rows=max_rows - len(retained))
                retained.extend(expanded)
                documents += 1
                if documents % 4 == 0:
                    # Buffered Motor rows may not suspend. Let deadlines and
                    # disconnects run before consuming the next small batch.
                    await asyncio.sleep(0)
            return retained
        finally:
            # Never overlap cursor cleanup with an executor getMore operation.
            await cursor.close()

    task = asyncio.create_task(consume())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        try:
            await join_task(task)
        except (asyncio.CancelledError, Exception):
            pass  # Preserve caller cancellation after observing owned cleanup.
        raise
