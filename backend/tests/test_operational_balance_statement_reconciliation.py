"""Synthetic weekly statements bind existing effects; they never move bank money."""
from copy import deepcopy
import asyncio

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException
from mongomock_motor import AsyncMongoMockClient

from operational_balance_store import digest, initial, STATES, read
from operational_balance_service import report
from operational_balance_engine import reconcile
from operational_balance_routes import make_operational_balance_router
from operational_customer_returns import attach_returns
from operational_provider_statement_intake import normalize_statement
from operational_provider_statement_reconciliation import build_review, statement_document, STATEMENTS, RECONCILIATION
from test_operational_balance_statement_intake import content, workbook, sale


START = '2026-11-01T00:00:00+00:00'
PAID = '2026-11-02T10:00:00+00:00'
AS_OF = '2026-11-10T12:00:00+00:00'
STAMP = '2026-11-15T12:00:00+00:00'


def saved_state(provider='tabby', amount='100.00'):
    state = initial('owner')
    state.update(status='active', revision=1, started_at=START)
    state['engine'] = {
        'as_of': AS_OF,
        'facts': {'capture:paid-one': {'order_id': 'order-one', 'provider': provider, 'amount': amount}},
        'obligations': {'provider:order-one': {'id': 'provider:order-one', 'kind': 'provider',
            'party_type': 'provider', 'party_id': provider, 'currency': 'SAR', 'confirmed': amount,
            'expected': '0.00', 'direction': 'receivable', 'order_number': '1001'}},
        'provider_fee_inputs': {'settings': {}, 'policies': [], 'source_mode': 'legacy_settings',
            'orders': {'order-one': {'order_number': '1001', 'currency': 'SAR', 'created_at': PAID,
                'updated_at': PAID, 'payment': {'provider': provider, 'currency': 'SAR', 'gross': amount,
                    'captures': [{'id': 'paid-one', 'amount': amount, 'captured_at': PAID}], 'refunds': []}}}},
        'issues': [],
    }
    return state


def document(provider='tabby'):
    raw = content(workbook(provider))
    return statement_document('owner', 'owner', 'weekly.xlsx', normalize_statement(raw), STAMP)


def refund_content(*, day='2026-11-03', amount=25):
    row = sale(**{'Sale/Refund Date': day, 'Type': 'partial refund', 'Order Amount': -amount,
        'Refundable Commission': -1.25, 'Non Refundable Commission': 0, 'Fixed Fee': 0,
        'Total Fee': -1.25, 'VAT Amount': -.19, 'Total Deduction': -1.44,
        'Transferred amount': -(amount - 1.44)})
    return content(workbook(rows=[row]))


def with_policy(state):
    state['engine']['provider_fee_inputs']['policies'] = [{
        'id': 'synthetic-policy', 'provider': 'tabby', 'currency': 'SAR', 'status': 'active',
        'effective_from': '2026-01-01', 'percentage': '2', 'fixed_amount': '0',
        'vat_treatment': 'exempt', 'refund_fee_treatment': 'retain', 'cancellation_fee_treatment': 'retain'}]
    return state


async def client_for(state=None):
    db = AsyncMongoMockClient()['statement_reconciliation_test']
    await db.users.insert_many([{'id': 'owner', 'role': 'owner', 'is_active': True},
        {'id': 'other', 'role': 'owner', 'is_active': True},
        {'id': 'staff', 'role': 'employee', 'created_by': 'owner', 'is_active': True}])
    selected = deepcopy(state or saved_state())
    await db[STATES].insert_one(selected)
    for oid, order in selected.get('engine', {}).get('provider_fee_inputs', {}).get('orders', {}).items():
        payment = order.get('payment') or {}
        captures = payment.get('captures') or []
        if len(captures) != 1:
            continue
        capture = captures[0]
        await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': order['order_number'],
            'raw_by_source': {'salla_direct': {'id': oid, 'reference_id': order['order_number'],
                'currency': order['currency'], 'date': order['created_at'], 'updated_at': order['updated_at'],
                'payment': {'method': payment['provider'], 'status': 'captured',
                    'reference': capture['id'], 'paid_at': capture['captured_at'], 'paid_amount': capture['amount']}}}})
    app = FastAPI()
    async def principal(x_test_user: str = Header(default='owner')):
        return {'id': x_test_user}
    app.include_router(make_operational_balance_router(db, principal), prefix='/api')
    return db, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated')


BASE = '/api/operational-balances/provider-statements'


