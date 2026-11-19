"""Scoped workspace receipt queue and canonical-writer authorization contract."""
from hashlib import sha256

import pytest

from test_operational_balance_workspace import BASE, grant, resource, run


async def seed(db):
    await db.store_drivers.insert_many([
        {'id': 'driver', 'user_id': 'owner', 'name': 'الموصل التجريبي'},
        {'id': 'hidden-driver', 'user_id': 'owner', 'name': 'موصل غير مسموح'},
    ])
    for number in range(8):
        assignment = 'assignment-' + str(number)
        await db.store_delivery_assignments.insert_one({'user_id': 'owner', 'id': assignment,
            'driver_id': 'driver', 'order_number': str(1000 + number), 'customer_name_snapshot': 'عميل تجريبي',
            'status': 'delivered'})
        await db.store_delivery_payment_reviews.insert_one({'user_id': 'owner', 'assignment_id': assignment,
            'driver_id': 'driver', 'status': 'approved' if number == 7 else 'pending',
            'payment_method': 'bank_transfer', 'amount': '100.00', 'currency': 'SAR',
            'bank_account_id': 'bank', 'receipt_reference': 'receipt-' + str(number),
            'submitted_at': f'2026-11-{number + 10:02}T12:00:00+00:00'})
    await db.store_delivery_payment_reviews.insert_many([
        {'user_id': 'owner', 'assignment_id': 'hidden', 'driver_id': 'hidden-driver', 'status': 'pending',
         'amount': '900.00', 'bank_account_id': 'bank'},
        {'user_id': 'other', 'assignment_id': 'foreign', 'driver_id': 'driver', 'status': 'pending',
         'amount': '999.00', 'bank_account_id': 'bank'},
    ])


async def reviewer(db, *, movements='all', use=True, operations=None, version=1):
    await grant(db, operations=['courier.review'] if operations is None else operations, resources=[
        resource('store_driver', 'driver', use=False, movements=movements, balance=False),
        resource('bank', 'bank', use=use, movements='all', balance=False),
    ], version=version)


STAFF = {'x-test-user': 'staff'}


def test_queue_is_paged_scoped_and_status_history_is_separate():
    async def scenario(db, http):
        await seed(db)
        await reviewer(db)
        first = await http.get(BASE + '/reviews', headers=STAFF)
        assert first.status_code == 200, first.text
        page = first.json()
        assert (page['total'], page['total_pages'], len(page['items'])) == (7, 2, 5)
        assert {row['driver_id'] for row in page['items']} == {'driver'}
        assert all(row['customer_name'] == 'عميل تجريبي' for row in page['items'])
        assert all('user_id' not in row and 'receipt_url' not in row for row in page['items'])
        second = (await http.get(BASE + '/reviews', params={'page': 2}, headers=STAFF)).json()
        assert len(second['items']) == 2
        assert {r['assignment_id'] for r in page['items']}.isdisjoint(r['assignment_id'] for r in second['items'])
        history = (await http.get(BASE + '/reviews', params={'status': 'approved'}, headers=STAFF)).json()
        assert history['total'] == 1 and history['items'][0]['assignment_id'] == 'assignment-7'
        assert (await http.get(BASE + '/reviews', params={'driver_id': 'hidden-driver'}, headers=STAFF)).status_code == 404
        assert (await http.get(BASE + '/reviews/foreign', headers=STAFF)).status_code == 404
        assert (await http.get(BASE + '/reviews/hidden', headers=STAFF)).status_code == 404
    run(scenario)


def test_viewing_or_receiving_money_does_not_grant_review_and_own_does_not_expand():
    async def scenario(db, http):
        await seed(db)
        await reviewer(db, operations=['courier.receive'])
        assert (await http.get(BASE + '/reviews', headers=STAFF)).status_code == 403
        await reviewer(db, movements='own', version=2)
        result = await http.get(BASE + '/reviews', headers=STAFF)
        assert result.status_code == 200 and result.json()['total'] == 0
        assert (await http.get(BASE + '/reviews/assignment-0', headers=STAFF)).status_code == 404
        await reviewer(db, version=3)
        await db.users.update_one({'id': 'staff'}, {'$set': {'denied_permissions': ['store_delivery.payments.review']}})
        assert (await http.get(BASE + '/reviews', headers=STAFF)).status_code == 403
    run(scenario)


