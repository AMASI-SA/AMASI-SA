"""Both mapping fixes together, using actual source paths and sealed test Mongo.

No production credentials, existing databases, or provider/bank API is used.
"""
from copy import deepcopy
from decimal import Decimal
import pytest

import test_operational_balance_bundle_legacy_receipts as legacy
import test_bundle_reporting_financial as financial
from test_operational_balance_native_purchase_link_mongo import seed, choice, approval, native_contents
from test_operational_balance_integration import run
from operational_balance_store import read
from operational_balance_sources import collect_sources
from operational_balance_service import refresh, report
from operational_open_cost_exposure import project
import operational_native_purchase_link_service as links
from operational_bundle_cost_projection import _leaf_key
from bundle_fulfillment_v2 import identity
from stock_component_consumption_service import PLANS, UNITS, RECEIPTS, selection_tokens


async def native_rose_receipts(db, item, snapshot):
    await db[PLANS].insert_one({'_id': 'plan', 'user_id': 'owner', 'order_id': 'ORDER',
        'bundle_snapshots': {legacy.LINE: deepcopy(snapshot)}, 'lines': [deepcopy(item)]})
    rose = next(l for l in snapshot['leaves'] if l.get('product_id') == financial.ROSE)
    assert rose['sourcing_mode'] == 'supplier' and rose['supplier_id'] == 'supplier'
    await db[RECEIPTS].insert_one({'id': 'rose-lot', 'user_id': 'owner', 'status': 'posted',
        'source_type': 'purchase_invoice', 'source_id': 'native', 'purchase_invoice_id': 'native',
        'supplier_id': 'supplier', 'posted_at': '2026-11-02T10:00:00Z', 'quantity': '2', **identity(rose)})
    for i in range(1, 5):
        leaves = deepcopy(snapshot['leaves'])
        allocations = []
        for leaf in leaves:
            leaf.update(state='awaiting_supplier', reserved_quantity='0', received_quantity='0',
                        consumed_quantity='0', receipt_ids=[])
            if leaf.get('product_id') == financial.ROSE and i <= 2:
                leaf.update(reserved_quantity='1', received_quantity='1', receipt_ids=['rose-lot'])
                allocations.append({'id': 'allocation' + str(i), 'bundle_leaf_id': _leaf_key(leaf),
                    'quantity': '1', 'lot_id': 'rose-lot', 'location_id': 'test-location',
                    'inventory_identity': identity(leaf)})
        await db[UNITS].insert_one({'_id': 'unit' + str(i), 'user_id': 'owner', 'plan_id': 'plan',
            'order_id': 'ORDER', 'order_line_id': legacy.LINE, 'unit_index': i, 'state': 'reserved',
            'product_id': financial.ROOT, 'created_at': legacy.CREATED, 'variant_id': None,
            'selected_context': [list(v) for v in selection_tokens(item)],
            'bundle_leaves': leaves, 'allocations': allocations})