async def upload(client, raw=None, *, request='upload-statement-one', **form):
    return await client.post(BASE + '/upload', data={'request_id': request,
        'expected_session_scope': digest(['owner', 'owner']), 'expected_started_at': START, **form},
        files={'file': ('synthetic.xlsx', raw or content(workbook()),
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})


def decision(view, *, request='confirm-statement-one'):
    row = view['rows'][0]
    action = row['allowed_actions'][0]
    payload = {'request_id': request, 'expected_session_scope': view['session_scope'],
        'expected_preview_hash': view['preview_hash'], 'confirmed': True,
        'decisions': [{'row_id': row['id'], 'action': action}]}
    if action in {'accept_statement', 'link_native_refund'}:
        payload['decisions'][0]['existing_id'] = row['existing'][0]['id']
    else:
        payload['confirmed_unrecorded'] = True
        if row.get('proposed_effect', {}).get('employee_case_id'):
            payload['decisions'][0]['existing_id'] = row['proposed_effect']['employee_case_id']
    return payload


def test_exact_unique_saved_capture_is_matched_without_mutating_source_or_state():
    state, statement = saved_state(), document()
    before = deepcopy((state, statement))
    view = build_review(state, statement, 'owner')
    row = view['rows'][0]
    assert row['status'] == 'matched'
    assert row['principal_amount'] == '100.00' and row['fee_total'] == '9.19'
    assert row['existing'][0]['id'] == 'capture:paid-one'
    assert view['bank_posting_allowed'] is False
    assert view['session_scope'] == digest(['owner', 'owner'])
    assert (state, statement) == before


def test_missing_order_is_actionable_and_never_an_implicit_capture():
    state = saved_state()
    state['engine']['provider_fee_inputs']['orders'] = {}
    view = build_review(state, document(), 'owner')
    row = view['rows'][0]
    assert row['status'] == 'missing' and row['can_confirm'] is False
    assert row['action_url'] == '/operational-balances'
    assert view['counts']['missing'] == 1


def test_different_principal_is_review_only_and_cannot_be_approved_as_a_fee():
    view = build_review(saved_state(amount='90.00'), document(), 'owner')
    row = view['rows'][0]
    assert row['status'] == 'different' and row['can_confirm'] is False
    assert row['existing'][0]['amount'] == '90.00'
    assert row['allowed_actions'] == []


@pytest.mark.asyncio
async def test_real_xlsx_upload_matches_exact_capture_once_and_preserves_all_money():
    original = saved_state()
    db, client = await client_for(original)
    raw = content(workbook())
    async with client:
        response = await upload(client, raw)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['rows'][0]['status'] == 'matched'
        assert result['applied_row_ids'] == [result['rows'][0]['id']]
        after = await read(db, 'owner')
        assert after['movements'] == original['movements'] and after['openings'] == original['openings']
        assert after['engine']['facts'] == original['engine']['facts']
        assert after['engine']['obligations'] == original['engine']['obligations']
        assert after['engine']['as_of'] == AS_OF
        assert len(after[RECONCILIATION]['events']) == 1
        again = await upload(client, raw)
        assert again.status_code == 200 and again.json()['replayed'] is True
        assert await read(db, 'owner') == after
        duplicate = await upload(client, raw, request='upload-statement-new-request')
        assert duplicate.status_code == 200 and duplicate.json()['duplicate'] is True
        assert len((await read(db, 'owner'))[RECONCILIATION]['events']) == 1
        assert await db[STATEMENTS].count_documents({}) == 1
        assert await db.general_ledger.count_documents({}) == 0
        assert await db.payment_adjustments.count_documents({}) == 0
        assert await db.unified_orders.count_documents({}) == 1


@pytest.mark.asyncio
async def test_real_xlsx_missing_refund_preview_confirm_replay_and_refresh_are_one_effect():
    original = with_policy(saved_state())
    db, client = await client_for(original)
    async with client:
        response = await upload(client, refund_content())
        assert response.status_code == 200, response.text
        view = response.json()
        row = view['rows'][0]
        assert row['status'] == 'missing' and row['can_confirm'], row
        assert row['allowed_actions'] == ['record_missing_refund']
        assert row['proposed_effect']['remaining_before'] == '100.00'
        assert row['proposed_effect']['remaining_after'] == '75.00'
        assert not any(key.startswith('refund:') for key in (await read(db, 'owner'))['engine']['facts'])
        url = BASE + '/' + view['statement']['id'] + '/confirm'
        payload = decision(view)
        denied = await client.post(url, json={**payload, 'confirmed_unrecorded': False})
        assert denied.status_code == 422
        accepted = await client.post(url, json=payload)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()['applied_row_ids'] == [row['id']]
        after = await read(db, 'owner')
        refunds = {key: val for key, val in after['engine']['facts'].items() if key.startswith('refund:')}
        assert len(refunds) == 1 and next(iter(refunds.values()))['amount'] == '25.00'
        assert after['engine']['obligations']['provider:order-one']['confirmed'] == '75.00'
        assert after['movements'] == original['movements'] and after['openings'] == original['openings']
        assert after['engine']['as_of'] == AS_OF
        assert report(after)['summary']['actual_liquidity'] == '0.00'
        replayed = await client.post(url, json=payload)
        assert replayed.status_code == 200 and replayed.json()['replayed'] is True
        assert await read(db, 'owner') == after
        source_order = {'id': 'order-one', **deepcopy(original['engine']['provider_fee_inputs']['orders']['order-one'])}
        sources = attach_returns(after, {'orders': [source_order],
            'fee_policies': original['engine']['provider_fee_inputs']['policies']})
        refreshed = reconcile(after, sources, AS_OF)
        assert refreshed['engine']['obligations']['provider:order-one']['confirmed'] == '75.00'
        assert {key: val for key, val in refreshed['engine']['facts'].items() if key.startswith('refund:')} == refunds
        assert report(refreshed)['summary']['actual_liquidity'] == '0.00'


@pytest.mark.asyncio
async def test_pending_employee_compensation_is_linked_in_place_without_second_debit():
    original = with_policy(saved_state())
    original['customer_returns'] = [{
        'id': 'employee-proof', 'case_type': 'order_compensation', 'status': 'salla_refund_recorded',
        'order_id': 'order-one', 'order_number': '1001', 'currency': 'SAR', 'amount': '25.00',
        'refund_source_type': None, 'refund_source_id': None, 'refund_reference': 'employee-refund-ref',
        'refunded_at': '2026-11-03T10:00:00+00:00', 'created_by': 'staff', 'created_at': PAID,
        'note': 'Synthetic employee refund evidence', 'items': [],
        'financial_effect': 'none_already_refunded_in_salla',
        'shipping': {'kind': 'none', 'id': None, 'amount': '0.00', 'status': 'completed', 'reference': ''}}]
    db, client = await client_for(original)
    async with client:
        response = await upload(client, refund_content())
        assert response.status_code == 200, response.text
        view = response.json()
        assert view['rows'][0]['can_confirm'], view['rows'][0]
        assert view['rows'][0]['existing'][0]['financial_effect'] == 'evidence_only'
        assert view['rows'][0]['proposed_effect']['employee_case_id'] == 'employee-proof'
        invalid = decision(view)
        invalid['decisions'][0].pop('existing_id')
        rejected = await client.post(BASE + '/' + view['statement']['id'] + '/confirm', json=invalid)
        assert rejected.status_code == 422
        response = await client.post(BASE + '/' + view['statement']['id'] + '/confirm', json=decision(view))
        assert response.status_code == 200, response.text
        after = await read(db, 'owner')
        assert len(after['customer_returns']) == 1
        case = after['customer_returns'][0]
        assert case['id'] == 'employee-proof' and case['created_by'] == 'staff'
        assert case['note'] == original['customer_returns'][0]['note']
        assert case['refunded_at'] == original['customer_returns'][0]['refunded_at']
        assert case['case_type'] == 'order_compensation' and case['status'] == 'salla_refund_confirmed'
        assert case['financial_effect'] == 'confirmed_statement_provider_refund_fact'
        provider = next(row for row in report(after)['parties'] if row['party_type'] == 'provider' and row['party_id'] == 'tabby')
        assert provider['actual'] == '0.00'
        assert after['engine']['obligations']['provider:order-one']['confirmed'] == '75.00'
        assert after['audit'][-1]['before']['employee_cases'][0] == original['customer_returns'][0]
        source_order = {'id': 'order-one', **deepcopy(original['engine']['provider_fee_inputs']['orders']['order-one'])}
        refreshed = reconcile(after, attach_returns(after, {'orders': [source_order],
            'fee_policies': original['engine']['provider_fee_inputs']['policies']}), AS_OF)
        assert refreshed['engine']['obligations']['provider:order-one']['confirmed'] == '75.00'
        assert sum(key.startswith('refund:') for key in refreshed['engine']['facts']) == 1
        provider = next(row for row in report(refreshed)['parties'] if row['party_type'] == 'provider' and row['party_id'] == 'tabby')
        assert provider['actual'] == '0.00'


@pytest.mark.asyncio
async def test_owner_scope_period_staleness_and_get_purity_at_http_boundary():
    db, client = await client_for()
    async with client:
        for actor in ('staff', 'other'):
            response = await client.get(BASE, headers={'x-test-user': actor})
            assert response.status_code == (403 if actor == 'staff' else 200)
        before = await read(db, 'owner')
        assert (await client.get(BASE)).json()['items'] == []
        assert await read(db, 'owner') == before
        assert await db[STATEMENTS].count_documents({}) == 0
        rejected = await upload(client, expected_session_scope='f' * 64)
        assert rejected.status_code == 409
        rejected = await upload(client, expected_started_at='2026-10-01T00:00:00+00:00')
        assert rejected.status_code == 409
        assert await db[STATEMENTS].count_documents({}) == 0
        view = (await upload(client)).json()
        snapshot = await read(db, 'owner')
        detail_url = BASE + '/' + view['statement']['id'] + '/review'
        assert (await client.get(detail_url)).status_code == 200
        assert (await client.get(detail_url, headers={'x-test-user': 'other'})).status_code == 404
        assert await read(db, 'owner') == snapshot


@pytest.mark.asyncio
async def test_later_different_native_refund_reference_never_doubles_statement_principal():
    original = with_policy(saved_state())
    db, client = await client_for(original)
    async with client:
        view = (await upload(client, refund_content(amount=20))).json()
        assert view['rows'][0]['can_confirm'], view['rows'][0]
        reply = await client.post(BASE + '/' + view['statement']['id'] + '/confirm', json=decision(view))
        assert reply.status_code == 200, reply.text
        state = await read(db, 'owner')
        before = deepcopy(state)
        order = {'id': 'order-one', **deepcopy(original['engine']['provider_fee_inputs']['orders']['order-one'])}
        order['payment']['refunds'] = [{'id': 'later-native-refund', 'amount': '20.00',
                                       'status': 'executed', 'refunded_at': '2026-11-03T12:00:00+00:00'}]
        sources = {'orders': [order]}
        sources = attach_returns(state, sources)
        assert any(issue['code'] == 'provider_statement_refund_alias_review' for issue in sources['issues'])
        assert sources['orders'][0]['payment']['refunds'][0]['id'] != 'later-native-refund'
        refreshed = reconcile(state, sources, AS_OF)
        assert refreshed['engine']['obligations']['provider:order-one']['confirmed'] == '80.00'
        assert sum(key.startswith('refund:') for key in refreshed['engine']['facts']) == 1
        assert state == before
        assert state['engine']['obligations']['provider:order-one']['confirmed'] == '80.00'
        assert sum(key.startswith('refund:') for key in state['engine']['facts']) == 1


@pytest.mark.asyncio
async def test_native_alias_link_keeps_native_timestamp_and_removes_only_order_hold_without_money():
    original = with_policy(saved_state())
    db, client = await client_for(original)
    async with client:
        view = (await upload(client, refund_content(amount=20))).json()
        url = BASE + '/' + view['statement']['id']
        assert (await client.post(url + '/confirm', json=decision(view))).status_code == 200
        native = {'id': 'native-refund-later', 'amount': '20.00', 'currency': 'SAR',
            'status': 'executed', 'refunded_at': '2026-11-03T12:00:00+00:00'}
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
            'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions': [native],
            'raw_by_source.salla_direct.payment_actions.refund_action.refund_amount': '20.00'}})
        before = await read(db, 'owner')
        result = await client.get(url + '/review')
        assert result.status_code == 200, result.text
        fresh = result.json()
        row = fresh['rows'][0]
        assert row['allowed_actions'] == ['link_native_refund'], row
        assert row['native_alias'] == {'id': native['id'], 'reference': native['id'],
            'amount': '20.00', 'native_currency': 'SAR', 'event_date': '2026-11-03', 'source': 'canonical_salla'}
        request = decision(fresh, request='link-native-refund-one')
        assert 'confirmed_unrecorded' not in request
        linked = await client.post(url + '/confirm', json=request)
        assert linked.status_code == 200, linked.text
        after = await read(db, 'owner')
        assert after['engine']['facts'] == before['engine']['facts']
        assert after['engine']['obligations'] == before['engine']['obligations']
        assert after['movements'] == before['movements'] and after['openings'] == before['openings']
        record = next(iter(after[RECONCILIATION]['refund_records'].values()))
        assert record['refunded_at'] != native['refunded_at']
        assert record['native_aliases'][native['id']]['refunded_at'] == native['refunded_at']
        assert (await client.post(url + '/confirm', json=request)).json()['replayed'] is True
        assert await read(db, 'owner') == after
        source_order = {'id': 'order-one', **deepcopy(original['engine']['provider_fee_inputs']['orders']['order-one'])}
        source_order['payment']['refunds'] = [native]
        sources = attach_returns(after, {'orders': [source_order], 'fee_policies': original['engine']['provider_fee_inputs']['policies']})
        assert not any(issue['code'] == 'provider_statement_refund_alias_review' for issue in sources.get('issues', []))
        refreshed = reconcile(after, sources, AS_OF)
        assert refreshed['engine']['obligations']['provider:order-one']['confirmed'] == '80.00'
        assert sum(key.startswith('refund:') for key in refreshed['engine']['facts']) == 1


