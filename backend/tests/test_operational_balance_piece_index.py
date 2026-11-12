"""Preparation-piece indexing preserves the old matching and request lifetime."""
import asyncio
from copy import deepcopy
from threading import Event

from fastapi import HTTPException
import pytest

import operational_balance_sources as sources
from operational_demand_refresh import LEASES, run_visible_refresh
from operational_piece_index import index_preparation_pieces
from test_operational_balance_demand_refresh import DB as LeaseDB, Request, allowed
from test_operational_balance_sources import DB, NOW, START, order, row


def piece(identity, *, number='10', item='salla:10:line', unit=1, **fields):
    return row(id=identity, order_number=number, order_item_id=item, unit_index=unit, **fields)


def legacy_match(pieces, number, item):
    return [p for p in pieces if str(p.get('order_number')) == number
            and sources.physical_order_item_id(p) == item]


def test_index_preserves_exact_normalization_order_duplicates_and_input_identity():
    repeated = piece('duplicate', number=10, item='  salla:10:line  ')
    pieces = [piece('first'), repeated, piece('other', number=' 10'), repeated,
              piece('none', number=None, item=None), piece('list', number=['10'], item=['line']),
              piece('dict', number={'number': 10}, item={'id': 'line'}),
              piece('bool', number=True, item=' line '), piece('last', unit='2')]
    before = deepcopy(pieces)
    indexed = index_preparation_pieces(pieces, sources.physical_order_item_id)
    for number in ('10', ' 10', 'None', "['10']", "{'number': 10}", 'True', 'missing'):
        for item in ('salla:10:line', 'line', '', None):
            actual = indexed.get((number, item), [])
            expected = legacy_match(pieces, number, item)
            assert [id(p) for p in actual] == [id(p) for p in expected]
    assert pieces == before
    assert indexed[('10', 'salla:10:line')] == [pieces[0], repeated, repeated, pieces[-1]]


class LegacyLookup:
    def __init__(self, pieces):
        self.pieces = pieces

    def get(self, key, default):
        return legacy_match(self.pieces, *key)


@pytest.mark.parametrize('pieces', [
    [],
    [piece('one'), piece('two', unit=2)],
    [piece('one', number=10, item=' salla:10:line '), piece('two', unit='2')],
    [piece('same'), piece('same')],
    [piece('one'), piece('two', unit=3)],
    [piece('invalid-list', item=['salla:10:line']), piece('invalid-dict', item={'id': 'line'})],
    [piece('one'), piece('two', unit=2), piece('experiment', experiment_mode=True),
     piece('run', experiment_run_id='synthetic-run')],
    [piece('received', supplier_id='supplier', supplier_dispatch_status='received',
           execution_status='received_from_supplier', received_at=NOW), piece('pending', unit=2)],
    [piece('other-first', number='20', item='salla:20:line', unit=2), piece('one'),
     piece('other-second', number=20, item=' salla:20:line '), piece('two', unit=2)],
])
def test_actual_collector_matches_old_scan_including_partial_coverage(monkeypatch, pieces):
    async def scenario():
        data = {'unified_orders': [order(), order(id='200', reference_id='20')],
                'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')],
                'mezan_suppliers_v2': [row(id='supplier')],
                'mezan_preparation_pieces_v1': pieces}
        before = deepcopy(data)
        indexed = await sources.collect_sources(DB(data), 'owner', START, NOW)
        monkeypatch.setattr(sources, 'index_preparation_pieces', lambda rows, _: LegacyLookup(rows))
        legacy = await sources.collect_sources(DB(data), 'owner', START, NOW)
        assert indexed == legacy
        assert data == before
    asyncio.run(scenario())


def test_duplicate_units_remain_ambiguous_in_actual_collector():
    async def scenario():
        data = {'unified_orders': [order()],
                'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')],
                'mezan_preparation_pieces_v1': [piece('one'), piece('one')]}
        result = await sources.collect_sources(DB(data), 'owner', START, NOW)
        assert result['orders'][0]['items'] == []
        assert result['orders'][0]['items_complete'] is False
        assert any(issue['code'] == 'preparation_piece_coverage_ambiguous' for issue in result['issues'])
    asyncio.run(scenario())


def test_actual_collector_indexes_each_piece_once_and_looks_up_each_item(monkeypatch):
    async def scenario():
        counts = {'builds': 0, 'pieces': 0, 'lookups': 0}
        original = sources.index_preparation_pieces

        class Lookup(dict):
            def get(self, key, default):
                counts['lookups'] += 1
                return super().get(key, default)

        def build(rows, normalizer):
            counts['builds'] += 1
            def normalize(value):
                counts['pieces'] += 1
                return normalizer(value)
            return Lookup(original(rows, normalize))

        monkeypatch.setattr(sources, 'index_preparation_pieces', build)
        data = {'unified_orders': [order(id=str(i), reference_id=str(i)) for i in range(200)],
                'mezan_product_cost_profiles_v2': [row(salla_product_id='p1', base_cost='20')],
                'mezan_preparation_pieces_v1': [piece(str(i), number='unrelated') for i in range(2000)]}
        result = await sources.collect_sources(DB(data), 'owner', START, NOW)
        assert len(result['orders']) == 200
        assert counts == {'builds': 1, 'pieces': 2000, 'lookups': 200}
    asyncio.run(scenario())


def test_disconnect_during_actual_index_build_joins_before_return(monkeypatch):
    async def scenario():
        data = {'unified_orders': [order()],
                'mezan_preparation_pieces_v1': [piece(str(i)) for i in range(20000)]}
        before = deepcopy(data)
        source_db, lease_db, request = DB(data), LeaseDB(), Request()
        release, stopped = Event(), Event()
        loop = asyncio.get_running_loop()
        observed = 0
        original = sources.index_preparation_pieces

        class Database:
            def __getitem__(self, name):
                return lease_db[name] if name == LEASES else source_db[name]

        async def disconnect_and_release():
            request.disconnect()
            await asyncio.sleep(0)
            release.set()

        def build(rows, normalizer):
            def normalize(value):
                nonlocal observed
                observed += 1
                if observed == 10:
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(disconnect_and_release()))
                    assert release.wait(3), 'Synthetic disconnect listener did not progress'
                return normalizer(value)
            try:
                return original(rows, normalize)
            finally:
                stopped.set()

        monkeypatch.setattr(sources, 'index_preparation_pieces', build)
        try:
            with pytest.raises(HTTPException) as failure:
                await run_visible_refresh(request, Database(), 'owner', 'staff', allowed,
                    lambda: sources.collect_sources(source_db, 'owner', START, NOW))
            assert failure.value.status_code == 499
            assert observed == 10 and stopped.is_set()
            assert data == before and lease_db.data[LEASES] == {}
            assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        finally:
            release.set()
    asyncio.run(scenario())
