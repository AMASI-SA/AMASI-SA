"""Request-owned daily reads and cooperative preparation; never a scheduler."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event

from operational_balance_store import fail

_STOP = ContextVar('operational_daily_read_stop', default=None)


@contextmanager
def preparation_scope(stop):
    token = _STOP.set(stop)
    try:
        yield
    finally:
        _STOP.reset(token)


def preparation_checkpoint():
    stop = _STOP.get()
    if stop is not None and stop.is_set():
        raise asyncio.CancelledError


def checked_rows(values):
    for row in values:
        preparation_checkpoint()
        yield row


async def join_task(task):
    """Observe cleanup even when the outer HTTP task is cancelled repeatedly."""
    while not task.done():
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
    return task.result()


async def wait_for_cleanup(task):
    """Wait for an already-closing build without adopting its old result."""
    if task.done():
        return
    settled = asyncio.get_running_loop().create_future()

    def completed(_):
        if not settled.done():
            settled.set_result(None)

    task.add_done_callback(completed)
    try:
        await settled
    finally:
        task.remove_done_callback(completed)


async def prepare_in_thread(function, *args, **kwargs):
    if _STOP.get() is None:
        # Demand-refresh deadlines and ordinary caller cancellation must also
        # stop cooperative CPU work. A shared/read scope keeps its own event.
        with preparation_scope(Event()):
            return await prepare_in_thread(function, *args, **kwargs)
    preparation_checkpoint()
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        stop = _STOP.get()
        if stop is not None:
            stop.set()
        try:
            await join_task(task)
        except (asyncio.CancelledError, Exception):
            pass  # The cancelled request still observes the worker's termination.
        raise
    preparation_checkpoint()
    return result


async def run_visible_daily_read(request, operation, *,
                                 code='financial_movements_read_cancelled',
                                 message='أُلغيت قراءة الحركات بعد مغادرة الصفحة'):
    """Cancel one reader on disconnect; shared preparation owns its waiter count."""
    if await request.is_disconnected():
        fail(code, message, 499)
    disconnected = False
    work = asyncio.create_task(operation())

    async def listen():
        nonlocal disconnected
        while True:
            event = await request.receive()
            if event['type'] == 'http.disconnect':
                disconnected = True
                work.cancel()
                return

    listener = asyncio.create_task(listen())
    try:
        result = await asyncio.shield(work)
        if disconnected:
            fail(code, message, 499)
        return result
    except asyncio.CancelledError:
        work.cancel()
        try:
            await join_task(work)
        except (asyncio.CancelledError, Exception):
            pass
        if disconnected:
            fail(code, message, 499)
        raise
    finally:
        listener.cancel()
        try:
            await join_task(listener)
        except asyncio.CancelledError:
            pass
