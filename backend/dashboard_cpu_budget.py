"""Request-local cooperative checkpoints for the measured Dashboard reducers.

Budgets measure active thread CPU, not elapsed time or a maximum stall. A
checkpoint is only between complete ordered work units; large units and the
unchanged finalizers can still exceed a budget. No database reads or governor
admissions occur here.
"""
import asyncio
import time

PARSER_BUDGET_MS = 5
COST_PROFIT_BUDGET_MS = 5
ADVERTISING_BUDGET_MS = 1
PRODUCT_INDEX_BUDGET_MS = 5


class CPUWorkBudget:
    def __init__(self, milliseconds: int):
        self.seconds = milliseconds / 1000
        self.cpu = time.thread_time()

    async def checkpoint(self) -> None:
        if time.thread_time() - self.cpu < self.seconds:
            return
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def resume():
            if not future.done():
                future.set_result(None)

        handle = loop.call_soon(resume)
        try:
            await future
        finally:
            handle.cancel()
        self.cpu = time.thread_time()
