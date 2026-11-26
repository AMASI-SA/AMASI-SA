"""Bounded synthetic CPU profile of the unchanged complete source collector.

No MongoDB, network, production configuration, financial writer or runtime edit
is used. The existing deny-list test adapter validates every query. Its cursor
returns already prepared rows, deliberately excluding fixture deepcopy and BSON
transport from measured source preparation. Empty bundle authority is explicit:
this measures normalization, not populated historical bundle validation or I/O.

One baseline and one experiment-only candidate process the same 19,151 orders.
The candidate reuses per-product option authority, uses the existing independent
copy helper for bundle items, and overlays the three changed payment containers.
The payment overlay is ONLY a benchmark prototype for ordinary non-aliased dicts;
it is not a proposed replacement for the public independent-copy API. Production
adoption needs alias/subclass fallback contracts and separate regression review.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
import gc
import hashlib
import json
import os
from pathlib import Path
import pickle
import resource
import sys
import time


ROOT = Path(__file__).resolve().parents[4]
COUNT = 19_151
PRODUCTS = 128
OWNER = "owner"
START = "2026-11-01T00:00:00+00:00"
CREATED = "2026-11-02T09:00:00+00:00"
NOW = "2026-11-26T02:41:00+00:00"
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

import operational_balance_sources as sources
import operational_remaining_bundle_sources as bundles
import order_cost_authority as authority
import order_engine.mapper as mapper
import report_option_identity as option_identity
import salla_payment_collection as payment_collection
from operational_report_copy import copy_report_value
from test_operational_balance_cost_catalog_index import collector_database
from test_operational_balance_sources import Cursor


def fixture():
    names = (("color", "Color", "red", "Red", "Scarlet"),
             ("size", "Size", "large", "Large", "Large size"),
             ("shape", "Shape", "round", "Round", "Round shape"))
    products, bindings, profiles = [], [], []
    for p in range(PRODUCTS):
        pid = "p" + str(p)
        options = [{"id": oid, "name": name, "type": "dropdown",
            "translations": {"en": {"option_name": name}},
            "values": [{"id": vid, "name": current,
                        "identity_labels": [old], "translations": {"en": {"name": current}}},
                       *({"id": f"{vid}-unused-{j}", "name": f"Synthetic unused {j}"} for j in range(3))]}
            for oid, name, vid, old, current in names]
        products.append({"user_id": OWNER, "salla_product_id": pid, "options": options})
        profiles.append({"user_id": OWNER, "salla_product_id": pid, "base_cost": "20", "cost_currency": "SAR"})
        bindings.extend({"user_id": OWNER, "salla_product_id": pid, "id": f"{pid}-{oid}",
            "option_id": oid, "option_name": name, "value_id": vid, "value_name": old,
            "mode": "direct", "direct_amount": "1", "quantity": "1", "cost_currency": "SAR"}
            for oid, name, vid, old, _current in names)
    orders = []
    for i in range(COUNT):
        oid, number = str(i + 1), str(i + 100_000)
        group = i % 4
        items = []
        for j in range(4):
            pid = "p" + str(((i // 4) * 4 + j) % PRODUCTS)
            options = [{"option_id": k, "name": name, "value": {"id": v, "name": old},
                "metadata": {"required": True, "source": "synthetic"}}
                for k, name, v, old, _current in names]
            if group == 1:
                options = [{"name": name, "value": current,
                    "metadata": {"required": True, "source": "synthetic"}}
                    for _k, name, _v, _old, current in names]
            items.append({"id": f"{oid}-{j}", "product_id": pid, "quantity": 1,
                "name": "Synthetic product", "price": {"amount": "75", "currency": "SAR"},
                "product": {"id": pid, "name": "Synthetic product", "options": [],
                    "images": [{"url": "https://example.invalid/synthetic", "alt": "Synthetic"}]},
                "variant": {"id": "v" + str(j), "options": []},
                "options": options, "custom_fields": [{"key": "synthetic", "value": "synthetic"}],
                "taxes": [{"name": "VAT", "percent": "15", "amount": "11.25"}]})
        paid = "100.00" if group == 2 else "300.00"
        remaining = "200.00" if group == 2 else "0.00"
        raw = {"id": oid, "reference_id": number, "currency": "SAR", "created_at": CREATED,
            "updated_at": CREATED, "status": {"slug": "in_progress", "name": "Synthetic processing"},
            "amounts": {"total": {"amount": "300.00", "currency": "SAR"}}, "items": items,
            "customer": {"id": "customer-" + oid, "name": "Synthetic Customer",
                "address": {"city": "Synthetic City", "street": "Synthetic street"}},
            "shipping": {"shipment_id": "shipment-" + oid,
                "tracking_number": "track-" + oid, "cost": {"amount": "30.00", "currency": "SAR"}},
            "shipments": [{"id": "shipment-" + oid, "history": [
                {"status": "created", "at": CREATED}, {"status": "processing", "at": CREATED}]}],
            "payment_method": "cod" if group == 3 else "mada", "is_pending_payment": False}
        if group != 3:
            raw.update(payment={"method": "mada", "status": "paid", "paid_amount": paid,
                "reference": "tx-" + oid, "paid_at": CREATED},
                payment_methods=[{"payment_method": "mada", "amount": paid,
                    "currency": "SAR", "transaction_reference": "tx-" + oid}],
                payment_actions={"remaining_action": {"paid_amount": {"amount": paid, "currency": "SAR"},
                    "remaining_amount": remaining, "has_remaining_amount": group == 2},
                    "refund_action": {"paid_amount": {"amount": paid, "currency": "SAR"},
                        "refund_amount": {"amount": "0.00", "currency": "SAR"}, "refunded_transactions": []}})
        orders.append({"user_id": OWNER, "order_number": number, "order_id": oid,
            "raw_by_source": {"salla_direct": raw, "other": {"proof": [None, {"kept": True}]}},
            "g47_salla_snapshot": {"requires_authoritative_refresh": False, "source_updated_at": CREATED}})
    return {"unified_orders": orders, "mezan_products_v2": products,
        "mezan_product_option_cost_bindings_v2": bindings,
        "mezan_product_cost_profiles_v2": profiles, "mezan_cost_resources_v2": [],
        "mezan_product_resource_bindings_v2": []}


def source_fingerprint(data):
    from bson import encode
    digest = hashlib.sha256()
    sizes = {}
    for name, rows in sorted(data.items()):
        digest.update(name.encode())
        size = 0
        for row in rows:
            payload = encode(row)
            digest.update(payload)
            size += len(payload)
        sizes[name] = size
    return digest.hexdigest(), sizes


def runtime_hashes():
    names = ("operational_balance_sources.py", "operational_remaining_bundle_sources.py",
             "order_cost_authority.py", "salla_payment_collection.py", "report_option_identity.py",
             "operational_report_copy.py", "order_engine/mapper.py")
    return {name: hashlib.sha256((ROOT / "backend" / name).read_bytes()).hexdigest() for name in names}


def overlay_copy(payload):
    # Experiment only. The collector's ordinary synthetic dicts have no aliases
    # between these and other financial branches. A production helper must prove
    # that property or use the original independent deepcopy fallback.
    return {**payload, "payment_actions": {**payload["payment_actions"],
        "remaining_action": dict(payload["payment_actions"]["remaining_action"])}}


def option_candidate(cache):
    """Exact existing row-specific checks, experiment-only reused authority."""
    def resolve(data, product_id, item, bindings, tokens):
        if not bindings:
            return tokens
        if not tokens:
            raise ValueError('mz2_cost_option_value_unverified')
        if not any(sources._option_needs_catalog(bindings, token) for token in tokens):
            return tokens
        sources._catalog_recovery_preserves_ids(item)
        product = item.get('product') if isinstance(item.get('product'), dict) else {}
        native_product = item.get('parent_product_id') or item.get('product_id') or product.get('id')
        if not sources._catalog_identity(native_product) or str(native_product) != product_id:
            raise ValueError('mz2_cost_option_catalog_unverified')
        if product_id in data.get('_option_catalog_errors', set()):
            raise ValueError('mz2_cost_option_catalog_unverified')
        key = (id(data), product_id, tuple(id(row) for row in bindings))
        saved = cache.get(key)
        if saved is None:
            candidates = [p for p in data.get('mezan_products_v2', []) if isinstance(p, dict)
                and sources._catalog_identity(p.get('salla_product_id')) and str(p['salla_product_id']) == product_id]
            if len(candidates) != 1:
                raise ValueError('mz2_cost_option_catalog_unverified')
            try:
                for source in (candidates[0].get('raw_salla'), candidates[0].get('raw_salla_details'), candidates[0]):
                    if not isinstance(source, dict):
                        continue
                    options = source.get('options', [])
                    if options is None:
                        continue
                    if not isinstance(options, list):
                        raise ValueError('invalid_options')
                    for option in options:
                        values = option.get('values', [])
                        if values is None:
                            values = []
                        if not sources._catalog_identity(option.get('id')) or not isinstance(values, list) or any(
                                not isinstance(v, dict) or not sources._catalog_identity(v.get('id')) for v in values):
                            raise ValueError('invalid_option_identity')
                        for entry in [option, *values]:
                            labels = entry.get('identity_labels', [])
                            translations = entry.get('translations', {})
                            if (labels is not None and (not isinstance(labels, list) or any(not isinstance(v, str) for v in labels))
                                    or translations is not None and (not isinstance(translations, dict)
                                        or any(not isinstance(v, dict) for v in translations.values()))):
                                raise ValueError('invalid_option_labels')
                catalog = option_identity.catalog_options(candidates[0])
            except (TypeError, AttributeError, ValueError):
                raise ValueError('mz2_cost_option_catalog_unverified') from None
            bound = {str(b.get('option_id')) for b in bindings if b.get('option_id')}
            if not bound or not bound <= catalog.keys():
                raise ValueError('mz2_cost_option_catalog_unverified')
            for binding in bindings:
                option, value = str(binding.get('option_id') or ''), str(binding.get('value_id') or '')
                if option not in catalog or value not in catalog[option]['values']:
                    raise ValueError('mz2_cost_option_catalog_unverified')
                if binding.get('option_name'):
                    catalog[option]['labels'].add(sources._option_label(binding['option_name']))
                if binding.get('value_name'):
                    catalog[option]['values'][value].add(sources._option_value_label(binding['value_name']))
            cache[key] = saved = catalog, bound
        catalog, bound = saved
        resolved = set()
        for token in tokens:
            option, value, name, label, origin = token
            named = [key for key, entry in catalog.items()
                if name in {sources._option_label(v) for v in entry['labels']}]
            if option and (option not in catalog or named and option not in named):
                raise ValueError('mz2_cost_option_identity_conflict')
            choices = [option] if option else named
            if len(choices) != 1:
                raise ValueError('mz2_cost_option_identity_ambiguous')
            option = choices[0]
            if option in bound:
                values = catalog[option]['values']
                named_values = [key for key, labels in values.items()
                    if label in {sources._option_value_label(v) for v in labels}]
                if value and (value not in values or named_values and value not in named_values):
                    raise ValueError('mz2_cost_option_identity_conflict')
                matches = [value] if value else named_values
                if len(matches) != 1:
                    raise ValueError('mz2_cost_option_value_unverified')
                value = matches[0]
            resolved.add((option, value, name, label, origin))
        for option, entry in catalog.items():
            if not entry.get('multiple') and len({value for oid, value, _, _, _ in resolved if oid == option and value}) > 1:
                raise ValueError('mz2_cost_option_value_conflict')
        return resolved
    return resolve


@contextmanager
def instrumentation(mode, metrics):
    saved = []
    cache = {}
    def patch(module, name, value):
        saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)
    def timed(module, name, label):
        original = getattr(module, name)
        def wrapped(*args, **kwargs):
            wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
            try:
                return original(*args, **kwargs)
            finally:
                metric = metrics['helpers'].setdefault(label, {'calls': 0, 'wall_ns': 0, 'thread_cpu_ns': 0})
                metric['calls'] += 1
                metric['wall_ns'] += time.perf_counter_ns() - wall
                metric['thread_cpu_ns'] += time.thread_time_ns() - cpu
        patch(module, name, wrapped)
    @contextmanager
    def stage(name):
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        try:
            yield
        finally:
            metrics['stages'][name] = {'wall_ms': (time.perf_counter_ns() - wall) / 1e6,
                                     'thread_cpu_ms': (time.thread_time_ns() - cpu) / 1e6}
    async def prepared_rows(cursor, count=None, *, length=None):
        # Copies by the fake database are not production parsing work. A new
        # result list still models cursor ownership; source objects stay stable.
        return list(cursor.values[:length if length is not None else count])
    patch(Cursor, 'to_list', prepared_rows)
    patch(sources, 'refresh_stage', stage)
    patch(bundles, 'refresh_stage', stage)
    if mode == 'candidate':
        patch(sources, '_verified_option_tokens', option_candidate(cache))
        patch(bundles, 'deepcopy', copy_report_value)
        patch(payment_collection, 'deepcopy', overlay_copy)
    for module, name, label in (
            (bundles, 'normalized_orders', 'bundle_normalized_orders'),
            (bundles, 'deepcopy', 'bundle_item_copy'),
            (mapper, '_normalise_options', 'native_option_normalization'),
            (sources, 'cost_components', 'cost_components'),
            (sources, '_verified_option_tokens', 'verified_option_tokens'),
            (option_identity, 'catalog_options', 'catalog_options'),
            (payment_collection, 'settled_payment_snapshot', 'settled_payment_snapshot'),
            (payment_collection, 'deepcopy', 'payment_payload_copy'),
            (authority, 'economic_item', 'authority_economic_item')):
        timed(module, name, label)
    try:
        yield cache
    finally:
        for module, name, original in reversed(saved):
            setattr(module, name, original)


async def measured(data, mode):
    database = collector_database(data)
    state = {'owner_id': OWNER, 'status': 'active', 'revision': 7, 'started_at': START,
        'openings': {}, 'movements': [], 'requests': [],
        'engine': {'as_of': START, 'facts': {}, 'obligations': {}, 'issues': []}}
    metrics = {'mode': mode, 'helpers': {}, 'stages': {}}
    ticks, gc_start = [], {}
    def observe_gc(phase, info):
        generation = info['generation']
        if phase == 'start':
            gc_start[generation] = time.perf_counter_ns()
        elif generation in gc_start:
            ticks.append((generation, time.perf_counter_ns() - gc_start.pop(generation)))
    gc.collect()
    metrics['gc_before'] = gc.get_stats()
    gc.callbacks.append(observe_gc)
    before = pickle.dumps(state, protocol=5)
    try:
        with instrumentation(mode, metrics) as cache:
            wall, cpu = time.perf_counter_ns(), time.process_time_ns()
            result = await asyncio.wait_for(sources.collect_sources(database, OWNER, START, NOW,
                operational_state=state), timeout=85)
            metrics['wall_ms'] = (time.perf_counter_ns() - wall) / 1e6
            metrics['process_cpu_ms'] = (time.process_time_ns() - cpu) / 1e6
            metrics['option_authority_cache_entries'] = len(cache)
    finally:
        gc.callbacks.remove(observe_gc)
    assert pickle.dumps(state, protocol=5) == before
    metrics['gc_after'] = gc.get_stats()
    metrics['gc_wall_ms'] = sum(value for _, value in ticks) / 1e6
    metrics['gc_collections'] = dict(Counter(generation for generation, _ in ticks))
    metrics['process_high_water_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    metrics['output_orders'] = len(result['orders'])
    metrics['output_items'] = sum(len(order['items']) for order in result['orders'])
    metrics['output_issues'] = dict(Counter(issue['code'] for issue in result['issues']))
    metrics['queries_by_collection'] = dict(Counter(database.reads))
    metrics['query_sequence_sha256'] = hashlib.sha256(json.dumps(database.reads).encode()).hexdigest()
    # Pickle records complete values/types/order/aliases; equality below is also
    # checked directly. JSON is only for public, synthetic evidence summaries.
    payload = pickle.dumps(result, protocol=5)
    metrics['output_pickle_bytes'] = len(payload)
    metrics['output_pickle_sha256'] = hashlib.sha256(payload).hexdigest()
    for values in metrics['helpers'].values():
        values['wall_ms'] = round(values.pop('wall_ns') / 1e6, 3)
        values['thread_cpu_ms'] = round(values.pop('thread_cpu_ns') / 1e6, 3)
    return result, metrics


async def run():
    initial_hashes = runtime_hashes()
    print('Preparing 19,151 synthetic nested orders; no database or network.', flush=True)
    data = fixture()
    before, sizes = source_fingerprint(data)
    report = {'experiment': 'offline_complete_source_preparation', 'order_count': COUNT,
        'items_per_order': 4, 'options_per_item': 3, 'product_count': PRODUCTS,
        'group_order_counts': dict(Counter(i % 4 for i in range(COUNT))),
        'group_semantics': {'0': 'provider clear, explicit option IDs',
            '1': 'provider clear, catalog name recovery', '2': 'provider partial, explicit option IDs',
            '3': 'COD, explicit option IDs'},
        'source_bson_bytes': sizes, 'input_sha256': before,
        'runtime_sha256': initial_hashes, 'runs': [],
        'limitations': ['Synthetic distribution; actual live helper frequencies are unknown.',
            'No I/O or concurrent profit work; no claim about complete 85-second workflow.',
            'Empty bundle authority; populated physical/supplier validation is not profiled.',
            'Fixture setup, hashing and fake-cursor cloning are outside timed processing.',
            'Inclusive helper and stage measurements overlap and must not be summed.',
            'RSS is process high-water in one process, not an independent peak comparison.',
            'Prototype catalog reuse and payment overlay are benchmark-only, not runtime changes.']}
    print('Baseline complete source collection.', flush=True)
    baseline, metrics = await measured(data, 'baseline')
    report['runs'].append(metrics)
    assert source_fingerprint(data)[0] == before
    print(json.dumps({'mode': 'baseline', 'wall_ms': round(metrics['wall_ms'], 3),
                      'orders': metrics['output_orders']}), flush=True)
    print('Experiment-only candidate complete source collection.', flush=True)
    candidate, metrics = await measured(data, 'candidate')
    report['runs'].append(metrics)
    assert candidate == baseline, 'complete source values changed'
    assert report['runs'][0]['output_pickle_sha256'] == metrics['output_pickle_sha256'], 'types/order/aliases changed'
    assert report['runs'][0]['query_sequence_sha256'] == metrics['query_sequence_sha256'], 'read sequence changed'
    assert source_fingerprint(data)[0] == before, 'synthetic inputs mutated'
    assert runtime_hashes() == initial_hashes, 'runtime source changed during experiment'
    report.update(complete_output_equal=True, complete_serialized_output_equal=True,
                  query_sequence_equal=True, inputs_unchanged=True, runtime_unchanged=True)
    print(json.dumps({'mode': 'candidate', 'wall_ms': round(metrics['wall_ms'], 3),
                      'orders': metrics['output_orders'], 'complete_output_equal': True}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    result = asyncio.run(run())
    arguments.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print('Saved synthetic profile evidence.', flush=True)
