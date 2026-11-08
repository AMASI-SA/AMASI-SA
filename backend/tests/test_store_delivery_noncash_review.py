"""Synthetic noncash review fixtures and guarded read-only/unit contracts."""
import asyncio
from copy import deepcopy
from hashlib import sha256
from io import BytesIO

import pytest
from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from openpyxl import Workbook

from accounting_onboarding_ssot import create_typed_fact, TypedFactCreate
from operational_balance_store import STATES, initial, digest
from operational_balance_service import report
import operational_driver_payment_review as service
from operational_driver_review_store import EVENTS

START = '2026-10-05T09:00:00+00:00'
DELIVERED = '2026-10-06T10:00:00+00:00'
NOW = '2026-10-07T12:00:00+00:00'


async def seed(db, method='card_terminal', suffix='1', *, setup=True, day='2026-10-06'):
    if setup:
        await db.users.update_one({'id': 'owner'}, {'$set': {'role': 'owner', 'is_active': True}}, upsert=True)
        await db.users.update_one({'id': 'accountant'}, {'$set': {'role': 'accountant', 'created_by': 'owner', 'is_active': True}}, upsert=True)
        await db.mz2_financial_accounts.update_one({'id': 'bank', 'user_id': 'owner'}, {'$set': {
            'account_type': 'bank', 'currency': 'SAR', 'status': 'active', 'name': 'Synthetic bank'}}, upsert=True)
        state = initial('owner')
        state.update(status='active', started_at=START, revision=1,
            openings={'opening-bank': {'id': 'opening-bank', 'party_type': 'bank', 'party_id': 'bank',
                      'name': 'Synthetic bank', 'currency': 'SAR', 'direction': 'for_us', 'amount': '1000.00'}},
            engine={'as_of': NOW, 'obligations': {'shipping:order-1': {'id': 'shipping:order-1', 'kind': 'shipping',
                'party_type': 'store_driver', 'party_id': 'driver', 'currency': 'SAR', 'direction': 'payable',
                'confirmed': '20.00', 'expected': '0.00', 'business_date': '2026-10-06'}}})
        await db[STATES].insert_one(state)
        await db.store_drivers.insert_one({'id': 'driver', 'user_id': 'owner', 'name': 'Synthetic driver', 'status': 'active'})
    pos = await create_typed_fact(db, 'owner', 'owner', TypedFactCreate(category='other_receivable',
        display_name='Synthetic POS', reference='pos-reference', amount='0.00', currency='SAR',
        cutover_date='2026-10-05', evidence='synthetic documented identity'))
    assignment, oid, number = 'assignment-' + suffix, 'order-' + suffix, suffix
    identity = {'user_id': 'owner', 'assignment_id': assignment, 'order_id': oid,
                'order_number': number, 'driver_id': 'driver'}
    await db.store_delivery_assignments.insert_one({**identity, 'id': assignment, 'active': True,
        'status': 'delivered', 'delivered_at': DELIVERED})
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': number, 'raw_by_source': {'salla_direct': {
        'id': oid, 'reference_id': number, 'created_at': '2026-10-01T10:00:00+00:00',
        'currency': 'SAR', 'total': {'amount': '300.00', 'currency': 'SAR'}, 'payment': {'method': 'cod'}}}})
    await db.store_delivery_collections.insert_one({**identity, 'id': 'collection-' + suffix,
        'amount': '200.00', 'amount_source': 'unified_orders.remaining_amount', 'payment_method': method,
        'cod_custody_amount': '0', 'collected_at': DELIVERED, 'receipt_reference': 'token-' + suffix,
        'bank_account_id': 'bank'})
    await db.store_delivery_payment_reviews.insert_one({**identity, 'id': 'review-' + suffix, 'amount': '200.00',
        'payment_method': method, 'receipt_reference': 'token-' + suffix, 'status': 'pending',
        'submitted_at': DELIVERED, 'bank_account_id': 'bank'})
    content = ('synthetic image ' + suffix).encode()
    await db.store_delivery_receipts.insert_one({**identity, 'token': 'token-' + suffix, 'content': content,
        'sha256': sha256(content).hexdigest(), 'status': 'bound', 'bound_at': DELIVERED})
    movement = None
    if method == 'bank_transfer':
        book = Workbook(); sheet = book.active
        sheet.append(['date', 'credit', 'debit', 'description', 'reference'])
        sheet.append([day, 200, 0, 'Customer transfer', 'BANK-REF-' + suffix])
        output = BytesIO(); book.save(output); content = output.getvalue()
        from accounting_daily_movements import parse_daily_movement_xlsx
        fact = parse_daily_movement_xlsx(content)['rows'][0]
        from accounting_advertising_contract import digest as bank_digest
        movement = 'bank-movement-' + suffix
        file_id, hashed = 'file-' + suffix, sha256(content).hexdigest()
        await db.mz2_daily_movement_files.insert_one({'id': file_id, 'user_id': 'owner', 'file_hash': hashed,
            'bank_account_id': 'bank', 'source': 'mz2_bank_statement'})
        await db.accounting_source_files.insert_one({'file_id': file_id, 'user_id': 'owner', 'sha256': hashed, 'content': content})
        await db.mz2_daily_movements.insert_one({'_id': bank_digest(['owner', 'bank', 'reference', fact['reference']]),
            'user_id': 'owner', 'id': movement, 'file_id': file_id, 'file_hash': hashed, 'row_no': fact['row_no'],
            'bank_account_id': 'bank', 'currency': 'SAR', 'direction': 'in', 'amount': '200.00', 'status': 'unclassified',
            'source': 'bank_statement_import', 'movement_date': fact['date'], 'reference': fact['reference']})
    return assignment, service.ReviewInput(decision='approved', note='Synthetic accountant reviewed receipt',
        destination_financial_id='bank' if movement else pos['id'], settlement_reference=movement,
        pos_reviewed_amount=None if movement else '200.00', expected_financial_source='operational',
        expected_review_revision=1, expected_session_scope=digest(['owner', 'owner']))


