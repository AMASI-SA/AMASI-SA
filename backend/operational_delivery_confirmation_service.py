"""Owner-only preview/CAS for evidence; no fee, cash or opening writer."""
import asyncio

from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern

from operational_balance_archive import ArchiveError, SNAPSHOT, unpack_financial_snapshot
from operational_balance_service import active_gate
from operational_balance_store import (STATES, audit, check_authorization, digest, fail, initial,
                                       mutate, now, remember, replay)
from operational_delivery_confirmation import (PREFIX, binding, public_record, record_confirmation, saved_record)
from operational_delivery_confirmation_sources import read_candidate
from operational_financial_activity_runtime import prepare_in_thread
from operational_supplier_invoice_preview import SnapshotReads


async def _owner(db, owner, actor):
    user = await db.users.find_one({'id': actor}, {'_id': 0, 'role': 1, 'name': 1, 'full_name': 1,
                                                'disabled': 1, 'is_active': 1, 'deleted_at': 1})
    if (owner != actor or not user or user.get('role') != 'owner' or user.get('disabled')
            or user.get('deleted_at') or user.get('is_active') is False):
        fail('delivery_confirmation_owner_required', 'تأكيد التوصيل متاح للمالك فقط', 403)
    await check_authorization()
    return user


def _scope(owner, actor, payload):
    if payload.get('expected_session_scope') != digest([owner, actor]):
        fail('operational_actor_scope_changed', 'تغير الحساب؛ أعد فتح الصفحة', 403)


def _issue(error):
    from operational_balance_completeness import reason_for
    code = str(error)
    if not code.startswith(('operational_delivery_', 'shipping_')) or not all(c.islower() or c == '_' for c in code):
        code = 'operational_delivery_confirmation_source_invalid'
    return reason_for({'code': code})


async def _snapshot(db, owner, actor, payload, stamp):
    async with await db.client.start_session() as session:
        async with session.start_transaction(read_concern=ReadConcern('snapshot')):
            scoped = SnapshotReads(db, owner, session)
            # No audit-page scan or all-source refresh is needed to review one date gap.
            stored = await scoped[STATES].find_one({'_id': owner, 'owner_id': owner},
                {'owner_id': 1, 'revision': 1, 'status': 1, 'started_at': 1,
                 'engine.facts': 1, 'courier_tariffs': 1, SNAPSHOT: 1}) or initial(owner)
            state = await prepare_in_thread(unpack_financial_snapshot, stored, selected={'facts'})
            await active_gate(scoped, owner, state)
            record = saved_record(state, payload['order_id'])
            if record and (record['order_number'], record['party_type'], record['party_id']) != (
                    payload['order_number'], payload['party_type'], payload['party_id']):
                raise ValueError('operational_delivery_confirmation_source_changed')
            event, source_hash, issues = None, None, []
            try:
                event, source_hash = await read_candidate(scoped, owner, state, payload, stamp, record=record)
            except (ValueError, TypeError, KeyError, ArithmeticError) as error:
                issues.append(_issue(error))
            public = public_record(state, record) if record else None
            result = {'can_confirm': bool(event and not record and not issues),
                      'confirmation_state': public['state'] if public else 'available' if event and not issues else 'unavailable',
                      'session_scope': digest([owner, actor]), 'period_start': state['started_at'],
                      'issues': issues, 'recorded_confirmation': public}
            # The current clock is only a validation bound. Advancing it cannot
            # stale an otherwise unchanged preview between the two HTTP requests.
            result['preview_hash'] = digest([state['revision'], source_hash, binding(event) if event else None, result])
            return state, result, event, source_hash, record


async def _preview(db, owner, actor, payload, stamp):
    try:
        return await asyncio.wait_for(_snapshot(db, owner, actor, payload, stamp), timeout=20)
    except asyncio.TimeoutError:
        fail('delivery_confirmation_snapshot_timeout', 'تعذرت معاينة الطلب في الوقت المتاح؛ أعد المحاولة', 503)
    except (PyMongoError, ArchiveError):
        fail('delivery_confirmation_snapshot_unavailable', 'تعذرت قراءة إثبات الطلب من لقطة متسقة', 503)
    except (ValueError, TypeError, KeyError, ArithmeticError) as error:
        issue = _issue(error)
        fail(issue['code'], issue['message'])


async def preview(db, owner, actor, payload, *, clock=None):
    _scope(owner, actor, payload)
    await _owner(db, owner, actor)
    state, result, _, _, _ = await _preview(db, owner, actor, payload, clock or now())
    await active_gate(db, owner, state)
    await _owner(db, owner, actor)
    return result


async def confirm(db, owner, actor, payload, *, clock=None):
    _scope(owner, actor, payload)
    await _owner(db, owner, actor)
    if payload.get('confirmed_after_start') is not True:
        fail('delivery_confirmation_required', 'يلزم تأكيد التوصيل بعد بداية التشغيل', 422)
    stamp, action = clock or now(), 'confirm_delivery_after_start:' + actor

    async def apply(state):
        user = await _owner(db, owner, actor)
        await active_gate(db, owner, state)
        old = replay(state, action, payload)
        if old is not None:
            saved_record(state, payload['order_id'])
            return old
        snapshot, result, event, source_hash, record = await _preview(db, owner, actor, payload, stamp)
        if snapshot['revision'] != state['revision']:
            fail('delivery_confirmation_preview_stale', 'تغيرت بيانات الطلب؛ أعد التأكيد من الصفحة')
        if record:
            # A second click/request cannot create another attestation or fee.
            return {'ok': True, 'pending_reconciliation': result['recorded_confirmation']['state'] != 'applied',
                    'confirmed_at': record['confirmed_at'],
                    'session_scope': result['session_scope'], 'order_id': record['order_id'],
                    'order_number': record['order_number'], 'recorded_confirmation': result['recorded_confirmation']}
        if not result['can_confirm'] or result['preview_hash'] != payload.get('expected_preview_hash'):
            fail('delivery_confirmation_preview_stale', 'تغيرت بيانات الطلب أو ظهرت أدلة متعارضة؛ أعد المعاينة')
        record = record_confirmation(state, event, actor=actor, actor_name=user.get('name') or user.get('full_name'),
                                     confirmed_at=stamp, source_hash=source_hash, preview_hash=result['preview_hash'])
        await _owner(db, owner, actor)
        await active_gate(db, owner, state)
        state.setdefault('engine', {}).setdefault('facts', {})[PREFIX + payload['order_id']] = record
        audit(state, actor, 'confirm_delivery_after_start', None, record,
              'تأكيد المالك بالتوصيل بعد بداية التشغيل دون تحديد تاريخ فعلي', 'owner_delivery_confirmation', stamp)
        response = {'ok': True, 'pending_reconciliation': True, 'session_scope': result['session_scope'],
                    'confirmed_at': record['confirmed_at'],
                    'order_id': record['order_id'], 'order_number': record['order_number'],
                    'recorded_confirmation': public_record(state, record)}
        remember(state, action, payload, response)
        return response

    return await mutate(db, owner, apply)
