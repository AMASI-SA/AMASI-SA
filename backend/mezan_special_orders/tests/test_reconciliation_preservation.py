"""Preserve the newer Production employee counters while enabling local sources."""
from copy import deepcopy
import preparation_supplier_dispatch as dispatch


def test_existing_employee_stage_summary_empty_is_preserved():
    result = dispatch.employee_workspace_stage_summary([])
    assert result == dispatch.employee_workspace_summary([], [])
    assert result['total_assigned_pieces'] == 0


def test_existing_employee_piece_counts_and_handoff_exclusion_are_preserved():
    rows = [
        {'piece_id': 'synthetic-a', 'batch_id': 'batch-a', 'order_number': 'SALLA-TEST',
         'status': dispatch.PIECE_STATUS_RECEIVED},
        {'piece_id': 'synthetic-b', 'batch_id': 'batch-b', 'order_number': 'MZ-'+'A'*32,
         'status': dispatch.PIECE_STATUS_RECEIVED, 'branch_handoff_at': '2026-09-28T00:00:00Z'},
    ]
    before = deepcopy(rows)
    actual = dispatch.employee_workspace_stage_summary(rows)
    files = [dispatch._file_view({}, [row], piece_grain=True, waiting_only=True) for row in rows]
    assert actual == dispatch.employee_workspace_summary(files, rows)
    assert actual['total_assigned_pieces'] == 1
    assert actual['received_pieces_awaiting_branch_handoff'] == 1
    assert rows == before
