"""Explicit completion-and-label action, separate from ordinary print reads."""
import asyncio
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from pymongo import ReturnDocument
from assembly_print_completion import _capture
from assembly_status_policy import canonical_status
from order_engine.amasi_shipping_compat import carrier_label
from fulfillment_carrier_label import _workflow_patch


def _same_material_snapshot(before, current):
    # Salla's webhook may publish our acknowledged completed status during the
    # label read. Allow that expected transition, never cancellation or changed
    # products, custody, shipping identity, address or component refresh flags.
    if not current or canonical_status({**current['source'], 'raw_by_source': {'salla_direct': current['raw']}}) not in {'in_progress', 'completed'}:
        return False
    def material(snapshot):
        value = deepcopy(snapshot)
        for key in ('order_status', 'order_status_slug'):
            value['source'].pop(key, None)
        for key in ('status', 'status_slug'):
            value['raw'].pop(key, None)
        state = value['source'].get('g47_salla_snapshot') or {}
        value['source']['g47_salla_snapshot'] = {
            key: bool(state.get(key)) for key in ('component_pending', 'requires_authoritative_refresh')}
        return value
    return material(before) == material(current)


async def complete_and_refresh(db, owner, number, authorize):
    await authorize()
    before = await _capture(db, owner, number)
    if not before or canonical_status({**before['source'], 'raw_by_source': {'salla_direct': before['raw']}}) not in {'in_progress', 'completed'}:
        raise HTTPException(409, detail={'code': 'assembly_completion_required',
            'message': 'أكمل جاهز لجميع القطع وحدّث الطلب قبل جلب البوليصة.'})
    instant = datetime.now(timezone.utc)
    until = (instant + timedelta(seconds=120)).isoformat()
    token = uuid.uuid4().hex
    selector = {'user_id': owner, 'order_number': number, 'assembly_status': 'completed'}
    claim = await db.order_review_workflows.find_one_and_update({**selector,
        '$and': [
            {'$or': [{'manual_label_refresh.until': {'$exists': False}}, {'manual_label_refresh.until': {'$lte': instant.isoformat()}}]},
            {'$or': [{'assembly_delivery.lease_until': {'$exists': False}}, {'assembly_delivery.lease_until': {'$lte': instant.isoformat()}}]},
        ]}, {'$set': {'manual_label_refresh': {'token': token, 'until': until}}}, return_document=ReturnDocument.AFTER)
    if not claim:
        raise HTTPException(409, detail={'code': 'operation_in_progress', 'message': 'تحديث البوليصة جارٍ؛ انتظر اكتماله.'})
    try:
        await authorize()
        # Recheck actual current local coverage after acquiring the order lease.
        current = await _capture(db, owner, number)
        if not _same_material_snapshot(before, current):
            raise HTTPException(409, detail={'code': 'shipping_snapshot_changed'})
        result = await asyncio.wait_for(carrier_label(db, owner, number, issue=False,
            completed_only=True, explicit_complete=True), timeout=90)
        await authorize()
        if not _same_material_snapshot(before, await _capture(db, owner, number)):
            raise HTTPException(409, detail={'code': 'shipping_snapshot_changed'})
        saved = await db.order_review_workflows.update_one(
            {**selector, 'manual_label_refresh.token': token}, {'$set': _workflow_patch(result, now=datetime.now(timezone.utc).isoformat())})
        if saved.matched_count != 1:
            raise HTTPException(409, detail={'code': 'shipping_snapshot_changed'})
        return {**result, 'order_number': number, 'assembly_completion_confirmed': True,
                'contract_version': 1, 'print_state': 'ready' if result.get('ready') else 'pending'}
    finally:
        await db.order_review_workflows.update_one({**selector, 'manual_label_refresh.token': token},
            {'$unset': {'manual_label_refresh': ''}})
