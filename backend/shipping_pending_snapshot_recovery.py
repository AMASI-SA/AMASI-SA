"""Refresh stranded component evidence without advancing assembly or stock."""


async def recover_pending_print_snapshot(db, user_id, order_number):
    from fulfillment_carrier_label import _require_print_completed_workflow
    from order_engine.salla_refresh import refresh_order_from_salla

    source = await db.unified_orders.find_one(
        {"user_id": str(user_id), "order_number": order_number}) or {}
    if not (source.get("g47_salla_snapshot") or {}).get("component_pending"):
        return

    async def require_completion(scoped):
        await _require_print_completed_workflow(
            scoped, user_id=str(user_id), order_number=order_number)

    await require_completion(db)
    # Network reads happen outside the owner transaction. The refresh fences
    # persistence and only carries acceptance across an unchanged contract.
    # The caller must still run all original print/status/provider guards.
    await refresh_order_from_salla(
        db, str(user_id), order_number, force=True,
        allow_auto_fulfillment=False, allow_delivery_observation=False,
        allow_billing_eligibility=False,
        before_persist=require_completion,
    )