def test_receipt_bytes_are_bound_to_authorized_review_and_hash():
    async def scenario(db, http):
        await seed(db)
        await reviewer(db)
        content = b'synthetic-image-fixture'
        await db.store_delivery_receipts.insert_one({'user_id': 'owner', 'driver_id': 'driver',
            'assignment_id': 'assignment-0', 'token': 'receipt-0', 'status': 'bound',
            'content': content, 'sha256': sha256(content).hexdigest(), 'content_type': 'image/png'})
        response = await http.get(BASE + '/reviews/assignment-0/receipt', headers=STAFF)
        assert response.status_code == 200 and response.content == content
        assert response.headers['cache-control'] == 'no-store'
        await db.store_delivery_receipts.update_one({'token': 'receipt-0'}, {'$set': {'content': b'changed'}})
        assert (await http.get(BASE + '/reviews/assignment-0/receipt', headers=STAFF)).status_code == 404
        await reviewer(db, movements='none', version=2)
        assert (await http.get(BASE + '/reviews/assignment-0/receipt', headers=STAFF)).status_code == 404
    run(scenario)


def test_approval_reuses_existing_writer_and_rechecks_destination_before_any_dispatch(monkeypatch):
    import operational_driver_payment_review as service
    calls = []
    async def writer(db, *, owner, actor, assignment_id, payload, authorize_review):
        await authorize_review(db)
        calls.append((owner, actor, assignment_id, payload.decision))
        return {'decision': payload.decision, 'financial_source': 'operational'}
    monkeypatch.setattr(service, 'review_payment', writer)
    async def scenario(db, http):
        await seed(db)
        await reviewer(db, use=False)
        payload = {'decision': 'approved', 'note': 'تمت المطابقة', 'destination_financial_id': 'bank',
            'settlement_reference': 'imported-1', 'expected_financial_source': 'operational',
            'expected_review_revision': 1, 'expected_session_scope': 'scope'}
        denied = await http.post(BASE + '/reviews/assignment-0', json=payload, headers=STAFF)
        assert denied.status_code == 403 and not calls
        await reviewer(db, use=True, version=2)
        response = await http.post(BASE + '/reviews/assignment-0', json=payload, headers=STAFF)
        assert response.status_code == 200, response.text
        assert calls == [('owner', 'staff', 'assignment-0', 'approved')]
        assert (await http.post(BASE + '/reviews/assignment-0', json={**payload,
            'expected_financial_source': 'native'}, headers=STAFF)).status_code == 409
        assert len(calls) == 1
    run(scenario)


def test_authorization_callback_observes_revoked_grant_before_transaction_work(monkeypatch):
    import operational_driver_payment_review as service
    reached_money = []
    async def writer(db, *, authorize_review, **kwargs):
        await authorize_review(db)
        await reviewer(db, operations=[], version=2)
        await authorize_review(db)
        reached_money.append(True)
    monkeypatch.setattr(service, 'review_payment', writer)
    async def scenario(db, http):
        await seed(db)
        await reviewer(db)
        response = await http.post(BASE + '/reviews/assignment-0', headers=STAFF, json={
            'decision': 'rejected', 'note': 'صورة غير واضحة', 'expected_financial_source': 'operational',
            'expected_review_revision': 1, 'expected_session_scope': 'scope'})
        assert response.status_code == 403 and not reached_money
        assert (await db.store_delivery_payment_reviews.find_one({'assignment_id': 'assignment-0'}))['status'] == 'pending'
    run(scenario)


