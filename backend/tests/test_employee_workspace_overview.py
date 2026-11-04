from unittest.mock import AsyncMock, MagicMock
import pytest
import preparation_supplier_dispatch as module

@pytest.mark.asyncio
async def test_overview_skips_detail_collections_and_refreshes_current_eligibility(monkeypatch):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[{'piece_id':'p1','batch_id':'b1','order_number':'1'}])
    pieces = MagicMock()
    pieces.find.return_value = cursor
    class DB:
        def __getitem__(self, name):
            assert name == module.PIECES, 'overview must not load images, registry, dispatch history or suppliers'
            return pieces
    annotation = AsyncMock(return_value=[])
    monkeypatch.setattr(module, 'annotate_waiting_pieces', annotation)
    for _ in range(2):
        result = await module._employee_workspace(DB(), user_id='owner', employee_id='employee', limit=0, piece_grain=True, overview_only=True)
        assert result['employee_id'] == 'employee'
        assert result['details_loaded'] is False
        assert result['files'] == result['supplier_accounts'] == result['suppliers'] == []
        assert result['summary']['waiting_review_pieces'] == 0
    assert annotation.await_count == 2
    assert pieces.find.call_args.args[0]['responsible_employee_id'] == 'employee'
    assert pieces.find.call_args.args[0]['user_id'] == 'owner'