def run_mock(fn):
    async def scenario():
        return await fn(AsyncMongoMockClient()['synthetic_review'])
    return asyncio.run(scenario())


@pytest.mark.parametrize('method', ['bank_transfer', 'card_terminal'])
def test_options_are_scoped_read_only_and_keep_old_order_eligible(method):
    async def check(db):
        assignment, _ = await seed(db, method)
        before = await db[STATES].find_one({})
        result = await service.options(db, owner='owner', actor='owner', assignment_id=assignment, clock=NOW)
        assert result['financial_source'] == 'operational' and result['can_approve']
        assert result['amount'] == '200.00' and result['expected_review_revision'] == 1
        assert result['bank_movements'] == ([{'id': 'bank-movement-1', 'movement_date': '2026-10-06',
            'amount': '200.00', 'description': None, 'reference': 'BANK-REF-1', 'bank_account_id': 'bank'}]
            if method == 'bank_transfer' else [])
        assert await db[STATES].find_one({}) == before
        assert await db[EVENTS].count_documents({}) == 0
    run_mock(check)


@pytest.mark.parametrize('case', ['receipt_hash', 'receipt_missing', 'foreign_receipt', 'early_bound',
    'currency', 'collection_amount', 'collection_time', 'no_delivery_time', 'old_delivery',
    'not_delivered', 'foreign_order', 'inactive_assignment', 'native_journal', 'manual_payment'])
def test_invalid_sources_block_approval_and_never_create_money(case):
    async def check(db):
        assignment, _ = await seed(db)
        if case == 'receipt_missing': await db.store_delivery_receipts.delete_many({})
        elif case == 'receipt_hash': await db.store_delivery_receipts.update_one({}, {'$set': {'content': b'wrong'}})
        elif case == 'foreign_receipt': await db.store_delivery_receipts.update_one({}, {'$set': {'user_id': 'foreign'}})
        elif case == 'early_bound': await db.store_delivery_receipts.update_one({}, {'$set': {'bound_at': START}})
        elif case == 'currency': await db.store_delivery_collections.update_one({}, {'$set': {'currency': 'QAR'}})
        elif case == 'collection_amount': await db.store_delivery_collections.update_one({}, {'$set': {'amount': '199'}})
        elif case == 'collection_time': await db.store_delivery_collections.update_one({}, {'$set': {'collected_at': NOW}})
        elif case == 'no_delivery_time': await db.store_delivery_assignments.update_one({}, {'$unset': {'delivered_at': ''}})
        elif case == 'old_delivery': await db.store_delivery_assignments.update_one({}, {'$set': {'delivered_at': START}})
        elif case == 'not_delivered': await db.store_delivery_assignments.update_one({}, {'$set': {'status': 'assigned'}})
        elif case == 'inactive_assignment': await db.store_delivery_assignments.update_one({}, {'$set': {'active': False}})
        elif case == 'foreign_order': await db.unified_orders.update_one({}, {'$set': {'user_id': 'foreign'}})
        elif case == 'native_journal': await db.store_delivery_collections.update_one({}, {'$set': {'financial_txn_group_id': 'native'}})
        elif case == 'manual_payment': await db[STATES].update_one({}, {'$set': {'movements': [{'direction': 'incoming', 'order_number': '1'}]}})
        before = await db[STATES].find_one({})
        result = await service.options(db, owner='owner', actor='owner', assignment_id=assignment, clock=NOW)
        assert not result['can_approve'] and result['blocker']
        assert await db[STATES].find_one({}) == before
    run_mock(check)