def test_bank_candidates_are_exact_paged_scoped_and_read_only():
    async def scenario(db, http):
        await seed(db)
        await reviewer(db)
        normal = {'user_id': 'owner', 'bank_account_id': 'bank', 'amount': '100.00', 'currency': 'SAR',
            'status': 'unclassified', 'source': 'bank_statement_import', 'direction': 'in',
            'movement_date': '2026-11-10', 'description': 'تحويل عميل', 'reference': 'reference'}
        await db.mz2_daily_movements.insert_many([{**normal, 'id': f'eligible-{n}'} for n in range(7)])
        for key, patch in {
            'wrong-bank': {'bank_account_id': 'foreign'}, 'wrong-amount': {'amount': '101.00'},
            'foreign-owner': {'user_id': 'other'}, 'consumed': {'operational_event_id': 'used'},
            'outgoing': {'direction': 'out'}, 'provider': {'suggested_provider': 'secret-provider'},
            'old': {'movement_date': '2026-11-01'}, 'unproved': {'source': 'manual'},
        }.items():
            await db.mz2_daily_movements.insert_one({**normal, **patch, 'id': key})
        before = await db.mz2_daily_movements.find({}).to_list(None)
        url = BASE + '/reviews/assignment-0/bank-movements'
        first = await http.get(url, headers=STAFF)
        assert first.status_code == 200, first.text
        page = first.json()
        assert (page['total'], page['total_pages'], len(page['items'])) == (7, 2, 5)
        assert all(r['id'].startswith('eligible-') and '_id' not in r and 'user_id' not in r for r in page['items'])
        second = (await http.get(url, params={'page': 2}, headers=STAFF)).json()
        assert len(second['items']) == 2
        assert {r['id'] for r in page['items']}.isdisjoint(r['id'] for r in second['items'])
        assert await db.mz2_daily_movements.find({}).to_list(None) == before
        assert (await http.get(BASE + '/reviews/foreign/bank-movements', headers=STAFF)).status_code == 404
        await reviewer(db, use=False, version=2)
        assert (await http.get(url, headers=STAFF)).status_code == 403
        await grant(db, operations=['courier.review'], resources=[
            resource('store_driver', 'driver', movements='all'), resource('bank', 'bank', movements='own')], version=3)
        assert (await http.get(url, headers=STAFF)).status_code == 403
    run(scenario)


@pytest.mark.parametrize('review_amount,legacy_amount,expected_total', [
    ('9007199254740993.25', 9007199254740994.0, 1),
    ('100.25', 100.25, 7),
    ('100.00', 100, 7),
])
def test_bank_candidate_numeric_compatibility_preserves_exact_amount_before_counting_and_paging(
        review_amount, legacy_amount, expected_total):
    async def scenario(db, http):
        await seed(db)
        await reviewer(db)
        await db.store_delivery_payment_reviews.update_one({'assignment_id': 'assignment-0'},
            {'$set': {'amount': review_amount}})
        normal = {'user_id': 'owner', 'bank_account_id': 'bank', 'currency': 'SAR',
            'status': 'unclassified', 'source': 'bank_statement_import', 'direction': 'in',
            'movement_date': '2026-11-10'}
        await db.mz2_daily_movements.insert_many([
            {**normal, 'id': 'exact-string', 'amount': review_amount},
            *[{**normal, 'id': f'numeric-{n}', 'amount': legacy_amount} for n in range(6)],
        ])
        url = BASE + '/reviews/assignment-0/bank-movements'
        response = await http.get(url, headers=STAFF)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first['total'] == expected_total
        assert (first['total_pages'], len(first['items'])) == ((1, 1) if expected_total == 1 else (2, 5))
        if expected_total == 1:
            assert first['items'][0]['id'] == 'exact-string'
        else:
            second = (await http.get(url, params={'page': 2}, headers=STAFF)).json()
            assert second['total'] == expected_total and len(second['items']) == 2
            assert {row['id'] for row in first['items'] + second['items']} == {
                'exact-string', *{f'numeric-{n}' for n in range(6)}}
    run(scenario)


def test_review_audit_name_is_owner_scoped_and_bank_selector_requires_full_bank_scope(monkeypatch):
    import operational_driver_payment_review as service
    async def options(*args, **kwargs):
        return {'bank_account_id': 'bank', 'pos_receivables': []}
    monkeypatch.setattr(service, 'options', options)
    async def scenario(db, http):
        await seed(db)
        await reviewer(db)
        await db.store_delivery_payment_reviews.update_one({'assignment_id': 'assignment-7'}, {'$set': {
            'reviewed_by': 'staff', 'reviewed_at': '2026-11-18T12:00:00Z'}})
        detail = (await http.get(BASE + '/reviews/assignment-7', headers=STAFF)).json()
        assert detail['reviewed_by_name'] == 'خالد أحمد' and detail['courier_balance_effect'] == '0.00'
        await db.store_delivery_payment_reviews.update_one({'assignment_id': 'assignment-7'}, {'$set': {'reviewed_by': 'other'}})
        assert (await http.get(BASE + '/reviews/assignment-7', headers=STAFF)).json()['reviewed_by_name'] is None
        available = (await http.get(BASE + '/reviews/assignment-0/options', headers=STAFF)).json()
        assert available['banks'] == [{'id': 'bank', 'name': 'الإنماء'}]
        await grant(db, operations=['courier.review'], resources=[
            resource('store_driver', 'driver', movements='all'), resource('bank', 'bank', movements='none')], version=2)
        hidden = (await http.get(BASE + '/reviews/assignment-0/options', headers=STAFF)).json()
        assert hidden['banks'] == [] and not hidden['can_select_bank_movement']
    run(scenario)


