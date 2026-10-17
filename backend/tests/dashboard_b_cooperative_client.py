"""Independent-process open-loop HTTP load source; loopback fixtures only."""
import asyncio
import hashlib
import json
import sys
import time

import httpx


async def run(config):
    assert config['base'].startswith('http://127.0.0.1:')
    records = []
    start = time.perf_counter()
    limits = httpx.Limits(max_connections=256, max_keepalive_connections=64)
    async with httpx.AsyncClient(base_url=config['base'], limits=limits, trust_env=False) as client:
        async def request(kind, merchant, number, scheduled):
            sent = time.perf_counter()
            path = '/dashboard-v2' if kind == 'dashboard' else '/fixture/' + kind
            row = {'kind': kind, 'merchant': merchant, 'id': f'{kind}-{number}',
                   'scheduled': scheduled, 'sent': sent, 'dispatch_ms': (sent-scheduled)*1000}
            try:
                params = {'from_date':'2026-10-01', 'to_date':'2026-10-08'} if kind == 'dashboard' else None
                response = await client.get(path, params=params,
                    headers={'x-fixture-merchant':merchant, 'x-fixture-id':row['id']},
                    timeout=httpx.Timeout(180 if kind == 'dashboard' else 15, connect=5))
                row.update(status=response.status_code, bytes=len(response.content),
                           digest=hashlib.sha256(response.content).hexdigest(), timeout=False)
            except httpx.TimeoutException as exc:
                row.update(status=None, timeout=True, error=type(exc).__name__)
            except httpx.HTTPError as exc:
                row.update(status=None, timeout=False, error=type(exc).__name__)
            row['end'] = time.perf_counter()
            row['http_ms'] = (row['end']-sent)*1000
            row['scheduled_ms'] = (row['end']-scheduled)*1000
            records.append(row)

        dashboards = [asyncio.create_task(request('dashboard', merchant, i, start))
                      for i, merchant in enumerate(config['dashboards'])]
        interactive = set()
        tick = 0
        # The generator runs in a separate OS process, never on the API loop.
        while (any(not t.done() for t in dashboards) if dashboards else
               time.perf_counter() < start + config.get('baseline_seconds', 5)):
            scheduled = start + config.get('phase',0) + tick * .25
            await asyncio.sleep(max(0, scheduled-time.perf_counter()))
            for kind in ('small-list', 'order', 'ping'):
                if len(interactive) >= 256:
                    records.append({'kind':kind, 'id':f'{kind}-{tick}',
                                    'generator_overflow':True, 'scheduled':scheduled})
                    continue
                task = asyncio.create_task(request(kind, config['interactive_merchant'], tick, scheduled))
                interactive.add(task)
                task.add_done_callback(interactive.discard)
            tick += 1
        offered_end = time.perf_counter()
        await asyncio.gather(*dashboards, *list(interactive))
    return {'start':start, 'offered_end':offered_end, 'end':time.perf_counter(),
            'offered_rps':12, 'records':records}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(run(json.load(sys.stdin)))))
