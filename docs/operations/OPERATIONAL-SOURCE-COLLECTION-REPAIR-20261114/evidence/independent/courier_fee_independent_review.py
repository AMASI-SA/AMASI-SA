"""Synthetic independent parity/cancellation review; no external data or I/O."""
import argparse
import ast
import asyncio
from collections import Counter
from copy import deepcopy
from decimal import Decimal, FloatOperation, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import pickle
import random
import subprocess
from threading import Event

import operational_courier_fee_history as candidate

ROOT = Path('/workspace/scratch/019fa8a18b3d/mezan-source-collection-fix')
BASE = '9c71435f4a19977c09f4331dee8fddbfa12a45eb'
EXPECTED = 'd03979de277bae09d5536df5b98e62138e2633f819d92957d0f4e552a7f0df6d'
HELPER = ROOT / 'backend/operational_courier_fee_history.py'


def git_bytes(path):
    return subprocess.check_output(['git', 'show', BASE + ':' + path], cwd=ROOT)


OLD_BYTES = git_bytes('backend/operational_delivery_evidence.py')
OLD_NODE = next(node for node in ast.parse(OLD_BYTES).body
                if isinstance(node, ast.FunctionDef) and node.name == 'recorded_courier_fee')
OLD_MODULE = ast.Module(body=[OLD_NODE], type_ignores=[])
old_namespace = {'Decimal': Decimal}
exec(compile(OLD_MODULE, '<exact-J-recorded-courier-fee>', 'exec'), old_namespace)
old = old_namespace['recorded_courier_fee']


def state(facts=None, obligations=None, movements=None):
    return {'engine': {'facts': {} if facts is None else facts,
                       'obligations': {} if obligations is None else obligations},
            'movements': [] if movements is None else movements}


def shipping(amount='0'):
    return {'kind': 'shipping', 'confirmed': amount}


def allocation(key, amount='0'):
    return {'obligation_id': key, 'amount': amount}


def context_result(function, mode):
    with localcontext() as context:
        context.clear_flags()
        context.traps[InvalidOperation] = mode != 'untrapped_invalid'
        context.traps[FloatOperation] = mode == 'trapped_float'
        try:
            result = ('value', function())
        except Exception as error:
            result = ('error', type(error).__module__, type(error).__qualname__, str(error))
        return result, sorted(flag.__name__ for flag, active in context.flags.items() if active)


def fixed_cases():
    cases = [('absent', state())]
    for cost in [None, 0, False, '', [], {}, 'NaN', Decimal('NaN')]:
        cases.append(('root-cost', state({'shipping:a': {'cost': cost}, 3: {}}, movements=[None])))
    for facts in [
        {'shipping:a': None, 'shipping_fee:a': {}},
        {3: {}, 'shipping:a:tail': {}}, {'shipping:a:tail': {}, 3: {}},
        {'shipping_fee:a': None}, {'shipping:a:tail': None},
        {'shipping:a:b': {'cost': None}}, {'shipping:a::b:': {}},
        {'shipping::': {}}, {'shipping:\uffff:a': {}},
    ]:
        cases.append(('facts-order', state(facts, {'shipping:a': None}, [None])))
    amounts = ['0', '1', '-1', 'NaN', 'sNaN', 'Infinity', '-Infinity', 'bad', None,
               True, False, {}, [], b'1', 0.25, Decimal('NaN'), Decimal('1.00')]
    for amount in amounts:
        cases.append(('confirmed', state(obligations={'shipping:a': shipping(amount)})))
        cases.append(('allocated', state(obligations={'shipping:a': shipping()},
            movements=[{'allocations': [allocation('shipping:a', amount)]}])))
    for obligations in [
        {'shipping:a:x': shipping('1'), 'shipping:a:y': None},
        {'shipping:a:y': None, 'shipping:a:x': shipping('1')},
        {'elsewhere': None}, {3: shipping()},
        {'shipping:a:x': shipping('NaN'), 'shipping:a:y': shipping('1')},
        {'shipping:a:y': shipping('1'), 'shipping:a:x': shipping('NaN')},
        {'shipping:a': {'kind': 'other', 'confirmed': 'bad'}},
        {'shipping:a': {'kind': 'shipping'}},
    ]:
        cases.append(('obligation-order', state(obligations=obligations)))
    for movements in [
        [None], [{'allocations': None}], [{'allocations': [None]}],
        [{'allocations': [allocation([], '1')]}],
        [{'allocations': [allocation({}, '1')]}],
        [{'allocations': [allocation('unrelated', 'bad')]}],
        [{'allocations': [allocation('shipping:a', '1'), None]}],
        [{'allocations': [allocation('shipping:a', '0'), allocation('shipping:a', 'NaN')]}],
        [{'allocations': [allocation('shipping:a', '1')]}, None],
        [{'allocations': (allocation('shipping:a', '1'),)}],
        [{'allocations': [{'amount': 'bad'}]}],
    ]:
        for present in [False, True]:
            cases.append(('allocation-order', state(obligations={'shipping:a': shipping()} if present else {},
                                                     movements=movements)))
    for value in [None, False, 0, [], [1], {'engine': None}, {'engine': []},
                  {'engine': {'facts': None}}, {'engine': {'obligations': []}}, {'movements': None}]:
        cases.append(('outer-shape', value))
    return cases


