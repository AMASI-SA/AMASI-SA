"""Compatibility entrypoints: operational balances have no background work.

The owner's visible workspace requests a bounded refresh explicitly. Starting a
web process or worker must never scan source collections, reconcile balances,
acquire financial evidence, or poll accounting mode for this feature.
"""


async def tick(db, **kwargs):
    """Retained for old runtime imports; deliberately performs no reads or writes."""
    return None


async def run(db, interval=None, **kwargs):
    """No scheduler is registered. A stale caller also remains harmless."""
    return None
