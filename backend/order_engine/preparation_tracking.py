"""Order-page preparation read model. Companion for Mezan, NOT installed by mobile.

Only tenant/order-scoped reads; no materialization, indexes, status transitions,
webhooks, session reads or writes. Does not import operation installers.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Any, Callable
from fastapi import Depends, HTTPException, Response

PIECES = 'mezan_preparation_pieces_v1'
WORKFLOWS = 'order_review_workflows'
MAX_PIECES = 5000
PIECE_FIELDS = (
    'piece_id', 'order_item_id', 'unit_index', 'status', 'assignment_status',
    'responsible_employee_id', 'responsible_employee_name', 'supplier_id', 'supplier_name',
    'execution_status', 'supplier_dispatch_status', 'supplier_receiving_session_id',
    'preparation_receipt_status', 'assembly_status', 'active_hold_id',
    'experiment_archived_at', 'updated_at',
)
STAGES = {
    'assigned': 'مسند إلى موظف التجهيز — لم يبدأ',
    'preparing': 'قيد التجهيز لدى الموظف',
    'at_supplier': 'لدى المورد',
    'supplier_ready': 'جاهز لدى المورد — بانتظار الاستلام',
    'supplier_receiving': 'جارٍ استلامه من المورد',
    'remaining_services': 'بانتظار استكمال الخدمات',
    'supplier_received': 'تم الاستلام من المورد',
    'employee_receiving': 'جاهز للاستلام من موظف التجهيز',
    'assembly': 'في التجميع والعنونة',
    'ready': 'جاهز للشحن',
    'completed': 'تم التنفيذ — ضمن الشحن',
    'delivering': 'جاري التوصيل',
    'delivered': 'تم التوصيل',
    'cancelled': 'ملغي',
    'returned': 'مسترجع',
    'blocked': 'موقوف',
    'unassigned': 'بانتظار الإسناد',
    'unknown': 'المرحلة غير معروفة',
}

class TrackingShapeError(ValueError):
    pass


def text(value: Any) -> str:
    # Do not stringify objects or accept placeholder labels as people's names.
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ''


def model_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return value.model_dump(mode='json')


def person(piece: dict, prefix: str) -> dict | None:
    identifier, name = text(piece.get(prefix + '_id')), text(piece.get(prefix + '_name'))
    if name in {'—', '-', 'null', 'None'}:
        name = ''
    return {'id': identifier or None, 'name': name or None} if identifier or name else None


def resolve_stage(piece: dict, workflow: dict, order: dict) -> str:
    status, dispatch = text(piece.get('status')), text(piece.get('supplier_dispatch_status'))
    # Cancelled/held pieces must not be painted as delivered by an order-wide stage.
    native = text(order.get('status_native')) or text(order.get('status'))
    if status in {'cancelled', 'canceled'} or native.casefold() in {'cancelled', 'canceled', 'deleted', 'محذوف', 'ملغى', 'ملغي', 'تم الإلغاء', 'تم الالغاء'}:
        return 'cancelled'
    if native.casefold() in {'returned', 'refunded', 'restored', 'مسترجع', 'تم الاسترجاع'}:
        return 'returned'
    if piece.get('active_hold_id') or status == 'blocked':
        return 'blocked'
    final = text(workflow.get('stage'))
    if final in {'completed', 'delivering', 'delivered'}:
        return final
    if text(piece.get('assembly_status')) == 'ready':
        return 'ready'
    if status == 'ready_for_assembly' or text(piece.get('preparation_receipt_status')) == 'received':
        return 'assembly'
    if status == 'ready_for_employee_receipt':
        return 'employee_receiving'
    if text(piece.get('supplier_receiving_session_id')):
        return 'supplier_receiving'
    if dispatch == 'ready':
        return 'supplier_ready'
    if dispatch == 'sent':
        return 'at_supplier'
    if dispatch == 'partial_received' or text(piece.get('execution_status')) == 'awaiting_remaining_services':
        return 'remaining_services'
    if status == 'received' or dispatch == 'received':
        return 'supplier_received'
    if text(piece.get('assignment_status')) in {'unassigned', 'unassigned_after_rejection'}:
        return 'unassigned'
    if status == 'in_progress':
        return 'preparing'
    if status == 'assigned':
        return 'assigned'
    return 'unknown'


def _public_piece(piece: dict, workflow: dict, order: dict) -> dict:
    stage = resolve_stage(piece, workflow, order)
    assignment = text(piece.get('assignment_status'))
    employee = None if assignment in {'unassigned', 'unassigned_after_rejection'} else person(piece, 'responsible_employee')
    unit = piece.get('unit_index')
    return {
        'piece_id': text(piece.get('piece_id')),
        'unit_index': unit if type(unit) is int and unit > 0 else None,
        'stage': stage, 'stage_label': STAGES[stage],
        'employee': employee, 'supplier': person(piece, 'supplier'),
        'source': 'piece_registry',
    }


def build_tracking(order: dict, workflow: dict, rows: list[dict], *, truncated: bool, observed_at: str) -> dict:
    item_rows = order.get('items')
    if not isinstance(item_rows, list):
        raise TrackingShapeError('order_items_missing')
    items, seen_ids, seen_units = {}, set(), set()
    for row in item_rows:
        item_id = text(row.get('order_item_id'))
        quantity = row.get('quantity')
        if not item_id or item_id in items or not isinstance(quantity, (int, float)) or isinstance(quantity, bool) or not 0 < quantity <= 100000:
            raise TrackingShapeError('order_item_identity_or_quantity_invalid')
        items[item_id] = {'order_item_id': item_id, 'ordered_quantity': quantity, 'pieces': []}
    ignored = 0
    for row in rows:
        if row.get('experiment_archived_at') is not None:
            continue
        item_id, piece_id = text(row.get('order_item_id')), text(row.get('piece_id'))
        if item_id not in items:
            ignored += 1
            continue
        if not piece_id or piece_id in seen_ids:
            raise TrackingShapeError('duplicate_or_missing_piece_identity')
        unit = row.get('unit_index')
        if type(unit) is int and unit > 0:
            slot = (item_id, unit)
            if slot in seen_units:
                raise TrackingShapeError('conflicting_active_piece_units')
            seen_units.add(slot)
        seen_ids.add(piece_id)
        items[item_id]['pieces'].append(_public_piece(row, workflow, order))
    direct_seen = set()
    for row in workflow.get('items') or []:
        if not isinstance(row, dict) or text(row.get('preparation_route')) != 'direct_assembly':
            continue
        item_id = text(row.get('order_item_id'))
        if item_id not in items:
            continue
        if item_id in direct_seen:
            raise TrackingShapeError('duplicate_direct_assembly_item')
        direct_seen.add(item_id)
        if items[item_id]['pieces']:
            raise TrackingShapeError('physical_and_direct_routes_conflict')
        quantity = items[item_id]['ordered_quantity']
        if int(quantity) != quantity or quantity > MAX_PIECES or row.get('quantity') != quantity:
            raise TrackingShapeError('direct_assembly_quantity_changed')
        stored, ready = row.get('direct_assembly_piece_ids') or [], row.get('assembly_ready_piece_ids') or []
        if not isinstance(stored, list) or not isinstance(ready, list):
            raise TrackingShapeError('direct_assembly_ids_invalid')
        stored = [text(v).lower() for v in stored]
        ready = {text(v).lower() for v in ready}
        for index in range(1, int(quantity) + 1):
            if len(seen_ids) >= MAX_PIECES:
                truncated = True
                break
            piece_id = (text(stored[index-1]) if index <= len(stored) else
                        'direct_' + hashlib.sha256(f"{order['order_number']}:{item_id}:{index}".encode()).hexdigest()[:32])
            if not piece_id or piece_id in seen_ids:
                raise TrackingShapeError('direct_assembly_duplicate_piece')
            seen_ids.add(piece_id)
            piece = {'piece_id': piece_id, 'unit_index': index, 'status': 'ready_for_assembly',
                     'assembly_status': 'ready' if piece_id in ready else 'pending', 'active_hold_id': row.get('active_hold_id')}
            view = _public_piece(piece, workflow, order)
            view['source'] = 'direct_assembly'
            items[item_id]['pieces'].append(view)
    for item in items.values():
        count = len(item['pieces'])
        if count > item['ordered_quantity']:
            raise TrackingShapeError('piece_count_exceeds_order_quantity')
        item['coverage'] = ('partial' if truncated else 'complete' if count == item['ordered_quantity'] else 'no_records' if count == 0 else 'partial')
        item['pieces'].sort(key=lambda p: (p['unit_index'] or 10**9, p['piece_id']))
    return {'schema_version': 1, 'order_number': order['order_number'], 'observed_at': observed_at,
            'source': 'mezan_preparation_read_model', 'truncated': truncated,
            'unmapped_piece_count': ignored, 'items': list(items.values())}


async def read_preparation_tracking(db: Any, *, user_id: str, order: Any) -> dict:
    order = model_dict(order)
    number = text(order.get('order_number'))
    if not number or not text(user_id):
        raise TrackingShapeError('tenant_order_required')
    workflow = await db[WORKFLOWS].find_one(
        {'user_id': str(user_id), 'order_number': number},
        {'_id': 0, 'stage': 1, 'items.order_item_id': 1, 'items.quantity': 1,
         'items.preparation_route': 1, 'items.direct_assembly_piece_ids': 1,
         'items.assembly_ready_piece_ids': 1, 'items.active_hold_id': 1},
    ) or {}
    rows = await db[PIECES].find(
        {'user_id': str(user_id), 'order_number': number, 'experiment_archived_at': None},
        {'_id': 0, **{field: 1 for field in PIECE_FIELDS}},
    ).sort([('order_item_id', 1), ('unit_index', 1), ('piece_id', 1)]).limit(MAX_PIECES + 1).to_list(MAX_PIECES + 1)
    return build_tracking(order, workflow, rows[:MAX_PIECES], truncated=len(rows) > MAX_PIECES,
                          observed_at=datetime.now(timezone.utc).isoformat())


def install_preparation_tracking_read_route(router: Any, *, db: Any, current_user: Callable,
        require_owner: Callable, get_order: Callable, repository: Callable, not_found_error: type[Exception]) -> None:
    @router.get('/{order_number}/preparation-tracking')
    async def preparation_tracking(order_number: str, response: Response, user: dict = Depends(current_user)) -> dict:
        owner = require_owner(user)
        try:
            order = await get_order(repository(), user_id=str(owner['id']), order_number=order_number)
            result = await read_preparation_tracking(db, user_id=str(owner['id']), order=order)
        except not_found_error as exc:
            raise HTTPException(status_code=404, detail={'code': 'order_not_found'}) from exc
        except TrackingShapeError as exc:
            raise HTTPException(status_code=409, detail={'code': 'order_preparation_tracking_inconsistent',
                'message': 'تحتاج بيانات إسناد القطع إلى مراجعة؛ لا يمكن عرضها على أنها مكتملة.'}) from exc
        response.headers['Cache-Control'] = 'no-store'
        return result
