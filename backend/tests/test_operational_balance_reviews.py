"""Scoped workspace receipt queue and canonical-writer authorization contract."""
from hashlib import sha256

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
