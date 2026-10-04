"""Dashboard opts in; canonical financial functions and writers remain intact."""
import ast
import asyncio
from datetime import date
from pathlib import Path
import subprocess

import pytest

from dashboard_order_reads import dashboard_order_read_scope, dashboard_spill
from dashboard_summary_fixture import BASE_SHA, REPOSITORY, extract_dashboard, seed_dashboard, financial_business_payload
from test_dashboard_summary_parity_mongo import database
from recurring_obligations_routes import INVOICES, OBLIGATIONS, compute_recurring_obligations_for_range
from dashboard_v2_routes import _dashboard_recurring_totals, _gather_dashboard_reads


@pytest.mark.asyncio
async def test_dashboard_settlement_wallet_totals_match_legacy(database):
    await seed_dashboard(database, count=17)
    for i in range(257):
        await database.payment_adjustments.insert_one({
            'user_id': 'owner', 'adjusted_at': '2026-09-15',
            'payment_method': ['mada', 'tamara', 'tabby', 'cash on delivery'][i % 4],
            'provider': 'salla' if i % 4 == 0 else 'other',
            'adjustment_amount': -.005 if i % 2 else 1.005,
            'original_amount': 123.456, 'new_amount': 98.765,
            'order_created_at': '2020-01-01', 'unused_evidence': 'x' * 1024,
        })
    kwargs = dict(user={'id': 'owner'}, from_date='2026-09-01', to_date='2026-09-30',
                  include_legacy_analyses=False, allow_self_heal=False)
    baseline = await extract_dashboard(database, revision=BASE_SHA)(**kwargs)
    async with dashboard_order_read_scope(bounded=True):
        current = await extract_dashboard(database)(**kwargs)
    assert financial_business_payload(current) == baseline
    assert current['totals']['settlements_by_provider']['salla']['count'] == 65
    assert current['totals']['salla_settlements_outside_14d'] != 0
    assert await database.payment_adjustments.count_documents({}) == 257


@pytest.mark.asyncio
async def test_router_recurring_opt_in_preserves_default_and_exact_totals(database):
    await database[OBLIGATIONS].insert_many([
        dict(id=str(i), user_id='owner', status='active', start_date='2026-01-01',
             cycle='monthly', period_amount=19.995, expense_type=kind,
             estimation_basis='last_3_invoices')
        for i, kind in enumerate(('rent', 'electricity', 'water'))
    ])
    await database[INVOICES].insert_many([
        dict(user_id='owner', obligation_id=str(i % 3), period_start='2026-08-01',
             period_end='2026-08-31', amount=31.005+i, evidence='x' * 1024)
        for i in range(257)
    ])
    args = (database, 'owner', date(2026, 9, 1), date(2026, 9, 30))
    baseline = await compute_recurring_obligations_for_range(*args)
    assert await _dashboard_recurring_totals(*args) == baseline
    async with dashboard_order_read_scope(bounded=True):
        actual = await _dashboard_recurring_totals(*args)
    assert actual == baseline
    # The bounded scope must not leak to the next default financial caller.
    assert await compute_recurring_obligations_for_range(*args) == baseline
    assert await database[INVOICES].count_documents({}) == 257


@pytest.mark.parametrize('path,allowed', [
    ('backend/settlements_routes.py', {'aggregate_settlements_by_provider'}),
    ('backend/recurring_obligations_routes.py', {'compute_recurring_obligations_for_range'}),
    ('backend/balances.py', {'compute_balances'}),
])
def test_all_other_financial_functions_are_identical_to_base(path, allowed):
    before = subprocess.run(['git', 'show', f'{BASE_SHA}:{path}'], cwd=REPOSITORY,
                            capture_output=True, encoding='utf-8', check=True).stdout
    current = (REPOSITORY / path).read_text(encoding='utf-8')
    def functions(source):
        return {node.name: ast.dump(node, include_attributes=False)
                for node in ast.parse(source).body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name not in allowed}
    assert functions(current) == functions(before)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [ValueError, asyncio.CancelledError])
async def test_failed_sibling_drains_readers_before_private_spool_cleanup(failure):
    started, finalized = asyncio.Event(), asyncio.Event()
    children = []
    async with dashboard_order_read_scope(bounded=True):
        store = dashboard_spill()
        rows = store.sequence('delayed-reader')
        async def delayed_cursor():
            children.append(asyncio.current_task())
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                # Cleanup is allowed to access request state until drained.
                rows.append({'cursor_closed': True})
                finalized.set()
        async def fail():
            await started.wait()
            raise failure('injected read failure')
        with pytest.raises(failure):
            await _gather_dashboard_reads(delayed_cursor(), fail())
        assert finalized.is_set()
        assert all(task.done() for task in children)
        assert list(rows) == [{'cursor_closed': True}]
        directory = store.directory
    assert not directory.exists()