@pytest.mark.asyncio
async def test_native_alias_after_partial_payout_links_metadata_but_keeps_fee_conflict_and_money():
    from operational_provider_statement_fees import statement_event_issue
    db, client = await client_for(with_policy(saved_state()))
    async with client:
        view = (await upload(client, refund_content(amount=20))).json()
        url = BASE + '/' + view['statement']['id']
        assert (await client.post(url + '/confirm', json=decision(view))).status_code == 200
        before = await read(db, 'owner')
        before['movements'].append({'id': 'partial-payout', 'kind': 'settlement', 'party_type': 'provider',
            'party_id': 'tabby', 'currency': 'SAR', 'amount': '47.00', 'actual_fee_amount': '3.00',
            'direction': 'incoming', 'bank_id': 'bank-one', 'bank_kind': 'bank',
            'allocations': [{'obligation_id': 'provider:order-one', 'amount': '50.00'}]})
        before['engine']['issues'] = [{'code': 'provider_statement_refund_alias_review',
            'source_id': 'provider:order-one', 'order_id': 'order-one', 'provider': 'tabby'}]
        before['revision'] += 1
        await db[STATES].replace_one({'_id': 'owner'}, before)
        event = next(iter(before[RECONCILIATION]['events'].values()))
        assert statement_event_issue(before, event) == 'provider_statement_fee_settlement_conflict'
        native = {'id': 'native-after-payout', 'amount': '20.00', 'currency': 'SAR',
            'status': 'executed', 'refunded_at': '2026-11-03T12:00:00+00:00'}
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
            'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions': [native]}})
        response = await client.get(url + '/review')
        assert response.status_code == 200, response.text
        fresh = response.json()
        row = fresh['rows'][0]
        assert row['allowed_actions'] == ['link_native_refund'], row
        assert row.get('fee_evidence_applied') is not True
        request = decision(fresh, request='link-native-after-payout')
        result = await client.post(url + '/confirm', json=request)
        assert result.status_code == 200, result.text
        after = await read(db, 'owner')
        for field in ('facts', 'obligations', 'provider_fee_inputs'):
            assert after['engine'][field] == before['engine'][field]
        for field in ('movements', 'openings'):
            assert after[field] == before[field]
        assert after[RECONCILIATION]['events'] == before[RECONCILIATION]['events']
        assert statement_event_issue(after, event) == 'provider_statement_fee_settlement_conflict'
        provider = next(item for item in report(after)['details']['provider_balances']['SAR'] if item['provider'] == 'tabby')
        assert provider['fees_complete'] is False and provider['fee_status'] == 'review'
        assert provider['actual_settlement_fees'] == '3.00'
        assert report(after)['summary']['actual_liquidity'] == report(before)['summary']['actual_liquidity'] == '47.00'
        assert not any(issue['code'] == 'provider_statement_refund_alias_review' for issue in after['engine']['issues'])
        assert next(iter(after[RECONCILIATION]['refund_records'].values()))['native_aliases'][native['id']]['refunded_at'] == native['refunded_at']
        assert (await client.post(url + '/confirm', json=request)).json()['replayed'] is True
        assert await read(db, 'owner') == after


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [
    {'payment_actions.refund_action.refunded_transactions': [{'id': 'native-refund', 'amount': 25}]},
    {'payment_actions.refund_action.refund_amount': 25},
    {'payment.refunded_amount': 25},
    {'payment.refunds': [{'id': 'native-refund', 'amount': 25}]},
])
async def test_canonical_salla_refund_observations_block_new_principal(native):
    db, client = await client_for(with_policy(saved_state()))
    await db.unified_orders.update_one({'user_id': 'owner'},
        {'$set': {'raw_by_source.salla_direct.' + key: value for key, value in native.items()}})
    async with client:
        response = await upload(client, refund_content())
        assert response.status_code == 200, response.text
        row = response.json()['rows'][0]
        assert row['can_confirm'] is False and row['reason_code'] == 'statement_native_duplicate_review'
        assert not any(key.startswith('refund:') for key in (await read(db, 'owner'))['engine']['facts'])


