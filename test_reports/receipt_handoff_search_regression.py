from test_review_local_assembly import LocalAssemblyTests
import preparation_piece_operations as pieces

async def check(self):
    self.mount_assignment()
    await self.complete_review(direct=False)
    cards = await self.catalog_cards()
    for index, card in enumerate(cards):
        response = await self.assign_cards([card], f"search-handoff-{index}")
        self.assertEqual(response.status_code, 200, response.text)
    rows = await self.db[pieces.PIECES].find({}).sort("unit_index", 1).to_list(10)
    for row in rows:
        await self.ready_for_receipt(row)
    self.assertEqual((await self.workflow())["stage"], "in_progress")
    stock_before = await self.on_hand()
    response = await self.receive(rows[0])
    self.assertEqual(response.status_code, 200, response.text)
    result = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
    self.assertEqual(result.status_code, 200, result.text)
    found = {row["piece_id"]: row for row in result.json()["pieces"]}
    self.assertTrue(found[rows[0]["piece_id"]]["can_mark_ready"], found[rows[0]["piece_id"]].get("assembly_blocker_code"))
    self.assertFalse(found[rows[1]["piece_id"]]["can_mark_ready"])
    self.assertEqual(await self.on_hand(), stock_before)
    self.assertEqual((await self.workflow())["stage"], "in_progress")

LocalAssemblyTests.test_partial_custody_exact_search = check

original_check = check
async def check_pending(self):
    await original_check(self)
    await self.db.unified_orders.update_one({"user_id": "owner", "order_number": "local-assembly"}, {"$set": {"g47_salla_snapshot.component_pending": True}})
    before = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": "local-assembly"})
    result = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
    self.assertEqual(result.status_code, 200, result.text)
    self.assertTrue(all(not row["can_mark_ready"] and row["assembly_blocker_code"] == "assembly_order_not_ready" for row in result.json()["pieces"]))
    after = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": "local-assembly"})
    self.assertEqual(before, after)
LocalAssemblyTests.test_pending_snapshot_exact_search = check_pending

async def check_all_pending(self):
    await check_pending(self)
    rows = await self.db[pieces.PIECES].find({}).sort("unit_index", 1).to_list(10)
    result = await self.receive(rows[1])
    self.assertEqual(result.status_code, 200, result.text)
    self.assertEqual((await self.workflow())["preparation_receipt_status"], "completed")
    result = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
    self.assertEqual(result.status_code, 200, result.text)
    self.assertTrue(all(not row["can_mark_ready"] and row["assembly_blocker_code"] == "assembly_order_not_ready" for row in result.json()["pieces"]))
LocalAssemblyTests.test_all_custody_pending_exact_search = check_all_pending
