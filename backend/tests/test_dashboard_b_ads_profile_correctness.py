"""Advertising timer equivalence only; existing parser/cost experiments fixed."""
import asyncio
import copy
import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import dashboard_b_computation_experiment as computation
from dashboard_v2_ads_executive import build_salla_ads_executive_breakdown
from dashboard_b_ads_instrumentation import Recorder, instrumented
from dashboard_b_ads_profile_experiment import candidate
from dashboard_b_equivalence_fixtures import CASES, DEFAULT_KWARGS, OWNER, seed_mixed
from test_dashboard_b_baseline_mongo import populate
from test_dashboard_b_concurrency_gate import CASES as MUTATIONS, Gate, GatedDB
from test_dashboard_b_request_scope_mongo import isolated, invoke


def encoded(value):
    # Retain dictionary insertion order as well as list order in this gate.
    return json.dumps(value, ensure_ascii=False, default=str, separators=(',', ':')).encode('utf8')


def advertising():
    return {'breakdown': {'snapchat': 100.005, 'tiktok': 0, 'meta': 12.015, 'google_transitional': 0},
            'providers': {'snapchat': {'orders': 2.5, 'revenue': 999999, 'data_state': 'complete'},
                          'tiktok': {'orders': 0}, 'meta': {'orders': 3.5}}}


def fixture(case):
    ads = advertising()
    rows = [{'order_number': 'same', 'currency': 'SAR', 'total_amount': 10.015, 'source': 'snapchat'}]
    if case == 'nested_attribution':
        rows = [
            {'source': 'store', 'raw_by_source': {'salla_direct': {'source_details': {'utm_source': 'instagram'}}}},
            {'source': {'channel': 'Tik Tok'}},
            {'source': 'snapchat', 'utm_source': 'facebook'},
            {'traffic_source': ['unrecognized', {'value': 'Google Ads'}]},
            {'source': {'source': {'name': 'سناب شات'}}},
            {'marketing': {'tracking': {'metadata': {'utm_source': 'tiktok'}}}},
            {'utm_source': 'make', 'raw_by_source': {'salla_direct': {'utm_source': 'قوقل'}}},
            {'source': ('store', {'platform': 'meta'})},
        ]
        for i, row in enumerate(rows):
            row.update(order_number=str(i), currency='SAR', total_amount=100)
    elif case == 'duplicates':
        rows = [copy.deepcopy(rows[0]) for _ in range(7)]
    elif case == 'mixed_fx':
        rows += [{'order_number': 'qar', 'source': 'snapchat', 'currency': 'QAR', 'total_amount': 302.20,
                  'total_amount_sar': 311.01, 'accounting_currency': 'SAR', 'currency_conversion_status': 'verified'},
                 {'order_number': 'unknown', 'source': 'store', 'currency': 'KWD', 'total_amount': 28.84}]
    elif case == 'unknown_over_100':
        rows = [{'order_number': str(i % 9), 'source': 'store' if i % 2 else 'snapchat',
                 'currency': 'KWD', 'total_amount': 28.84} for i in range(125)]
    elif case == 'refunds':
        rows = [dict(rows[0], order_status=status, actual_refund_amount=100,
                     actual_partial_refund_amount=5) for status in ('completed', 'cancelled', 'refunded')]
    elif case == 'google_spend':
        ads['breakdown']['google_transitional'] = 50
        ads['providers']['google'] = {'orders': 9999, 'revenue': 9999}
    elif case == 'rounding':
        rows = [dict(rows[0], source=source, total_amount=value)
                for source in ('snapchat', 'meta', 'tiktok', 'google')
                for value in (.005, .015, 100000000.01, .1, .2)]
    elif case == 'empty':
        rows = []
    return rows, ads


@pytest.mark.parametrize('fine', [False, True], ids=['coarse', 'fine'])
@pytest.mark.parametrize('case', ['nested_attribution', 'duplicates', 'mixed_fx', 'unknown_over_100',
                                  'refunds', 'google_spend', 'rounding', 'empty'])