@pytest.mark.asyncio
async def test_day_only_refund_after_intraday_snapshot_is_not_assumed_executed_earlier():
    state = with_policy(saved_state())
    state['engine']['as_of'] = '2026-11-03T10:00:00+00:00'
    db, client = await client_for(state)
    async with client:
        result = (await upload(client, refund_content(day='2026-11-03'))).json()
        assert result['rows'][0]['can_confirm'] is False


@pytest.mark.asyncio
async def test_history_projects_metadata_only_and_distinguishes_never_reviewed(monkeypatch):
    import operational_provider_statement_reconciliation as service
    db, client = await client_for()
    async with client:
        uploaded = (await upload(client)).json()
    extra = document('salla')
    await db[STATEMENTS].insert_one(extra)
    before = await read(db, 'owner')
    accesses = []
    class ProjectedCollection:
        def __init__(self, name): self.name = name
        def find(self, query, projection):
            accesses.append((self.name, deepcopy(projection)))
            assert projection and 'statement' not in projection and 'engine.facts' not in projection
            return db[self.name].find(query, projection)
        def __getattr__(self, name): return getattr(db[self.name], name)
    class MetadataDB:
        def __getitem__(self, name):
            return ProjectedCollection(name) if name in {STATEMENTS, STATES} else db[name]
    def forbidden(*args, **kwargs): raise AssertionError('history must not load/reconcile full statement/state')
    monkeypatch.setattr(service, 'read', forbidden)
    monkeypatch.setattr(service, 'build_review', forbidden)
    monkeypatch.setattr(service, '_review', forbidden)
    result = await service.history(MetadataDB(), 'owner', 'owner')
    assert {name for name, _ in accesses} == {STATES, STATEMENTS}
    items = {item['id']: item for item in result['items']}
    assert items[uploaded['statement']['id']]['counts'] == uploaded['counts']
    assert items[uploaded['statement']['id']]['reviewed_at'] == uploaded['reviewed_at']
    assert items[extra['id']]['counts'] is None and items[extra['id']]['reviewed_at'] is None
    assert items[extra['id']]['status'] == 'needs_review' and 'rows' not in items[extra['id']]
    assert await read(db, 'owner') == before


