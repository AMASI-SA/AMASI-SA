"""Local ASGI/replica-set comparison; pass a checkout's backend directory.

BUILD20_SCAN_TEST_MONGO_URL must name a disposable loopback replica set.
The fixture stubs authentication and instruction checks, but this benchmark
restores the real local Mezan price lookup. No external providers are called.
"""
import asyncio
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

backend = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(backend), str(backend / "tests")]
import pytest
import supplier_receiving_routes as r
from test_supplier_scan_recovery import env, seed, post_scan


async def main():
    real_price = r._supplier_product_reference_price
    output = []
    for count in (10, 50):
        patch = pytest.MonkeyPatch()
        fixture = env.__wrapped__(patch)
        state = await anext(fixture)
        db, http, _ = state
        patch.setattr(r, '_supplier_product_reference_price', real_price)
        session, pieces = await seed(state, count)
        await db[r.PRODUCTS].insert_one({'user_id': 'merchant', 'id': 'product-1', 'salla_product_id': 'product-1'})
        await db[r.COST_PROFILES].insert_one({'user_id': 'merchant', 'salla_product_id': 'product-1', 'base_cost': 21})
        times, price_times = [], []
        async def measured_price(*args, **kwargs):
            start = perf_counter()
            try:
                return await real_price(*args, **kwargs)
            finally:
                price_times.append((perf_counter() - start) * 1000)
        patch.setattr(r, '_supplier_product_reference_price', measured_price)
        try:
            for index, piece in enumerate(pieces):
                start = perf_counter()
                response = await post_scan(http, session['id'], piece, f'benchmark-{index:04}', 1)
                times.append((perf_counter() - start) * 1000)
                assert response.status_code == 200, response.text
            output.append({'count': count, 'median_ms': round(statistics.median(times), 2),
                           'p95_ms': round(sorted(times)[int(.95 * (len(times)-1))], 2),
                           'total_ms': round(sum(times), 2),
                           'price_lookup_median_ms': round(statistics.median(price_times), 2)})
        finally:
            try:
                await anext(fixture)
            except StopAsyncIteration:
                pass
            patch.undo()
    print(json.dumps(output, indent=2))


asyncio.run(main())
