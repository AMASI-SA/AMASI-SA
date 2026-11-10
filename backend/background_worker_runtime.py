"""Start only the jobs already enabled in the web runtime being replaced.

Imports happen after the canonical application composition has installed its
existing sender/ownership/security wrappers. Existing per-job Mongo fences
remain the authority across replicas; no disabled legacy/AI job is enabled.
"""
import asyncio


async def start_tasks(db, *, wake_event=None, assembly_wake_event=None):
    tasks = []
    try:
        from salla_orders_v3.worker import start_salla_orders_v3_shadow_runtime
        shadow = await start_salla_orders_v3_shadow_runtime(db)
        if shadow is not None:
            tasks.append(shadow)
        from integrations.qoyod.worker import start_worker as pipeline
        tasks.append(pipeline(db, interval_sec=5.0, batch_limit=25))
        from integrations.qoyod_manual.auto_send import start_worker as automatic
        tasks.append(automatic(db, batch_limit=5, wake_event=wake_event))
        from integrations.qoyod_manual.auto_cycle import followup_loop
        from integrations.qoyod.scheduled_scan import scheduler
        from salla_integration.scheduled_reconciliation import scheduler as salla_reconciliation
        from salla_integration.service import salla_token_maintenance_loop
        from operational_balance_worker import run as balances
        from order_review_resume_worker import start_worker as review
        from assembly_completion_delivery import start_worker as delivery
        from salla_integration.shipping_label_inbox import start_worker as labels
        from salla_integration.payment_update_inbox import start_worker as payments
        from salla_integration.cost_option_recovery import start_worker as cost_options
        from operational_push import run as push
        for name, factory in (
            ('qoyod-followup', followup_loop), ('qoyod-full-scan', scheduler),
            ('salla-token-maintenance', salla_token_maintenance_loop),
            ('salla-orders-12h', salla_reconciliation),
            ('operational-balances', balances), ('operational-push', push),
        ):
            tasks.append(asyncio.create_task(factory(db), name=name))
        task = await delivery(db, wake_event=assembly_wake_event)
        if task is not None:
            tasks.append(task)
        for start in (review, labels, payments, cost_options):
            task = await start(db)
            if task is not None:
                tasks.append(task)
        from integrations_control_center.ads_auto_sync_scheduler import auto_sync_enabled, auto_sync_loop
        if auto_sync_enabled():
            tasks.append(asyncio.create_task(auto_sync_loop(db), name='advertising-sync'))
        from snapchat_v2.scheduler import shadow_scheduler_enabled, shadow_scheduler_loop
        if shadow_scheduler_enabled():
            tasks.append(asyncio.create_task(shadow_scheduler_loop(db), name='snapchat-shadow'))
        return tasks
    except BaseException:
        await stop_tasks(tasks)
        raise


async def stop_tasks(tasks):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    from integrations.qoyod.scheduled_scan import stop_scans
    await stop_scans()