@pytest.mark.parametrize('patch', [{'disabled': True}, {'is_active': False}, {'role': 'employee'},
                                 {'denied_permissions': ['store_delivery.payments.review']}])
def test_current_actor_not_browser_claims_authorizes_review(patch):
    async def check(db):
        assignment, _ = await seed(db)
        await db.users.update_one({'id': 'owner'}, {'$set': patch})
        with pytest.raises(HTTPException) as error:
            await service.options(db, owner='owner', actor='owner', assignment_id=assignment, clock=NOW)
        assert error.value.status_code == 403
    run_mock(check)


@pytest.mark.parametrize('case', ['date_cutover', 'old_date', 'future_date', 'wrong_bank', 'wrong_amount',
                                 'consumed', 'corrupt_bytes', 'row_reference', 'row_identity', 'foreign_file'])
def test_bank_requires_original_bytes_and_unambiguous_post_cutover_evidence(case):
    async def check(db):
        day = {'date_cutover': '2026-10-05', 'old_date': '2026-10-04', 'future_date': '2026-10-08'}.get(case, '2026-10-06')
        assignment, payload = await seed(db, 'bank_transfer', day=day)
        if case == 'wrong_bank': await db.mz2_daily_movements.update_one({}, {'$set': {'bank_account_id': 'wrong'}})
        if case == 'wrong_amount': await db.mz2_daily_movements.update_one({}, {'$set': {'amount': '199.99'}})
        if case == 'consumed': await db.mz2_daily_movements.update_one({}, {'$set': {'status': 'operational_consumed'}})
        if case == 'corrupt_bytes': await db.accounting_source_files.update_one({}, {'$set': {'content': b'changed'}})
        if case == 'row_reference': await db.mz2_daily_movements.update_one({}, {'$set': {'reference': 'changed'}})
        if case == 'row_identity': await db.mz2_daily_movements.update_one({}, {'$set': {'row_no': 999}})
        if case == 'foreign_file': await db.accounting_source_files.update_one({}, {'$set': {'user_id': 'foreign'}})
        state = await db[STATES].find_one({})
        with pytest.raises(HTTPException):
            await service._bank_proof(db, 'owner', 'bank', payload.settlement_reference, '200.00', state, NOW)
        choices = await service.options(db, owner='owner', actor='owner', assignment_id=assignment, clock=NOW)
        assert choices['bank_movements'] == []
        assert await db[STATES].find_one({}) == state
    run_mock(check)


@pytest.mark.parametrize('case', ['operational_claim', 'native_claim', 'manual_reference'])
def test_bank_options_exclude_claimed_evidence_without_consuming_candidates(case):
    async def check(db):
        assignment, payload = await seed(db, 'bank_transfer')
        identity = ['mz2_daily_movements', payload.settlement_reference]
        if case == 'operational_claim':
            await db[EVENTS].insert_one({'user_id': 'owner',
                '_id': digest(['owner', 'operational-driver-review-claim', 'reference', identity]), 'kind': 'claim'})
        elif case == 'native_claim':
            await db.mz2_shipping_native_events_v2.insert_one({'user_id': 'owner', 'destination': {
                'source_namespace': identity[0], 'source_record_id': identity[1]}})
        else:
            await db[STATES].update_one({}, {'$set': {'movements': [
                {'direction': 'incoming', 'reference': 'BANK-REF-1'}]}})
        before = await db[STATES].find_one({})
        movement = await db.mz2_daily_movements.find_one({})
        choices = await service.options(db, owner='owner', actor='owner', assignment_id=assignment, clock=NOW)
        assert choices['bank_movements'] == []
        assert await db[STATES].find_one({}) == before
        assert await db.mz2_daily_movements.find_one({}) == movement
    run_mock(check)