def scoped_options_case(scenario, *, role='employee', use=True, movements='all', operational=False):
    """Actual HTTP policies and candidate readers over disposable in-memory data."""
    import asyncio
    from uuid import uuid4
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from mongomock_motor import AsyncMongoMockClient
    from operational_balance_routes import make_operational_balance_router
    from store_delivery_payment_review_routes import make_store_delivery_payment_review_router
    from .test_store_delivery_noncash_review import seed as complete_review
    from .test_operational_driver_bank_statements import prepare_review, BANK

    async def execute():
        client = AsyncMongoMockClient()
        db = client['scoped_bank_options_' + uuid4().hex]
        if operational:
            assignment = await prepare_review(db)
            bank = BANK
        else:
            assignment, _ = await complete_review(db, 'bank_transfer')
            bank = 'bank'
        policy = {'owner_id': 'owner', 'version': 1, 'enabled': True, 'operations': ['courier.review'],
            'resources': [resource('store_driver', 'driver', use=False, movements='all'),
                          resource('bank', bank, use=use, movements=movements, balance=True)]}
        await db.users.insert_one({'id': 'staff', 'role': role, 'created_by': 'owner', 'is_active': True,
                                   'operational_balance_access': policy})
        await db.store_delivery_receipts.update_one({'assignment_id': assignment},
                                                    {'$set': {'content_type': 'image/png'}})
        async def principal(): return await db.users.find_one({'id': 'staff'}, {'_id': 0})
        app = FastAPI()
        app.include_router(make_operational_balance_router(db, principal), prefix='/api')
        app.include_router(make_store_delivery_payment_review_router(db, principal))
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://synthetic') as http:
                await scenario(db, http, assignment, bank)
        finally:
            client.close()
    asyncio.run(execute())


async def operational_candidate(db, bank, identity='operational-bank-movement:synthetic', day='2026-10-06', **changes):
    from operational_driver_bank_store import MOVEMENTS, SOURCE
    from operational_driver_bank_statements import seal
    from .test_store_delivery_noncash_review import START
    row = {'_id': identity, 'id': identity, 'user_id': 'owner', 'bank_account_id': bank,
        'source': SOURCE, 'financial_source': 'operational', 'started_at': START,
        'file_id': 'synthetic-file', 'file_hash': 'a' * 64, 'row_no': 2,
        'movement_date': day, 'amount': '200.00', 'currency': 'SAR', 'direction': 'in',
        'reference': identity, 'description': 'Synthetic incoming payment', 'status': 'unclassified', **changes}
    row['seal'] = seal(row)
    await db[MOVEMENTS].insert_one(row)
    return identity


@pytest.mark.parametrize('role', ['employee', 'accountant'])
@pytest.mark.parametrize('use,movements', [(False, 'all'), (True, 'none'), (False, 'none')])
def test_scoped_receipt_reader_cannot_obtain_bank_rows_through_options(role, use, movements):
    async def scenario(db, http, assignment, bank):
        await operational_candidate(db, bank)
        path = BASE + '/reviews/' + assignment
        response = await http.get(path + '/options')
        assert response.status_code == 200, response.text
        assert response.json()['bank_movements'] == []
        assert response.json()['banks'] == []
        assert not response.json()['can_select_bank_movement']
        assert (await http.get(path + '/receipt')).status_code == 200
        assert (await http.get(path + '/bank-movements')).status_code == 403
        assert (await db.store_delivery_payment_reviews.find_one({'assignment_id': assignment}))['status'] == 'pending'
    scoped_options_case(scenario, role=role, use=use, movements=movements)


