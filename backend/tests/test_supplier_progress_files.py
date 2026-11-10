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
    await db[dispatch.PIECES].update_many({'supplier_dispatch_status': 'sent'}, {'$set': {'experiment_archived_at': ''}})
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


@pytest.mark.asyncio
async def test_file_stays_on_dispatch_day_without_repeating_after_receipts(monkeypatch):
    db, original, pieces, _ = await seed(monkeypatch)
    # A second file for the same supplier on the next day must not repeat the first.
    second = deepcopy(original)
    second.update(id='next-file', sent_at='2026-11-11T09:00:00Z', piece_count=1,
                  piece_ids=['next-piece'], source_files=[{'cards': [{'piece_id': 'next-piece'}]}])
    piece = deepcopy(pieces[-1])
    piece.update(piece_id='next-piece', supplier_dispatch_id='next-file')
    await db[dispatch.PIECES].insert_one(piece)
    await db[dispatch.DISPATCHES].insert_one(second)
    for day, expected_id, count in [('2026-11-10', 'file', 5), ('2026-11-11', 'next-file', 1)]:
        result = await supplier_progress_workspace(db, user_id='owner', employee_id='worker', day=day)
        assert [r['id'] for a in result['supplier_accounts'] for r in a['dispatches']] == [expected_id]
        assert result['progress_supplier_totals'] == {'supplier': count}
    await db[dispatch.PIECES].update_many({'supplier_dispatch_id': 'file'}, {'$set': {'supplier_dispatch_status': 'received'}})
    assert (await supplier_progress_days(db, user_id='owner', employee_id='worker'))['days'] == [{'key': '2026-11-11'}]


@pytest.mark.asyncio
async def test_download_rechecks_receipts_and_passes_only_five_units_to_pdf_renderer(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    import supplier_dispatch_pdf as pdf
    db, file, _, _ = await seed(monkeypatch)
    await db[pdf.BATCHES].insert_one({'user_id': 'owner', 'id': 'batch',
        'lines': [{'order_item_id': 'item', 'product_name': 'Synthetic item'}]})
    await db[dispatch.PIECES].update_many({}, {'$set': {'order_item_id': 'item'}})
    monkeypatch.setattr(pdf, '_line_from_batch_storage', lambda row, batch: SimpleNamespace(quantity=row['quantity']))
    rendered = []
    def render(lines, **kwargs):
        rendered.append(lines)
        return b'%PDF-synthetic-renderer-boundary'
    monkeypatch.setattr(pdf, 'generate_amasi_product_file_pdf', render)
    await pdf.build_supplier_dispatch_pdf(db, user_id='owner', dispatch=file, remaining_only=True)
    assert len(rendered[-1]) == 5
    # A receipt after opening the screen must also be excluded on download.
    await db[dispatch.PIECES].update_one({'piece_id': 'p25'}, {'$set': {'supplier_dispatch_status': 'received'}})
    await pdf.build_supplier_dispatch_pdf(db, user_id='owner', dispatch=file, remaining_only=True)
    assert len(rendered[-1]) == 4
    await db[dispatch.PIECES].update_many({}, {'$set': {'supplier_dispatch_status': 'received'}})
    with pytest.raises(HTTPException) as error:
        await pdf.build_supplier_dispatch_pdf(db, user_id='owner', dispatch=file, remaining_only=True)
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'supplier_dispatch_fully_received'
    assert len(rendered) == 2


@pytest.mark.asyncio
async def test_progress_and_download_exclude_cancelled_reassigned_and_terminal_pieces(monkeypatch):
    from supplier_dispatch_pdf import _active_remaining_dispatch_pieces
    db, file, _, _ = await seed(monkeypatch)
    for pid, changes in [('p25', {'status': 'cancelled'}),
                         ('p26', {'supplier_dispatch_id': 'other-file'}),
                         ('p27', {'order_number': 'completed-order'}),
                         ('p28', {'supplier_dispatch_id': ''})]:
        await db[dispatch.PIECES].update_one({'piece_id': pid}, {'$set': changes})
    await db.unified_orders.insert_one({'user_id': 'owner', 'order_number': 'completed-order', 'order_status': 'completed'})
    result = await supplier_progress_workspace(db, user_id='owner', employee_id='worker', day='2026-11-10')
    assert result['progress_active_piece_ids'] == ['p29']
    pieces = await db[dispatch.PIECES].find({'user_id': 'owner'}).to_list(None)
    assert [p['piece_id'] for p in await _active_remaining_dispatch_pieces(db, 'owner', file, pieces)] == ['p29']
    progress = result['supplier_accounts'][0]['dispatches'][0]['progress']
    assert progress['received'] == 25, 'excluded pieces are not falsely marked received'


@pytest.mark.asyncio
async def test_completed_historical_files_are_not_loaded_for_date_navigation(monkeypatch):
    db, file, _, _ = await seed(monkeypatch)
    old = deepcopy(file)
    old.update(id='old-completed-file', piece_ids=['old-received-piece'], piece_count=1)
    await db[dispatch.DISPATCHES].insert_one(old)
    await db[dispatch.PIECES].insert_one({'user_id': 'owner', 'piece_id': 'old-received-piece',
        'responsible_employee_id': 'worker', 'supplier_dispatch_id': old['id'], 'supplier_dispatch_status': 'received'})
    calls = []
    collection = db[dispatch.DISPATCHES]
    original = collection.find
    def observe(query, *args, **kwargs):
        calls.append(query)
        return original(query, *args, **kwargs)
    monkeypatch.setattr(collection, 'find', observe)
    class ObservedDB:
        def __getitem__(self, name):
            return collection if name == dispatch.DISPATCHES else db[name]
        def __getattr__(self, name):
            return getattr(db, name)
    assert (await supplier_progress_days(ObservedDB(), user_id='owner', employee_id='worker'))['days'] == [{'key': '2026-11-10'}]
    assert calls[0]['id'] == {'$in': ['file']}
