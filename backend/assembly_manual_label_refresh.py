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
from order_engine import shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING
from fulfillment_carrier_label import _workflow_patch


def _same_material_snapshot(before, current):
    # Salla's webhook may publish our acknowledged completed status during the
    # label read. Allow that expected transition, never cancellation or changed
    # products, custody, shipping identity, address or component refresh flags.
    if not current or canonical_status({**current['source'], 'raw_by_source': {'salla_direct': current['raw']}}) not in {'in_progress', 'completed'}:
        return False
    same_store_courier = (
        shipping._internal_carrier(before['source'])
        and shipping._internal_carrier(current['source'])
        and shipping._store_label_carriers_match(
            shipping._mobile_label_carrier(before['source']),
            shipping._mobile_label_carrier(current['source'])))
    def material(snapshot):
        value = deepcopy(snapshot)
        for key in ('order_status', 'order_status_slug'):
            value['source'].pop(key, None)
        for key in ('status', 'status_slug'):
            value['raw'].pop(key, None)
        state = value['source'].get('g47_salla_snapshot') or {}
        value['source']['g47_salla_snapshot'] = {
            key: bool(state.get(key)) for key in ('component_pending', 'requires_authoritative_refresh')}
        # Our completed POST can publish a newer observation of the same
        # carrier for external labels too. Observation clocks/provenance are
        # not document contents. Keep carrier identity, shipment facts, address,
        # products and custody exact; external shipment freshness stays fenced.
        for metadata in (value['source'].get(CURRENT_SHIPPING), value.get('shipping')):
            if isinstance(metadata, dict):
                for key in ('provider_updated_at', 'carrier_updated_at',
                            'event_name', 'source_kind', 'source_path',
                            'verified_order_completed_transition'):
                    metadata.pop(key, None)
                if same_store_courier:
                    metadata.pop('shipment_updated_at', None)
        return value
    return material(before) == material(current)


def _response(result, number):
    return {**result, 'order_number': number, 'assembly_completion_confirmed': True,
            'contract_version': 1, 'print_state': 'ready' if result.get('ready') else 'pending'}


async def _read_during_delivery(db, owner, number, authorize, before):
    # A completion worker can still own its lease after Salla has completed and
    # issued the AWB. Its write lease must not veto an independent current read.
    # A concurrent explicit action remains single-flight. Never take, clear or
    # extend either lease and never repeat a provider status/shipment write here.
    instant = datetime.now(timezone.utc).isoformat()
    workflow = await db.order_review_workflows.find_one({
        'user_id': owner, 'order_number': number, 'assembly_status': 'completed',
        'assembly_delivery': {'$exists': True},
        '$or': [{'manual_label_refresh.until': {'$exists': False}},
                {'manual_label_refresh.until': {'$lte': instant}}],
    }, {'_id': 0, 'assembly_status': 1})
    if not workflow:
        return None
    await authorize()
    try:
        result = await asyncio.wait_for(carrier_label(db, owner, number,
            issue=False, completed_only=True), timeout=90)
    except shipping.ShippingLabelError as exc:
        if exc.code == 'carrier_order_in_progress':
            return None
        raise
    await authorize()
    if not _same_material_snapshot(before, await _capture(db, owner, number)):
        raise HTTPException(409, detail={'code': 'shipping_snapshot_changed'})
    # Return fresh evidence to this request. Publishing into the worker's
    # workflow would race its result/error bookkeeping, so leave it untouched.
    return _response(result, number)


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
        observed = await _read_during_delivery(db, owner, number, authorize, before)
        if observed is not None:
            return observed
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
        return _response(result, number)
    finally:
        await db.order_review_workflows.update_one({**selector, 'manual_label_refresh.token': token},
            {'$unset': {'manual_label_refresh': ''}})