@pytest.mark.parametrize('value', [None, True, -1, 0, 'NaN', 'Infinity', '200.001', {}, []])
def test_invalid_money_never_becomes_zero(value):
    with pytest.raises(HTTPException): service.exact_money(value)


def test_scope_and_strict_revision_are_required_before_any_transaction():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        service.ReviewInput(decision='approved', expected_review_revision=True)
    async def check(db):
        assignment, payload = await seed(db)
        payload.expected_session_scope = 'wrong'
        with pytest.raises(HTTPException):
            await service.review_payment(db, owner='owner', actor='owner', assignment_id=assignment, payload=payload, clock=NOW)
        assert await db[EVENTS].count_documents({}) == 0
    run_mock(check)


@pytest.mark.parametrize('patch', [
    {'status': 'refunded'}, {'payment': {'status': 'refunded'}}, {'refund_amount': '10'},
    {'cancelled_at': NOW}, {'total_amount': '301.00'},
])
def test_contradictory_order_cannot_approve_non_cash_money(patch):
    async def check(db):
        assignment, _ = await seed(db)
        await db.unified_orders.update_one({}, {'$set': {'raw_by_source.salla_direct.' + key: value for key, value in patch.items()}})
        before = await db[STATES].find_one({})
        result = await service.options(db, owner='owner', actor='owner', assignment_id=assignment, clock=NOW)
        assert result['can_approve'] is False and result['blocker']
        assert await db[STATES].find_one({}) == before
    run_mock(check)


@pytest.mark.parametrize('case', ['native_ledger', 'unscoped_read', 'foreign_read', 'extra_write', 'native_control_write',
                                  'wrong_state_revision', 'page_scan', 'page_owner', 'page_operator', 'page_write'])
def test_capability_denial_is_latched_before_calling_database(case):
    from operational_balance_store import AUDIT_PAGES
    from operational_driver_review_store import ReviewDatabase
    async def check(db):
        scoped = ReviewDatabase(db, object(), 'owner')
        with pytest.raises(HTTPException):
            if case == 'native_ledger': scoped.general_ledger
            elif case == 'unscoped_read': scoped.store_delivery_collections.find({})
            elif case == 'foreign_read': scoped.store_delivery_collections.find({'user_id': 'other'})
            elif case == 'page_scan': scoped[AUDIT_PAGES].find({'owner_id': 'owner', '_id': 'page'})
            elif case == 'page_owner': await scoped[AUDIT_PAGES].find_one({'owner_id': 'other', '_id': 'page'})
            elif case == 'page_operator': await scoped[AUDIT_PAGES].find_one({'owner_id': 'owner', '_id': {'$ne': None}})
            elif case == 'page_write': await scoped[AUDIT_PAGES].insert_one({'owner_id': 'owner', '_id': 'page'})
            elif case == 'extra_write': await scoped.store_delivery_collections.update_one({'_id': 'x','user_id':'owner'}, {'$set':{'amount':'999','financial_source':'operational'}})
            elif case == 'native_control_write': await scoped.mz2_atomic_owners.update_one({'_id':'owner'}, {'$set':{'ledger_backend_state':'v2_active'}})
            else: await scoped[STATES].replace_one({'_id':'owner','revision':1},{'_id':'owner','owner_id':'owner','revision':7})
        assert scoped.failed
    run_mock(check)


@pytest.mark.parametrize('value', ['0.00', 0])
def test_zero_refund_fields_do_not_block_valid_receipt(value):
    async def check(db):
        assignment, _ = await seed(db)
        await db.unified_orders.update_one({}, {'$set': {'raw_by_source.salla_direct.refunded_amount': value}})
        assert (await service.options(db, owner='owner', actor='owner', assignment_id=assignment, clock=NOW))['can_approve']
    run_mock(check)
