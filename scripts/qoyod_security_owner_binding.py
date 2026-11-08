#!/usr/bin/env python3
"""Operator-only Qoyod binding recovery. Dry run by default; never infer ownership.

Run inside the backend environment with MONGO_URL/DB_NAME already configured.
The approved owner id must come from an independently verified owner session.
No HTTP endpoint, provider call, scan, sender, or financial write is involved.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os


async def recover_binding(db, *, owner_id, expected_owner, apply=False, approval=None):
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError('explicit_owner_id_required')
    actor = await db.users.find_one({'id': owner_id}, {
        '_id': 0, 'id': 1, 'role': 1, 'is_active': 1, 'disabled': 1})
    if (not actor or actor.get('role') != 'owner'
            or actor.get('is_active') is False or actor.get('disabled') is True):
        raise ValueError('active_owner_required')
    settings = await db.qoyod_settings.find_one({'user_id': 'main'}, {
        '_id': 1, 'security_owner_id': 1})
    if not settings:
        raise ValueError('existing_qoyod_settings_required')
    current = settings.get('security_owner_id')
    if current is not None and not isinstance(current, str):
        raise ValueError('invalid_existing_binding_requires_review')
    if current == owner_id:
        return {'state': 'already_bound', 'changed': False}
    if current != expected_owner:
        raise ValueError('binding_changed_or_expected_owner_mismatch')
    if not apply:
        return {'state': 'missing' if current is None else 'different_owner',
                'changed': False, 'eligible_for_explicit_repair': True}
    if not isinstance(approval, str) or not approval.strip():
        raise ValueError('approval_reference_required')
    # CAS and embedded audit are one atomic update. No upsert or other settings.
    result = await db.qoyod_settings.update_one({
        '_id': settings['_id'], 'user_id': 'main', 'security_owner_id': current,
    }, {'$set': {'security_owner_id': owner_id}, '$push': {
        'security_owner_binding_audit': {
            'previous_owner_id': current, 'owner_id': owner_id,
            'approval_reference': approval,
            'at': datetime.now(timezone.utc).isoformat(),
            'source': 'operator_explicit_recovery',
        }}})
    if result.modified_count != 1:
        raise ValueError('binding_changed_during_repair')
    return {'state': 'bound', 'changed': True}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-id', required=True)
    expected = parser.add_mutually_exclusive_group(required=True)
    expected.add_argument('--expect-unbound', action='store_true')
    expected.add_argument('--expected-owner-id')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approval-reference')
    args = parser.parse_args()
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ['MONGO_URL'], serverSelectionTimeoutMS=10000)
    try:
        result = await recover_binding(client[os.environ['DB_NAME']],
            owner_id=args.owner_id, expected_owner=args.expected_owner_id,
            apply=args.apply, approval=args.approval_reference)
        print(json.dumps(result))
    finally:
        client.close()


if __name__ == '__main__':
    asyncio.run(main())