def randomized_cases():
    rng = random.Random(1916)
    keys = ['shipping:a', 'shipping:a:', 'shipping:a:b', 'shipping:a:b:c', 'shipping:a::b',
            'shipping:b', 'shipping:aa', 'shipping:', 'shipping::b', 'shipping_fee:a',
            'shipping_fee:a:b', 'capture:x', 'shipping:\uffff', 'shipping:\uffff:x']
    amounts = ['0', '1', '-1', 'NaN', 'sNaN', 'Infinity', '-Infinity', 'invalid', None,
               False, True, 0.25, {}, []]
    for number in range(600):
        shuffled = rng.sample(keys, rng.randrange(len(keys) + 1))
        facts = {key: {'cost': rng.choice([None, None, None, 0, False, '', '1'])}
                 for key in shuffled[:rng.randrange(len(shuffled) + 1)]}
        obligations = {key: {'kind': rng.choice(['shipping', 'shipping', 'other']),
                             'confirmed': rng.choice(amounts)}
                       for key in rng.sample(keys, rng.randrange(len(keys) + 1))}
        movements = [{'allocations': [allocation(rng.choice(keys + [None]), rng.choice(amounts))
                                      for _ in range(rng.randrange(7))]}
                     for _ in range(rng.randrange(6))]
        yield 'random-' + str(number), state(facts, obligations, movements)


def differential():
    comparisons = 0
    identities = ['', 'a', 'a:', 'a:b', 'a:b:c', 'a::b', ':b', '\uffff', 'absent']
    for label, value in fixed_cases() + list(randomized_cases()):
        before = pickle.dumps(value, protocol=5)
        for identity in identities:
            for mode in ['default', 'untrapped_invalid', 'trapped_float']:
                expected = context_result(lambda: old(value, identity), mode)
                actual = context_result(lambda: candidate.build_recorded_courier_fee_lookup(value)(identity), mode)
                assert actual == expected, (label, identity, mode, expected, actual)
                comparisons += 1
        assert pickle.dumps(value, protocol=5) == before, ('input-mutated', label)
    for identity in [None, False, 0, [], b'a']:
        assert context_result(lambda: old(state(), identity), 'default') == context_result(
            lambda: candidate.build_recorded_courier_fee_lookup(state())(identity), 'default')
        comparisons += 1
    events = []
    class ObservedDict(dict):
        def get(self, *args):
            events.append(('get', args[0]))
            return super().get(*args)
        def items(self):
            events.append(('items',))
            return super().items()
    for value in [ObservedDict(state()), {'engine': ObservedDict({'facts': {}, 'obligations': {}})},
                  state(facts=ObservedDict({'shipping:a': {'cost': 0}})),
                  state(obligations={'shipping:a': ObservedDict(shipping('1'))})]:
        events.clear()
        expected = context_result(lambda: old(value, 'a'), 'default')
        old_events = list(events)
        events.clear()
        actual = context_result(lambda: candidate.build_recorded_courier_fee_lookup(value)('a'), 'default')
        assert actual == expected and events == old_events, (expected, actual, old_events, events)
        comparisons += 1
    return comparisons


