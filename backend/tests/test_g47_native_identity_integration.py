"""WP02: real Mongo proof that retained G47 uses only native financial IDs.

The existing local-only fixture supplies permissions, opening evidence and real
transactions. Command monitoring covers application requests, not fixture setup.
"""
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_g47_purchase_approval_integration as purchase
import test_g47_supplier_payment_integration as payment_fixture


class IdentityCommands(CommandListener):
    def __init__(self):
        self.enabled = False
        self.commands = []

    def started(self, event):
        if self.enabled:
            self.commands.append((event.command_name, dict(event.command)))

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


class G47NativeIdentityIntegration(unittest.IsolatedAsyncioTestCase):
    seed = purchase.PurchaseApprovalMongoIntegration.seed
    drop_disposable_database = purchase.PurchaseApprovalMongoIntegration.drop_disposable_database
    payload = purchase.PurchaseApprovalMongoIntegration.payload
    draft = purchase.PurchaseApprovalMongoIntegration.draft
    request_for = purchase.PurchaseApprovalMongoIntegration.request_for
    approve = purchase.PurchaseApprovalMongoIntegration.approve
    seed_journal = payment_fixture.SupplierPaymentMongoIntegration.seed_journal
    approved = payment_fixture.SupplierPaymentMongoIntegration.approved
    context = payment_fixture.SupplierPaymentMongoIntegration.context
    payment = payment_fixture.SupplierPaymentMongoIntegration.payment
    assert_balances = payment_fixture.SupplierPaymentMongoIntegration.assert_balances
    assert_legacy_empty = payment_fixture.SupplierPaymentMongoIntegration.assert_legacy_empty

    async def asyncSetUp(self):
        self.commands = IdentityCommands()

        def client(*args, **kwargs):
            return AsyncIOMotorClient(*args, event_listeners=[self.commands], **kwargs)

        with patch.object(purchase, "AsyncIOMotorClient", client):
            await payment_fixture.SupplierPaymentMongoIntegration.asyncSetUp(self)
        # No historical supplier/counterparty is needed for a new native owner.
        await self.db.suppliers.delete_many({})
        await self.db.counterparties.delete_many({})

    def assert_native_commands(self):
        denied = {"suppliers", "counterparties", "accounts", "general_ledger",
                  "account_transactions", "financial_movements"}
        seen = []
        for name, command in self.commands.commands:
            collection = command.get(name)
            if isinstance(collection, str) and collection in denied:
                seen.append((name, collection))
            if name == "aggregate":
                for stage in command.get("pipeline", []):
                    for operator in ("$lookup", "$graphLookup"):
                        if stage.get(operator, {}).get("from") in denied:
                            seen.append((operator, stage[operator]["from"]))
        self.assertTrue(self.commands.commands, "Application requests must execute")
        self.assertEqual(seen, [], "No Legacy identity, balance or financial write commands")

    async def test_native_only_purchase_payment_statement_and_exact_replay(self):
        self.commands.enabled = True
        catalog = await self.client.get("/api/purchase-invoices/catalog")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        identities = catalog.json()["supplier_identities"]
        self.assertEqual(len(identities), 1)
        self.assertEqual(identities[0]["source"], "mezan_suppliers_v2")
        self.assertEqual((identities[0]["id"], identities[0]["entity_id"], identities[0]["counterparty_id"]),
                         ("supplier", "supplier", "supplier"))
        invoice = await self.approved()
        base, context = await self.context(invoice)
        self.assertEqual([row["id"] for row in context["banks"]], ["bank"])
        request = self.payment(context, "30.00")
        first = await self.client.post(base + "/payments", json=request)
        replay = await self.client.post(base + "/payments", json=request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(replay.json(), first.json())
        statement = await self.client.get("/api/purchase-invoices/supplier/supplier/statement")
        ledger = await self.client.get("/api/accounting/suppliers/supplier/ledger-detail")
        self.assertEqual(statement.status_code, 200, statement.text)
        self.assertEqual(ledger.status_code, 200, ledger.text)
        self.assertEqual(statement.json()["totals"]["balance_owed"], "0.00")
        self.commands.enabled = False
        self.assert_native_commands()
        await self.assert_balances(0, 7000)
        self.assertEqual(await self.db[payment_fixture.OPERATIONS].count_documents({"status": "succeeded"}), 1)

    async def test_legacy_only_supplier_is_rejected_without_reading_legacy(self):
        await self.db.mezan_suppliers_v2.delete_many({})
        for collection in ("suppliers", "counterparties"):
            await self.db[collection].insert_one({"id": "supplier", "user_id": "owner",
                                                  "kind": "supplier", "status": "active"})
        self.commands.enabled = True
        response = await self.client.post("/api/purchase-invoices", json=self.payload())
        self.commands.enabled = False
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "supplier_v2_identity_required")
        self.assert_native_commands()
        self.assertEqual(await self.db.purchase_invoices.count_documents({}), 0)

    async def test_invalid_native_bank_never_uses_shadow_legacy_account_or_guessed_currency(self):
        invoice = await self.approved()
        base, context = await self.context(invoice)
        await self.db.accounts.insert_one({"id": "bank", "user_id": "owner", "account_type": "bank",
                                           "currency": "SAR", "status": "active", "balance": 999999})
        valid = await self.db.mz2_financial_accounts.find_one({"id": "bank"})
        mutations = [{"currency": None}, {"currency": "USD"}, {"status": None},
                     {"status": "inactive"}, {"archived": True}, {"deleted": True},
                     {"is_active": False}, {"active": False}, {"account_type": "asset"},
                     {"user_id": "foreign"}]
        for changes in mutations + [None]:
            with self.subTest(changes=changes):
                await self.db.mz2_financial_accounts.delete_many({})
                if changes is not None:
                    await self.db.mz2_financial_accounts.insert_one({**valid, **changes})
                self.commands.enabled = True
                response = await self.client.post(base + "/payments", json=self.payment(context))
                self.commands.enabled = False
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "supplier_payment_bank_unavailable")
        self.assert_native_commands()
        self.assertEqual(await self.db[payment_fixture.OPERATIONS].count_documents({"status": "succeeded"}), 0)
        self.assertEqual(await self.db.accounting_journal_groups_v2.count_documents({}), 2)

    async def test_native_cash_keeps_bank_main_identity_without_legacy_lookup(self):
        await self.db.mz2_financial_accounts.update_one({"id": "bank"}, {"$set": {"account_type": "cash"}})
        self.commands.enabled = True
        invoice = await self.approved()
        base, context = await self.context(invoice)
        request = self.payment(context, "30.00")
        first = await self.client.post(base + "/payments", json=request)
        replay = await self.client.post(base + "/payments", json=request)
        self.commands.enabled = False
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), replay.json())
        self.assert_native_commands()
        await self.assert_balances(0, 7000)
        legs = await self.db.accounting_general_ledger_v2.find({"entry_type": "supplier_payment",
                                                              "side": "credit"}).to_list(10)
        self.assertEqual(len(legs), 1)
        self.assertEqual((legs[0]["entity_type"], legs[0]["entity_id"], legs[0]["sub_account"]),
                         ("bank", "bank", "main"))

    async def test_missing_currency_account_is_not_selectable(self):
        invoice = await self.approved()
        await self.db.mz2_financial_accounts.update_one({"id": "bank"}, {"$unset": {"currency": ""}})
        self.commands.enabled = True
        _, context = await self.context(invoice)
        self.commands.enabled = False
        self.assertEqual(context["banks"], [])
        self.assert_native_commands()

    async def test_native_supplier_foreign_or_inactive_is_rejected_at_draft(self):
        for changes in ({"status": "inactive"}, {"archived": True}, {"user_id": "foreign"}):
            with self.subTest(changes=changes):
                await self.db.mezan_suppliers_v2.delete_many({})
                await self.db.mezan_suppliers_v2.insert_one({"id": "supplier", "user_id": "owner",
                                                            "status": "active", **changes})
                self.commands.enabled = True
                response = await self.client.post("/api/purchase-invoices", json=self.payload())
                self.commands.enabled = False
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "supplier_v2_identity_required")
        self.assert_native_commands()