def test_ads_profile_exact_bytes_and_input_immutability(fine, case):
    rows, ads = fixture(case)
    before = encoded((rows, ads))
    expected = build_salla_ads_executive_breakdown(rows, ads)
    actual = instrumented(Recorder(), fine=fine)(rows, ads)
    assert encoded(actual) == encoded(expected)
    assert encoded((rows, ads)) == before
    assert list(actual['providers']) == ['snapchat', 'tiktok', 'meta', 'google']
    if case == 'duplicates':
        assert actual['providers']['snapchat']['salla_orders'] == 7
    if case == 'unknown_over_100':
        assert actual['coverage']['unverified_currency_orders'] == 125
        assert actual['coverage']['unverified_currency_order_numbers'] == [str(i % 9) for i in range(100)]
    if case == 'mixed_fx':
        assert actual['providers']['snapchat']['sales_currency_conversion_complete'] is True
        assert actual['total']['salla_sales_sar'] is None
    if case == 'google_spend':
        assert actual['providers']['google']['platform_reported_orders'] is None
        assert actual['total']['platform_cost_per_order_sar'] is None


@pytest.mark.parametrize('fine', [False, True])
@pytest.mark.parametrize('value', [None, True, -1, 'invalid', float('nan'), float('inf'), 0, .005, 2.5])
def test_invalid_spend_platform_count_and_rounding(fine, value):
    rows, ads = fixture('rounding')
    ads['breakdown']['snapchat'] = value
    ads['providers']['meta']['orders'] = value
    before = encoded((rows, ads))
    assert encoded(instrumented(Recorder(), fine=fine)(rows, ads)) == encoded(build_salla_ads_executive_breakdown(rows, ads))
    assert encoded((rows, ads)) == before


@pytest.mark.parametrize('fine', [False, True])
@pytest.mark.parametrize('bad', ['order_none', 'order_scalar', 'ads_none', 'breakdown_list', 'provider_scalar'])
def test_ads_profile_exception_parity(fine, bad):
    rows, ads = fixture('empty')
    if bad == 'order_none': rows = [None]
    elif bad == 'order_scalar': rows = [7]
    elif bad == 'ads_none': ads = None
    elif bad == 'breakdown_list': ads['breakdown'] = [1]
    else: ads['providers']['snapchat'] = 7
    with pytest.raises(Exception) as reference:
        build_salla_ads_executive_breakdown(rows, ads)
    with pytest.raises(type(reference.value)) as actual:
        instrumented(Recorder(), fine=fine)(rows, ads)
    assert str(actual.value) == str(reference.value)


@contextmanager
def configured(mode):
    def factory(db, scope):
        return candidate(db, scope, mode=mode)
    with patch.object(computation, 'candidate', factory):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda row: row['name'])
async def test_dashboard_profile_mixed_permissions_and_tenants(case):
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        if case['settings']:
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': case['settings']})
        results = []
        for mode in ('current', 'coarse', 'fine'):
            with configured(mode):
                response, metrics = await invoke(db, commands, 'computation', case['kwargs'], case['user'])
            results.append((response, metrics))
        for response, metrics in results[1:]:
            assert encoded(response) == encoded(results[0][0])
            assert metrics['mongo_commands'] == results[0][1]['mongo_commands']
            assert metrics['documents_read'] == results[0][1]['documents_read']
        if case['name'] == 'permission_denied':
            assert all(r['http_error'] == 403 and not m['mongo_commands'] for r, m in results)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', MUTATIONS, ids=lambda row: row[0])
async def test_ads_profile_existing_independent_read_mutations(mutation):
    name, kind, fields, filters, _ = mutation
    async with isolated() as (raw, db, commands):
        outcomes, traces = {}, {}
        for mode in ('current', 'coarse', 'fine'):
            await populate(raw, 1)
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': {'report_included_statuses': ['completed']}})
            await raw.daily_costs.insert_one({'user_id': OWNER['id'], 'date': '2026-10-05', 'google_ads': 10})
            gate = Gate(raw, kind, fields)
            writer = asyncio.create_task(gate.writer())
            try:
                with configured(mode):
                    response, _ = await invoke(GatedDB(db, gate), commands, 'computation', {**DEFAULT_KWARGS, **filters})
                await writer
            finally:
                if not writer.done(): writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
            assert gate.committed.is_set() and gate.reads == 2
            outcomes[mode], traces[mode] = response, gate.trace
        assert all(encoded(result) == encoded(outcomes['current']) for result in outcomes.values()), name
        target = os.environ.get('DASHBOARD_ADS_CORRECTNESS_PATH')
        if target:
            path = Path(target)
            evidence = json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
            evidence[name] = {'equivalent': True, 'outcomes': outcomes, 'traces': traces}
            path.write_text(json.dumps(evidence, default=str, ensure_ascii=False), encoding='utf8')
