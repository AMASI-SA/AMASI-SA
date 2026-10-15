"""Supplier setup capability attacks and paused administration on local Mongo."""
import asyncio
from copy import deepcopy
import hashlib

from fastapi import HTTPException
import pytest

from accounting_atomic import atomic_owner
from supplier_debit_setup_atomic import supplier_debit_setup_atomic_owner as setup
from supplier_debit_setup_atomic import MAPPINGS, EXPENSES, AUDIT
from supplier_debit_identity_v2 import resolve_debit
from test_supplier_debit_identity_v2 import management_api, BASE, CREATE_EXPENSE
from test_supplier_native_invoice_v2 import env, http, OWNER, identities, receiving_session


@pytest.mark.asyncio
@pytest.mark.parametrize('collection,method', [
    ('accounting_general_ledger_v2', 'insert_one'), ('accounting_journal_groups_v2', 'insert_one'),
    ('general_ledger', 'insert_one'), ('accounts', 'insert_one'), ('mz2_financial_accounts', 'insert_one'),
    ('mz2_opening_balance_drafts', 'insert_one'), ('mz2_atomic_owners', 'update_one'),
    ('users', 'update_one'), ('mezan_supplier_invoices_v2', 'insert_one'),
    ('mezan_supplier_receiving_sessions_v1', 'update_one'),
    ('mz2_write_control_audit', 'insert_one'), (AUDIT, 'update_one'),
    (AUDIT, 'replace_one'), (AUDIT, 'delete_many'), (MAPPINGS, 'delete_many'),
])
@pytest.mark.parametrize('caught', [False, True])
async def test_forbidden_write_aborts_all_effects_even_if_caught(management_api, collection, method, caught):
    _, db, _ = management_api
    await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'writes_paused': True}})
    before = await db.mz2_atomic_owners.find_one({'_id': 'owner'})
    original = await db[collection].find({}).to_list(None)
    async def callback(scoped):
        await scoped[EXPENSES].insert_one({'user_id': 'owner', 'id': 'must-abort'})
        try:
            operation = getattr(scoped[collection], method)
            if method == 'insert_one':
                await operation({'user_id': 'owner', 'id': 'forbidden'})
            else:
                await operation({'user_id': 'owner'}, {'$set': {'writes_paused': False, 'role': 'owner'}})
        except HTTPException:
            if not caught:
                raise
        return 'callback tried to commit'
    with pytest.raises(HTTPException):
        await setup(db, 'owner', callback)
    assert await db[EXPENSES].count_documents({}) == 0
    assert await db[collection].find({}).to_list(None) == original
    assert await db.mz2_atomic_owners.find_one({'_id': 'owner'}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('escape', ['_db', '_session', 'client', 'command', 'get_collection',
    'collection', 'aggregate', 'session_kwarg', 'foreign_insert', 'foreign_replace',
    'owner_update', 'pipeline_update', 'nested_financial', 'nested_setup', 'unapproved_read',
    'unapproved_journal_metadata', 'forged_cutover', 'nested_write_control'])
async def test_escape_and_owner_scope_denials_are_latched(management_api, escape):
    _, db, _ = management_api
    async def callback(scoped):
        await scoped[AUDIT].insert_one({'user_id': 'owner', 'action': 'must-abort'})
        try:
            if escape in {'_db', '_session', 'client', 'command', 'get_collection'}:
                getattr(scoped, escape)
            elif escape == 'collection':
                scoped[AUDIT].database
            elif escape == 'aggregate':
                scoped[MAPPINGS].aggregate([{'$out': 'general_ledger'}])
            elif escape == 'session_kwarg':
                await scoped[MAPPINGS].insert_one({'user_id': 'owner'}, session=None)
            elif escape == 'foreign_insert':
                await scoped[EXPENSES].insert_one({'user_id': 'other'})
            elif escape == 'foreign_replace':
                await scoped[EXPENSES].replace_one({'id': 'x'}, {'user_id': 'other'}, upsert=True)
            elif escape == 'owner_update':
                await scoped[MAPPINGS].update_one({'id': 'x'}, {'$set': {'user_id': 'other'}})
            elif escape == 'pipeline_update':
                await scoped[MAPPINGS].update_one({'id': 'x'}, [{'$set': {'user_id': 'other'}}])
            elif escape == 'nested_financial':
                await atomic_owner(db, 'owner', lambda nested: nested.users.update_one({}, {'$set': {'role': 'owner'}}))
            elif escape == 'nested_write_control':
                from accounting_write_control import set_write_state
                await set_write_state(db, owner='owner', actor_id='owner', paused=False, revision=0,
                    reason='Forbidden control change through setup callback')
            elif escape == 'nested_setup':
                await setup(db, 'owner', lambda nested: nested[AUDIT].insert_one({'user_id': 'owner'}))
            elif escape == 'unapproved_journal_metadata':
                await scoped.opening_metadata('unrelated-journal')
            elif escape == 'forged_cutover':
                await scoped.verified_opening({'opening_active_txn_group_id': 'unrelated-journal'})
            else:
                await scoped['accounts'].find_one({'user_id': 'owner'})
        except HTTPException:
            pass
    with pytest.raises(HTTPException):
        await setup(db, 'owner', callback)
    assert await db[AUDIT].count_documents({}) == 0
    assert (await db.mz2_atomic_owners.find_one({'_id': 'owner'}))['revision'] == 0


@pytest.mark.asyncio
async def test_capability_cannot_write_after_callback_returns(management_api):
    _, db, _ = management_api
    captured = []
    async def callback(scoped):
        captured.append(scoped[AUDIT])
    await setup(db, 'owner', callback)
    with pytest.raises(HTTPException):
        await captured[0].insert_one({'user_id': 'owner', 'action': 'too-late'})
    assert await db[AUDIT].count_documents({}) == 0


@pytest.mark.asyncio
async def test_paused_product_mapping_create_update_and_audit_use_approved_opening(env, http):
    db, _ = env
    await identities(db)
    await db.mz2_atomic_owners.update_one({'_id': OWNER}, {'$set': {'writes_paused': True}})
    # http fixture registers the receiving router; add only the real setup router.
    from supplier_debit_identity_v2 import make_supplier_debit_router
    async def current(): return {'id': OWNER}
    http._transport.app.include_router(make_supplier_debit_router(db, current))
    payload = {'confirmed': True, 'reason': 'Explicit paused product setup', 'source_kind': 'product',
        'source_id': 'product', 'financial_treatment': 'INVENTORY_ASSET', 'entity_type': 'asset',
        'entity_id': 'inventory-main', 'sub_account': 'inventory', 'currency': 'SAR', 'version': 0}
    response = await http.put(BASE, json=payload)
    assert response.status_code == 200, response.text
    response = await http.put(BASE, json={**payload, 'version': 1, 'entity_id': 'inventory-variant'})
    assert response.status_code == 200 and response.json()['version'] == 2, response.text
    assert await db[AUDIT].count_documents({'user_id': OWNER}) == 2
    assert (await db.mz2_atomic_owners.find_one({'_id': OWNER}))['writes_paused'] is True
    session, close = await receiving_session(db)
    response = await http.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close', json=close)
    assert response.status_code == 423, response.text
    assert response.json()['detail']['code'] == 'mz2_writes_paused'
    assert await db.mezan_supplier_invoices_v2.count_documents({}) == 0
    assert await db.accounting_journal_groups_v2.count_documents({'txn_type': 'supplier_invoice'}) == 0


@pytest.mark.asyncio
async def test_concurrent_paused_setup_serializes_and_preserves_controls(management_api):
    _, db, _ = management_api
    await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'writes_paused': True, 'control_revision': 7}})
    await db[MAPPINGS].insert_one({'user_id': 'owner', 'id': 'counter', 'version': 0})
    async def change(scoped):
        row = await scoped[MAPPINGS].find_one({'id': 'counter'})
        await asyncio.sleep(0.01)
        await scoped[MAPPINGS].update_one({'id': 'counter'}, {'$set': {'version': row['version'] + 1}})
        await scoped[AUDIT].insert_one({'user_id': 'owner', 'version': row['version'] + 1})
    await asyncio.gather(*(setup(db, 'owner', change) for _ in range(6)))
    assert (await db[MAPPINGS].find_one({'id': 'counter'}))['version'] == 6
    assert sorted(row['version'] for row in await db[AUDIT].find({}).to_list(None)) == list(range(1, 7))
    assert await db.mz2_atomic_owners.find_one({'_id': 'owner'}) == {
        '_id': 'owner', 'revision': 6, 'writes_paused': True, 'control_revision': 7}


