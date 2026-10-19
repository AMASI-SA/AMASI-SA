from unittest.mock import AsyncMock, patch
from test_g47_component_lifecycle_integration import ComponentRouteTests
import fulfillment_v2_routes as f
import preparation_piece_operations as pieces
from stock_component_consumption_service import PLANS, UNITS

async def recover_legacy(self):
    await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
    payload = self.source_payload(number="legacy-recovery")
    result = await self.webhook(payload)
    self.assertTrue(result["synced"])
    selector={"user_id":"owner","order_number":"legacy-recovery"}
    source=await self.db.unified_orders.find_one(selector)
    lifecycle=await self.db[f.COMPONENT_LIFECYCLES].find_one(selector)
    self.assertEqual(lifecycle["state"], f.LEGACY_COMPONENT_COHORT)
    revision=source["g47_salla_snapshot"]["revision"]
    await self.db.unified_orders.update_one(selector,{"$set":{"g47_salla_snapshot.component_pending":True}})
    await f.record_component_intake_failure(self.db,user_id="owner",order_number="legacy-recovery",source_updated_at=lifecycle["source_updated_at"],source_revision=revision)
    before=await self.db[f.WORKFLOWS].find_one(selector)
    preview=await f.revalidate_unchanged_component_snapshot(self.db,**selector,source_revision=revision,dry_run=True)
    self.assertEqual(preview["reason"],"component_reconciliation_required")
    order=await pieces._current_assembly_order(self.db,**selector)
    import stock_component_consumption_service as stock
    never=AsyncMock(side_effect=AssertionError("legacy recovery touched stock"))
    with patch.object(stock,"reserve_component_stock",never),patch.object(stock,"release_component_stock",never):
        first=await f.reconcile_component_order_lifecycle(self.db,user_id="owner",order=order,source_updated_at=lifecycle["source_updated_at"],source_revision=revision,strict=True)
        second=await f.reconcile_component_order_lifecycle(self.db,user_id="owner",order=order,source_updated_at=lifecycle["source_updated_at"],source_revision=revision,strict=True)
    self.assertTrue(first["accepted"])
    self.assertEqual(first["state"],f.LEGACY_COMPONENT_COHORT)
    self.assertEqual(first["generation"],second["generation"])
    self.assertEqual(before,await self.db[f.WORKFLOWS].find_one(selector))
    self.assertEqual(await self.db[PLANS].count_documents({}),0)
    self.assertEqual(await self.db[UNITS].count_documents({}),0)
    self.assertFalse((await self.db.unified_orders.find_one(selector))["g47_salla_snapshot"]["component_pending"])
ComponentRouteTests.test_legacy_blocked_recovery_without_stock = recover_legacy
