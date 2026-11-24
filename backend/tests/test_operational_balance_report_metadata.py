"""Owner diagnostic receipts cannot affect financial reports or leak through them."""
from copy import deepcopy

from operational_balance_service import report
from operational_balance_store import initial


def test_financial_report_excludes_changing_private_diagnostics_without_mutating_saved_state():
    state = initial('synthetic-owner')
    state['engine']['as_of'] = '2026-11-24T00:00:00+00:00'
    expected = report(state)
    for revision in (2, 3):
        state['engine']['automatic_source_coverage'] = {
            'owner_id': 'synthetic-owner', 'calculation_revision': revision, 'rows': []}
        state['engine']['recovery_proof_v1'] = {
            'owner_id': 'synthetic-owner', 'calculation_revision': revision,
            'new_obligation_count': 1 if revision == 2 else 0}
        saved = deepcopy(state)
        actual = report(state)
        assert actual == expected
        assert 'automatic_source_coverage' not in actual['details']
        assert 'recovery_proof_v1' not in actual['details']
        assert state == saved
