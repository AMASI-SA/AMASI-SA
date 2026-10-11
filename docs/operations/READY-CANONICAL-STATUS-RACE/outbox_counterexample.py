"""Run from PR1321's backend with its tests on PYTHONPATH; synthetic IO only.

This is a diagnostic counterexample, not a passing safety acceptance test.
The output must be reviewed as BLOCKED if any post/stale confirmation occurs.
"""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock, patch

import test_assembly_completion_delivery as fixture
from orders_db import upsert_order


async def scenario(boundary):
    case = fixture.DeliveryTests()
    await case.asyncSetUp()
    try:
        await case.finish()
        shipping = fixture.delivery.shipping
        posts = []
        changed = False

        async def delivered():
            nonlocal changed
            if changed:
                return
            changed = True
            row = await case.db.unified_orders.find_one({"order_number": "local-assembly"})
            raw = deepcopy(row["raw_by_source"]["salla_direct"])
            raw["status"] = {"slug": "delivered", "name": "delivered"}
            await upsert_order(case.db, "owner", "local-assembly", {
                "order_status": "delivered", "order_status_slug": "delivered",
            }, "salla_direct", raw=raw)

        async def resolve(*args):
            status = "in_progress" if boundary == "status_post" and not posts else "completed"
            if boundary == "status_post":
                await delivered()
            return "internal", {"id": "internal", "reference_id": "local-assembly", "status": {"slug": status}}

        async def rows(*args):
            await delivered()
            return [{"id": "current", "status": "draft", "type": "shipment"}]

        async def refresh(*args):
            if boundary == "publication":
                await delivered()
            return {"ready": boundary != "awb_post" or bool(posts),
                    "label_url": "https://example.test/stale.pdf"}

        async def post(*args, **kwargs):
            assert kwargs["single_post_attempt"] is True
            posts.append(args[3])
            return {"success": True}

        with patch.object(shipping, "_resolve_order", resolve), \
             patch.object(shipping, "refresh_shipping_label", refresh), \
             patch.object(shipping, "_print_shipment_rows", rows), \
             patch.object(shipping, "_create_payload", return_value={"order_id": "internal"}), \
             patch.object(shipping, "call_salla", post):
            result = await fixture.delivery.resume(case.db, user_id="owner", order_number="local-assembly", manual=True)
        row = await case.db.unified_orders.find_one({"order_number": "local-assembly"})
        return {"boundary": boundary, "canonical_status": row["order_status_slug"],
                "mock_posts": posts, "ready": result.get("ready"),
                "outbox_state": (await case.workflow())[fixture.delivery.FIELD]["state"],
                "error_code": result.get("error_code"), "real_provider_calls": 0}
    finally:
        await case.asyncTearDown()


async def main():
    for boundary in ("status_post", "awb_post", "publication"):
        print(json.dumps(await scenario(boundary)), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