@pytest.mark.parametrize('schema', [1, 2])
def test_receipt_then_explicit_native_matching_preserves_supplier_and_bank_once(monkeypatch, schema):
    monkeypatch.setattr(financial, 'OWNER', 'owner')
    monkeypatch.setattr(legacy, 'OWNER', 'owner')
    async def test(db):
        original = await seed(db)
        _, item, snapshot = await legacy.fixture(db, monkeypatch, schema=schema, rose_supplier=True)
        await legacy.pieces(db, received=4, completed=4)
        await native_rose_receipts(db, item, snapshot)
        native = await native_contents(db)
        before = await refresh(db, 'owner', clock=legacy.NOW)
        sources = await collect_sources(db, 'owner', before['started_at'], legacy.NOW, operational_state=before)
        assert any(p['code'] == 'bundle_received_purchase_financial_matching_required' for p in sources['issues'])
        prior = await project(db, 'owner', before, as_of=legacy.NOW)
        assert 'SAR' in prior['currencies'], (prior, sources)
        assert prior['currencies']['SAR']['poststart_supplier_unreceived'] == '80.00', prior
        proposal = await links.preview(db, 'owner', 'owner', choice(), clock=legacy.NOW)
        assert proposal['can_approve'], proposal
        assert before == await read(db, 'owner') and native == await native_contents(db)
        payload = approval(proposal)
        result = await links.approve(db, 'owner', 'owner', payload, clock=legacy.NOW)
        assert result == await links.approve(db, 'owner', 'owner', payload, clock=legacy.NOW)
        first = await refresh(db, 'owner', clock=legacy.NOW)
        second = await refresh(db, 'owner', clock=legacy.NOW)
        assert first['openings'] == second['openings'] == original['openings']
        assert first['movements'] == second['movements'] and len(first['movements']) == 1
        parties = report(second)['parties']
        assert next(p for p in parties if p['party_type'] == 'bank')['actual'] == '975.00'
        assert next(p for p in parties if p['party_id'] == 'supplier')['outstanding_payable'] == '55.00'
        sources = await collect_sources(db, 'owner', second['started_at'], legacy.NOW, operational_state=second)
        assert not any('bundle_' in p['code'] for p in sources['issues']), sources['issues']
        exposed = await project(db, 'owner', second, as_of=legacy.NOW)
        assert exposed['currencies']['SAR']['poststart_supplier_unreceived'] == '80.00', exposed
        assert exposed['currencies']['SAR']['poststart_supplier_received_uninvoiced'] == '100.00', exposed
        assert native == await native_contents(db)
        # A later source proof failure re-opens the warning, not the old money.
        await db.accounting_audit_log_v2.delete_one({'user_id': 'owner'})
        saved = await read(db, 'owner')
        sources = await collect_sources(db, 'owner', saved['started_at'], legacy.NOW, operational_state=saved)
        assert any(p['code'] == 'bundle_received_purchase_financial_matching_required' for p in sources['issues'])
        assert saved == await read(db, 'owner')
    run(test)


@pytest.mark.parametrize('count', [1, 80])
def test_financial_receipt_source_is_batched_and_owner_scoped(monkeypatch, count):
    import test_operational_balance_remaining_products as physical
    monkeypatch.setattr(financial, 'OWNER', 'owner')
    monkeypatch.setattr(legacy, 'OWNER', 'owner')
    monkeypatch.setattr(physical, 'OWNER', 'owner')
    class FinancialReadAudit(physical.ReadAudit):
        ALLOWED = physical.ReadAudit.ALLOWED | {'mezan_preparation_pieces_v1'}
    async def test(db):
        raw, _, _ = await legacy.fixture(db, monkeypatch)
        original = await db.mezan_component_order_lifecycle_v1.find_one({'user_id': 'owner'})
        orders = [raw]
        for n in range(1, count):
            number = 'ORDER-' + str(n)
            line = 'salla:' + number + ':LINE'
            order = deepcopy(raw)
            order.update(id='order-id-' + str(n), reference_id=number)
            orders.append(order)
            copied = deepcopy(original)
            copied.pop('_id')
            copied['order_number'] = number
            for field in ('reporting_cost_snapshots', 'bundle_intake_snapshots'):
                copied[field] = {line: value for _, value in copied[field].items()}
            await db.mezan_component_order_lifecycle_v1.insert_one(copied)
        audit = FinancialReadAudit(db)
        output = await legacy.load_remaining_bundles(audit, 'owner', orders, legacy.NOW)
        assert len(output) == count and all(v['complete'] for v in output.values())
        assert sum(Decimal(i['cost']) for v in output.values() for i in v['items']) == Decimal(260 * count)
        assert len(audit.reads) == 8
        assert sum(name == 'mezan_preparation_pieces_v1' for name, _ in audit.reads) == 1
    run(test)
