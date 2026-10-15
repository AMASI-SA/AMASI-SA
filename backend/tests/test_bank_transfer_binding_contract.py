"""Explicit setup binding: real isolated Mongo, no live services."""
import os
from uuid import uuid4
import unittest
from fastapi import APIRouter, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from accounting_atomic import atomic_owner
from accounting_bank_transfer_bindings import (
    BankTransferBindingIn, UPSTREAM_SOURCE, save_bank_transfer_binding,
)
from accounting_bank_transfer_receipts import _resolve_order_bank, install_bank_transfer_receipt_routes


class BindingContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AsyncIOMotorClient(os.environ['MZ2_TEST_MONGO_URI'])
        self.db = self.client['tracka_binding_' + uuid4().hex]
        self.actor = {'id': 'owner', 'role': 'owner', 'is_active': True}
        await self.db.users.insert_one(self.actor.copy())
        await self.db.mz2_atomic_owners.insert_one({'_id': 'owner', 'revision': 0, 'writes_paused': False})
        await self.db.mz2_financial_accounts.insert_one({
            'id': 'bank', 'user_id': 'owner', 'account_type': 'bank', 'currency': 'SAR', 'status': 'active'})
        self.payload = BankTransferBindingIn(upstream_source=UPSTREAM_SOURCE, upstream_value='Bank label from Salla',
            financial_account_id='bank', confirmation='CONFIRM_MZ2_BANK_TRANSFER_BINDING', evidence_ref='synthetic-proof')

    async def asyncTearDown(self):
        await self.client.drop_database(self.db.name)
        self.client.close()

    async def save(self, payload=None):
        return await atomic_owner(self.db, 'owner', lambda scoped: save_bank_transfer_binding(
            scoped, 'owner', self.actor, payload or self.payload))

    async def test_explicit_exact_scoped_binding_and_audit_without_financial_delta(self):
        # Even a canonical-looking upstream ID is not an implicit binding.
        self.assertEqual((await _resolve_order_bank(self.db, 'owner', 'bank'))['code'], 'MZ2_LINK_REQUIRED')
        await self.save()
        self.assertEqual((await _resolve_order_bank(self.db, 'owner', self.payload.upstream_value))['bank_account_id'], 'bank')
        for owner, value in [('other', self.payload.upstream_value), ('owner', self.payload.upstream_value.lower()),
                             ('owner', self.payload.upstream_value + ' ')]:
            self.assertEqual((await _resolve_order_bank(self.db, owner, value))['code'], 'MZ2_LINK_REQUIRED')
        await self.save()
        doc = await self.db.mz2_bank_transfer_bindings.find_one({})
        self.assertEqual(doc['revision'], 2)
        self.assertEqual(len(doc['audit']), 2)
        self.assertEqual(doc['audit'][0]['actor_id'], 'owner')
        self.assertEqual(doc['audit'][1]['previous_financial_account_id'], 'bank')
        for name in ['general_ledger', 'mz2_ledger_entries', 'account_transactions']:
            self.assertEqual(await self.db[name].count_documents({}), 0)

    async def test_invalid_fk_and_revalidation_fail_closed(self):
        await self.save()
        for change in [{'status': 'inactive'}, {'currency': 'USD'}, {'account_type': 'cash'},
                       {'deleted': True}, {'is_active': False}, {'user_id': 'other'}]:
            await self.db.mz2_financial_accounts.update_one({'id': 'bank'}, {'$set': change})
            self.assertEqual((await _resolve_order_bank(self.db, 'owner', self.payload.upstream_value))['code'], 'MZ2_LINK_REQUIRED')
            with self.assertRaises(HTTPException): await self.save()
            await self.db.mz2_financial_accounts.update_one({'id': 'bank'}, {'$set': {
                'status': 'active', 'currency': 'SAR', 'account_type': 'bank', 'user_id': 'owner',
                'deleted': False, 'is_active': True}})
        await self.db.mz2_financial_accounts.delete_many({})
        await self.db.accounts.insert_one({'id': 'bank', 'user_id': 'owner', 'account_type': 'bank'})
        self.assertEqual((await _resolve_order_bank(self.db, 'owner', self.payload.upstream_value))['code'], 'MZ2_LINK_REQUIRED')
        with self.assertRaises(HTTPException): await self.save()
        self.assertEqual((await self.db.mz2_bank_transfer_bindings.find_one({}))['revision'], 1)

    async def test_route_pause_prevents_setup_mutation(self):
        router = APIRouter()
        install_bank_transfer_receipt_routes(router, self.db, lambda: self.actor)
        endpoint = next(r.endpoint for r in router.routes if r.name == 'bind_bank')
        await self.db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'writes_paused': True}})
        with self.assertRaises(HTTPException) as exc:
            await endpoint(self.payload, self.actor)
        self.assertEqual(exc.exception.status_code, 423)
        self.assertEqual(await self.db.mz2_bank_transfer_bindings.count_documents({}), 0)

    async def test_audit_and_binding_rollback_together(self):
        async def fail(scoped):
            await save_bank_transfer_binding(scoped, 'owner', self.actor, self.payload)
            raise ValueError('abort synthetic transaction')
        with self.assertRaises(ValueError): await atomic_owner(self.db, 'owner', fail)
        self.assertEqual(await self.db.mz2_bank_transfer_bindings.count_documents({}), 0)

    async def test_confirmation_source_and_permission_are_required(self):
        from pydantic import ValidationError
        for field, value in [('confirmation', ''), ('upstream_source', 'legacy.settings')]:
            with self.assertRaises(ValidationError):
                BankTransferBindingIn(**{**self.payload.model_dump(), field: value})
        employee = {'id': 'employee', 'role': 'employee', 'owner_id': 'owner', 'is_active': True}
        await self.db.users.insert_one(employee.copy())
        router = APIRouter()
        install_bank_transfer_receipt_routes(router, self.db, lambda: employee)
        endpoint = next(r.endpoint for r in router.routes if r.name == 'bind_bank')
        with self.assertRaises(HTTPException) as exc:
            await endpoint(self.payload, employee)
        self.assertEqual(exc.exception.status_code, 403)
        self.assertEqual(await self.db.mz2_bank_transfer_bindings.count_documents({}), 0)
