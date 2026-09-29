"""P01 preparation is operational; real posting remains separately guarded."""
import os
import unittest
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from accounting_financial_accounts import PERMISSIONS, install_financial_account_routes
from accounting_module_contract import EVIDENCE_SECTIONS
from accounting_settlement_routes import _find_bank
from accounting_write_control import AccountingDatabase, protect_accounting_routes

BASE = "/api/accounting-module/financial-accounts"
OPENING = BASE + "/opening-balances"


class OpeningPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ["MZ2_TEST_MONGO_URI"]
        if urlsplit(uri).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError("isolated loopback replica required")
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["p01_opening_prepare_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.owner = "synthetic-owner"
        self.actor = {"id": self.owner, "role": "owner", "is_active": True,
                      "accounting_permissions": [*PERMISSIONS.values(), "accounting.settlements.view", "accounting.rules.manage"]}
        await self.db.users.insert_one(dict(self.actor))
        await self.db.mz2_atomic_owners.insert_one({"_id": self.owner, "revision": 0, "writes_paused": True})
        await self.db.settings.insert_one({"user_id": self.owner, "shipping_companies": [{"name": "SMSA"}]})
        async def user(request: Request):
            return {"id": request.headers.get("X-Test-User", self.owner)}
        wrapped = AccountingDatabase(self.db)
        router = APIRouter()
        install_financial_account_routes(router, wrapped, user)
        protect_accounting_routes(router, wrapped)
        app = FastAPI()
        app.include_router(router, prefix="/api")
        self.client = AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://isolated.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.mongo.drop_database(self.db.name)
        self.mongo.close()

    async def bank(self, name="Synthetic bank"):
        response = await self.client.post(BASE, json={"name": name, "account_type": "bank", "currency": "SAR", "idempotency_key": uuid4().hex})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def evidence(self):
        sections = {}
        for section in EVIDENCE_SECTIONS:
            sid = section["id"]
            result = await self.client.post(OPENING + "/evidence", data={"purpose": "opening_balance", "section_id": sid},
                files={"file": ("synthetic.txt", ("synthetic-" + sid).encode(), "text/plain")})
            self.assertEqual(result.status_code, 200, result.text)
            sections[sid] = result.json()["source_file_id"]
        result = await self.client.post(OPENING + "/evidence", data={"purpose": "cutover"},
            files={"file": ("cutover.txt", b"synthetic-cutover", "text/plain")})
        self.assertEqual(result.status_code, 200, result.text)
        return sections, result.json()["source_file_id"]

    def payload(self, bank, evidence, *, extra=()):
        sections, cutover = evidence
        return {"idempotency_key": uuid4().hex, "cutover_at": "2026-09-30T00:00:00+03:00",
                "cutover_timezone": "Asia/Riyadh", "cutover_evidence_file_id": cutover,
                "section_evidence_file_ids": sections, "lines": [{
                    "category": "financial_account", "financial_account_id": bank["id"],
                    "meaning": "zero", "original_amount": "0", "evidence_file_id": sections["banks_cash"],
                }, *extra]}

    def line(self, category, entity, section, evidence, *, amount="10"):
        return {"category": category, "entity_id": entity,
                "meaning": "zero" if amount == "0" else "owed_by_us" if category.endswith("payable") else "available_to_us",
                "original_amount": amount, "evidence_file_id": evidence[0][section]}

    async def draft(self, payload):
        response = await self.client.post(OPENING + "/drafts", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def action(self, draft, action):
        return await self.client.post(OPENING + f"/drafts/{draft['id']}/{action}", json={
            "version": draft["version"], "idempotency_key": uuid4().hex, "note": "synthetic review",
        })

    async def assert_no_financial_effect(self):
        for collection in ("general_ledger", "accounting_general_ledger_v2", "accounting_journal_groups_v2", "liabilities"):
            self.assertEqual(await self.db[collection].count_documents({}), 0, collection)
        settings = await self.db.settings.find_one({"user_id": self.owner})
        self.assertNotIn("mezan2_financial_cutover", settings)

    async def test_paused_full_prepare_and_post_stays_blocked(self):
        bank, evidence = await self.bank(), await self.evidence()
        draft = await self.draft(self.payload(bank, evidence))
        preview = await self.action(draft, "preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        review = await self.action(preview.json(), "review")
        self.assertEqual(review.status_code, 200, review.text)
        self.assertEqual(review.json()["status"], "reviewed")
        result = await self.action(review.json(), "post")
        self.assertEqual(result.status_code, 423, result.text)
        self.assertEqual(result.json()["detail"]["code"], "mz2_writes_paused")
        await self.assert_no_financial_effect()
        row = await self.db.mz2_atomic_owners.find_one({"_id": self.owner})
        self.assertTrue(row["writes_paused"])
        self.assertNotIn("ledger_backend_state", row)

    async def test_metadata_updates_work_while_paused_and_missing_control_stays_fail_closed(self):
        await self.db.mz2_atomic_owners.delete_one({"_id": self.owner})
        bank = await self.bank()
        response = await self.client.patch(BASE + "/accounts/" + bank["id"], json={"version": 1, "name": "Updated bank"})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.request("DELETE", BASE + "/accounts/" + bank["id"], json={"version": 2, "reason": "Synthetic archive"})
        self.assertEqual(response.status_code, 200, response.text)
        row = await self.db.mz2_atomic_owners.find_one({"_id": self.owner})
        self.assertNotIn("writes_paused", row)
        self.assertNotIn("ledger_backend_state", row)
        await self.assert_no_financial_effect()

    async def test_context_has_real_tenant_scoped_separate_identities(self):
        await self.db.operating_salaries.insert_one({"user_id": self.owner, "id": "employee-1", "name": "Same human", "category": "employee"})
        await self.db.store_drivers.insert_one({"user_id": self.owner, "id": "driver-1", "name": "Same human", "status": "active"})
        await self.db.suppliers.insert_many([{"user_id": self.owner, "id": "supplier-1", "name": "Linked"}, {"user_id": self.owner, "id": "supplier-unlinked", "name": "Unlinked"}])
        await self.db.counterparties.insert_many([
            {"user_id": self.owner, "id": "supplier-1", "kind": "supplier", "name": "Linked"},
            {"user_id": self.owner, "id": "person-1", "kind": "general", "name": "External"},
            {"user_id": "other-owner", "id": "foreign-person", "kind": "general", "name": "Foreign"},
        ])
        response = await self.client.get(BASE + "/opening-context")
        self.assertEqual(response.status_code, 200, response.text)
        context = response.json()
        for category in ("employee_salary_payable", "employee_advance", "employee_custody"):
            self.assertEqual([r["id"] for r in context["entities"][category]], ["employee-1"])
        self.assertEqual([r["id"] for r in context["entities"]["store_driver_fee_payable"]], ["driver-1"])
        self.assertEqual([r["id"] for r in context["entities"]["supplier_payable"]], ["supplier-1"])
        self.assertEqual([r["id"] for r in context["entities"]["customer_receivable"]], ["person-1"])
        self.assertEqual({r["id"] for r in context["entities"]["provider_receivable"]}, {"salla", "tamara", "tabby", "emkan"})
        self.assertFalse(context["supplier_advance_supported"])
        await self.assert_no_financial_effect()

    async def test_unknown_or_foreign_entities_fail_closed(self):
        bank, evidence = await self.bank(), await self.evidence()
        for category, section in (("supplier_payable", "suppliers"), ("employee_custody", "payroll_obligations"),
                                  ("customer_receivable", "suppliers"), ("store_driver_cod_receivable", "couriers_cod")):
            with self.subTest(category=category):
                payload = self.payload(bank, evidence, extra=[self.line(category, "foreign-id", section, evidence)])
                response = await self.client.post(OPENING + "/drafts", json=payload)
                self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_financial_effect()

    async def bind(self, bank, evidence, provider="salla"):
        return await self.client.put(BASE + "/provider-bindings/" + provider, json={
            "bank_account_id": bank["id"], "evidence_ref": evidence[0]["providers"], "confirmed": True,
        })

    async def test_nonzero_provider_requires_canonical_verified_mapping(self):
        bank, evidence = await self.bank(), await self.evidence()
        line = self.line("provider_receivable", "salla", "providers", evidence)
        response = await self.client.post(OPENING + "/drafts", json=self.payload(bank, evidence, extra=[line]))
        self.assertEqual(response.status_code, 409, response.text)
        binding = await self.bind(bank, evidence)
        self.assertEqual(binding.status_code, 200, binding.text)
        self.assertTrue(binding.json()["configured"])
        self.assertFalse(binding.json()["needs_confirmation"])
        self.assertEqual((await self.bind(bank, evidence)).json()["revision"], binding.json()["revision"])
        draft = await self.draft(self.payload(bank, evidence, extra=[line]))
        self.assertEqual(draft["lines"][1]["entity_snapshot"]["provider_binding"]["bank_account_id"], bank["id"])
        self.assertEqual((await _find_bank(self.db, self.owner, bank["id"]))["id"], bank["id"])
        await self.assert_no_financial_effect()

    async def test_zero_provider_can_remain_explicitly_unbound(self):
        bank, evidence = await self.bank(), await self.evidence()
        draft = await self.draft(self.payload(bank, evidence, extra=[self.line(
            "provider_receivable", "tabby", "providers", evidence, amount="0")]))
        self.assertTrue(draft["zero_only"])
        self.assertEqual(len(draft["zero_accounts"]), 2)

    async def test_binding_identity_and_evidence_are_exact_and_permission_checked(self):
        bank, evidence = await self.bank(), await self.evidence()
        for provider, bank_id, evidence_id in (("mada", bank["id"], evidence[0]["providers"]),
            ("salla", "foreign-bank", evidence[0]["providers"]), ("salla", bank["id"], evidence[0]["banks_cash"])):
            response = await self.client.put(BASE + "/provider-bindings/" + provider,
                json={"bank_account_id": bank_id, "evidence_ref": evidence_id, "confirmed": True})
            self.assertIn(response.status_code, (409, 422), response.text)
        await self.db.users.insert_one({"id": "synthetic-employee", "role": "employee", "created_by": self.owner,
            "is_active": True, "accounting_permissions": [*PERMISSIONS.values(), "accounting.settlements.view"]})
        self.client.headers["X-Test-User"] = "synthetic-employee"
        self.assertEqual((await self.bind(bank, evidence)).status_code, 403)
        self.assertEqual(await self.db.accounting_provider_bank_bindings_v2.count_documents({}), 0)

    async def test_entity_snapshot_change_blocks_preview(self):
        bank, evidence = await self.bank(), await self.evidence()
        await self.db.counterparties.insert_one({"user_id": self.owner, "id": "person-1", "kind": "general", "name": "External"})
        draft = await self.draft(self.payload(bank, evidence, extra=[self.line("customer_receivable", "person-1", "suppliers", evidence)]))
        await self.db.counterparties.update_one({"user_id": self.owner, "id": "person-1"}, {"$set": {"status": "archived"}})
        response = await self.action(draft, "preview")
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_financial_effect()

    async def test_changed_binding_after_review_blocks_post_before_financial_effects(self):
        bank, evidence = await self.bank(), await self.evidence()
        self.assertEqual((await self.bind(bank, evidence)).status_code, 200)
        draft = await self.draft(self.payload(bank, evidence, extra=[self.line("provider_receivable", "salla", "providers", evidence)]))
        preview = await self.action(draft, "preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        review = await self.action(preview.json(), "review")
        self.assertEqual(review.status_code, 200, review.text)
        await self.db.accounting_provider_bank_bindings_v2.update_one({"user_id": self.owner, "provider": "salla"}, {"$inc": {"revision": 1}})
        await self.db.mz2_atomic_owners.update_one({"_id": self.owner}, {"$set": {"writes_paused": False,
            "ledger_backend_state": "v2_active", "ledger_backend_revision": 1, "ledger_backend_contract_revision": 1,
            "ledger_backend_activation_ref": "SYNTHETIC-ACTIVATION"}})
        response = await self.action(review.json(), "post")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "opening_entity_snapshot_changed")
        await self.assert_no_financial_effect()

    async def test_duplicate_bank_id_across_registries_is_not_guessed(self):
        bank = await self.bank()
        await self.db.accounts.insert_one({"user_id": self.owner, "id": bank["id"], "account_type": "bank", "name": bank["name"]})
        with self.assertRaises(HTTPException) as error:
            await _find_bank(self.db, self.owner, bank["id"])
        self.assertEqual(error.exception.detail["code"], "opening_bank_identity_ambiguous")

    async def test_bank_lookup_preserves_legacy_and_denies_foreign_or_archived_v2(self):
        legacy = {"user_id": self.owner, "id": "legacy-bank", "account_type": "bank", "name": "Existing bank"}
        await self.db.accounts.insert_one(dict(legacy))
        self.assertEqual((await _find_bank(self.db, self.owner, "legacy-bank"))["id"], "legacy-bank")
        bank = await self.bank()
        self.assertIsNone(await _find_bank(self.db, "other-owner", bank["id"]))
        self.assertIsNone(await _find_bank(self.db, "other-owner", "legacy-bank"))
        await self.db.mz2_financial_accounts.update_one({"user_id": self.owner, "id": bank["id"]}, {"$set": {"status": "archived"}})
        self.assertIsNone(await _find_bank(self.db, self.owner, bank["id"]))
        await self.assert_no_financial_effect()


if __name__ == "__main__":
    unittest.main()
