"""Explicit Preview-only maintenance: rematch one complete Salla draft.

Dry run is the default. Applying requires the exact dry-run input digest.
Uses grouped importer semantics; never posts journals or changes source amounts.
"""
import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True


def input_digest(draft, entries):
    return hashlib.sha256(json.dumps([draft, sorted(entries, key=lambda x: x['id'])],
                                     sort_keys=True, default=str).encode()).hexdigest()


async def main(args):
    from dotenv import dotenv_values
    from motor.motor_asyncio import AsyncIOMotorClient
    assert Path('/opt/mezan-preview-runtime-20260916/preview_password_runtime.py').is_file()
    guard = json.loads(subprocess.check_output(
        [sys.executable, '-B', '/app/scripts/production_release_guard.py', 'status'], text=True))
    assert guard.get('active') is False, 'Release is active'
    env = dotenv_values('/app/backend/.env')
    assert urlsplit(env['MONGO_URL']).hostname in {'localhost', '127.0.0.1', '::1'}
    sys.path.insert(0, '/app/backend')
    from accounting_settlement_routes import _scope, _recomputed_draft
    from accounting_settlement_service import has_blocking_reasons
    from settlements_import.service import _apply_entries, _consolidate_rows
    client = AsyncIOMotorClient(env['MONGO_URL'])
    db = client[env['DB_NAME']]
    drafts = await db.accounting_settlements_v2.find(
        {'provider': 'salla', 'statement_reference': args.reference}, {'_id': 0}).to_list(2)
    assert len(drafts) == 1, 'Draft must be unique'
    draft = drafts[0]
    assert draft['status'] in {'draft', 'needs_review', 'rejected'}
    owner = draft['user_id']
    actor = await db.users.find_one({'id': owner}, {'_id': 0})
    assert actor is not None
    actor, scoped_owner = await _scope(db, actor, 'accounting.drafts.create')
    assert owner == scoped_owner
    scope = {'user_id': owner, 'file_id': draft['source_file_id']}
    entries = await db.settlement_entries.find(scope, {'_id': 0}).to_list(None)
    assert entries and all(e.get('provider') == 'salla' for e in entries)
    assert all(e.get('order_number') for e in entries), 'Non-order entries require separate review'
    assert all(e.get('event_type') not in {'salla_purchase', 'settlement_fee'} for e in entries)
    grouped = {}
    for entry in entries:
        grouped.setdefault(entry['order_number'], []).append(entry)
    found = {r['order_number'] async for r in db.unified_orders.find(
        {'user_id': owner, 'order_number': {'$in': list(grouped)}}, {'_id': 0, 'order_number': 1})}
    digest = input_digest(draft, entries)
    summary = {'rows': len(entries), 'unique_orders': len(grouped),
               'matched_orders': len(found), 'missing_orders': len(set(grouped) - found),
               'repeated_order_groups': sum(len(v) > 1 for v in grouped.values()),
               'digest': digest, 'applied': False}
    print(json.dumps(summary), flush=True)
    if not args.apply_digest:
        client.close()
        return
    assert args.apply_digest == digest, 'Inputs changed since dry run'
    assert set(grouped) == found, 'Missing orders remain'
    ledger_count = await db.general_ledger.count_documents({'user_id': owner})
    now = datetime.now(timezone.utc).isoformat()
    claim_query = {'id': draft['id'], 'user_id': owner, 'status': draft['status'],
                   'version': draft['version']}
    claim = await db.accounting_settlements_v2.update_one(claim_query, {'$set': {
        'status': 'rematching', 'rematch_started_at': now}})
    assert claim.matched_count == 1, 'Draft changed or is busy'
    try:
        result = await _apply_entries(db, owner, 'salla', entries, file_id=draft['source_file_id'])
        assert result['matched_set'] == found
        for number, rows in grouped.items():
            expected = _consolidate_rows(rows)['actual_fields']
            actual = await db.unified_orders.find_one({'user_id': owner, 'order_number': number})
            for key, value in expected.items():
                field = key if key.startswith('actual_') else 'actual_' + key
                assert actual.get(field) == value, 'Grouped application verification failed'
        await db.settlement_entries.update_many(scope, {'$set': {
            'matched': True, 'automatic_rematched_at': now}})
        snapshot = {**draft.get('source_snapshot', {}), 'matched': len(found),
                    'unmatched': 0, 'unmatched_orders': [], 'unmatched_entries': [],
                    'matched_rows': len(entries)}
        await db.settlement_files.update_one({'id': draft['source_file_id'], 'user_id': owner},
            {'$set': {k: snapshot[k] for k in ['matched', 'unmatched', 'unmatched_orders', 'matched_rows']}})
        updated = await _recomputed_draft(db, owner_id=owner, draft={**draft,
            'source_snapshot': snapshot, 'updated_by': actor['id'], 'updated_at': now,
            'version': draft['version'] + 1})
        updated['status'] = 'needs_review' if has_blocking_reasons(updated['review_reasons']) else 'draft'
        updated.setdefault('revision_log', []).append({'version': updated['version'],
            'actor_id': actor['id'], 'at': now, 'reason': 'preview_grouped_rematch_after_order_import',
            'input_digest': digest, 'rows': len(entries), 'unique_orders': len(found)})
        assert updated['amounts'] == draft['amounts']
        assert await db.general_ledger.count_documents({'user_id': owner}) == ledger_count
        saved = await db.accounting_settlements_v2.replace_one(
            {'id': draft['id'], 'user_id': owner, 'status': 'rematching'}, updated)
        assert saved.matched_count == 1
        summary.update(applied=True, remaining_unmatched=0, journal_count_unchanged=True,
                       status=updated['status'], balanced=bool((updated.get('journal_preview') or {}).get('balanced')))
        print(json.dumps(summary), flush=True)
    except Exception:
        # Fail closed: a partial run must never become reviewable/postable.
        await db.accounting_settlements_v2.update_one(
            {'id': draft['id'], 'user_id': owner, 'status': 'rematching'},
            {'$set': {'rematch_failed_at': datetime.now(timezone.utc).isoformat()}})
        raise
    finally:
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference', required=True)
    parser.add_argument('--apply-digest')
    asyncio.run(main(parser.parse_args()))
