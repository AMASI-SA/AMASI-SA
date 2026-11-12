"""Read-only visible HTTP lifetime for operational report pages."""
from threading import Event

from operational_financial_activity_runtime import (
    preparation_checkpoint, preparation_scope, run_visible_daily_read,
)


async def run_visible_report_read(request, operation):
    """Own this reader's CPU stop event; shared caches own separate events."""
    stop = Event()

    async def read_page():
        with preparation_scope(stop):
            preparation_checkpoint()
            result = await operation()
            preparation_checkpoint()
            return result

    try:
        return await run_visible_daily_read(request, read_page,
            code="operational_report_read_cancelled", message="أُلغيت قراءة التقرير بعد مغادرة الصفحة")
    finally:
        stop.set()