@pytest.mark.asyncio
async def test_idempotency_receipts_are_compact_and_replay_keeps_current_review_read_only():
    db, client = await client_for(with_policy(saved_state()))
    async with client:
        first = (await upload(client, refund_content())).json()
        url = BASE + '/' + first['statement']['id'] + '/confirm'
        payload = decision(first)
        assert (await client.post(url, json=payload)).status_code == 200
        state = await read(db, 'owner')
        for receipt in state['requests'].values():
            assert set(receipt['result']) == {'statement_id', 'started_at', 'applied_row_ids', 'accepted_at', 'duplicate'}
            assert 'rows' not in receipt['result'] and 'statement' not in receipt['result']
        again = await client.post(url, json=payload)
        assert again.status_code == 200 and again.json()['replayed'] is True
        assert again.json()['counts']['confirmed'] == 1
        assert await read(db, 'owner') == state


@pytest.mark.asyncio
async def test_missing_capture_uses_saved_native_proof_and_only_adds_provider_principal():
    state = with_policy(saved_state())
    state['engine']['facts'].clear()
    state['engine']['obligations'].clear()
    db, client = await client_for(state)
    async with client:
        view = (await upload(client)).json()
        assert view['rows'][0]['allowed_actions'] == ['record_missing_capture'], view['rows'][0]
        accepted = await client.post(BASE + '/' + view['statement']['id'] + '/confirm', json=decision(view))
        assert accepted.status_code == 200, accepted.text
        after = await read(db, 'owner')
        assert after['engine']['facts'] == saved_state()['engine']['facts']
        assert after['engine']['obligations']['provider:order-one']['confirmed'] == '100.00'
        assert after['movements'] == state['movements'] and after['openings'] == state['openings']


@pytest.mark.asyncio
@pytest.mark.parametrize('conflict', [False, True])
async def test_statement_plan_fills_only_missing_exact_configured_plan_for_refund(conflict):
    state = with_policy(saved_state())
    state['engine']['provider_fee_inputs']['policies'][0]['plan_id'] = 'Installments: 3 Months'
    if conflict:
        state['engine']['provider_fee_inputs']['orders']['order-one']['payment']['plan_id'] = 'another-source-plan'
    db, client = await client_for(state)
    async with client:
        view = (await upload(client, refund_content())).json()
        assert view['rows'][0]['can_confirm'] is (not conflict), view['rows'][0]
        if conflict:
            return
        accepted = await client.post(BASE + '/' + view['statement']['id'] + '/confirm', json=decision(view))
        assert accepted.status_code == 200, accepted.text
        after = await read(db, 'owner')
        payment = after['engine']['provider_fee_inputs']['orders']['order-one']['payment']
        assert payment['plan_id'] == 'Installments: 3 Months'
        assert payment['plan_evidence']['statement_id'] == view['statement']['id']
        assert after['engine']['obligations']['provider:order-one']['confirmed'] == '75.00'