@pytest.mark.parametrize('operational', [False, True])
def test_full_scoped_reviewer_reads_actual_bank_candidates_without_import_permission(operational):
    async def scenario(db, http, assignment, bank):
        identity = await operational_candidate(db, bank)
        path = BASE + '/reviews/' + assignment
        response = await http.get(path + '/options')
        assert response.status_code == 200, response.text
        assert response.json()['can_approve'] and response.json()['can_select_bank_movement']
        assert [row['id'] for row in response.json()['banks']] == [bank]
        assert response.json()['bank_movements'] == []
        rows = await http.get(path + '/bank-movements')
        assert rows.status_code == 200, rows.text
        assert identity in {row['id'] for row in rows.json()['items']}
        from operational_driver_payment_review import options
        legacy = await options(db, owner='owner', actor='owner', assignment_id=assignment)
        assert identity in {row['id'] for row in legacy['bank_movements']}
        assert (await http.get('/store-delivery/payment-review/bank-statements/context')).status_code == 403
        upload = await http.post('/store-delivery/payment-review/bank-statements/upload',
            data={'assignment_id': assignment, 'bank_account_id': bank, 'request_id': 'blocked-scoped-upload',
                  'expected_started_at': '2026-10-05T09:00:00+00:00', 'expected_session_scope': 'a' * 64},
            files={'file': ('synthetic.xlsx', b'not-read-before-authorization', 'application/octet-stream')})
        assert upload.status_code == 403, upload.text
        assert (await db.store_delivery_payment_reviews.find_one({'assignment_id': assignment}))['status'] == 'pending'
    scoped_options_case(scenario, operational=operational)


def test_receipt_callback_alone_never_grants_the_nested_bank_reader():
    import operational_driver_payment_review as service
    from operational_balance_reviews import review_access
    async def scenario(db, http, assignment, bank):
        await operational_candidate(db, bank)
        async def authorize(database):
            await review_access(database, {'id': 'staff'}, expected_owner='owner', assignment_id=assignment)
        response = await service.options(db, owner='owner', actor='staff', assignment_id=assignment,
                                         authorize_review=authorize)
        assert response['bank_movements'] == []
        assert response['can_approve']
    scoped_options_case(scenario, role='accountant')


def test_workspace_pages_native_and_operational_rows_together_in_stable_order():
    async def scenario(db, http, assignment, bank):
        await db.mz2_daily_movements.update_many({}, {'$set': {'status': 'matched'}})
        for number in (1, 3, 5, 7):
            await db.mz2_daily_movements.insert_one({'user_id': 'owner', 'id': f'native-{number}',
                'bank_account_id': bank, 'amount': 200 if number == 3 else '200.00', 'currency': 'SAR',
                'status': 'unclassified', 'source': 'bank_statement_import', 'direction': 'in',
                'movement_date': f'2026-10-{number + 5:02}', 'reference': f'NATIVE-{number}'})
        for number in (2, 4, 6):
            await operational_candidate(db, bank, f'operational-bank-movement:{number}', f'2026-10-{number + 5:02}')
        await operational_candidate(db, bank, 'wrong-period', started_at='2025-10-05T09:00:00+00:00')
        await operational_candidate(db, bank, 'wrong-bank', bank_account_id='another-bank')
        await operational_candidate(db, bank, 'outgoing', direction='out')
        await operational_candidate(db, bank, 'consumed', operational_event_id='used')
        path = BASE + '/reviews/' + assignment + '/bank-movements'
        page1 = (await http.get(path)).json()
        page2 = (await http.get(path, params={'page': 2})).json()
        assert page1['total'] == page2['total'] == 7
        assert page1['total_pages'] == page2['total_pages'] == 2
        assert [row['id'] for row in page1['items']] == ['native-7', 'operational-bank-movement:6',
            'native-5', 'operational-bank-movement:4', 'native-3']
        assert [row['id'] for row in page2['items']] == ['operational-bank-movement:2', 'native-1']
        assert all(set(row) <= {'id', 'movement_date', 'amount', 'currency', 'reference', 'description', 'bank_account_id'}
                   for row in page1['items'] + page2['items'])
    scoped_options_case(scenario)


