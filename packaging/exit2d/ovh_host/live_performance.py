"""Operator-run external HTTPS performance capture; synthetic sessions in memory only.

No database access, credential persistence, provider calls or operational writes.
Supply cookies from a genuine login to the dedicated synthetic instance only.
"""
import argparse
import concurrent.futures
import getpass
import json
import math
from pathlib import Path
import statistics
import threading
import time
import urllib.error
import urllib.request
from config import load

PATHS = ('/api/auth/me', '/api/supplier-dispatch-v1/waiting-page',
         '/api/supplier-dispatch-v1/workspace?grain=piece',
         '/api/preparation-work-v1/assembly/orders?state=in_progress',
         '/api/fulfillment-v2/ready-to-ship')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def fetch(origin, path, cookie=''):
    # Default platform trust store, verified TLS, no ambient HTTP proxy or redirects.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {'Origin': origin}
    if cookie:
        headers['Cookie'] = cookie
    request = urllib.request.Request(origin + path, headers=headers)
    started = time.perf_counter()
    with opener.open(request, timeout=30) as response:
        if not response.headers.get('Content-Type', '').startswith('application/json'):
            raise ValueError('expected JSON response')
        value = json.loads(response.read(4 * 1024 * 1024))
    return value, (time.perf_counter() - started) * 1000

def handshake(config):
    metadata, _ = fetch(config['origin'].rstrip('/'), '/build-meta.json')
    if metadata != {'kind': 'synthetic-hosting-only-v1', 'source_sha': config['source_sha']}:
        raise ValueError('synthetic source handshake refused before credentials')

def summarize(values):
    ordered = sorted(values)
    return {'requests': len(values), 'p50_ms': round(statistics.median(values), 3),
            'p95_ms': round(ordered[max(0, math.ceil(len(ordered) * .95) - 1)], 3),
            'maximum_ms': round(ordered[-1], 3)}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--config', required=True)
    parser.add_argument('--seconds', type=int, default=120); parser.add_argument('--client-location', default='unverified')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 30 <= args.seconds <= 900:
        parser.error('duration must be 30..900 seconds')
    config = load(args.config); handshake(config)
    owner = getpass.getpass('Synthetic owner session Cookie (not saved): ')
    employee = getpass.getpass('Synthetic employee session Cookie (not saved): ')
    owner_me, _ = fetch(config['origin'], '/api/auth/me', owner)
    employee_me, _ = fetch(config['origin'], '/api/auth/me', employee)
    if owner_me.get('role') != 'owner' or employee_me.get('role') not in ('viewer', 'employee'):
        raise ValueError('genuine owner and employee sessions required')
    stop = threading.Event(); analytics = []; errors = []; samples = {p: [] for p in PATHS}
    def reports():
        while not stop.is_set():
            try:
                _, elapsed = fetch(config['origin'], '/api/dashboard?from_date=2026-09-06&to_date=2026-09-06', owner)
                analytics.append(elapsed)
            except Exception as error:
                errors.append('analytics:' + type(error).__name__)
            stop.wait(.25)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(reports) for _ in range(4)]
        until = time.monotonic() + args.seconds
        try:
            while time.monotonic() < until:
                for path in PATHS:
                    try:
                        _, elapsed = fetch(config['origin'], path, employee); samples[path].append(elapsed)
                    except Exception as error:
                        errors.append(path + ':' + type(error).__name__)
                    time.sleep(.2)
        finally:
            stop.set()
            for future in futures: future.result()
    receipt = {'classification': 'synthetic-only', 'source_sha': config['source_sha'],
               'client_location_operator_declared': args.client_location, 'analytics_requests': len(analytics),
               'paths': {k: summarize(v) for k, v in samples.items() if v}, 'errors': errors,
               'snap_overlap': 'must-correlate-host-snap-progress-receipt-and-UTC-monitor-window',
               'operational_writes': False, 'production_contacted': False}
    args.output.write_text(json.dumps(receipt, indent=2))
    if errors or any(len(x) < 20 for x in samples.values()) or len(analytics) < 4:
        raise SystemExit('Performance acceptance incomplete or failed; sanitized receipt saved')
    print('Synthetic HTTPS measurements saved; compare with host CPU/RAM and Snap progress before acceptance')

if __name__ == '__main__': main()
