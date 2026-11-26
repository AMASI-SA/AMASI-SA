"""Bounded offline attachment experiment; synthetic data, no DB or network.

Both paths call the actual attachment helper and assert the same custody values.
The old path omits the optional index. Timings include fixture/index construction
and attachment, without the dictionary-call profiler used for operation counts.
These results do not estimate production cardinality or whole-refresh latency.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter, process_time


ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
from test_operational_balance_delivery_collection_index import attachment_work


def main():
    paths = ('backend/operational_balance_sources.py',
             'backend/operational_delivery_collection_index.py',
             'backend/tests/test_operational_balance_delivery_collection_index.py')
    before = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
    counts = [{'orders': size, 'collection_rows': size,
               'old_receipt_dict_reads': attachment_work(size, use_index=False),
               'indexed_receipt_dict_reads': attachment_work(size)} for size in (64, 128)]
    runs = []
    for use_index in (False, True, True, False):
        wall, cpu = perf_counter(), process_time()
        attachment_work(1500, use_index=use_index, measure_reads=False)
        runs.append({'mode': 'index' if use_index else 'full_scan', 'orders': 1500,
                     'collection_rows': 1500, 'wall_ms': round((perf_counter() - wall) * 1000, 3),
                     'cpu_ms': round((process_time() - cpu) * 1000, 3)})
    after = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
    assert before == after
    result = {'scope': 'synthetic offline actual attachment, no production queries',
              'operation_counts': counts, 'timings': runs,
              'source_sha256': before, 'source_unchanged_during_measurement': True}
    target = Path(__file__).with_name('measure_delivery_collection_index_result.json')
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
