"""Synthetic read-only diagnostic; prints observed gaps, never connects to production.

Run from repository root with PYTHONPATH=backend:backend/tests python <this file>.
This is an investigation artifact, not a passing regression test for desired behavior.
"""
import json
from copy import deepcopy
from test_operational_open_cost_exposure import (
    BEFORE, START, native_order, seeded_db, operating_state, preview,
)
from operational_courier_tariffs import terms


def main():
    for old in (True, False):
        db = seeded_db(native_order(
            created=BEFORE if old else '2026-11-01T01:00:00+00:00',
            shipping={'company_code': 'aramex', 'status': 'pending'},
        ))
        state = operating_state()
        state.update(owner_id='owner', courier_tariffs=[{
            'id': 'aramex', 'name': 'Aramex', 'keys': ['aramex', 'Aramex'],
            'currency': 'SAR', 'terms': terms({'cost_per_order': 30, 'vat_percent': 15}),
            'payment_mode': 'prepaid', 'cash_account': {
                'id': 'synthetic-salla-cash', 'name': 'Synthetic cash',
                'currency': 'SAR', 'kind': 'cash',
            }, 'version_id': 'synthetic-approved-aramex', 'effective_from': START,
            'confirmed_by': 'owner', 'confirmed_at': START,
        }])
        before = deepcopy((db.data, state))
        result = preview(db, state)
        assert before == (db.data, state), 'Diagnostic must never change financial state'
        print(json.dumps({
            'created_before_start': old, 'issues': result['issues'],
            'shipping_complete': result['bucket_completeness']['shipping_expected'],
            'shipping_groups': [g for g in result['groups'] if g['kind'] != 'supplier'],
            'currencies': result['currencies'],
        }, ensure_ascii=False))


if __name__ == '__main__':
    main()