def test_legacy_confirmed_compensation_keeps_its_existing_direct_debit_and_forged_marker_fails():
    state = with_policy(saved_state())
    state['customer_returns'] = [{'id': 'legacy-case', 'case_type': 'order_compensation',
        'status': 'salla_refund_confirmed', 'refund_source_type': 'provider', 'refund_source_id': 'tabby',
        'refund_source_name': 'Tabby', 'order_id': 'order-one', 'order_number': '1001',
        'currency': 'SAR', 'amount': '25.00', 'refund_reference': 'legacy',
        'refunded_at': '2026-11-03T10:00:00+00:00'}]
    before = deepcopy(state)
    result = report(state)
    party = next(row for row in result['parties'] if row['party_type'] == 'provider' and row['party_id'] == 'tabby')
    assert party['actual'] == '-25.00' and state == before
    source = statement_document('owner', 'owner', 'synthetic-refund.xlsx', normalize_statement(refund_content()), STAMP)
    row = build_review(state, source, 'owner')['rows'][0]
    assert row['can_confirm'] is False and row['allowed_actions'] == []
    assert row['existing'][0]['financial_effect'] == 'already_recorded'
    assert state == before and not any(key.startswith('refund:') for key in state['engine']['facts'])
    state['customer_returns'][0].update(financial_effect='confirmed_statement_provider_refund_fact',
                                        statement_refund_record_id='forged')
    with pytest.raises(ValueError, match='provider_statement_refund_link_invalid'):
        report(state)


@pytest.mark.asyncio
async def test_native_source_change_after_preview_rejects_confirm_without_financial_effect():
    db, client = await client_for(with_policy(saved_state()))
    async with client:
        view = (await upload(client, refund_content())).json()
        before = await read(db, 'owner')
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
            'raw_by_source.salla_direct.payment_actions.refund_action.refund_amount': '25.00'}})
        rejected = await client.post(BASE + '/' + view['statement']['id'] + '/confirm', json=decision(view))
        assert rejected.status_code == 409 and rejected.json()['detail']['code'] == 'provider_statement_preview_stale'
        assert await read(db, 'owner') == before


@pytest.mark.asyncio
async def test_cas_rechecks_source_changes_after_review_before_commit(monkeypatch):
    import operational_provider_statement_reconciliation as service
    db, client = await client_for(with_policy(saved_state()))
    async with client:
        view = (await upload(client, refund_content())).json()
        before = await read(db, 'owner')
        original, calls = service._external_evidence, 0
        async def changed_source(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
                    'raw_by_source.salla_direct.payment_actions.refund_action.refund_amount': '25.00'}})
            return await original(*args)
        monkeypatch.setattr(service, '_external_evidence', changed_source)
        rejected = await client.post(BASE + '/' + view['statement']['id'] + '/confirm', json=decision(view))
        assert calls >= 2
        assert rejected.status_code == 409 and rejected.json()['detail']['code'] == 'provider_statement_preview_stale'
        assert await read(db, 'owner') == before


@pytest.mark.asyncio
async def test_collect_sources_keeps_alias_proof_with_payment_none_and_other_order_refreshes():
    from operational_balance_sources import collect_sources
    original = with_policy(saved_state())
    db, client = await client_for(original)
    await db.mz2_provider_fee_policies_v2.insert_one({'user_id': 'owner',
        'policies': deepcopy(original['engine']['provider_fee_inputs']['policies'])})
    await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
        'raw_by_source.salla_direct.total': {'amount': '100.00', 'currency': 'SAR'},
        'raw_by_source.salla_direct.items': []}})
    async with client:
        view = (await upload(client, refund_content(amount=20))).json()
        url = BASE + '/' + view['statement']['id']
        assert (await client.post(url + '/confirm', json=decision(view))).status_code == 200
        approved = await read(db, 'owner')
        native = {'id': 'native-refund-later', 'amount': '20.00', 'currency': 'SAR',
            'status': 'executed', 'refunded_at': '2026-11-03T12:00:00+00:00'}
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
            'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions': [native],
            'raw_by_source.salla_direct.payment_actions.refund_action.refund_amount': '20.00'}})
        second = deepcopy(await db.unified_orders.find_one({'user_id': 'owner'}, {'_id': 0}))
        second['order_number'] = '1002'
        second_raw = second['raw_by_source']['salla_direct']
        second_raw.update(id='order-two', reference_id='1002')
        second_raw.pop('payment_actions')
        second_raw['payment']['reference'] = 'paid-two'
        await db.unified_orders.insert_one(second)
        sources = await collect_sources(db, 'owner', START, AS_OF, operational_state=approved)
        first_order = next(order for order in sources['orders'] if order['id'] == 'order-one')
        assert first_order.get('payment') is None
        observed = first_order['statement_refund_observation']
        assert observed['unresolved'] is False and observed['refunds'][0]['reference'] == native['id']
        assert 'raw_by_source' not in observed and 'raw' not in observed
        assert 'statement_refund_observation' not in next(order for order in sources['orders'] if order['id'] == 'order-two')
        held = reconcile(approved, attach_returns(approved, sources), AS_OF)
        assert held['engine']['obligations']['provider:order-one']['confirmed'] == '80.00'
        assert held['engine']['obligations']['provider:order-two']['confirmed'] == '100.00'
        totals = next(row for row in report(held)['details']['provider_balances']['SAR'] if row['provider'] == 'tabby')
        assert totals['held_orders'] == 1 and totals['complete'] is False
        assert sum(key.startswith('refund:') for key in held['engine']['facts']) == 1
        held['revision'] = approved['revision'] + 1
        await db[STATES].replace_one({'_id': 'owner'}, held)
        fresh = (await client.get(url + '/review')).json()
        assert fresh['rows'][0]['allowed_actions'] == ['link_native_refund'], fresh['rows'][0]
        reply = await client.post(url + '/confirm', json=decision(fresh, request='link-collected-native'))
        assert reply.status_code == 200, reply.text
        linked = await read(db, 'owner')
        sources = await collect_sources(db, 'owner', START, AS_OF, operational_state=linked)
        refreshed = reconcile(linked, attach_returns(linked, sources), AS_OF)
        assert refreshed['engine']['obligations']['provider:order-one']['confirmed'] == '80.00'
        assert refreshed['engine']['obligations']['provider:order-two']['confirmed'] == '100.00'
        assert sum(key.startswith('refund:') for key in refreshed['engine']['facts']) == 1
        totals = next(row for row in report(refreshed)['details']['provider_balances']['SAR'] if row['provider'] == 'tabby')
        assert totals['held_orders'] == 0
        assert not any(issue['code'] == 'provider_statement_refund_alias_review' for issue in refreshed['engine']['issues'])


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['amount', 'date', 'second_alias', 'repeat'])
async def test_linked_native_alias_is_revalidated_and_repeated_identical_source_stays_one_effect(change):
    from operational_provider_statement_reconciliation import statement_refund_observation
    original = with_policy(saved_state())
    db, client = await client_for(original)
    async with client:
        view = (await upload(client, refund_content(amount=20))).json()
        url = BASE + '/' + view['statement']['id']
        assert (await client.post(url + '/confirm', json=decision(view))).status_code == 200
        native = {'id': 'native-one', 'amount': '20.00', 'currency': 'SAR',
            'status': 'executed', 'refunded_at': '2026-11-03T12:00:00+00:00'}
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
            'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions': [native]}})
        fresh = (await client.get(url + '/review')).json()
        assert (await client.post(url + '/confirm', json=decision(fresh, request='link-native-one'))).status_code == 200
        state = await read(db, 'owner')
        observed = [deepcopy(native)]
        if change == 'amount': observed[0]['amount'] = '21.00'
        if change == 'date': observed[0]['refunded_at'] = '2026-11-03T13:00:00+00:00'
        if change == 'second_alias': observed.append({**native, 'id': 'native-two'})
        if change == 'repeat': observed.append(deepcopy(native))
        await db.unified_orders.update_one({'user_id': 'owner'}, {'$set': {
            'raw_by_source.salla_direct.payment_actions.refund_action.refunded_transactions': observed}})
        raw = (await db.unified_orders.find_one({'user_id': 'owner'}))['raw_by_source']['salla_direct']
        source_order = {'id': 'order-one', **deepcopy(original['engine']['provider_fee_inputs']['orders']['order-one'])}
        source_order['payment'] = None
        source_order['statement_refund_observation'] = statement_refund_observation(state, raw,
            owner='owner', order_id='order-one', order_number='1001')
        sources = attach_returns(state, {'orders': [source_order],
            'fee_policies': original['engine']['provider_fee_inputs']['policies']})
        assert any(i['code'] == 'provider_statement_refund_alias_review' for i in sources.get('issues', [])) is (change != 'repeat')
        refreshed = reconcile(state, sources, AS_OF)
        assert refreshed['engine']['obligations']['provider:order-one']['confirmed'] == '80.00'
        assert sum(key.startswith('refund:') for key in refreshed['engine']['facts']) == 1
        review = (await client.get(url + '/review')).json()['rows'][0]
        assert review['can_confirm'] is False
        assert review['status'] == ('already_confirmed' if change == 'repeat' else 'different')