class CountLoops(ast.NodeTransformer):
    def wrap(self, iterable):
        label = ast.unparse(iterable)
        return ast.copy_location(ast.Call(func=ast.Name(id='_review_count_iter', ctx=ast.Load()),
            args=[iterable, ast.Constant(label)], keywords=[]), iterable)
    def visit_For(self, node):
        node = self.generic_visit(node)
        node.iter = self.wrap(node.iter)
        return node
    def visit_comprehension(self, node):
        node = self.generic_visit(node)
        node.iter = self.wrap(node.iter)
        return node


def counted_functions():
    counter = Counter()
    def count_iter(values, label):
        for value in values:
            counter[label] += 1
            yield value
    baseline_namespace = {'Decimal': Decimal, '_review_count_iter': count_iter}
    helper_namespace = {'_review_count_iter': count_iter}
    for tree, namespace, filename in [
        (deepcopy(OLD_MODULE), baseline_namespace, '<counted-exact-J>'),
        (ast.parse(HELPER.read_bytes()), helper_namespace, '<counted-frozen-helper>'),
    ]:
        transformed = ast.fix_missing_locations(CountLoops().visit(tree))
        exec(compile(transformed, filename, 'exec'), namespace)
    return counter, baseline_namespace['recorded_courier_fee'], helper_namespace['build_recorded_courier_fee_lookup']


def cohort(size):
    return state({'capture:' + str(i): {} for i in range(size)},
                 {'shipping:' + str(i): shipping() for i in range(size)},
                 [{'allocations': [allocation('provider:' + str(i), '1')]} for i in range(size)])


def work_counts(negative):
    counter, baseline, builder = counted_functions()
    result = {}
    for label, make in [('baseline', lambda value: lambda identity: baseline(value, identity)),
                        ('candidate', (lambda value: lambda identity: baseline(value, identity)) if negative else builder)]:
        counts = []
        for size in [64, 128]:
            value = cohort(size)
            before = pickle.dumps(value, protocol=5)
            counter.clear()
            lookup = make(value)
            assert [lookup(str(i)) for i in range(size)] == [False] * size
            assert pickle.dumps(value, protocol=5) == before
            counts.append(sum(counter.values()))
        result[label] = {'size64_visits': counts[0], 'size128_visits': counts[1],
                         'doubling_ratio': counts[1] / counts[0]}
    return result


def fresh_attempts():
    value = cohort(2)
    first = candidate.build_recorded_courier_fee_lookup(value)
    assert first('0') is False
    value['engine']['facts']['shipping:0:base'] = {}
    second = candidate.build_recorded_courier_fee_lookup(value)
    assert second is not first and second('0') is True
    value['engine'] = {'facts': {}, 'obligations': {'shipping:0': shipping()}}
    value['movements'] = [{'allocations': [allocation('shipping:0', '1')]}]
    third = candidate.build_recorded_courier_fee_lookup(value)
    assert third is not second and third('0') is True and third('1') is False
    value['movements'] = []
    fourth = candidate.build_recorded_courier_fee_lookup(value)
    assert fourth('0') is False