def test_workspace_receipt_options_do_not_depend_on_the_embedded_candidate_limit():
    from operational_driver_payment_review import options
    async def scenario(db, http, assignment, bank):
        for collection in (db.store_delivery_collections, db.store_delivery_payment_reviews):
            await collection.update_one({'assignment_id': assignment}, {'$set': {'amount': '500.00'}})
        await db.unified_orders.update_one({'user_id': 'owner'},
            {'$set': {'raw_by_source.salla_direct.total.amount': '500.00'}})
        await db.mz2_daily_movements.insert_many([
            {'user_id': 'owner', 'id': f'native-volume-{number:03}', 'bank_account_id': bank,
             'amount': '500.00', 'currency': 'SAR', 'status': 'unclassified',
             'source': 'bank_statement_import', 'direction': 'in', 'movement_date': '2026-10-06',
             'reference': f'VOLUME-{number}', 'file_id': 'volume-file', 'file_hash': 'a' * 64}
            for number in range(201)])
        path = BASE + '/reviews/' + assignment
        first = await http.get(path + '/bank-movements')
        last = await http.get(path + '/bank-movements', params={'page': 41})
        assert first.status_code == last.status_code == 200
        assert first.json()['total'] == last.json()['total'] == 201
        assert len(first.json()['items']) == 5 and len(last.json()['items']) == 1
        legacy = await options(db, owner='owner', actor='owner', assignment_id=assignment)
        assert not legacy['can_approve']
        assert legacy['blocker'] == 'operational_driver_bank_candidate_scope_too_large'
        response = await http.get(path + '/options')
        assert response.status_code == 200, response.text
        assert response.json()['can_approve'], response.text
        assert response.json()['blocker'] is None
        assert response.json()['bank_movements'] == []
    scoped_options_case(scenario)


def test_revoked_bank_scope_during_candidate_read_does_not_return_rows(monkeypatch):
    from operational_driver_bank_store import MOVEMENTS
    async def scenario(db, http, assignment, bank):
        await operational_candidate(db, bank)
        collection_type = type(db[MOVEMENTS])
        original_find = collection_type.find
        revoked = False
        class Cursor:
            def __init__(self, target): self.target = target
            def sort(self, *args): self.target = self.target.sort(*args); return self
            def limit(self, *args): self.target = self.target.limit(*args); return self
            async def to_list(self, *args, **kwargs):
                nonlocal revoked
                rows = await self.target.to_list(*args, **kwargs)
                if not revoked:
                    revoked = True
                    await db.users.update_one({'id': 'staff'}, {'$set': {
                        'operational_balance_access.resources.1.movements': 'none'}})
                return rows
        def find(collection, *args, **kwargs):
            cursor = original_find(collection, *args, **kwargs)
            return Cursor(cursor) if collection.name == MOVEMENTS else cursor
        monkeypatch.setattr(collection_type, 'find', find)
        response = await http.get(BASE + '/reviews/' + assignment + '/bank-movements')
        assert revoked
        assert response.status_code == 403, response.text
        assert (await db.store_delivery_payment_reviews.find_one({'assignment_id': assignment}))['status'] == 'pending'
    scoped_options_case(scenario)


def test_bank_scope_is_rechecked_after_the_final_catalog_read(monkeypatch):
    import store_delivery_bank_identity as identity
    original_catalog = identity.collection_bank_accounts
    revoked = False
    async def revoke_after_catalog(db, *args, **kwargs):
        nonlocal revoked
        result = await original_catalog(db, *args, **kwargs)
        if kwargs.get('actor_id') == 'staff':
            await db.users.update_one({'id': 'staff'}, {'$set': {
                'operational_balance_access.resources.1.movements': 'none'}})
            revoked = True
        return result
    monkeypatch.setattr(identity, 'collection_bank_accounts', revoke_after_catalog)
    async def scenario(db, http, assignment, bank):
        await operational_candidate(db, bank)
        response = await http.get(BASE + '/reviews/' + assignment + '/options')
        assert revoked
        assert response.status_code == 200, response.text
        assert response.json()['can_approve']
        assert response.json()['bank_movements'] == response.json()['banks'] == []
        assert not response.json()['can_select_bank_movement']
    scoped_options_case(scenario)


def test_bank_scope_is_rechecked_after_the_final_receipt_policy_read(monkeypatch):
    import operational_balance_reviews as service
    original_access = service.review_access
    revoked = False
    async def revoke_after_receipt_access(db, *args, **kwargs):
        nonlocal revoked
        result = await original_access(db, *args, **kwargs)
        if kwargs.get('destination'):
            await db.users.update_one({'id': 'staff'}, {'$set': {
                'operational_balance_access.resources.1.movements': 'none'}})
            revoked = True
        return result
    monkeypatch.setattr(service, 'review_access', revoke_after_receipt_access)
    async def scenario(db, http, assignment, bank):
        await operational_candidate(db, bank)
        response = await http.get(BASE + '/reviews/' + assignment + '/bank-movements')
        assert revoked
        assert response.status_code == 403, response.text
        assert response.json()['detail']['code'] == 'operational_review_bank_movements_forbidden'
    scoped_options_case(scenario)