@pytest.mark.asyncio
async def test_upload_derives_variable_only_average_in_same_cas_and_replay_keeps_exact_source():
    db, client = await client_for()
    raw = content(workbook())
    async with client:
        response = await upload(client, raw)
        assert response.status_code == 200, response.text
        view = response.json()
        average = view['statement_average']
        assert average['available'] is True
        assert average['calculation'] == 'sale_variable_fees_before_vat_over_sale_principal'
        assert average['rate_percent'] == '6.99000000'
        assert average['fee_variable_net_total'] == '6.99' and average['fee_fixed_net_total'] == '1.00'
        assert average['fixed_amount_per_sale'] == '1.00000000' and average['vat_rate_percent'] == '15.00000000'
        assert view['rows'][0]['fee_total'] == '9.19'  # Actual complete fee is separate from variable percentage.
        after = await read(db, 'owner')
        profiles = after[RECONCILIATION]['average_profiles']
        assert len(profiles) == 1
        assert after['engine']['provider_fee_inputs']['average_profiles'] == list(profiles.values())
        assert after['engine']['as_of'] == AS_OF
        assert after['movements'] == [] and after['openings'] == {}
        repeated = await upload(client, raw)
        assert repeated.json()['replayed'] is True and await read(db, 'owner') == after
        duplicate = await upload(client, raw, request='upload-average-duplicate')
        assert duplicate.json()['duplicate'] is True
        assert (await read(db, 'owner'))[RECONCILIATION]['average_profiles'] == profiles
        history = (await client.get(BASE)).json()
        assert history['items'][0]['statement_average'] == average
        assert (await client.get(BASE + '/' + view['statement']['id'] + '/review')).json()['statement_average'] == average


@pytest.mark.asyncio
async def test_source_average_uses_newest_statement_date_and_keeps_older_file_reviewable():
    db, client = await client_for()
    async with client:
        first = (await upload(client)).json()
        wb = workbook(rows=[sale(**{'Refundable Commission': 6, 'Non Refundable Commission': 2,
            'Total Fee': 9, 'VAT Amount': 1.35, 'Total Deduction': 10.35, 'Transferred amount': 89.65})])
        wb.active['B1'], wb.active['B2'] = 'SYNTHETIC-NEWER-WEEK', '2026-11-14'
        newest = await upload(client, content(wb), request='upload-newer-average')
        assert newest.status_code == 200, newest.text
        newest = newest.json()
        assert newest['statement_average']['rate_percent'] == '8.00000000'
        state = await read(db, 'owner')
        expected = deepcopy(state[RECONCILIATION]['average_profiles'])
        assert next(iter(expected.values()))['statement_id'] == newest['statement']['id']
        wb = workbook()
        wb.active['B1'], wb.active['B2'] = 'SYNTHETIC-OLDER-WEEK', '2026-11-08'
        older = (await upload(client, content(wb), request='upload-older-average')).json()
        assert older['statement_average']['available'] is True
        assert older['statement_average']['statement_id'] == older['statement']['id']
        assert (await read(db, 'owner'))[RECONCILIATION]['average_profiles'] == expected
        assert first['statement_average']['statement_id'] != newest['statement_average']['statement_id']


