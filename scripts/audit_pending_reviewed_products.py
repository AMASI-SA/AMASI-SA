"""Read-only audit of pending source snapshots with completed local review.

Reports exact repair proof separately from remaining supplier-file quantities.
Uses immutable allocations, never the potentially stale preparation_progress.
Run in the backend environment with MONGO_URL and DB_NAME. No provider calls.
Apply an individually reviewed result with revalidate_component_snapshot.py
and its reported expected_revision; that command rereads under the owner fence.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


async def audit(db, user_id):
    from fulfillment_v2_routes import revalidate_unchanged_component_snapshot
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import get_order
    from review_local_policy import load_local_review_workflows, local_review_stage_eligible
    from reviewed_products_catalog import aggregate_reviewed_products, apply_preparation_allocations

    rows = []
    query = {"user_id": user_id, "g47_salla_snapshot.component_pending": True}
    async for source in db.unified_orders.find(query, {"order_number": 1, "g47_salla_snapshot": 1}):
        number = source.get("order_number")
        workflows = await load_local_review_workflows(db, user_id=user_id, order_numbers=[number])
        workflow = workflows.get(number)
        if not workflow or workflow.get("stage") not in {"reviewed", "in_progress"}:
            continue
        revision = source["g47_salla_snapshot"].get("revision")
        try:
            order = await get_order(MongoOrderRepository(db), user_id=user_id, order_number=number)
            if not local_review_stage_eligible(order, workflow):
                continue
            result = await revalidate_unchanged_component_snapshot(
                db, user_id=user_id, order_number=number, source_revision=revision, dry_run=True)
            allocations = await db.mezan_preparation_unit_allocations_v2.find({
                "user_id": user_id, "order_number": number,
                "status": {"$in": ["reserved", "committed"]},
            }).to_list(None)
            catalog = apply_preparation_allocations(aggregate_reviewed_products([(order, workflow)], []), allocations)
            rows.append({"order_number": number, "expected_revision": revision,
                         "eligible_for_revalidation": result.get("eligible_for_revalidation") is True,
                         "reason": result["reason"],
                         "remaining_supplier_quantity": catalog["summary"]["remaining_quantity"]})
        except Exception as exc:
            # Do not leak driver errors, customer facts or connection details.
            rows.append({"order_number": number, "expected_revision": revision,
                         "eligible_for_revalidation": False, "error_type": type(exc).__name__})
    proven = [r for r in rows if r.get("eligible_for_revalidation")
              and r.get("reason") == "zero_option_price_currency_only"]
    return {"dry_run": True, "pending_active_reviewed_orders": len(rows),
            "proven_zero_price_currency_orders": len(proven),
            "proven_orders_with_remaining_supplier_units": sum(r["remaining_supplier_quantity"] > 0 for r in proven),
            "proven_remaining_supplier_units": sum(r["remaining_supplier_quantity"] for r in proven),
            "orders": sorted(rows, key=lambda r: r["order_number"])}


async def run(args):
    from motor.motor_asyncio import AsyncIOMotorClient
    url, name = os.environ.get("MONGO_URL"), os.environ.get("DB_NAME")
    if not url or not name:
        raise SystemExit("MONGO_URL and DB_NAME are required")
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=10000)
    try:
        result = await audit(client[name], args.user_id)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if any("error_type" in row for row in result["orders"]) else 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    sys.exit(asyncio.run(run(parser.parse_args())))
