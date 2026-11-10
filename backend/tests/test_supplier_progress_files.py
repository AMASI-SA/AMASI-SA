from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient
import preparation_supplier_dispatch as dispatch
from preparation_supplier_progress import supplier_progress_days, supplier_progress_workspace
from supplier_dispatch_pdf import _remaining_dispatch_pieces


async def seed(monkeypatch):
    db = AsyncMongoMockClient().supplier_progress
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': 'order', 'order_status': 'in_progress'})
    pieces = [{'user_id': 'owner', 'piece_id': f'p{i:02}', 'batch_id': 'batch',
        'order_number': 'order', 'responsible_employee_id': 'worker',
        'supplier_id': 'supplier', 'supplier_dispatch_id': 'file',
        'assigned_at': '2026-11-01T09:00:00Z', 'status': 'in_progress',
        'supplier_dispatch_status': 'sent'} for i in range(30)]
    for piece in pieces[:25]:
        piece.update(status='received', responsible_employee_id='receiving',
            supplier_dispatch_status='received', supplier_receiving_history=[{
                'supplier_id': 'supplier', 'received_at': '2026-11-11T09:00:00Z'}])
    await db[dispatch.PIECES].insert_many(deepcopy(pieces))
    file = {'user_id': 'owner', 'id': 'file', 'sent_by_id': 'worker',
        'supplier_id': 'supplier', 'supplier_name': 'Synthetic supplier',
        'sent_at': '2026-11-09T21:00:00Z', 'status': 'partial_received', 'piece_count': 30,
        'piece_ids': [p['piece_id'] for p in pieces],
        'source_files': [{'cards': [{'piece_id': p['piece_id']} for p in pieces]}]}
    await db[dispatch.DISPATCHES].insert_one(deepcopy(file))
    hydrate = AsyncMock(side_effect=lambda *args, **kwargs: {'employee_id': 'worker', 'files': [], 'supplier_accounts': []})
    monkeypatch.setattr(dispatch, '_employee_workspace', hydrate)
    return db, file, pieces, hydrate


@pytest.mark.asyncio
async def test_supplier_day_file_30_received_25_remaining_pdf_5(monkeypatch):
    db, file, pieces, hydrate = await seed(monkeypatch)
    days = await supplier_progress_days(db, user_id='owner', employee_id='worker')
    assert days['days'] == [{'key': '2026-11-10'}]
    result = await supplier_progress_workspace(db, user_id='owner', employee_id='worker', day='2026-11-10')
    account = result['supplier_accounts'][0]
    assert result['progress_supplier_totals'] == {'supplier': 5}
    assert len(account['dispatches']) == 1
    progress = account['dispatches'][0]['progress']
    assert (progress['total'], progress['received'], progress['remaining']) == (30, 25, 5)
    assert hydrate.call_args.kwargs['piece_ids'] == [f'p{i:02}' for i in range(25, 30)]
    assert [p['piece_id'] for p in _remaining_dispatch_pieces(file, pieces)] == progress['remaining_piece_ids']
    other = await supplier_progress_workspace(db, user_id='owner', employee_id='worker', day='2026-11-01')
    assert other['supplier_accounts'] == []
    assert (await supplier_progress_days(db, user_id='other', employee_id='worker'))['days'] == []
    assert (await supplier_progress_days(db, user_id='owner', employee_id='other'))['days'] == []


@pytest.mark.asyncio
async def test_fully_received_file_disappears_from_days_and_accounts(monkeypatch):
    db, _, _, _ = await seed(monkeypatch)
    await db[dispatch.PIECES].update_many({}, {'$set': {'supplier_dispatch_status': 'received'}})
    assert (await supplier_progress_days(db, user_id='owner', employee_id='worker'))['days'] == []
    result = await supplier_progress_workspace(db, user_id='owner', employee_id='worker', day='2026-11-10')
    assert result['supplier_accounts'] == [] and result['progress_active_piece_ids'] == []
