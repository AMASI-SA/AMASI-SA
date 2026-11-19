"""Refresh stranded component evidence without advancing assembly or stock."""


async def recover_pending_print_snapshot(
    db, user_id, order_number, *, allow_print_catalog_rename=True, authorize=None,
):
    source = await db.unified_orders.find_one(
        {"user_id": str(user_id), "order_number": order_number}) or {}
    if not (source.get("g47_salla_snapshot") or {}).get("component_pending"):
        return

    from fulfillment_carrier_label import _require_print_completed_workflow
    from order_engine.salla_refresh import refresh_order_from_salla

    async def require_completion(scoped):
        await _require_print_completed_workflow(
            scoped, user_id=str(user_id), order_number=order_number)
        if authorize is not None:
            await authorize()

    await require_completion(db)
    # Network reads happen outside the owner transaction. The refresh fences
    # persistence and only carries acceptance across an unchanged contract.
    # The caller must still run all original print/status/provider guards.
    refresh = await refresh_order_from_salla(
        db, str(user_id), order_number, force=True,
        allow_auto_fulfillment=False, allow_delivery_observation=False,
        allow_billing_eligibility=False,
        before_persist=require_completion,
    )
    # Only printing can carry a proven fixed-choice label rename forward.
    # Receiving and Ready retain their existing acceptance behavior.
    if not allow_print_catalog_rename:
        if refresh.get("reason") == "stale_salla_snapshot":
            # The older provider response was discarded before the normal
            # component revalidation ran. Only the newer saved source can
            # prove the existing acceptance under the owner transaction.
            from assembly_status_policy import component_refresh_pending
            from fulfillment_v2_routes import (
                component_source_time, revalidate_unchanged_component_snapshot,
            )
            from operational_atomic import operational_owner

            async def revalidate_current(scoped):
                await require_completion(scoped)
                current = await scoped.unified_orders.find_one(
                    {"user_id": str(user_id), "order_number": order_number}) or {}
                mark = current.get("g47_salla_snapshot") or {}
                saved_clock = component_source_time(mark.get("source_updated_at"))
                received_clock = component_source_time(refresh.get("received_source_updated_at"))
                light = current.get("last_salla_direct_status_source_updated_at")
                light_clock = component_source_time(light)
                if (not received_clock or not saved_clock or received_clock >= saved_clock
                        or mark.get("cancelled") or not component_refresh_pending(current)
                        or (light and (not light_clock or light_clock > saved_clock))):
                    return
                await revalidate_unchanged_component_snapshot(
                    scoped, user_id=str(user_id), order_number=order_number,
                    source_revision=mark.get("revision"), allow_print_catalog_rename=False)

            await operational_owner(db, str(user_id), revalidate_current)
        return refresh
    source = await db.unified_orders.find_one(
        {"user_id": str(user_id), "order_number": order_number}) or {}
    watermark = source.get("g47_salla_snapshot") or {}
    if watermark.get("component_pending"):
        from fulfillment_v2_routes import revalidate_unchanged_component_snapshot
        if authorize is not None:
            await authorize()
        await revalidate_unchanged_component_snapshot(
            db, user_id=str(user_id), order_number=order_number,
            source_revision=watermark.get("revision"), allow_print_catalog_rename=True)
