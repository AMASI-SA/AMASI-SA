"""Request-lifetime sharing only: no cached dashboard payload survives a request."""
import asyncio
import functools
import inspect
import weakref

from fastapi import HTTPException


class DashboardReadCoordinator:
    def __init__(self):
        self._loops = weakref.WeakKeyDictionary()

    async def run(self, key, factory):
        tasks = self._loops.setdefault(asyncio.get_running_loop(), {})
        task = tasks.get(key)
        if task is None:
            if len(tasks) >= 64:
                raise HTTPException(503, detail={"code": "dashboard_busy", "retryable": True})
            async def execute():
                try:
                    return await factory()
                finally:
                    tasks.pop(key, None)
            task = asyncio.create_task(execute())
            # Consume an exception if all disconnected waiters cancel. Active
            # waiters still receive that same exception; nothing is suppressed.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            tasks[key] = task
        return await asyncio.shield(task)

    def endpoint(self, authorize):
        def decorate(function):
            signature = inspect.signature(function)
            @functools.wraps(function)
            async def wrapped(*args, **kwargs):
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                user = authorize(bound.arguments["user"])
                key = (function.__name__, str(user["id"]), tuple(
                    (name, value) for name, value in bound.arguments.items() if name != "user"
                ))
                return await self.run(key, lambda: function(*args, **kwargs))
            return wrapped
        return decorate
