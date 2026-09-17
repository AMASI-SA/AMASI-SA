"""Order/item eligibility shared by revision clients; never inspects shipping.

The caller supplies authoritative order and preparation records, re-read just
before dispatch. A confirmation applies to a specific item revision, not to
an order or customer for all subsequent edits.
"""
from datetime import datetime, timezone

from order_revision_contracts import ContractRunnerError, canonical_digest


BLOCKED_STATES = frozenset({
    'completed', 'delivering', 'delivered',
    'تم_التنفيذ', 'جاري_التوصيل', 'جار_التوصيل', 'تم_التوصيل',
})
READY_STATES = frozenset({
    'ready', 'ready_for_employee_receipt', 'received', 'ready_for_assembly',
})
READY_WARNING = 'هذا المنتج جاهز بالفعل. هل أكد العميل طلب حذفه أو تعديله رغم اكتمال التجهيز؟'


def validate_order_state(order):
    status = order.get('status')
    values = [status.get('slug'), status.get('name')] if isinstance(status, dict) else [status]
    states = {str(x).strip().casefold().replace(' ', '_') for x in values if x}
    if not states:
        raise ContractRunnerError('order_state_unproven')
    if states & BLOCKED_STATES:
        raise ContractRunnerError('order_fulfillment_blocks_revision')


def item_revision_decision(order, *, method, item_id, preparation_records):
    validate_order_state(order)
    if method not in {'POST', 'PUT', 'DELETE'}:
        raise ContractRunnerError('invalid_revision_method')
    if method == 'POST':
        return {'confirmation_required': False, 'preparation_revision': None}
    if not item_id or not isinstance(preparation_records, list):
        raise ContractRunnerError('item_preparation_unproven')
    ready = []
    for record in preparation_records:
        if not isinstance(record, dict) or str(record.get('order_item_id')) != str(item_id):
            raise ContractRunnerError('item_preparation_identity_mismatch')
        if (record.get('status') in READY_STATES
                or record.get('preparation_status') == 'ready'
                or record.get('assembly_status') == 'ready'
                or record.get('assembly_ready_piece_ids')):
            ready.append(record)
    revision = canonical_digest(sorted(
        (canonical_digest(_digest_value(record)) for record in preparation_records)))
    result = {'confirmation_required': bool(ready), 'preparation_revision': revision}
    if ready:
        result['warning'] = READY_WARNING
    return result


def _digest_value(value):
    # Mongo returns datetime objects; use a canonical UTC representation.
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {k: _digest_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_digest_value(v) for v in value]
    return value


def validate_ready_confirmation(decision, confirmation, *, actor_id, item_id):
    if not decision['confirmation_required']:
        return None
    if (not isinstance(confirmation, dict)
            or set(confirmation) != {'customer_requested', 'item_id', 'preparation_revision', 'reason'}
            or confirmation.get('customer_requested') is not True
            or str(confirmation.get('item_id')) != str(item_id)
            or confirmation.get('preparation_revision') != decision['preparation_revision']
            or not isinstance(confirmation.get('reason'), str)
            or not 1 <= len(confirmation['reason'].strip()) <= 1000
            or not isinstance(actor_id, str) or not actor_id.strip()):
        raise ContractRunnerError('ready_item_customer_confirmation_required')
    return {**confirmation, 'actor_id': actor_id}


async def read_preparation(db, owner, order_number, item_id):
    """Read Mezan preparation only. Never call Salla shipping endpoints."""
    query = {'user_id': owner, 'order_number': str(order_number), 'order_item_id': str(item_id)}
    pieces = await db['mezan_preparation_pieces_v1'].find(query).to_list(length=1001)
    if len(pieces) > 1000:
        raise ContractRunnerError('item_preparation_unproven')
    records = []
    for piece in pieces:
        held = piece.get('active_hold_id')
        if piece.get('status') == 'cancelled' and not held:
            continue
        record = {k: piece.get(k) for k in (
            'order_item_id', 'piece_id', 'status', 'assembly_status', 'execution_status',
            'updated_at', 'assembly_ready_at', 'completed_at', 'active_hold_id')}
        if held:
            hold = await db['mezan_fulfillment_holds_v1'].find_one(
                {'user_id': owner, 'id': held, 'order_number': str(order_number), 'status': 'active'})
            before = next((x for x in (hold or {}).get('before_states', [])
                           if x.get('piece_id') == piece.get('piece_id')), None)
            if not before:
                raise ContractRunnerError('item_preparation_hold_unproven')
            record['status'] = before.get('status')
            record['execution_status'] = before.get('execution_status')
        records.append(record)
    workflow = await db['order_review_workflows'].find_one(
        {'user_id': owner, 'order_number': str(order_number)})
    if workflow:
        validate_order_state({'status': workflow.get('stage') or 'pending'})
        for group, key in (('items', 'order_item_id'), ('operational_items', 'source_order_item_id')):
            for row in workflow.get(group) or []:
                if str(row.get(key)) == str(item_id):
                    records.append({'order_item_id': str(item_id), 'source': group, **{
                        k: row.get(k) for k in ('preparation_status', 'assembly_status',
                            'assembly_ready_piece_ids', 'assembly_ready_at', 'updated_at')}})
    return records
