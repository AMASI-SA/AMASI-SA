"""Reviewed worker lifecycle pairs, classified without executing application code.

Exact module + nested function identity prevents allowing every future callback
from an approved module. Registration drift, unknowns and unpaired hooks fail
before any scheduler is started. This does not enable provider feature flags.
"""
from inspect import iscoroutinefunction

# Each pair is a periodic worker lifecycle, not web security or schema setup.
PAIRS = (
 ("advertising_product_watch_scheduler_v3", "attach_advertising_product_watch_scheduler", "_start", "_stop"),
 ("campaign_ai_subprocess_scheduler", "attach_campaign_ai_subprocess_scheduler", "_start_campaign_ai_subprocess_scheduler", "_stop_campaign_ai_subprocess_scheduler"),
 ("product_google_taxonomy_ai_pilot", "make_product_google_taxonomy_ai_pilot_router", "start_resumable_run_loop", "stop_resumable_run_loop"),
 ("integrations_control_center.ads_auto_sync_scheduler", "attach_ads_auto_sync_scheduler", "start", "stop"),
 ("integrations_control_center.snapchat_capi_purchases", "attach_snapchat_capi_purchase_routes", "start", "stop"),
 ("snapchat_v2.scheduler", "attach_shadow_scheduler", "start", "stop"),
)


def classify_callbacks(starts, stops):
    allowed_starts = {(m, o + ".<locals>." + a): (m, o + ".<locals>." + b) for m, o, a, b in PAIRS}
    allowed_stops = set(allowed_starts.values())

    def identities(callbacks, allowed):
        result = []
        for cb in callbacks:
            identity = (cb.__module__, cb.__qualname__)
            if identity not in allowed or not iscoroutinefunction(cb):
                raise RuntimeError("unclassified worker callback: " + ".".join(identity))
            if identity in result:
                raise RuntimeError("duplicate worker callback: " + ".".join(identity))
            result.append(identity)
        return result

    starts, stops = tuple(starts), tuple(stops)
    start_ids = identities(starts, allowed_starts)
    stop_ids = identities(stops, allowed_stops)
    if {allowed_starts[k] for k in start_ids} != set(stop_ids):
        raise RuntimeError("worker callback is missing its lifecycle pair")
    return starts, stops


async def drain_worker_tasks(direct_tasks, stops, timeout_seconds=6):
    """Withdraw direct work immediately; never release a claim on failed drain."""
    import asyncio
    for task in direct_tasks:
        task.cancel()
    hooks = [asyncio.create_task(cb()) for cb in stops]
    all_tasks = [*direct_tasks, *hooks]
    if not all_tasks:
        return
    done, pending = await asyncio.wait(all_tasks, timeout=timeout_seconds)
    if pending:
        for task in pending:
            task.cancel()
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        # Caller retains the claim for expiry and process supervisor escalation.
        raise RuntimeError("worker shutdown deadline exceeded; claim retained")
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            raise RuntimeError("worker shutdown failed; claim retained") from task.exception()
