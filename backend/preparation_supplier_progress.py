"""Supplier dispatch-day navigation and outstanding-file counts.

Reads only; the existing remaining-PDF route rechecks receipts at download.
"""
from preparation_progress_dates import progress_day
from preparation_section_reads import _nonterminal_numbers
from supplier_dispatch_pdf import _remaining_dispatch_pieces, _active_remaining_dispatch_pieces


async def _files(db, owner, employee, day=None):
    from preparation_supplier_dispatch import DISPATCHES, PIECES
    # Start with current employee custody, not all historical dispatch contents.
    # This query uses the existing owner/employee index; completed file members
    # are never loaded merely to build the date list.
    candidates = await db[PIECES].find({
        'user_id': owner, 'responsible_employee_id': employee,
        'status': {'$ne': 'cancelled'}, 'experiment_archived_at': None,
        'supplier_dispatch_status': {'$ne': 'received'},
        'supplier_dispatch_id': {'$nin': [None, '']}},
        {'_id': 0, 'supplier_dispatch_id': 1}).to_list(None)
    dispatch_ids = sorted({piece['supplier_dispatch_id'] for piece in candidates if piece.get('supplier_dispatch_id')})
    if not dispatch_ids:
        return [], {}
    rows = await db[DISPATCHES].find({
        'user_id': owner, 'sent_by_id': employee, 'id': {'$in': dispatch_ids}, 'status': {'$ne': 'building'}},
        {'_id': 0, 'id': 1, 'supplier_id': 1, 'supplier_name': 1,
         'supplier_file_number': 1, 'piece_ids': 1, 'piece_count': 1,
         'sent_at': 1, 'status': 1, 'source_file_numbers': 1,
         'source_files.file_number': 1, 'source_files.cards.piece_id': 1}).to_list(None)
    if day is not None:
        rows = [row for row in rows if progress_day(row.get('sent_at')) == day]
    for row in rows:
        row['piece_ids'] = sorted(set(row.get('piece_ids') or [
            card['piece_id'] for source in row.get('source_files') or []
            for card in source.get('cards') or [] if card.get('piece_id')]))
    ids = sorted({pid for row in rows for pid in row['piece_ids']})
    pieces = await db[PIECES].find({'user_id': owner, 'piece_id': {'$in': ids}},
        {'_id': 0, **{field: 1 for field in (
            'piece_id', 'batch_id', 'order_number', 'status', 'experiment_archived_at',
            'responsible_employee_id', 'supplier_id', 'supplier_dispatch_id',
            'supplier_dispatch_status', 'supplier_receiving_history')}}).to_list(None) if ids else []
    by_id = {piece['piece_id']: piece for piece in pieces}
    numbers = sorted({piece.get('order_number') for piece in pieces if piece.get('order_number')})
    eligible = await _nonterminal_numbers(db, owner, numbers) if numbers else set()
    active = []
    for row in rows:
        members = [by_id[pid] for pid in row['piece_ids'] if pid in by_id]
        current = [piece for piece in members
            if piece.get('responsible_employee_id') == employee
            and piece.get('supplier_id') == row.get('supplier_id')
            and piece.get('supplier_dispatch_id') == row.get('id')]
        remaining = await _active_remaining_dispatch_pieces(db, owner, row, current, eligible=eligible)
        if not remaining:
            continue
        total = max(len(row['piece_ids']), int(row.get('piece_count') or 0))
        row['progress'] = {'total': total, 'remaining': len(remaining),
            'received': len(members) - len(_remaining_dispatch_pieces(row, members)),
            'remaining_piece_ids': sorted(piece['piece_id'] for piece in remaining),
            'piece_identity_complete': len(members) == total}
        active.append(row)
    return active, by_id


async def supplier_progress_days(db, *, user_id, employee_id):
    rows, _ = await _files(db, user_id, employee_id)
    keys = sorted({progress_day(row.get('sent_at')) for row in rows}, reverse=True)
    keys.sort(key=lambda key: key == '__unknown__')
    return {'ok': True, 'employee_id': employee_id, 'read_only': True,
            'date_basis': 'supplier_dispatched_at', 'days': [{'key': key} for key in keys]}


async def supplier_progress_workspace(db, *, user_id, employee_id, day, after=''):
    from preparation_supplier_dispatch import _employee_workspace
    rows, pieces = await _files(db, user_id, employee_id, day)
    remaining = sorted({pid for row in rows for pid in row['progress']['remaining_piece_ids']})
    page = [pid for pid in remaining if pid > after][:11]
    page_ids = page[:10]
    batches = sorted({pieces[pid].get('batch_id') for pid in page_ids if pieces[pid].get('batch_id')})
    workspace = await _employee_workspace(db, user_id=user_id, employee_id=employee_id,
        limit=10, piece_grain=True, batch_ids=batches, piece_ids=page_ids, dispatch_rows=rows)
    accounts = {row['supplier_id']: row for row in workspace['supplier_accounts']}
    totals = {}
    for row in rows:
        sid = row['supplier_id']
        totals.setdefault(sid, set()).update(row['progress']['remaining_piece_ids'])
        accounts.setdefault(sid, {'supplier_id': sid, 'supplier_name': row.get('supplier_name') or 'مورد',
            'products': [], 'sent_quantity': 0, 'ready_quantity': 0, 'received_quantity': 0})
    for sid, account in accounts.items():
        account['dispatches'] = [row for row in rows if row['supplier_id'] == sid]
    workspace['supplier_accounts'] = list(accounts.values())
    total = sum(row['progress']['total'] for row in rows)
    received = sum(row['progress']['received'] for row in rows)
    return {'ok': True, 'read_only': True, **workspace, 'date_basis': 'supplier_dispatched_at',
        'next_cursor': page_ids[-1] if len(page) > 10 else None,
        'progress_active_piece_ids': remaining,
        'progress_supplier_totals': {sid: len(ids) for sid, ids in totals.items()},
        'progress_day_summary': {'key': day, 'assigned': total, 'ready': received,
            'remaining': len(remaining), 'waiting_for_supplier': 0, 'with_suppliers': len(remaining)}}
