"""Self-statement contract against disposable Mongo and the real money writer."""
import asyncio
from copy import deepcopy
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

from employee_finance_notification_routes import make_employee_finance_notification_router
from mobile_app_permissions import EMPLOYEE_BALANCE_PERMISSION, MOBILE_APP_ACCESS, mobile_app_access_for_user
from mobile_app_request_context import mobile_app_request_user
from operational_balance_archive import SNAPSHOT, compact_financial_snapshot
from operational_balance_service import create_movement, save_opening
from operational_balance_store import STATES, mutate, read


class _Trace(CommandListener):
    def __init__(self): self.commands = []
    def started(self, event): self.commands.append((event.command_name, deepcopy(event.command)))
    def succeeded(self, event): pass
    def failed(self, event): pass


class EmployeeBalanceReadsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.trace = _Trace()
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[self.trace])
        self.db = self.mongo["self_balance_" + uuid4().hex]
        self.actor_id = "staff"
        await self.db.users.insert_many([
            {"id": "owner", "role": "owner", "name": "مسجل القيد", "is_active": True},
            {"id": "staff", "role": "employee", "created_by": "owner", "name": "موظف", "is_active": True},
            {"id": "peer", "role": "employee", "created_by": "owner", "name": "آخر", "is_active": True},
            {"id": "foreign", "role": "employee", "created_by": "foreign-owner", "is_active": True},
            {"id": "driver", "role": "store_driver", "created_by": "owner", "is_active": True},
        ])
        await self.db.mezan_employees_v2.insert_many([
            {"id": "self", "user_id": "owner", "account_user_id": "staff", "display_name": "موظف الاختبار", "status": "active"},
            {"id": "peer-employee", "user_id": "owner", "account_user_id": "peer", "display_name": "موظف آخر", "status": "active"},
            {"id": "foreign-employee", "user_id": "foreign-owner", "account_user_id": "foreign", "status": "active"},
        ])
        await self.db[MOBILE_APP_ACCESS].insert_many([
            {"owner_user_id": "foreign-owner" if actor == "foreign" else "owner", "user_id": actor,
             "enabled": True, "permissions": ["app.page.my_products"]} for actor in ("staff", "peer", "foreign", "driver")])
        await self.db.mz2_financial_accounts.insert_one({"id": "bank", "user_id": "owner",
            "name": "بنك الاختبار", "account_type": "bank", "currency": "SAR", "status": "active"})
        await self.db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": "owner", "name": "المورد", "status": "active"})
        for kind, identity, amount, direction in (
            ("bank", "bank", "10000", "for_us"), ("employee", "self", "2000", "for_party"),
            ("employee", "peer-employee", "9000", "for_party"),
            ("employee_custody", "self", "500", "for_us"), ("supplier", "supplier", "1000", "for_party"),
        ):
            await save_opening(self.db, "owner", "owner", {"request_id": "opening-"+kind+identity,
                "party_type": kind, "party_id": identity, "amount": amount, "currency": "SAR", "direction": direction},
                clock="2026-11-01T10:00:00+00:00")
        await save_opening(self.db, "owner", "owner", {"request_id": "start-test"}, finish=True,
            clock="2026-11-01T10:00:00+00:00")
        async def current(request: Request):
            actor = await self.db.users.find_one({"id": self.actor_id}, {"_id": 0})
            return await mobile_app_request_user(self.db, {**actor, "_session_client": "amasi_mobile"},
                path=request.url.path, method=request.method)
        app = FastAPI()
        app.include_router(make_employee_finance_notification_router(self.db, current), prefix="/api")
        self.client = AsyncClient(transport=ASGITransport(app), base_url="http://test")

    async def asyncTearDown(self):
        if hasattr(self, "client"): await self.client.aclose()
        if hasattr(self, "mongo"):
            await self.mongo.drop_database(self.db.name)
            self.mongo.close()

    async def movement(self, **changes):
        clock = changes.pop("clock", "2026-11-20T12:00:00+00:00")
        payload = {"request_id": "payment-"+uuid4().hex, "party_type": "employee", "party_id": "self",
            "bank_id": "bank", "amount": "100.00", "currency": "SAR", "direction": "outgoing",
            "kind": "payment", "note": "دفعة الموظف الخاصة", "receipt_id": None, "order_number": None,
            "reference": "مرجع-"+uuid4().hex, "allocations": [], **changes}
        return await create_movement(self.db, "owner", "owner", payload, clock=clock)

    async def page(self, **params):
        return await self.client.get("/api/employee-finance/me/statement", params=params)

    async def test_canonical_credit_debit_custody_and_detail_are_separate(self):
        await self.movement(amount="250", business_date="2026-11-10")
        await self.movement(amount="100", bank_id=None, kind="correction", workspace_reason="accrual",
            business_date="2026-11-11")
        await self.movement(party_type="employee_custody", amount="100", business_date="2026-11-12")
        await self.movement(party_type="employee_custody", amount="50", direction="incoming", kind="collection",
            business_date="2026-11-13")
        await self.movement(party_type="supplier", party_id="supplier", bank_id="self", amount="125", source_account_type="employee_custody",
            business_date="2026-11-14")
        self.trace.commands.clear()
        response = await self.page()
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()
        self.assertEqual(value["summary"], {"current_balance": "1850.00", "balance_side": "credit",
            "custody_amount": "425.00", "currency": "SAR"})
        self.assertEqual(value["employee"], {"id": "self", "name": "موظف الاختبار"})
        self.assertEqual(value["balance_status"], "available")
        self.assertEqual([row["balance_after"] for row in value["items"]], ["1850.00", "1750.00", "2000.00"])
        self.assertEqual([row["credit_amount"] for row in value["items"]], ["100.00", "0.00", "2000.00"])
        self.assertEqual([row["debit_amount"] for row in value["items"]], ["0.00", "250.00", "0.00"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertFalse(any(name in {"insert", "update", "delete", "findAndModify"} for name, _ in self.trace.commands))
        state_reads = [command for name, command in self.trace.commands if name == "find" and command["find"] == STATES]
        self.assertEqual(len(state_reads), 1)
        self.assertNotIn("audit", state_reads[0]["projection"])
        self.assertNotIn("requests", state_reads[0]["projection"])
        self.assertFalse(any(command.get("find") in {"mz2_financial_accounts", "mezan_suppliers_v2", "orders_v2",
            "employee_finance_notifications_v1"} for _, command in self.trace.commands))
        detail = await self.client.get("/api/employee-finance/me/movements/"+value["items"][1]["id"],
            params={"snapshot": value["snapshot"]})
        self.assertEqual(detail.status_code, 200, detail.text)
        entry = detail.json()["item"]
        self.assertEqual(entry["amount"], "250.00")
        self.assertEqual(entry["side"], "debit")
        self.assertEqual(entry["source_label"], "بنك الاختبار")
        self.assertEqual(entry["recorded_by"], {"id": "owner", "name": "مسجل القيد"})
        self.assertEqual(entry["business_date"], "2026-11-10")
        self.assertEqual(entry["recorded_at"], "2026-11-20T12:00:00+00:00")
        self.assertEqual(entry["description"], "دفعة الموظف الخاصة")

    async def test_decimal_cents_and_negative_current_balance(self):
        await self.movement(amount="2100.25")
        value = (await self.page()).json()
        self.assertEqual(value["summary"]["current_balance"], "-100.25")
        self.assertEqual(value["summary"]["balance_side"], "debit")
        self.assertEqual(value["items"][0]["balance_after"], "-100.25")
        self.assertEqual(value["items"][0]["balance_side_after"], "debit")

    async def test_ten_row_pages_snapshot_and_no_duplicates(self):
        for n in range(25):
            await self.movement(amount="1.01", business_date="2026-11-10")
        first = (await self.page()).json()
        self.assertEqual(first["pagination"], {"page": 1, "page_size": 10, "total": 26, "total_pages": 3})
        second = (await self.page(page=2, snapshot=first["snapshot"])).json()
        last = (await self.page(page=3, snapshot=first["snapshot"])).json()
        rows = first["items"] + second["items"] + last["items"]
        self.assertEqual(len({row["id"] for row in rows}), 26)
        self.assertEqual(rows[0]["balance_after"], "1974.75")
        self.assertEqual(rows[-1]["balance_after"], "2000.00")
        await self.movement(amount="1.00")
        stale = await self.page(page=2, snapshot=first["snapshot"])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "employee_balance_snapshot_changed")
        stale_detail = await self.client.get("/api/employee-finance/me/movements/"+first["items"][0]["id"],
            params={"snapshot": first["snapshot"]})
        self.assertEqual(stale_detail.status_code, 409)
        self.assertEqual((await self.page(page_size=100)).status_code, 422)
        self.assertEqual((await self.page(page=2)).status_code, 422)

    async def test_backdated_entries_sort_by_movement_date_not_registration_time(self):
        await self.movement(amount="100", business_date="2026-11-15", clock="2026-11-18T10:00:00+00:00")
        await self.movement(amount="50", business_date="2026-11-05", clock="2026-11-20T10:00:00+00:00")
        value = (await self.page()).json()
        self.assertEqual([row["business_date"] for row in value["items"]], ["2026-11-15", "2026-11-05", "2026-11-01"])
        self.assertEqual([row["balance_after"] for row in value["items"]], ["1850.00", "1950.00", "2000.00"])

    async def test_other_employee_tenant_and_owner_cannot_select_this_ledger(self):
        value = (await self.page(employee_id="peer-employee", merchant_id="foreign-owner")).json()
        self.assertEqual(value["employee"]["id"], "self")
        self.actor_id = "peer"
        peer = (await self.page()).json()
        self.assertEqual(peer["summary"]["current_balance"], "9000.00")
        wrong_entry = await self.client.get("/api/employee-finance/me/movements/"+value["items"][0]["id"],
            params={"snapshot": peer["snapshot"]})
        self.assertEqual(wrong_entry.status_code, 404)
        self.actor_id = "foreign"
        self.assertEqual((await self.page()).status_code, 409)
        self.actor_id = "owner"
        self.assertEqual((await self.page(employee_id="self")).status_code, 403)
        self.actor_id = "driver"
        self.assertEqual((await self.page()).status_code, 403)

    async def test_unlinked_ambiguous_inactive_and_revoked_are_not_zero(self):
        await self.db.mezan_employees_v2.update_one({"id": "self"}, {"$set": {"status": "inactive"}})
        self.assertEqual((await self.page()).status_code, 403)
        await self.db.mezan_employees_v2.update_one({"id": "self"}, {"$set": {"status": "active"}})
        await self.db.mezan_employees_v2.insert_one({"id": "ambiguous", "user_id": "owner", "account_user_id": "staff", "status": "active"})
        self.assertEqual((await self.page()).status_code, 403)
        await self.db.mezan_employees_v2.delete_one({"id": "ambiguous"})
        await self.db[MOBILE_APP_ACCESS].update_one({"user_id": "staff"}, {"$set": {"enabled": False}})
        self.assertEqual((await self.page()).status_code, 403)
        await self.db[MOBILE_APP_ACCESS].update_one({"user_id": "staff"}, {"$set": {"enabled": True}})
        await self.db.users.update_one({"id": "staff"}, {"$set": {"disabled": True}})
        self.assertEqual((await self.page()).status_code, 403)

    async def test_valid_empty_ledger_is_zero_and_inactive_ledger_is_unavailable(self):
        await self.db.mezan_employees_v2.update_one({"id": "self"}, {"$set": {"id": "new-self"}})
        value = (await self.page()).json()
        self.assertEqual(value["items"], [])
        self.assertEqual(value["summary"], {"current_balance": "0.00", "balance_side": "zero", "custody_amount": "0.00", "currency": "SAR"})
        await self.db[STATES].update_one({"_id": "owner"}, {"$set": {"status": "draft"}})
        response = await self.page()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "employee_balance_ledger_unavailable")

    async def test_corrupt_packet_fails_closed_and_packed_salary_ledger_matches(self):
        async def salary(state):
            identity = "salary:self:2026-11-15"
            state["engine"].setdefault("obligations", {})[identity] = {"id": identity, "party_type": "employee",
                "party_id": "self", "currency": "SAR", "kind": "salary", "direction": "payable",
                "confirmed": "25.00", "expected": "0.00", "business_date": "2026-11-15"}
            state["engine"].setdefault("salary_days", {})[identity] = {"amount": "25.00", "currency": "SAR"}
        await mutate(self.db, "owner", salary)
        unpacked = (await self.page()).json()
        state = await read(self.db, "owner")
        packed = compact_financial_snapshot(state, 12000000)
        await self.db[STATES].replace_one({"_id": "owner"}, packed)
        packed_value = (await self.page()).json()
        self.assertEqual(packed_value, unpacked)
        self.assertEqual(packed_value["summary"]["current_balance"], "2025.00")
        self.assertEqual(packed_value["items"][0]["kind_label"], "راتب تلقائي")
        await self.db[STATES].update_one({"_id": "owner"}, {"$set": {SNAPSHOT+".sha256": "0"*64}})
        response = await self.page()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "employee_balance_snapshot_invalid")

    async def test_unknown_legacy_date_never_invents_a_running_balance(self):
        saved = await self.movement(amount="100")
        await self.db[STATES].update_one({"_id": "owner", "movements.id": saved["id"]},
            {"$unset": {"movements.$.occurred_at": ""}})
        value = (await self.page()).json()
        self.assertEqual(value["summary"]["current_balance"], "1900.00")
        self.assertEqual(value["balance_status"], "unreconciled")
        self.assertTrue(value["balance_note"])
        self.assertTrue(all(row["balance_after"] is None for row in value["items"]))

    async def test_legacy_aware_registration_time_is_normalized_for_mobile(self):
        saved = await self.movement(amount="100", business_date="2026-11-10")
        await self.db[STATES].update_one({"_id": "owner", "movements.id": saved["id"]},
            {"$set": {"movements.$.occurred_at": "2026-11-20 15:00:00+03:00"}})
        value = (await self.page()).json()
        item = value["items"][0]
        self.assertEqual(item["recorded_at"], "2026-11-20T15:00:00+03:00")
        self.assertEqual(item["business_date"], "2026-11-10")
        self.assertEqual(item["balance_after"], "1900.00")
        response = await self.client.get("/api/employee-finance/me/movements/"+item["id"],
            params={"snapshot": value["snapshot"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["item"]["recorded_at"], item["recorded_at"])

    async def test_binding_changed_during_read_discards_prepared_financial_rows(self):
        import employee_balance_reads as reads
        original = reads.prepare_in_thread
        async def changed(function, *args, **kwargs):
            result = await original(function, *args, **kwargs)
            if function is reads._project:
                await self.db.mezan_employees_v2.update_one({"id": "self"}, {"$set": {"account_user_id": "other-user"}})
            return result
        with patch.object(reads, "prepare_in_thread", changed):
            response = await self.page()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("summary", response.json())

    async def test_deadline_error_and_mutation_route_denial(self):
        import employee_balance_reads as reads
        async def delayed(*args, **kwargs):
            await asyncio.sleep(1)
        with patch.object(reads, "employee_balance_read", delayed), patch.object(reads, "READ_TIMEOUT_SECONDS", .01):
            response = await self.page()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "employee_balance_read_timeout")
        self.assertEqual((await self.client.post("/api/employee-finance/me/statement", json={})).status_code, 405)

    async def test_capability_is_derived_without_granting_report_or_writes(self):
        user = await self.db.users.find_one({"id": "staff"})
        access = await mobile_app_access_for_user(self.db, user)
        self.assertEqual(set(access["permissions"]), {"app.page.my_products", EMPLOYEE_BALANCE_PERMISSION})
        await self.db[MOBILE_APP_ACCESS].update_one({"user_id": "staff"}, {"$set": {"permissions": [EMPLOYEE_BALANCE_PERMISSION]}})
        forged = await mobile_app_access_for_user(self.db, user)
        self.assertEqual(forged["permissions"], [])
        self.assertEqual((await self.page()).status_code, 403)
