"""Real canonical-reader binding for the frozen fixture; in-memory DB only."""
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from mongomock_motor import AsyncMongoMockClient
import pytest

import legacy_dashboard_reader as canonical
from dashboard_runtime_gate_plugin import _runtime_legacy_adapter


def extracted_adapter(database):
    # Match the frozen loader's server-startup exclusion: imports are deliberately
    # absent, while the exact current dashboard wrapper body remains untouched.
    source = Path(canonical.__file__).with_name('server.py').read_text(encoding='utf-8')
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == 'dashboard')
    namespace = {'db': database}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'server.py:fixture-adapter', 'exec'), namespace)
    return namespace['dashboard']


@pytest.mark.asyncio
async def test_runtime_adapter_calls_real_canonical_reader_with_bound_tenant_and_parser():
    db = AsyncMongoMockClient()['runtime_adapter_fixture']
    await db.unified_orders.insert_many([
        {'user_id': owner, 'order_number': str(number), 'order_date': '2026-10-05',
         'order_status': 'completed', 'payment_method': 'mada', 'shipping_company': 'Fixture carrier',
         'currency': 'SAR', 'total_amount': amount, 'total_amount_sar': amount,
         'total_product_cost': 0, 'products': []}
        for owner, number, amount in [('fixture-owner', 1, 100), ('other-owner', 2, 999)]
    ])
    options = {'user': {'id': 'fixture-owner', 'role': 'owner'},
               'from_date': '2026-10-01', 'to_date': '2026-10-08',
               'include_legacy_analyses': False, 'allow_self_heal': False, 'persist_settings': False}
    expected = await canonical.dashboard(database=db, **options)
    handler = extracted_adapter(db)
    original_parser = canonical.orders_to_parsed_cooperative
    parsed = []

    async def observed_parser(orders):
        parsed.append([row['order_number'] for row in orders])
        return await original_parser(orders)

    with _runtime_legacy_adapter(handler, observed_parser) as adapted:
        assert adapted is handler
        assert handler.__globals__['_canonical_dashboard'] is canonical.dashboard
        actual = await adapted(**options)
    assert json.dumps(actual, ensure_ascii=False, default=str) == json.dumps(expected, ensure_ascii=False, default=str)
    assert actual['totals']['total_orders'] == 1
    assert actual['totals']['total_sales'] == 100
    assert parsed and all(numbers == ['1'] for numbers in parsed)
    assert canonical.orders_to_parsed_cooperative is original_parser
    assert '_canonical_dashboard' not in handler.__globals__
    assert await db.settings.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
async def test_runtime_adapter_propagates_database_failure_and_restores_bindings(failure):
    async def fail_read(*args, **kwargs):
        raise failure('synthetic database read interrupted')

    handler = extracted_adapter(SimpleNamespace(settings=SimpleNamespace(find_one=fail_read)))
    original_parser = canonical.orders_to_parsed_cooperative

    async def observed_parser(orders):
        return await original_parser(orders)

    with pytest.raises(failure):
        with _runtime_legacy_adapter(handler, observed_parser):
            await handler(user={'id': 'fixture-owner'}, persist_settings=False)
    assert canonical.orders_to_parsed_cooperative is original_parser
    assert '_canonical_dashboard' not in handler.__globals__