@pytest.mark.asyncio
async def test_new_owner_setup_does_not_bootstrap_financial_controls(management_api):
    client, db, _ = management_api
    await db.mz2_atomic_owners.delete_one({'_id': 'owner'})
    responses = await asyncio.gather(*(client.post(BASE + '/expense-identities', json={
        **CREATE_EXPENSE, 'request_id': f'first-setup-{i}'}) for i in range(3)))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert await db.mz2_atomic_owners.find_one({'_id': 'owner'}) == {'_id': 'owner', 'revision': 3}


@pytest.mark.asyncio
async def test_setup_and_write_control_share_the_same_serialization_lock(management_api):
    from accounting_write_control import set_write_state
    _, db, _ = management_api
    await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'writes_paused': True}})
    entered, release = asyncio.Event(), asyncio.Event()
    async def callback(scoped):
        await scoped[AUDIT].insert_one({'user_id': 'owner', 'action': 'serialized-setup'})
        entered.set()
        await release.wait()
    pending_setup = asyncio.create_task(setup(db, 'owner', callback))
    await asyncio.wait_for(entered.wait(), 5)
    control = asyncio.create_task(set_write_state(db, owner='owner', actor_id='owner', paused=False,
        revision=0, reason='Synthetic local serialization test'))
    try:
        await asyncio.sleep(0.05)
        assert not control.done()
    finally:
        release.set()
    await asyncio.gather(pending_setup, control)
    row = await db.mz2_atomic_owners.find_one({'_id': 'owner'})
    assert row['revision'] == 2 and row['control_revision'] == 1
    assert await db[AUDIT].count_documents({}) == 1


