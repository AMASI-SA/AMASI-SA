"""One approved production data repair, never an ownership inference policy.

The owner and main-settings identity were independently checked in Production
Database Manager on 2026-11-09. The merchant explicitly requested repair.
Only that exact record with a genuinely absent binding may be repaired. A
different binding, null/empty binding, or prior recovery audit is preserved for
operator review. No request handler calls this module.
"""
import asyncio
from datetime import datetime, timezone

from bson import ObjectId

MIGRATION_ID = 'QOYOD-OWNER-RECOVERY-20261109'
APPROVED_OWNER_ID = '5aee091a-cc47-42cd-b14c-a14e32f169cc'
APPROVED_SETTINGS_ID = ObjectId('6a3e951b5f40fa3bcd76f6d7')
TARGET_DATABASE = 'salla-analytics-test_database'


async def run_startup_recovery(db):
    """Keep one bounded attempt and retain its non-sensitive terminal outcome.

    This diagnostic is release-startup-only. It neither retries nor changes
    any eligibility/ownership precondition, and never stores credentials.
    """
    if db.name != TARGET_DATABASE:
        return 'not_target_database'
    try:
        state = await asyncio.wait_for(apply_approved_recovery(db), timeout=3)
    except asyncio.TimeoutError:
        state = 'timeout'
    except Exception:
        state = 'failed'
    try:
        await asyncio.wait_for(db.qoyod_owner_recovery_state.update_one(
            {'_id': MIGRATION_ID}, {'$set': {
                'state': state,
                'at': datetime.now(timezone.utc).isoformat(),
            }}, upsert=True,
        ), timeout=1)
    except Exception:
        # Diagnostic availability must not block startup or authorize access.
        return state + ':diagnostic_unavailable'
    return state


async def apply_approved_recovery(db):
    if db.name != TARGET_DATABASE:
        return 'not_target_database'
    identity = {'_id': APPROVED_SETTINGS_ID, 'user_id': 'main'}
    settings = await db.qoyod_settings.find_one(identity, {
        'security_owner_id': 1, 'plan_b_auto_send_orders_user_id': 1,
        'security_owner_binding_audit': 1,
    })
    if not settings:
        return 'target_unavailable'
    if settings.get('security_owner_id') == APPROVED_OWNER_ID:
        return 'already_bound'
    if ('security_owner_id' in settings
            or settings.get('plan_b_auto_send_orders_user_id') != APPROVED_OWNER_ID
            or 'security_owner_binding_audit' in settings):
        return 'precondition_changed'
    actor = await db.users.find_one({'id': APPROVED_OWNER_ID}, {
        '_id': 0, 'role': 1, 'is_active': 1, 'disabled': 1,
    })
    if (not actor or actor.get('role') != 'owner'
            or actor.get('is_active') is False or actor.get('disabled') is True):
        return 'owner_unavailable'
    result = await db.qoyod_settings.update_one({
        **identity, 'security_owner_id': {'$exists': False},
        'security_owner_binding_audit': {'$exists': False},
        'plan_b_auto_send_orders_user_id': APPROVED_OWNER_ID,
    }, {
        '$set': {'security_owner_id': APPROVED_OWNER_ID},
        '$push': {'security_owner_binding_audit': {
            'migration_id': MIGRATION_ID,
            'previous_owner_id': None,
            'owner_id': APPROVED_OWNER_ID,
            'approval_reference': 'merchant-explicit-repair-20261109-PR1822',
            'source': 'approved_release_recovery',
            'at': datetime.now(timezone.utc).isoformat(),
        }},
    })
    return 'bound' if result.modified_count == 1 else 'precondition_changed'
