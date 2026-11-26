"""One offline A/B collector pair with the actual option/payment opt-ins.

Uses the previously recorded 19,151-order synthetic fixture and instrumentation.
Only the two opt-ins are disabled in A. B uses the real candidate implementation.
Both keep the same current copy helper and unchanged stdlib bundle item copies.
The preceding three-change prototype is not installed by this script.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import profile_source_preparation as profile
import operational_option_authority as authority
import operational_payment_read_view as payment_view
from bson import ObjectId


def hashes():
    values = profile.runtime_hashes()
    for name in ('operational_option_authority.py', 'operational_payment_read_view.py',
                 'operational_provider_transaction_evidence.py'):
        values[name] = hashlib.sha256((profile.ROOT / 'backend' / name).read_bytes()).hexdigest()
    return values


@contextmanager
def optins(enabled, extra):
    saved = []
    def patch(module, name, value):
        saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)
    original_payment = profile.sources.normalize_operational_payment
    def normalize(*args, **kwargs):
        assert kwargs.get('read_only') is True, 'collector opt-in missing'
        extra['normalizer_calls'] = extra.get('normalizer_calls', 0) + 1
        kwargs['read_only'] = enabled
        return original_payment(*args, **kwargs)
    patch(profile.sources, 'normalize_operational_payment', normalize)
    if not enabled:
        patch(authority.OptionAuthorityScope, 'build', lambda _self: None)
    def timed(module, name, label):
        original = getattr(module, name)
        def wrapper(*args, **kwargs):
            started, cpu = time.perf_counter_ns(), time.thread_time_ns()
            try:
                result = original(*args, **kwargs)
                if name == 'build' and result is not None:
                    extra['authority_observed_containers'] = len(result.records)
                if name == 'validate':
                    extra['authority_cached_products'] = len(args[0].cache)
                return result
            finally:
                row = extra.setdefault(label, {'calls': 0, 'wall_ns': 0, 'cpu_ns': 0})
                row['calls'] += 1
                row['wall_ns'] += time.perf_counter_ns() - started
                row['cpu_ns'] += time.thread_time_ns() - cpu
        patch(module, name, wrapper)
    timed(authority.OptionAuthorityScope, 'build', 'authority_build')
    timed(authority.OptionAuthorityScope, 'validate', 'authority_validate')
    timed(payment_view, '_copy_payment_input', 'payment_input_copy')
    timed(payment_view, 'settled_payment_read_view', 'payment_read_view')
    try:
        yield
    finally:
        for module, name, original in reversed(saved):
            setattr(module, name, original)


def graph_ids(roots):
    pending, seen = list(roots), set()
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if type(value) is dict:
            pending.extend(value.keys())
            pending.extend(value.values())
        elif type(value) is ObjectId:
            pending.append(value.binary)
        elif type(value) in (list, tuple, set, frozenset):
            pending.extend(value)
    return seen


def private_bytes(roots, excluded):
    pending, seen, size = list(roots), set(excluded), 0
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        size += sys.getsizeof(value)
        if type(value) is dict:
            pending.extend(value.keys())
            pending.extend(value.values())
        elif type(value) in (list, tuple, set, frozenset):
            pending.extend(value)
    return size


def authority_footprint(data):
    """Untimed representation accounting; excludes all borrowed source objects."""
    excluded = graph_ids((data['mezan_products_v2'], data['mezan_product_option_cost_bindings_v2']))
    with authority.OptionAuthorityScope(data) as scope:
        assert scope.build() is scope
        roots = lambda: (scope.records, scope.binding_rows, scope.cache)
        built = private_bytes(roots(), excluded)
        groups = {}
        for row in data['mezan_product_option_cost_bindings_v2']:
            groups.setdefault(str(row['salla_product_id']), []).append(row)
        for product_id, bindings in groups.items():
            scope.prepare(data, product_id, bindings, profile.sources._option_catalog_authority)
        result = {'observed_containers': len(scope.records), 'cached_products': len(scope.cache),
            'snapshot_auxiliary_bytes': built, 'with_prepared_catalogs_auxiliary_bytes': private_bytes(roots(), excluded),
            'measurement': 'sys.getsizeof graph walk, excluding borrowed complete native input graph; not RSS'}
        scope.validate()
    return result


async def measure(data, enabled):
    extra = {}
    # These labels intentionally avoid the old script's literal 'candidate',
    # which installs the now-rejected three-change experimental prototype.
    label = 'actual_option_payment' if enabled else 'optins_disabled'
    with optins(enabled, extra):
        output, result = await profile.measured(data, label)
    for row in extra.values():
        if type(row) is dict:
            row['wall_ms'] = round(row.pop('wall_ns') / 1e6, 3)
            row['thread_cpu_ms'] = round(row.pop('cpu_ns') / 1e6, 3)
    result['actual_optin_instrumentation'] = extra
    return output, result


async def run():
    import asyncio
    source_hashes = hashes()
    print('Preparing the identical 19,151-order fixture; no database/network.', flush=True)
    data = profile.fixture()
    # Native binding rows are read without a projection and retain these Mongo
    # metadata values. The initial prototype omitted them; do not benchmark a
    # scope that would silently fall back on actual native record shapes.
    for index, binding in enumerate(data['mezan_product_option_cost_bindings_v2']):
        binding.update(_id=ObjectId(f'{index + 1:024x}'), created_at=datetime(2026, 11, 1),
                       updated_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    before, sizes = profile.source_fingerprint(data)
    footprint = authority_footprint(data)
    warm = {**data, 'unified_orders': data['unified_orders'][:8]}
    # A tiny untimed cohort enters both paths before the sole full A/B pair.
    await measure(warm, False)
    await measure(warm, True)
    assert profile.source_fingerprint(data)[0] == before
    print('Baseline: only option authority and payment read-view opt-ins disabled.', flush=True)
    baseline, old = await measure(data, False)
    print(json.dumps({'mode': old['mode'], 'wall_ms': round(old['wall_ms'], 3)}), flush=True)
    print('Candidate: actual option authority and actual payment read view.', flush=True)
    candidate, new = await measure(data, True)
    assert new['actual_optin_instrumentation']['authority_cached_products'] == profile.PRODUCTS
    assert new['helpers']['catalog_options']['calls'] == profile.PRODUCTS
    assert candidate == baseline
    assert old['output_pickle_sha256'] == new['output_pickle_sha256']
    assert old['query_sequence_sha256'] == new['query_sequence_sha256']
    assert profile.source_fingerprint(data)[0] == before
    assert hashes() == source_hashes, 'runtime changed during the isolated timing window'
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    print(json.dumps({'mode': new['mode'], 'wall_ms': round(new['wall_ms'], 3), 'complete_output_equal': True}), flush=True)
    return {'experiment': 'actual_option_payment_complete_collector', 'order_count': profile.COUNT,
        'items_per_order': 4, 'options_per_item': 3, 'product_count': profile.PRODUCTS,
        'binding_metadata': ['_id:ObjectId', 'created_at:datetime(naive)', 'updated_at:datetime(UTC)'],
        'source_bson_bytes': sizes, 'input_sha256': before, 'runtime_sha256': source_hashes,
        'untimed_warmup_orders_per_mode': 8, 'authority_footprint': footprint, 'runs': [old, new],
        'baseline_toggles': ['OptionAuthorityScope.build returns None', 'collector normalizer read_only=False'],
        'candidate_toggles': ['actual production option scope', 'actual production read_only=True normalizer'],
        'unchanged_in_both': ['bundle normalization and stdlib item deepcopy', 'all source queries/projections/limits',
                              'current copy_report_value implementation', 'complete validation and financial predicates'],
        'complete_output_equal': True, 'complete_serialized_output_equal': True, 'query_sequence_equal': True,
        'inputs_unchanged': True, 'runtime_unchanged': True,
        'limitations': ['Synthetic 25% catalog recovery,50% cleared-provider,25% partial,25%COD; live frequencies unknown.',
            'Only a single warmed A/B pair; timings are not a stable production guarantee.',
            'No MongoDB/BSON transport, concurrent profit, native evidence or populated bundle/supplier history.',
            'Inclusive helper/stage timings overlap and must not be summed.',
            'One-process RSS is cumulative high-water and includes retained baseline output; no peak-memory claim.',
            'The85s cap only bounds an offline source run; it does not model the whole production85s deadline.']}


if __name__ == '__main__':
    import asyncio
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run())
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print('Saved actual-candidate synthetic evidence.', flush=True)