async def cancelled_build():
    from fastapi import HTTPException
    from operational_demand_refresh import LEASES, run_visible_refresh
    from operational_financial_activity_runtime import prepare_in_thread
    from test_operational_balance_demand_refresh import DB, Request, allowed
    value = cohort(2000)
    before = pickle.dumps(value, protocol=5)
    entered, release, exited = Event(), Event(), Event()
    real_checkpoint = candidate._checkpoint
    visits = 0
    def controlled_checkpoint():
        nonlocal visits
        visits += 1
        if visits == 23:
            entered.set()
            assert release.wait(4), 'review cancellation did not release worker'
        real_checkpoint()
    def build():
        try:
            return candidate.build_recorded_courier_fee_lookup(value)
        finally:
            exited.set()
    async def operation():
        return await prepare_in_thread(build)
    candidate._checkpoint = controlled_checkpoint
    db, request = DB(), Request()
    task = asyncio.create_task(run_visible_refresh(request, db, 'owner', 'synthetic-reviewer', allowed, operation))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        request.disconnect()
        for _ in range(20):
            await asyncio.sleep(0)
        assert not task.done() and not exited.is_set(), 'HTTP returned before worker joined'
        release.set()
        try:
            await asyncio.wait_for(task, 3)
            raise AssertionError('disconnected request returned a lookup')
        except HTTPException as error:
            assert error.status_code == 499
        assert exited.is_set() and visits == 23
        assert db.data[LEASES] == {}
        assert all(name == LEASES for name, *_ in db.writes)
        assert pickle.dumps(value, protocol=5) == before
        assert not [pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()]
        return {'status': 499, 'joined': True, 'lease_empty': True, 'nonlease_writes': 0, 'visits': visits}
    finally:
        release.set()
        candidate._checkpoint = real_checkpoint


class RemoveOnlyIntendedChanges(ast.NodeTransformer):
    def visit_With(self, node):
        if (len(node.items) == 1 and isinstance(node.items[0].context_expr, ast.Call)
                and isinstance(node.items[0].context_expr.func, ast.Name)
                and node.items[0].context_expr.func.id == 'refresh_stage'):
            return [self.visit(child) for child in node.body]
        return self.generic_visit(node)
    def visit_Assign(self, node):
        if any(isinstance(t, ast.Name) and t.id == 'recorded_fee_lookup' for t in node.targets):
            return None
        return self.generic_visit(node)
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == 'recorded_fee_lookup':
            node = deepcopy(node)
            node.func.id = 'recorded_courier_fee'
            node.args.insert(0, ast.Name(id='operational_state', ctx=ast.Load()))
        return self.generic_visit(node)
    def visit_Import(self, node):
        return None
    def visit_ImportFrom(self, node):
        return None


def collector_ast_parity():
    def function(data):
        return next(n for n in ast.parse(data).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'collect_sources')
    before = function(git_bytes('backend/operational_balance_sources.py'))
    after = function((ROOT / 'backend/operational_balance_sources.py').read_bytes())
    # Flatten wrappers with NodeTransformer's usual list flattening behavior.
    class Normalize(RemoveOnlyIntendedChanges):
        def visit_With(self, node):
            if (len(node.items) == 1 and isinstance(node.items[0].context_expr, ast.Call)
                    and isinstance(node.items[0].context_expr.func, ast.Name)
                    and node.items[0].context_expr.func.id == 'refresh_stage'):
                body = []
                for child in node.body:
                    changed = self.visit(child)
                    if isinstance(changed, list):
                        body.extend(changed)
                    elif changed is not None:
                        body.append(changed)
                return body
            return self.generic_visit(node)
    assert ast.dump(Normalize().visit(before)) == ast.dump(Normalize().visit(after)), 'unintended collector logic change'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--negative-control', action='store_true')
    parser.add_argument('--json', required=True)
    args = parser.parse_args()
    helper_hash = hashlib.sha256(HELPER.read_bytes()).hexdigest()
    assert helper_hash == EXPECTED
    assert (ROOT / 'backend/operational_delivery_evidence.py').read_bytes() == OLD_BYTES
    report = {'base': BASE, 'helper_sha256': helper_hash, 'negative_control': args.negative_control}
    if not args.negative_control:
        report['differential_comparisons'] = differential()
        fresh_attempts()
        collector_ast_parity()
        report['collector_ast_parity'] = True
        report['cancellation'] = asyncio.run(cancelled_build())
    report['work'] = work_counts(args.negative_control)
    assert hashlib.sha256(HELPER.read_bytes()).hexdigest() == helper_hash
    Path(args.json).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, sort_keys=True), flush=True)
    old_count, new_count = report['work']['baseline'], report['work']['candidate']
    assert new_count['doubling_ratio'] <= 2.4, ('scan amplification remains', new_count)
    assert new_count['size128_visits'] < old_count['size128_visits'] / 4
    print('PASS', flush=True)


if __name__ == '__main__':
    main()
