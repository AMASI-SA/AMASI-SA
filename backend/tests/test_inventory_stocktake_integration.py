"""Native stocktake API against disposable loopback Mongo transactions.

Only authentication is replaced. Quantities, costs, admission/owner fences,
opening verification, periods and the sealed journal all remain real.
"""
import asyncio
from datetime import datetime
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_ledger_v2 import GROUPS_COLLECTION, verify_journal_v2
from inventory_stocktake_routes import make_inventory_stocktake_router
import inventory_stocktake_service as count_service
import test_g47_purchase_approval_integration as fixture

BASE = "/api/inventory-v2/stocktakes"


class StocktakeMongoIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mongo = AsyncIOMotorClient(fixture.local_uri("MZ2_TEST_MONGO_URI"), serverSelectionTimeoutMS=3000)
        self.assertTrue((await self.mongo.admin.command("hello")).get("setName"))
        self.db_name = "inventory_count_" + uuid.uuid4().hex
        self.db = self.mongo[self.db_name]
        self.addAsyncCleanup(self.cleanup_db)
        self.actor = {"id": "owner", "role": "owner", "is_active": True, "accounting_permissions": [
            "accounting.inventory.adjustments.review", "accounting.inventory.adjustments.post"]}
        await fixture.PurchaseApprovalMongoIntegration.seed(self, self.db)
        await self.db.warehouse_locations_warehouses.insert_one({"id": "warehouse", "user_id": "owner", "name": "Local test warehouse", "status": "active"})
        await self.db.mezan_cost_resources_v2.update_one({"id": "component-A"}, {"$set": {"unit": "gram"}})
        await self.db.expense_categories.insert_one({"user_id": "owner", "code": "inventory-variance", "name": "Local variance account", "status": "active"})
        self.app = fixture.app_for(self.db, self.actor)
        async def identity(): return dict(self.actor)
        self.app.include_router(make_inventory_stocktake_router(self.db, identity), prefix="/api")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://synthetic.local")
        self.addAsyncCleanup(self.client.aclose)
        self.flag = patch.dict(os.environ, {"MEZAN_STOCKTAKE_POSTING_ENABLED": "1"})
        self.flag.start(); self.addCleanup(self.flag.stop)
        payload = fixture.PurchaseApprovalMongoIntegration.payload(self)
        invoice = await self.checked(self.client.post("/api/purchase-invoices", json=payload))
        await self.checked(self.client.post(f"/api/purchase-invoices/{invoice['id']}/approve-receive",
            json=fixture.PurchaseApprovalMongoIntegration.request_for(self, invoice)))
        self.initial_journals = await self.db[GROUPS_COLLECTION].count_documents({})

    async def cleanup_db(self):
        self.assertTrue(self.db_name.startswith("inventory_count_"))
        await self.mongo.drop_database(self.db_name); self.mongo.close()

    async def checked(self, response, expected=200):
        response = await response
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    async def create(self, **changes):
        return await self.checked(self.client.post(BASE, json={"request_id": uuid.uuid4().hex, "title": "Synthetic physical count", **changes}))

    async def save(self, doc, counts):
        lines = [{"id": r["id"], "counted_quantity": counts.get(r["kind"]), "unit": r["unit"], "reason": "Local inspection"}
                 for r in doc["lines"] if r["kind"] in counts]
        return await self.checked(self.client.put(f"{BASE}/{doc['id']}", json={"request_id": uuid.uuid4().hex,
            "expected_revision": doc["revision"], "title": doc["title"], "lines": lines}))

    async def action(self, doc, action, **changes):
        return await self.checked(self.client.post(f"{BASE}/{doc['id']}/actions", json={"request_id": uuid.uuid4().hex,
            "expected_revision": doc["revision"], "action": action, "reason": "Synthetic variance investigation",
            "inventory_account_id": "inventory", "variance_account_code": "inventory-variance", **changes}))

    async def approved(self, counts=None):
        doc = await self.save(await self.create(), counts or {"product": "3"})
        doc = await self.action(doc, "submit")
        return await self.action(doc, "approve")

    def post_payload(self, doc):
        return {"operation_id": doc["posting_operation_id"], "expected_revision": doc["revision"]}

    async def post(self, doc, expected=200, client=None):
        return await self.checked((client or self.client).post(f"{BASE}/{doc['id']}/post", json=self.post_payload(doc)), expected)

    async def physical(self, location="location-product"):
        row = await self.db.warehouse_locations.find_one({"id": location})
        return row["occupancy"]["total_quantity"]

    async def test_draft_partial_save_reload_has_no_physical_or_financial_effect(self):
        doc = await self.create()
        self.assertEqual(doc["status"], "draft")
        self.assertEqual(len(doc["lines"]), 2)
        self.assertTrue(all(r["counted_quantity"] is None for r in doc["lines"]))
        saved = await self.save(doc, {"product": "3"})
        loaded = await self.checked(self.client.get(f"{BASE}/{doc['id']}"))
        self.assertEqual(loaded["revision"], saved["revision"])
        self.assertEqual(loaded["counted_lines"], 1)
        self.assertEqual(loaded["increase_value"], "10.00")
        self.assertEqual(await self.physical(), 2)
        self.assertEqual(await self.db[GROUPS_COLLECTION].count_documents({}), self.initial_journals)
        self.assertEqual([e["actor_id"] for e in loaded["audit"]], ["owner", "owner"])

    async def test_partial_positive_post_is_sealed_available_and_replay_safe(self):
        doc = await self.approved()
        posted = await self.post(doc)
        self.assertEqual(posted["status"], "posted")
        self.assertEqual(await self.physical(), 3)
        self.assertEqual(await self.physical("location-component"), 4)
        self.assertTrue((await verify_journal_v2(self.db, user_id="owner", txn_group_id=posted["txn_group_id"]))["verified"])
        receipts_before = await self.db.mezan_inventory_receipts_v2.count_documents({})
        self.assertEqual((await self.post(doc))["id"], posted["id"])
        self.assertEqual(await self.physical(), 3)
        self.assertEqual(await self.db.mezan_inventory_receipts_v2.count_documents({}), receipts_before)
        projection = await count_service.snapshot(self.db, "owner", {"is_owner": True}, {})
        self.assertEqual(sum(float(r["recorded_quantity"]) for r in projection if r["kind"] == "product"), 3)
        native = await count_service._projection(self.db, "owner")
        self.assertEqual(sum(r["available"] for r in native["items"] if r["kind"] == "product"), 3)
        receipt = await self.db.mezan_inventory_receipts_v2.find_one({"source_type": "inventory_adjustment"})
        self.assertNotIn("components_consumed", receipt)

    async def test_mixed_sign_component_fraction_and_product_native_units(self):
        doc = await self.approved({"product": "4", "component": "3.5"})
        self.assertEqual(doc["increase_value"], "20.00")
        self.assertEqual(doc["decrease_value"], "1.25")
        posted = await self.post(doc)
        self.assertEqual(await self.physical(), 4)
        self.assertEqual(await self.physical("location-component"), 3.5)
        self.assertTrue((await verify_journal_v2(self.db, user_id="owner", txn_group_id=posted["txn_group_id"]))["verified"])
        costs = [r async for r in self.db.mz2_inventory_cost_states.find({})]
        self.assertEqual(sorted(float(r["average_cost"]) for r in costs), [2.5, 10])

    async def test_default_closed_posting_never_changes_real_authorities(self):
        doc = await self.approved()
        with patch.dict(os.environ, {"MEZAN_STOCKTAKE_POSTING_ENABLED": ""}):
            context = await self.checked(self.client.get(BASE + "/context"))
            self.assertFalse(context["posting_enabled"])
            response = await self.post(doc, 423)
            self.assertEqual(response["detail"]["code"], "stocktake_posting_disabled")
        self.assertEqual(await self.physical(), 2)

    async def test_review_return_edit_reject_and_append_only_audit(self):
        doc = await self.save(await self.create(), {"product": "1"})
        doc = await self.action(doc, "submit")
        doc = await self.action(doc, "return", reason="Check the count again")
        doc = await self.save(doc, {"product": "0"})
        doc = await self.action(doc, "submit")
        doc = await self.action(doc, "reject", reason="Unreconciled evidence")
        self.assertEqual(doc["status"], "rejected")
        self.assertEqual([e["action"] for e in doc["audit"]], ["create", "save", "submit", "return", "save", "submit", "reject"])
        self.assertEqual(await self.physical(), 2)

    async def test_two_simultaneous_posts_have_one_physical_effect(self):
        doc = await self.approved()
        requests = [self.client.post(f"{BASE}/{doc['id']}/post", json=self.post_payload(doc)) for _ in range(2)]
        responses = await asyncio.gather(*requests)
        self.assertIn(200, [r.status_code for r in responses])
        self.assertTrue(all(r.status_code in {200, 429} for r in responses), [r.text for r in responses])
        self.assertEqual((await self.post(doc))["status"], "posted")
        self.assertEqual(await self.physical(), 3)
        self.assertEqual(await self.db.mezan_inventory_receipts_v2.count_documents({"source_type": "inventory_adjustment"}), 1)

    async def test_two_approved_counts_cannot_both_post_same_original_balance(self):
        first = await self.approved(); second = await self.approved()
        await self.post(first)
        failure = await self.post(second, 409)
        self.assertEqual(failure["detail"]["code"], "stocktake_snapshot_changed")
        self.assertEqual(await self.physical(), 3)

    async def test_live_quantity_and_cost_changes_require_fresh_review(self):
        doc = await self.approved()
        await self.db.warehouse_locations.update_one({"id": "location-product"}, {"$set": {"occupancy.items.0.quantity": 1, "occupancy.total_quantity": 1}})
        failure = await self.post(doc, 409)
        self.assertEqual(failure["detail"]["code"], "stocktake_snapshot_changed")
        doc = await self.action(doc, "return", reason="Changed source needs a new comparison")
        doc = await self.action(doc, "refresh_snapshot")
        self.assertEqual(next(r["recorded_quantity"] for r in doc["lines"] if r["kind"] == "product"), "1")
        doc = await self.action(await self.action(doc, "submit"), "approve")
        await self.db.mz2_inventory_cost_states.update_one({"inventory_identity.item_type": "product"}, {"$set": {"average_cost": "11.000000"}})
        self.assertEqual((await self.post(doc, 409))["detail"]["code"], "stocktake_snapshot_changed")
        self.assertEqual(await self.physical(), 1)

    async def test_negative_difference_cannot_consume_native_product_reservations(self):
        doc = await self.approved({"product": "0"})
        location = await self.db.warehouse_locations.find_one({"id": "location-product"})
        lot = location["occupancy"]["items"][0]["receipt_id"]
        await self.db.mezan_inventory_reservations_v2.insert_one({"user_id": "owner", "id": "synthetic-reservation", "status": "active", "allocations": [
            {"inventory_row_key": "receipt:" + lot, "location_id": "location-product", "quantity": 2}]})
        self.assertEqual((await self.post(doc, 409))["detail"]["code"], "stocktake_reserved_quantity")
        self.assertEqual(await self.physical(), 2)

    async def test_bundle_product_holds_remain_protected(self):
        doc = await self.approved({"product": "1"})
        location = await self.db.warehouse_locations.find_one({"id": "location-product"})
        lot = location["occupancy"]["items"][0]["receipt_id"]
        await self.db.mezan_component_consumption_units_v1.insert_one({"user_id": "owner", "state": "reserved", "bundle_leaves": [], "allocations": [
            {"inventory_identity": {"item_type": "product"}, "location_id": "location-product", "lot_id": lot, "quantity": "2"}]})
        self.assertEqual((await self.post(doc, 409))["detail"]["code"], "stocktake_reserved_quantity")
        self.assertEqual(await self.physical(), 2)

    async def test_component_reservations_and_held_lots_are_not_adjusted(self):
        doc = await self.approved({"component": "1"})
        location = await self.db.warehouse_locations.find_one({"id": "location-component"})
        lot = location["occupancy"]["items"][0]["receipt_id"]
        await self.db.mezan_component_consumption_units_v1.insert_one({"user_id": "owner", "state": "reserved", "allocations": [
            {"id": "synthetic-component-hold", "resource_id": "component-A", "location_id": "location-component", "lot_id": lot, "quantity": "3"}]})
        self.assertEqual((await self.post(doc, 409))["detail"]["code"], "stocktake_reserved_quantity")
        self.assertEqual(await self.physical("location-component"), 4)

    async def test_explicit_review_post_grants_are_rechecked_even_for_owner(self):
        doc = await self.save(await self.create(), {"product": "3"})
        doc = await self.action(doc, "submit")
        await self.db.users.update_one({"id": "owner"}, {"$set": {"accounting_permissions": []}})
        context = await self.checked(self.client.get(BASE + "/context"))
        self.assertFalse(context["capabilities"]["review"]); self.assertFalse(context["capabilities"]["post"])
        await self.checked(self.client.post(f"{BASE}/{doc['id']}/actions", json={"request_id": uuid.uuid4().hex,
            "expected_revision": doc["revision"], "action": "approve"}), 403)
        self.assertEqual(await self.physical(), 2)

    async def test_revoked_post_grant_and_disabled_actor_never_post(self):
        doc = await self.approved()
        await self.db.users.update_one({"id": "owner"}, {"$set": {"accounting_permissions": []}})
        await self.post(doc, 403)
        await self.db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
        await self.checked(self.client.get(f"{BASE}/{doc['id']}"), 403)
        self.assertEqual(await self.physical(), 2)

    async def test_native_units_and_fractional_products_are_validated_before_save(self):
        doc = await self.create(); row = next(r for r in doc["lines"] if r["kind"] == "product")
        payload = {"request_id": uuid.uuid4().hex, "expected_revision": doc["revision"], "title": doc["title"],
            "lines": [{"id": row["id"], "unit": "gram", "counted_quantity": "1"}]}
        self.assertEqual((await self.checked(self.client.put(f"{BASE}/{doc['id']}", json=payload), 422))["detail"]["code"], "stocktake_native_unit_required")
        payload["lines"][0].update(unit="piece", counted_quantity="1.25")
        self.assertEqual((await self.checked(self.client.put(f"{BASE}/{doc['id']}", json=payload), 422))["detail"]["code"], "product_quantity_must_be_integral")

    async def test_capacity_and_closed_period_leave_no_partial_journal_or_stock(self):
        await self.db.warehouse_locations.update_one({"id": "location-product"}, {"$set": {"max_items": 2}})
        doc = await self.approved()
        self.assertEqual((await self.post(doc, 409))["detail"]["code"], "stocktake_capacity_exceeded")
        self.assertEqual(await self.db[GROUPS_COLLECTION].count_documents({}), self.initial_journals)
        await self.db.warehouse_locations.update_one({"id": "location-product"}, {"$set": {"max_items": 100}})
        doc = await self.approved()
        await self.db.mz2_accounting_periods.insert_one({"user_id": "owner", "month": datetime.now().strftime("%Y-%m"), "closed": True})
        self.assertEqual((await self.post(doc, 409))["detail"]["code"], "accounting_period_closed")
        self.assertEqual(await self.physical(), 2)
        self.assertEqual(await self.db[GROUPS_COLLECTION].count_documents({}), self.initial_journals)

    async def test_fault_after_journal_creation_aborts_all_documents(self):
        doc = await self.approved()
        from fastapi import HTTPException
        async def injected_failure(*args, **kwargs): raise HTTPException(409, "synthetic_failure_after_real_journal")
        with patch.object(count_service, "place_inventory_receipt", injected_failure): await self.post(doc, 409)
        self.assertEqual(await self.physical(), 2)
        self.assertEqual(await self.db[GROUPS_COLLECTION].count_documents({}), self.initial_journals)
        self.assertEqual(await self.db.mezan_inventory_receipts_v2.count_documents({"source_type": "inventory_adjustment"}), 0)
        self.assertEqual((await self.checked(self.client.get(f"{BASE}/{doc['id']}")))["status"], "approved")

    async def test_idempotent_create_save_and_conflicting_replay_fail_closed(self):
        payload = {"request_id": uuid.uuid4().hex, "title": "Exact request"}
        doc = await self.checked(self.client.post(BASE, json=payload))
        duplicate = await self.checked(self.client.post(BASE, json=payload))
        self.assertEqual(doc["id"], duplicate["id"])
        await self.checked(self.client.post(BASE, json={**payload, "title": "Changed request"}), 409)
        save = {"request_id": uuid.uuid4().hex, "expected_revision": doc["revision"], "title": doc["title"], "lines": []}
        saved = await self.checked(self.client.put(f"{BASE}/{doc['id']}", json=save))
        self.assertEqual((await self.checked(self.client.put(f"{BASE}/{doc['id']}", json=save)))["revision"], saved["revision"])
        await self.checked(self.client.put(f"{BASE}/{doc['id']}", json={**save, "request_id": uuid.uuid4().hex}), 409)

    async def test_other_tenant_cannot_read_a_known_count_id(self):
        doc = await self.create()
        await self.db.users.insert_one({"id": "other-tenant", "role": "owner", "is_active": True})
        self.actor.update(id="other-tenant")
        await self.checked(self.client.get(f"{BASE}/{doc['id']}"), 404)
        self.assertEqual((await self.checked(self.client.get(BASE)))["items"], [])

    async def test_stock_movement_report_uses_real_posted_events_not_draft_values(self):
        doc = await self.approved({"component": "3.5"})
        before = await self.checked(self.client.get(BASE + "/movements"))
        self.assertFalse(any(r["source_type"] == "inventory_adjustment" for r in before["items"]))
        await self.post(doc)
        after = await self.checked(self.client.get(BASE + "/movements"))
        changes = [r for r in after["items"] if r["source_type"] == "inventory_adjustment"]
        self.assertEqual(len(changes), 1); self.assertEqual(changes[0]["quantity"], "0.5")
        self.assertEqual(changes[0]["direction"], "out"); self.assertEqual(changes[0]["value"], "1.25")
        self.assertEqual(changes[0]["kind"], "component"); self.assertEqual(changes[0]["unit"], "gram")

    async def test_same_full_location_mixed_lot_differences_accept_final_capacity(self):
        payload = fixture.PurchaseApprovalMongoIntegration.payload(self)
        payload.update(invoice_number="SECOND-LOCAL-LOT-" + uuid.uuid4().hex,
                       lines=[payload["lines"][1]])
        invoice = await self.checked(self.client.post("/api/purchase-invoices", json=payload))
        await self.checked(self.client.post(f"/api/purchase-invoices/{invoice['id']}/approve-receive",
            json=fixture.PurchaseApprovalMongoIntegration.request_for(self, invoice)))
        await self.db.warehouse_locations.update_one({"id": "location-product"}, {"$set": {"max_items": 4}})
        doc = await self.create(location_ids=["location-product"])
        self.assertEqual(len(doc["lines"]), 2)
        updates = [{"id": r["id"], "unit": r["unit"], "counted_quantity": str(3 if i == 0 else 1), "reason": "Correct lot attribution"}
                   for i, r in enumerate(doc["lines"])]
        doc = await self.checked(self.client.put(f"{BASE}/{doc['id']}", json={"request_id": uuid.uuid4().hex,
            "expected_revision": doc["revision"], "title": doc["title"], "lines": updates}))
        doc = await self.action(await self.action(doc, "submit"), "approve")
        posted = await self.post(doc)
        self.assertEqual(posted["status"], "posted"); self.assertEqual(await self.physical(), 4)

    async def test_lost_committed_response_recovers_with_same_server_operation(self):
        doc = await self.approved()
        class LosePostResponse(httpx.AsyncBaseTransport):
            def __init__(self, app): self.inner = httpx.ASGITransport(app=app); self.lost = False
            async def handle_async_request(self, request):
                response = await self.inner.handle_async_request(request)
                if request.url.path.endswith('/post') and not self.lost:
                    self.lost = True; await response.aread(); await response.aclose()
                    raise httpx.ReadError('Synthetic response loss after commit', request=request)
                return response
            async def aclose(self): await self.inner.aclose()
        async with httpx.AsyncClient(transport=LosePostResponse(self.app), base_url='http://synthetic.local') as recovery:
            with self.assertRaises(httpx.ReadError): await self.post(doc, client=recovery)
            self.assertEqual((await self.checked(recovery.get(f'{BASE}/{doc["id"]}')))["status"], 'posted')
            self.assertEqual((await self.post(doc, client=recovery))["status"], 'posted')
        self.assertEqual(await self.physical(), 3)
        self.assertEqual(await self.db.mezan_inventory_receipts_v2.count_documents({'source_type':'inventory_adjustment'}), 1)

    async def test_real_managed_employee_page_mode_and_warehouse_scope(self):
        doc = await self.create()
        staff = {'id':'count-employee','role':'employee','created_by':'owner','is_active':True,
                 'accounting_permissions':['accounting.inventory.count'],
                 'web_page_access':{'owner_id':'owner','grants':[{'id':'accounting.inventory.view','mode':'read'}]}}
        await self.db.users.insert_one(staff)
        await self.db.mezan_role_assignments_v2.insert_one({'user_id':'count-employee','owner_user_id':'owner','warehouse_ids':['warehouse']})
        self.actor.clear(); self.actor.update(staff)
        visible = await self.checked(self.client.get(f'{BASE}/{doc["id"]}'))
        self.assertFalse(visible['capabilities']['count'])
        purchase_context=await self.checked(self.client.get('/api/purchase-invoices/catalog'))
        self.assertFalse(purchase_context['can_write']);self.assertFalse(purchase_context['can_pay'])
        payload = {'request_id':uuid.uuid4().hex,'expected_revision':doc['revision'],'title':doc['title'],'lines':[]}
        await self.checked(self.client.put(f'{BASE}/{doc["id"]}',json=payload),403)
        await self.db.users.update_one({'id':'count-employee'},{'$set':{'web_page_access.grants.0.mode':'write'}})
        saved = await self.checked(self.client.put(f'{BASE}/{doc["id"]}',json=payload))
        self.assertEqual(saved['audit'][-1]['actor_id'],'count-employee')
        self.assertFalse(saved['capabilities']['review']); self.assertFalse(saved['capabilities']['post'])
        await self.db.mezan_role_assignments_v2.update_one({'user_id':'count-employee'},{'$set':{'warehouse_ids':[]}})
        await self.checked(self.client.get(f'{BASE}/{doc["id"]}'),403)

    async def test_inactive_component_cannot_gain_physical_stock(self):
        doc = await self.approved({'component':'5'})
        await self.db.mezan_cost_resources_v2.update_one({'id':'component-A'},{'$set':{'status':'inactive'}})
        self.assertEqual((await self.post(doc,409))['detail']['code'],'stocktake_lot_unavailable')
        self.assertEqual(await self.physical('location-component'),4)

    async def test_partial_location_scope_exposes_metadata_and_only_snapshots_selected_bin(self):
        context=await self.checked(self.client.get(BASE+'/context'))
        locations={r['id']:r for r in context['locations']}
        self.assertEqual(set(locations),{'location-component','location-product'})
        self.assertNotIn('unit_cost',locations['location-component'])
        doc=await self.create(warehouse_ids=['warehouse'],location_ids=['location-component'])
        self.assertEqual(len(doc['lines']),1);self.assertEqual(doc['lines'][0]['kind'],'component')

    async def test_audit_preserves_saved_values_employee_identity_and_approved_snapshot(self):
        await self.db.users.update_one({'id':'owner'},{'$set':{'name':'موظف الجرد التجريبي'}})
        doc=await self.create();doc=await self.save(doc,{'product':'3'});doc=await self.save(doc,{'product':'4'})
        event=doc['audit'][-1];change=event['change_set']['lines'][0]
        self.assertEqual(event['actor_name'],'موظف الجرد التجريبي')
        self.assertEqual(change['before']['counted_quantity'],'3');self.assertEqual(change['after']['counted_quantity'],'4')
        self.assertEqual(doc['audit'][1]['change_set']['lines'][0]['before']['counted_quantity'],None)
        doc=await self.action(await self.action(doc,'submit'),'approve')
        approval=doc['audit'][-1]
        self.assertEqual(approval['snapshot'][0]['counted_quantity'],'4')
        self.assertEqual(approval['snapshot'][0]['difference_value'],'20.00')
        posted=await self.post(doc)
        receipt=await self.db.mezan_inventory_receipts_v2.find_one({'source_type':'inventory_adjustment'})
        self.assertEqual(receipt['authorization_hash'],approval['authorization_hash'])
        self.assertEqual(posted['audit'][-1]['txn_group_id'],posted['txn_group_id'])
