"""Synthetic employee grants and canonical statement HTTP behavior."""
from copy import deepcopy

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Header
from mongomock_motor import AsyncMongoMockClient

from mobile_app_permissions import mobile_app_access_for_user, MOBILE_APP_ACCESS
from operational_balance_access import POLICY_FIELD, load_access
from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES, digest, read
from operational_provider_statement_reconciliation import STATEMENTS, RECONCILIATION, statement_document
from operational_provider_statement_intake import normalize_statement
from provider_statement_access import install_provider_statement_access_routes, PROVIDER_STATEMENTS_APP_PERMISSION
from test_operational_balance_statement_reconciliation import saved_state, START, STAMP
from test_operational_balance_statement_intake import content, workbook, sale

GRANT = '/api/employees-v2/management/employees/employee/provider-statement-access'
BASE = '/api/operational-balances/provider-statements'
MOBILE = BASE + '/mobile'
STAFF = {'X-Actor': 'staff', 'X-Mobile': '1'}


async def setup(existing=None):
    db = AsyncMongoMockClient()['employee_statements_test']
    staff = {'id': 'staff', 'created_by': 'owner', 'role': 'employee', 'is_active': True,
        'web_page_access': {'owner_id': 'owner', 'revision': 3, 'grants': [{'id': 'products.catalog', 'mode': 'read'}]}}
    if existing is not None:
        staff[POLICY_FIELD] = deepcopy(existing)
    await db.users.insert_many([{'id': 'owner', 'role': 'owner', 'is_active': True}, staff,
        {'id': 'foreign', 'role': 'owner', 'is_active': True}])
    await db.mezan_employees_v2.insert_one({'user_id': 'owner', 'id': 'employee', 'account_user_id': 'staff', 'status': 'active'})
    await db[STATES].insert_one(saved_state())
    async def principal(x_actor: str = Header(default='owner'), x_mobile: str = Header(default=''), x_borrow: str = Header(default='')):
        result = await db.users.find_one({'id': x_actor}, {'_id': 0})
        if x_borrow:
            result = {'id': 'owner', 'role': 'owner', '_mobile_actor_id': x_actor}
        if x_mobile:
            result['_session_client'] = 'amasi_mobile'
        return result
    app = FastAPI()
    router = APIRouter(prefix='/api/employees-v2')
    install_provider_statement_access_routes(router, db, principal)
    from customer_refund_access import install_customer_refund_access_routes
    install_customer_refund_access_routes(router, db, principal)
    app.include_router(router)
    app.include_router(make_operational_balance_router(db, principal), prefix='/api')
    return db, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic')


async def grant(client, mode='write', providers=None):
    response = await client.get(GRANT)
    assert response.status_code == 200, response.text
    before = response.json()['access']
    payload = {'mode': mode, 'providers': ([] if mode == 'none' else ['tabby']) if providers is None else providers,
        'expected_policy_version': before['policy_version'], 'expected_page_revision': before['page_revision'],
        'expected_session_scope': before['session_scope']}
    response = await client.put(GRANT, json=payload)
    return response, payload