async def reviewed_opening(db):
    """Approval metadata only: no test Opening Post, activation or journal call."""
    from accounting_financial_accounts import _verified_evidence, _canonical_hash
    owner = 'owner'
    content = b'Synthetic documented opening inventory identity'
    sha = hashlib.sha256(content).hexdigest()
    source = {'user_id': owner, 'file_id': 'proof', 'content': content, 'sha256': sha, 'size': len(content)}
    await db.accounting_source_files.insert_one(source)
    await db.mz2_opening_evidence.insert_one({'user_id': owner, 'source_file_id': 'proof',
        'purpose': 'opening_section', 'section_id': 'inventory', 'sha256': sha, 'size': len(content)})
    lines = [{'category': 'inventory_asset', 'entity_type': 'asset', 'entity_id': 'approved-inventory',
        'sub_account': 'inventory', 'ledger_currency': 'SAR'}]
    manifest = {'draft_id': 'reviewed', 'cutover_at': '2026-09-01T00:00:00Z', 'lines': lines}
    proof = await _verified_evidence(db, owner=owner,
        requirements=[{'source_file_id': 'proof', 'purpose': 'opening_section', 'section_id': 'inventory'}],
        approval_version=2, approved_by=owner, approved_at='2026-09-01T00:00:00Z')
    draft = {'user_id': owner, 'id': 'reviewed', 'active_slot': 'opening', 'status': 'reviewed',
        'version': 2, 'reviewed_by': owner, 'reviewed_at': '2026-09-01T00:00:00Z',
        'preview_id': 'preview', 'preview_manifest': manifest, 'preview_hash': _canonical_hash(manifest),
        'lines': lines, 'cutover_at': manifest['cutover_at'], 'evidence_snapshot': proof}
    draft['approval_hash'] = _canonical_hash({'draft_id': draft['id'], 'approval_version': 2,
        'preview_id': draft['preview_id'], 'approved_preview_hash': draft['preview_hash'], 'evidence': proof})
    await db.mz2_opening_balance_drafts.insert_one(deepcopy(draft))
    await db.mz2_opening_balance_audit.insert_one({'user_id': owner, 'draft_id': draft['id'],
        'event_type': 'review_approved', 'manifest': {'approval_version': 2,
            'approval_hash': draft['approval_hash'], 'preview_hash': draft['preview_hash']}})
    await db.mezan_products_v2.insert_one({'user_id': owner, 'mezan_product_id': 'product', 'status': 'active'})
    return draft


@pytest.mark.asyncio
async def test_preopening_reviewed_identity_is_setup_only_not_posting_authority(management_api):
    client, db, _ = management_api
    draft = await reviewed_opening(db)
    await db.mz2_atomic_owners.update_one({'_id': 'owner'}, {'$set': {'writes_paused': True}})
    payload = {'confirmed': True, 'reason': 'Prepare before opening post', 'source_kind': 'product',
        'source_id': 'product', 'financial_treatment': 'INVENTORY_ASSET', 'entity_type': 'asset',
        'entity_id': 'approved-inventory', 'sub_account': 'inventory', 'currency': 'SAR', 'version': 0,
        'opening_draft_id': draft['id']}
    response = await client.put(BASE, json=payload)
    assert response.status_code == 200, response.text
    assert await db[AUDIT].count_documents({}) == 1
    assert await db.accounting_journal_groups_v2.count_documents({}) == 0
    assert await db.mz2_opening_balance_drafts.find_one({'id': draft['id']}, {'_id': 0}) == draft
    # Even if someone invokes the runtime resolver in a setup capability, a
    # stored reviewed-draft reference cannot replace posted Opening authority.
    with pytest.raises(HTTPException) as error:
        await setup(db, 'owner', lambda scoped: resolve_debit(scoped, 'owner', 'product', 'product'))
    assert error.value.detail['code'] == 'MZ2_SUPPLIER_OPENING_IDENTITY_UNVERIFIED'
    await db.accounting_source_files.update_one({'file_id': 'proof'}, {'$set': {'content': b'tampered'}})
    response = await client.put(BASE, json={**payload, 'version': 1})
    assert response.status_code == 409, response.text
    assert await db[AUDIT].count_documents({}) == 1
