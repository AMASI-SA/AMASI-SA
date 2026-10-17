"""Cross-contract execution, using the actual Local v1 ASGI/Mongo fixture."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import test_review_local_assembly as local
import preparation_piece_operations as pieces
import order_review_completion as completion
from review_local_policy import assembly_execution_allowed, execution_status_allowed
from order_engine import shipping_label_service as shipping


class CrossTaskExecutionTests(local.LocalAssemblyTests):
    # Inherited mixed-order/receiving/catalog scenarios are deliberately rerun
    # against this policy, not treated as evidence from the Review branch.
    async def dump(self):
        rows = {name: await self.db[name].find({}).sort("_id", 1).to_list(None)
                for name in sorted(await self.db.list_collection_names())}
        # Existing route index/bootstrap may create an empty collection. Compare
        # every document, including owner controls, rather than namespace names.
        return {name: documents for name, documents in rows.items() if documents}

    async def test_status_rejection_both_contracts_search_endpoint_zero_writes(self):
        await self.complete_review(mixed=True)
        await self.db[completion.WORKFLOWS].update_one(
            {"order_number": "local-assembly"}, {"$set": {"stage": "in_progress"}})
        await self.db[pieces.PIECES].insert_one({
            "piece_id": "physical-policy", "user_id": "owner", "order_number": "local-assembly",
            "order_item_id": self.supplier_line_id, "unit_index": 1,
            "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY, "assembly_status": "pending",
            "preparation_receipt_status": "received", "supplier_dispatch_status": "received",
        })
        original = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        for mode in ("mezan_local_v1", None):
            await self.db[completion.WORKFLOWS].update_one(
                {"order_number": "local-assembly"}, {"$set": {"completion_mode": mode}})
            for status in ("shipped", "delivered", "", "unknown", "canceled"):
                with self.subTest(mode=mode, status=status):
                    current = original.model_copy(update={"status": status, "status_native": status})
                    with patch.object(pieces, "_current_assembly_order", AsyncMock(return_value=current)):
                        before = await self.dump()
                        search = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
                        self.assertEqual(search.status_code, 200, search.text)
                        self.assertTrue(search.json()["pieces"])
                        self.assertFalse(any(row["can_mark_ready"] for row in search.json()["pieces"]))
                        for piece_id in (self.ids[0], "physical-policy"):
                            await self.mark(piece_id, status=409)
                        self.assertEqual(await self.dump(), before)
        pieces.sync_completed_carrier_label.assert_not_awaited()

    async def test_duplicate_concurrent_virtual_consumes_each_unit_once(self):
        await self.complete_review()
        responses = await asyncio.gather(*(self.mark_piece(self.ids[0]) for _ in range(3)))
        self.assertTrue(all(r.status_code in {200, 409} for r in responses))
        self.assertTrue(any(r.status_code == 200 for r in responses))
        self.assertEqual(await self.on_hand(), 18)
        before = await self.on_hand()
        await self.mark(self.ids[0])
        self.assertEqual(await self.on_hand(), before)
        self.assertFalse((await self.workflow()).get("assembly_status") == "completed")

    async def test_final_assembly_internal_completion_uses_real_transition_readback(self):
        await self.complete_review()
        await self.mark(self.ids[0])
        order = {"id": "synthetic", "reference_id": "local-assembly", "status": "in_progress",
                 "shipping": {"company_name": "Store courier", "company_code": "0"}}
        calls = []
        async def provider(db, user_id, method, path, **kwargs):
            calls.append((method, path))
            if path == "/orders/statuses":
                return {"data": []}
            self.assertEqual(path, "/orders/synthetic/status" if method == "POST" else "/orders/synthetic")
            if method == "POST":
                self.assertEqual((await self.workflow())["assembly_status"], "completed")
                self.assertEqual(kwargs["json"], {"slug": "completed"})
                order["status"] = "completed"
                return {"success": True}
            return {"data": deepcopy(order)}
        async def finish(*args, **kwargs):
            latest, changed = await shipping._ensure_internal_order_completed(
                self.db, "owner", "local-assembly", "synthetic", deepcopy(order))
            self.assertEqual(latest["status"], "completed")
            return {"ready": True, "order_status_changed": changed}
        with patch.object(shipping, "call_salla", provider), patch.object(pieces, "sync_completed_carrier_label", finish):
            result = await self.mark(self.ids[1])
            self.assertTrue(result["progress"]["order_completed"])
            self.assertTrue(result["piece_ready_confirmed"])
            self.assertEqual(result["order_completion_status"], "pending")
            self.assertEqual(calls, [])
            await finish()
        self.assertEqual(sum(method == "POST" for method, _ in calls), 1)
        self.assertTrue(any(method == "GET" and path == "/orders/synthetic" for method, path in calls))

    async def test_completion_concurrent_attempt_and_uncertain_readback_do_not_repeat_post(self):
        await self.complete_review()
        await self.mark(self.ids[0])
        await self.mark(self.ids[1])
        order = {"id": "synthetic", "reference_id": "local-assembly", "status": "under_review",
                 "shipping": {"company_name": "Store courier", "company_code": "0"}}
        entered, release = asyncio.Event(), asyncio.Event()
        posts = []
        async def provider(db, user_id, method, path, **kwargs):
            if path == "/orders/statuses":
                return {"data": []}
            if method == "POST":
                posts.append(path)
                entered.set()
                await release.wait()
                return {"success": True}
            raise shipping.SallaError("synthetic readback failure", status_code=502)
        async def finish():
            return await shipping._ensure_internal_order_completed(
                self.db, "owner", "local-assembly", "synthetic", deepcopy(order))
        with patch.object(shipping, "call_salla", provider):
            first = asyncio.create_task(finish())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                with self.assertRaises(shipping.ShippingLabelError) as duplicate:
                    await finish()
                self.assertEqual(duplicate.exception.code, "store_courier_completion_unconfirmed")
            finally:
                release.set()
            with self.assertRaises(shipping.ShippingLabelError) as unconfirmed:
                await first
            self.assertEqual(unconfirmed.exception.code, "order_status_verification_failed")
            with self.assertRaises(shipping.ShippingLabelError):
                await finish()
        self.assertEqual(len(posts), 1)


def test_shared_policy_retains_legacy_stage_semantics_in_execution_envelope():
    for stage in ("in_progress", "ready_to_ship"):
        workflow = {"stage": stage}
        order = SimpleNamespace(status="under_review", status_native="under_review")
        assert assembly_execution_allowed(order, workflow)
        assert assembly_execution_allowed(order, workflow, virtual=True)
    order = SimpleNamespace(status="in_progress", status_native="in_progress")
    assert assembly_execution_allowed(order, {"stage": "reviewed"}, virtual=True)
    assert not assembly_execution_allowed(order, {"stage": "reviewed"})
    for status in ("shipped", "delivered", "unknown", ""):
        assert not execution_status_allowed(status)
    assert not execution_status_allowed("delivered", "in_progress")