@pytest.mark.asyncio
async def test_no_component_split_means_unavailable_average_without_blocking_actual_fee():
    db, client = await client_for(saved_state(provider='salla'))
    async with client:
        response = await upload(client, content(workbook('salla')))
        assert response.status_code == 200, response.text
        view = response.json()
        assert view['statement_average']['available'] is False
        assert view['statement_average']['reason_code'] == 'statement_average_component_split_missing'
        assert view['rows'][0]['fee_total'] == '3.45'
        assert len((await read(db, 'owner'))[RECONCILIATION]['events']) == 1
        assert not (await read(db, 'owner'))[RECONCILIATION].get('average_profiles')


@pytest.mark.asyncio
async def test_missing_vat_rate_keeps_variable_average_available_but_full_estimate_incomplete():
    db, client = await client_for()
    async with client:
        response = await upload(client, content(workbook(rows=[sale(**{'VAT Rate': None})])))
        assert response.status_code == 200, response.text
        average = response.json()['statement_average']
        assert average['available'] is True and average['rate_percent'] == '6.99000000'
        assert average['vat_rate'] is None and average['vat_rate_status'] == 'missing'
        assert average['full_estimate_requires_vat'] is True


@pytest.mark.asyncio
async def test_missing_effect_previews_are_bounded_per_upload_and_all_rows_remain_visible(monkeypatch):
    import operational_balance_engine as engine_module
    state = with_policy(saved_state())
    template = deepcopy(state['engine']['provider_fee_inputs']['orders']['order-one'])
    state['engine']['provider_fee_inputs']['orders'] = {}
    state['engine']['facts'], state['engine']['obligations'] = {}, {}
    rows = []
    base_refund = normalize_statement(refund_content())['rows'][0]
    for index in range(25):
        oid, number, receipt = 'order-' + str(index), str(1001 + index), 'paid-' + str(index)
        order = deepcopy(template)
        order['order_number'], order['payment']['captures'][0]['id'] = number, receipt
        state['engine']['provider_fee_inputs']['orders'][oid] = order
        state['engine']['facts']['capture:' + receipt] = {'order_id': oid, 'provider': 'tabby', 'amount': '100.00'}
        state['engine']['obligations']['provider:' + oid] = {**saved_state()['engine']['obligations']['provider:order-one'],
            'id': 'provider:' + oid, 'order_number': number}
        row = sale(**{'Sale/Refund Date': base_refund['event_date'], 'Type': 'partial refund', 'Order Amount': -25,
            'Refundable Commission': -1.25, 'Non Refundable Commission': 0, 'Fixed Fee': 0,
            'Total Fee': -1.25, 'VAT Amount': -.19, 'Total Deduction': -1.44, 'Transferred amount': -23.56})
        row[0] = number
        rows.append(row)
    original, calls = engine_module.reconcile, 0
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(engine_module, 'reconcile', counted)
    db, client = await client_for(state)
    async with client:
        response = await upload(client, content(workbook(rows=rows)))
        assert response.status_code == 200, response.text
        view = response.json()
        assert len(view['rows']) == 25 and calls == 20
        assert sum(row['can_confirm'] for row in view['rows']) == 20
        assert [row['reason_code'] for row in view['rows'][-5:]] == ['statement_missing_batch_limit'] * 5
        assert not any(key.startswith('refund:') for key in (await read(db, 'owner'))['engine']['facts'])


@pytest.mark.asyncio
async def test_upload_is_owner_only_and_cross_owner_native_rows_cannot_block_or_change_money():
    state = with_policy(saved_state())
    db, client = await client_for(state)
    await db.payment_refunds.insert_one({'user_id': 'other', 'provider': 'tabby', 'order_reference_id': '1001',
        'provider_refund_id': 'other-private-refund', 'amount': '25.00', 'currency': 'SAR', 'status': 'executed'})
    async with client:
        forbidden = await client.post(BASE + '/upload', headers={'x-test-user': 'staff'},
            data={'request_id': 'forbidden-upload', 'expected_session_scope': digest(['owner', 'staff']),
                  'expected_started_at': START}, files={'file': ('synthetic.xlsx', refund_content(),
                  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        assert forbidden.status_code == 403
        assert await db[STATEMENTS].count_documents({}) == 0
        assert await read(db, 'owner') == state
        view = (await upload(client, refund_content())).json()
        assert view['rows'][0]['can_confirm'] is True
        assert 'other-private-refund' not in str(view)


@pytest.mark.asyncio
async def test_concurrent_identical_confirmation_is_one_principal_and_one_receipt():
    db, client = await client_for(with_policy(saved_state()))
    async with client:
        view = (await upload(client, refund_content())).json()
        url, payload = BASE + '/' + view['statement']['id'] + '/confirm', decision(view)
        results = await asyncio.gather(client.post(url, json=payload), client.post(url, json=payload))
        assert all(response.status_code == 200 for response in results), [response.text for response in results]
        after = await read(db, 'owner')
        assert after['engine']['obligations']['provider:order-one']['confirmed'] == '75.00'
        assert sum(key.startswith('refund:') for key in after['engine']['facts']) == 1
        assert len(after['requests']) == 2  # One upload and one confirmation identity.
        assert sum(not response.json()['replayed'] for response in results) == 1