async def post_file(client, raw=None, *, provider='tabby', request_id='employee-upload-one', headers=None):
    return await client.post(MOBILE + '/upload', headers=STAFF if headers is None else headers,
        data={'provider': provider, 'request_id': request_id, 'expected_session_scope': digest(['owner', 'staff']),
              'expected_started_at': START}, files={'file': ('synthetic.xlsx', raw or content(workbook()),
              'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})


@pytest.mark.asyncio
async def test_shared_grant_preserves_unrelated_authority_and_adds_no_general_financial_grant():
    existing = {'owner_id': 'owner', 'version': 7, 'enabled': True, 'operations': ['employee.accrual'],
        'resources': [{'kind': 'employee', 'id': 'other', 'use': True, 'movements': 'own', 'balance': False, 'report': False, 'print': False}]}
    db, client = await setup(existing)
    async with client:
        result, payload = await grant(client)
        assert result.status_code == 200, result.text
        after = result.json()['access']
        assert after['policy_version'] == 8 and after['page_revision'] == 4
        assert after['session_scope'] == payload['expected_session_scope']
        actor = await db.users.find_one({'id': 'staff'})
        assert actor[POLICY_FIELD]['operations'] == existing['operations']
        assert actor[POLICY_FIELD]['resources'] == existing['resources']
        assert {'id': 'products.catalog', 'mode': 'read'} in actor['web_page_access']['grants']
        assert (await load_access(db, actor, 'owner')).resource('provider', 'tabby')['use'] is False
        assert not (await load_access(db, actor, 'owner')).can_operation('provider.statement_confirm')
        # Stale form cannot overwrite the new permission revision.
        assert (await client.put(GRANT, json=payload)).status_code == 409


@pytest.mark.asyncio
async def test_minimal_grant_exposes_literal_page_only_and_none_revokes_manager():
    db, client = await setup()
    async with client:
        assert (await grant(client, 'read'))[0].status_code == 200
        actor = await db.users.find_one({'id': 'staff'})
        access = await mobile_app_access_for_user(db, actor)
        assert PROVIDER_STATEMENTS_APP_PERMISSION in access['permissions'] and access['enabled']
        assert not set(access['permissions']) & {'operational_balance_reports_read', 'operational_balance_movements_write'}
        assert (await grant(client, 'none'))[0].status_code == 200
        await db[MOBILE_APP_ACCESS].insert_one({'owner_user_id': 'owner', 'user_id': 'staff', 'enabled': True, 'permissions': ['app.role.manager']})
        assert PROVIDER_STATEMENTS_APP_PERMISSION not in (await mobile_app_access_for_user(db, await db.users.find_one({'id': 'staff'})))['permissions']
        assert (await client.get(MOBILE, headers=STAFF)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [STAFF, {'X-Actor': 'staff', 'X-Borrow': '1'}, {'X-Actor': 'foreign'}])
async def test_only_real_owner_can_read_or_save_employee_grants(headers):
    db, client = await setup()
    async with client:
        response, payload = await grant(client)
        assert response.status_code == 200
        assert (await client.get(GRANT, headers=headers)).status_code in (403, 404)
        assert (await client.put(GRANT, headers=headers, json=payload)).status_code in (403, 404)


@pytest.mark.asyncio
async def test_grant_refuses_to_reactivate_other_frozen_financial_permissions():
    db, client = await setup({'owner_id': 'owner', 'version': 1, 'enabled': False, 'operations': ['employee.payment'], 'resources': []})
    async with client:
        response, _ = await grant(client)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'provider_statement_access_workspace_disabled'


@pytest.mark.asyncio
@pytest.mark.parametrize('providers', [['emkan'], ['tabby', 'tabby'], [], ['tabby', 'tamara', 'salla', 'tabby']])
async def test_invalid_provider_grants_are_rejected(providers):
    db, client = await setup()
    async with client:
        response, _ = await grant(client, providers=providers)
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_read_mode_provider_filtering_and_denial_of_other_paths():
    db, client = await setup()
    async with client:
        assert (await grant(client, 'read'))[0].status_code == 200
        visible = statement_document('owner', 'owner', 'tabby.xlsx', normalize_statement(content(workbook())), STAMP)
        hidden = statement_document('owner', 'owner', 'tamara.xlsx', normalize_statement(content(workbook('tamara'))), STAMP)
        await db[STATEMENTS].insert_many([visible, hidden])
        response = await client.get(MOBILE + '?page=1&page_size=10', headers=STAFF)
        assert response.status_code == 200, response.text
        view = response.json()
        assert response.headers['cache-control'] == 'no-store, private'
        assert view['permissions'] == {'read': True, 'upload': False}
        assert [row['id'] for row in view['items']] == [visible['id']]
        assert view['providers'] == [{'id': 'tabby', 'name': 'تابي'}]
        assert (await client.get(MOBILE + '/' + hidden['id'] + '/review', headers=STAFF)).status_code == 404
        assert (await post_file(client)).status_code == 403
        assert (await client.get(BASE + '/saved-files', headers=STAFF)).status_code == 403
        assert (await client.post(BASE + '/' + visible['id'] + '/confirm', headers=STAFF, json={})).status_code == 403
        assert (await client.get('/api/operational-balances/reports', headers=STAFF)).status_code == 403
        assert (await client.delete(MOBILE + '/' + visible['id'], headers=STAFF)).status_code in (403, 404, 405)
        browser = await client.get(BASE, headers={'X-Actor': 'staff'})
        assert browser.status_code == 200 and browser.json()['permissions']['use_saved_file'] is False


@pytest.mark.asyncio
async def test_upload_reuses_canonical_writer_and_idempotency_without_extra_principal():
    db, client = await setup()
    async with client:
        assert (await grant(client))[0].status_code == 200
        before = await read(db, 'owner')
        raw = content(workbook())
        response = await post_file(client, raw)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['replayed'] is False and result['pagination']['page_size'] == 10
        assert result['statement']['provider'] == 'tabby' and result['rows'][0]['event_date'] == '2026-11-02'
        assert 'existing' not in result['rows'][0] and 'can_confirm' not in result['rows'][0]
        after = await read(db, 'owner')
        assert after['engine']['facts'] == before['engine']['facts']
        assert after['engine']['obligations'] == before['engine']['obligations']
        assert after['movements'] == before['movements'] and after['openings'] == before['openings']
        assert (await db[STATEMENTS].find_one({'id': result['statement']['id']}))['uploaded_by'] == 'staff'
        replay = await post_file(client, raw)
        assert replay.status_code == 200 and replay.json()['replayed'] is True, replay.text
        assert await read(db, 'owner') == after
        changed = await post_file(client, raw, provider='salla')
        assert changed.status_code == 409
        assert await read(db, 'owner') == after


@pytest.mark.asyncio
async def test_selected_platform_must_match_parser_before_any_write():
    db, client = await setup()
    async with client:
        await grant(client, providers=['tabby', 'tamara'])
        before = await read(db, 'owner')
        response = await post_file(client, provider='tamara')
        assert response.status_code == 422, response.text
        assert response.json()['detail']['not_applied'] is True
        assert await db[STATEMENTS].count_documents({}) == 0
        assert await db.accounting_source_files.count_documents({}) == 0
        assert await read(db, 'owner') == before
        # An actually valid but ungranted provider is not exposed or saved.
        await grant(client, providers=['tabby'])
        denied = await post_file(client, content(workbook('tamara')), provider='tamara')
        assert denied.status_code == 403
        assert await db[STATEMENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_paging_is_exact_and_snapshot_changes_require_first_page():
    db, client = await setup()
    async with client:
        await grant(client, 'read')
        documents = []
        for index in range(12):
            parsed = normalize_statement(content(workbook()))
            parsed['statement_reference'] = str(index)
            documents.append(statement_document('owner', 'owner', f'{index}.xlsx', parsed, STAMP))
        await db[STATEMENTS].insert_many(documents)
        first = await client.get(MOBILE + '?page_size=10', headers=STAFF)
        assert first.status_code == 200, first.text
        first = first.json()
        assert len(first['items']) == 10 and first['pagination']['total'] == 12
        second = await client.get(MOBILE + '?page=2&snapshot=' + first['snapshot'], headers=STAFF)
        assert second.status_code == 200 and len(second.json()['items']) == 2
        assert not {r['id'] for r in first['items']} & {r['id'] for r in second.json()['items']}
        await db[STATES].update_one({'_id': 'owner'}, {'$inc': {'revision': 1}})
        stale = await client.get(MOBILE + '?page=2&snapshot=' + first['snapshot'], headers=STAFF)
        assert stale.status_code == 409
        assert (await client.get(MOBILE + '?page=2', headers=STAFF)).status_code == 409


@pytest.mark.asyncio
async def test_review_pages_are_bounded_without_external_payment_reads(monkeypatch):
    import operational_provider_statement_reconciliation as service
    db, client = await setup()
    async with client:
        await grant(client, 'read')
        parsed = normalize_statement(content(workbook(rows=[sale(**{'Order Number': str(1001+i)}) for i in range(13)])))
        doc = statement_document('owner', 'owner', 'many.xlsx', parsed, STAMP)
        await db[STATEMENTS].insert_one(doc)
        async def forbidden(*args, **kwargs):
            raise AssertionError('read-only mobile review fetched external payments')
        monkeypatch.setattr(service, '_external_evidence', forbidden)
        path = MOBILE + '/' + doc['id'] + '/review'
        response = await client.get(path, headers=STAFF)
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result['rows']) == 10 and result['pagination']['total'] == 13
        second = await client.get(path + '?page=2&preview_hash=' + result['preview_hash'], headers=STAFF)
        assert second.status_code == 200 and len(second.json()['rows']) == 3
        await db[STATES].update_one({'_id': 'owner'}, {'$inc': {'revision': 1}})
        assert (await client.get(path + '?page=2&preview_hash=' + result['preview_hash'], headers=STAFF)).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revoke', 'disable', 'relink', 'archive', 'web_read'])
async def test_live_changes_reject_an_old_upload_intent(change):
    db, client = await setup()
    async with client:
        await grant(client)
        before = await read(db, 'owner')
        if change == 'revoke':
            await grant(client, 'none')
        elif change == 'disable':
            await db.users.update_one({'id': 'staff'}, {'$set': {'disabled': True}})
        elif change == 'web_read':
            await db.users.update_one({'id': 'staff'}, {'$set': {'web_page_access.grants': [{'id': 'balances.statements', 'mode': 'read'}]}})
        else:
            await db.mezan_employees_v2.update_one({'id': 'employee'}, {'$set': {'account_user_id': 'elsewhere'} if change == 'relink' else {'status': 'archived'}})
        assert (await post_file(client)).status_code == 403
        assert await read(db, 'owner') == before
        assert await db[STATEMENTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_revocation_during_reconciliation_prevents_money_cas(monkeypatch):
    import operational_provider_statement_reconciliation as service
    db, client = await setup()
    async with client:
        await grant(client)
        before = await read(db, 'owner')
        original = service._external_evidence
        async def revoke(*args, **kwargs):
            await db.users.update_one({'id': 'staff'}, {'$set': {POLICY_FIELD + '.enabled': False}})
            return await original(*args, **kwargs)
        monkeypatch.setattr(service, '_external_evidence', revoke)
        response = await post_file(client)
        assert response.status_code in (403, 409), response.text
        assert await read(db, 'owner') == before


@pytest.mark.asyncio
async def test_invalid_file_never_creates_a_statement():
    db, client = await setup()
    async with client:
        await grant(client)
        rejected = await post_file(client, b'not excel')
        assert rejected.status_code == 400
        assert rejected.json()['detail']['not_applied'] is True
        assert await db[STATEMENTS].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['empty', 'oversize', 'unknown_workbook'])
async def test_file_rejection_releases_the_pending_attempt_only_before_writes(kind):
    import openpyxl
    db, client = await setup()
    async with client:
        await grant(client)
        before = await read(db, 'owner')
        raw = b'' if kind == 'empty' else b'x' * (10 * 1024 * 1024 + 1) if kind == 'oversize' else content(openpyxl.Workbook())
        response = await client.post(MOBILE + '/upload', headers=STAFF,
            data={'provider': 'tabby', 'request_id': 'invalid-file-attempt',
                'expected_session_scope': digest(['owner', 'staff']), 'expected_started_at': START},
            files={'file': ('invalid.xlsx', raw, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        assert response.status_code == {'empty': 400, 'oversize': 413, 'unknown_workbook': 422}[kind], response.text
        assert response.json()['detail']['not_applied'] is True
        assert await read(db, 'owner') == before and await db[STATEMENTS].count_documents({}) == 0
        assert await db.accounting_source_files.count_documents({}) == 0


@pytest.mark.asyncio
async def test_general_policy_edit_preserves_statement_grant_but_global_disable_revokes_access():
    db, client = await setup()
    async with client:
        await grant(client)
        actor = await db.users.find_one({'id': 'staff'})
        stored = deepcopy(actor[POLICY_FIELD]['provider_statements'])
        response = await client.put('/api/operational-balances/access/users/staff', json={
            'expected_version': actor[POLICY_FIELD]['version'], 'enabled': False, 'operations': [], 'resources': []})
        assert response.status_code == 200, response.text
        assert (await db.users.find_one({'id': 'staff'}))[POLICY_FIELD]['provider_statements'] == stored
        assert (await client.get(MOBILE, headers=STAFF)).status_code == 403
        current = (await client.get(GRANT)).json()['access']
        assert current['mode'] == 'write' and current['enabled'] is False


@pytest.mark.asyncio
async def test_refund_editor_preserves_statement_grant_and_cannot_unfreeze_it_incidentally(monkeypatch):
    db, client = await setup()
    async def sources(*args):
        return [{'kind': 'provider', 'id': 'tabby', 'name': 'تابي', 'currency': 'SAR'}]
    monkeypatch.setattr('customer_refund_access._source_catalog', sources)
    path = GRANT.replace('/provider-statement-access', '/customer-refund-access')
    async with client:
        await grant(client)
        before = await db.users.find_one({'id': 'staff'})
        current = (await client.get(path)).json()['access']
        payload = {'mode': 'read', 'sources': [{'kind': 'provider', 'id': 'tabby'}],
            'expected_policy_version': current['policy_version'], 'expected_page_revision': current['page_revision'],
            'expected_session_scope': current['session_scope']}
        response = await client.put(path, json=payload)
        assert response.status_code == 200, response.text
        after = await db.users.find_one({'id': 'staff'})
        assert after[POLICY_FIELD]['provider_statements'] == before[POLICY_FIELD]['provider_statements']
        await db.users.update_one({'id': 'staff'}, {'$set': {POLICY_FIELD + '.enabled': False}})
        current = (await client.get(path)).json()['access']
        payload.update(expected_policy_version=current['policy_version'], expected_page_revision=current['page_revision'])
        rejected = await client.put(path, json=payload)
        assert rejected.status_code == 409 and rejected.json()['detail']['code'] == 'customer_refund_workspace_disabled'


@pytest.mark.asyncio
async def test_native_permission_editor_does_not_borrow_owner_identity():
    from mobile_app_request_context import mobile_app_request_user
    db, client = await setup()
    async with client:
        actor = await db.users.find_one({'id': 'staff'})
        actor['_session_client'] = 'amasi_mobile'
        principal = await mobile_app_request_user(db, actor, path=GRANT, method='PUT')
        assert principal['id'] == 'staff' and not principal.get('_mobile_actor_id')


@pytest.mark.asyncio
async def test_post_commit_result_failure_is_uncertain_and_same_command_replays(monkeypatch):
    from fastapi import HTTPException
    import mobile_provider_statements as mobile
    db, client = await setup()
    async with client:
        await grant(client)
        original = mobile.review_page
        async def unavailable(*args, **kwargs):
            raise HTTPException(503, detail={'code': 'temporary_review_unavailable'})
        monkeypatch.setattr(mobile, 'review_page', unavailable)
        raw = content(workbook())
        response = await post_file(client, raw)
        assert response.status_code == 503 and response.json()['detail'].get('not_applied') is not True
        saved = await read(db, 'owner')
        assert saved['revision'] > 1 and len(saved['requests']) == 1
        monkeypatch.setattr(mobile, 'review_page', original)
        replayed = await post_file(client, raw)
        assert replayed.status_code == 200 and replayed.json()['replayed'] is True
        assert await read(db, 'owner') == saved


@pytest.mark.asyncio
@pytest.mark.parametrize('started', ['2026-11-01T00:00:00Z', '2026-11-01T00:00:00.000+00:00', '2026-11-01 00:00:00+00:00'])
async def test_period_token_retains_canonical_timestamp_spelling(started):
    db, client = await setup()
    async with client:
        await grant(client)
        await db[STATES].update_one({'_id': 'owner'}, {'$set': {'started_at': started}})
        history = await client.get(MOBILE, headers=STAFF)
        assert history.status_code == 200, history.text
        token = history.json()['started_at']
        assert token == started
        response = await client.post(MOBILE + '/upload', headers=STAFF,
            data={'provider': 'tabby', 'request_id': 'period-token-upload', 'expected_started_at': token,
                  'expected_session_scope': history.json()['session_scope']},
            files={'file': ('synthetic.xlsx', content(workbook()), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        assert response.status_code == 200, response.text
        assert response.json()['started_at'] == started
