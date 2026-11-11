"""Synthetic report CPU/loop benchmark, no server startup, DB or network.

Run with the same interpreter/workload before and after a change. This measures
one synchronous report call as used inside async routes. Timer delay proves loop
blocking in this controlled workload, NOT observed production fulfillment latency.
An optional --service-file loads a saved original service module with unchanged
repository dependencies for narrow before/after comparison.
"""
import argparse
from decimal import Decimal
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import time


def forbidden(*args, **kwargs):
    raise RuntimeError('Financial benchmark forbids network connections')


async def run(args):
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    socket.socket.connect = forbidden
    socket.create_connection = forbidden
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'backend/tests'))
    sys.path.insert(0, str(root / 'backend'))
    from test_operational_balance_cod_projection_performance import cod_fixture
    import operational_balance_service as service
    if args.service_file:
        spec = importlib.util.spec_from_file_location('financial_benchmark_service', args.service_file)
        service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(service)
    state = cod_fixture(args.movements, args.claims)
    original = json.dumps(state, sort_keys=True, separators=(',', ':'))
    service.report(state)  # warm imports, not included in samples
    times, cpu, lags, hashes = [], [], [], []
    for _ in range(args.repeat):
        loop = asyncio.get_running_loop()
        timer = loop.create_future()
        deadline = loop.time() + .001
        loop.call_at(deadline, lambda: timer.set_result(max(0, loop.time() - deadline)))
        start, cpu_start = time.perf_counter(), time.process_time()
        output = service.report(state)
        cpu.append(time.process_time() - cpu_start)
        times.append(time.perf_counter() - start)
        lags.append(await timer)
        canonical = json.dumps(output, sort_keys=True, separators=(',', ':'), default=str)
        hashes.append(hashlib.sha256(canonical.encode()).hexdigest())
        assert json.dumps(state, sort_keys=True, separators=(',', ':')) == original
        bank = next(row for row in output['parties'] if row['party_type'] == 'bank')
        assert bank['actual'] == f'{args.movements:.2f}'
        cod = output['details']['cod_reports']
        assert len(cod) == args.claims
        assert sum(Decimal(row['settled']) for row in cod.values()) == args.movements
    assert len(set(hashes)) == 1
    return {'label': args.label, 'movements': args.movements, 'cod_claims': args.claims,
            'samples': args.repeat, 'wall_ms': [round(x*1000, 3) for x in times],
            'p50_ms': round(statistics.median(times)*1000, 3), 'max_ms': round(max(times)*1000, 3),
            'cpu_p50_ms': round(statistics.median(cpu)*1000, 3),
            'event_loop_timer_max_delay_ms': round(max(lags)*1000, 3),
            'report_sha256': hashes[0], 'input_unchanged': True, 'independent_bank_cod_checks': 'PASS'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--movements', type=int, default=5000)
    parser.add_argument('--claims', type=int, default=100)
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--service-file', type=Path)
    parser.add_argument('--label', default='current')
    args = parser.parse_args()
    if min(args.movements, args.claims, args.repeat) < 1:
        parser.error('workload values must be positive')
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))
