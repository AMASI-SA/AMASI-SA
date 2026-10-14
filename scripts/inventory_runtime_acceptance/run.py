"""Synthetic acceptance runner. Executed only in a network-none Mongo namespace."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

source, evidence = map(Path, sys.argv[1:])
status = {}
def record(key, value):
    status[key] = value
    (evidence/'results.json').write_text(json.dumps(status, indent=2))
    print(key, value, flush=True)

# Check both IPv4 and IPv6: only loopback exists, with no external route.
interfaces = sorted(row['ifname'] for row in json.loads(
    subprocess.check_output(['/usr/sbin/ip', '-j', 'link', 'show'], text=True)))
assert interfaces == ['lo'], interfaces
routes = subprocess.check_output(['/usr/sbin/ip', '-j', 'route', 'show', 'table', 'all'], text=True)
routes6 = subprocess.check_output(['/usr/sbin/ip', '-6', '-j', 'route', 'show', 'table', 'all'], text=True)
for row in json.loads(routes) + json.loads(routes6):
    assert row.get('dev') in (None, 'lo'), row
    assert row.get('dst') != 'default', row
for address in ('192.0.2.1', '198.51.100.1', '203.0.113.1'):
    try:
        with socket.create_connection((address, 443), timeout=1):
            raise AssertionError('External egress unexpectedly reachable')
    except OSError as exc:
        assert exc.errno == 101, repr(exc)  # ENETUNREACH, not remote timeout
(evidence/'network-proof.json').write_text(json.dumps({
    'interfaces': interfaces, 'ipv4_routes': json.loads(routes),
    'ipv6_routes': json.loads(routes6), 'external_probes': 'ENETUNREACH',
    'environment_names_before_synthetic_configuration': sorted(os.environ),
}, indent=2))
record('EGRESS_ISOLATION', 'PASS')
assert not (source/'backend/.env').exists(), 'Unexpected environment file'
os.chdir(source/'backend')
uri = 'mongodb://127.0.0.1:27462/?replicaSet=operationalphysical'
os.environ.update({
    'MONGO_URL': uri, 'DB_NAME': 'runtime_acceptance_synthetic',
    'MZ2_TEST_MONGO_URI': uri, 'OPERATIONAL_TEST_MONGO_URI': uri,
    'MZ2_TEST_STANDALONE_URI': 'mongodb://127.0.0.1:27019/?directConnection=true',
    'PYTHONPATH': '..:.:tests', 'APP_ENV': 'test',
    'TEST_RELEASE_STARTUP_KEY': 'test:official-runtime-a27d1fb1',
    'JWT_SECRET': 'synthetic-runtime-only-key-not-a-production-secret-20261014',
    'ADMIN_EMAIL': 'runtime@example.invalid', 'ADMIN_PASSWORD': 'Synthetic-Runtime-Only-2026!',
    'EMAIL_OTP_SMTP_HOST': '127.0.0.1', 'EMAIL_OTP_SMTP_PORT': '2525',
    'EMAIL_OTP_FROM_EMAIL': 'runtime@example.invalid',
    'BACKEND_STARTUP_DELAY_SECONDS': '0', 'BACKEND_STARTUP_JITTER_SECONDS': '0',
    'MEZAN_SALLA_BRANCH_INVENTORY_SYNC_ENABLED': 'false',
})
from pymongo import MongoClient
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
client = MongoClient(uri, serverSelectionTimeoutMS=5000)
assert client.admin.command('hello')['setName'] == 'operationalphysical'
collection = client.runtime_acceptance_synthetic.transaction_probe
collection.insert_one({'_id': 'snapshot', 'value': 1})
with client.start_session() as session:
    with session.start_transaction(read_concern=ReadConcern('snapshot'), write_concern=WriteConcern('majority')):
        assert collection.find_one({'_id': 'snapshot'}, session=session)['value'] == 1
        collection.update_one({'_id': 'snapshot'}, {'$set': {'value': 2}}, session=session)
assert collection.find_one({'_id': 'snapshot'})['value'] == 2
record('MONGO_TRANSACTIONS', 'PASS')

processes = []
logs = []
try:
    # Two real independent Uvicorn processes, same clean synthetic database.
    for port in (18135, 18136):
        log = (evidence/f'backend-{port}.log').open('w')
        logs.append(log)
        processes.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server:app',
                                           '--host', '127.0.0.1', '--port', str(port)], stdout=log, stderr=subprocess.STDOUT))
    ready = {}
    deadline = time.monotonic() + 210
    while time.monotonic() < deadline and len(ready) < 2:
        for port, process in zip((18135, 18136), processes):
            if port in ready or process.poll() is not None:
                continue
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
                    if response.status == 200:
                        ready[port] = json.load(response)
            except Exception:
                pass
        if all(p.poll() is not None for p in processes):
            break
        time.sleep(1)
    (evidence/'health.json').write_text(json.dumps(ready, indent=2))
    record('STARTUP', 'PASS' if len(ready) == 2 else 'FAIL')
    record('HEALTH', 'PASS' if len(ready) == 2 else 'FAIL')

    if '--startup-only' in sys.argv:
        # The 13 unchanged contracts passed on run 38592205693. Preserve that
        # evidence; only rerun startup affected by synthetic SMTP configuration.
        sys.exit(0 if len(ready) == 2 else 1)

    targets = ['tests/test_inventory_write_gate.py']
    for name in (
        'test_exact_adoption_and_retry_do_not_double_quantity_cost_or_journal',
        'test_partial_single_invoice_adoption_is_rejected_without_effects',
        'test_valued_returns_reject_and_pending_residual_return_preserves_valuation',
        'test_failure_after_adoption_rolls_back_stock_cost_and_journal',
        'test_concurrent_distinct_invoices_cannot_over_adopt',
        'test_component_adoption_has_no_sku_and_no_double_stock',
    ):
        targets.append('tests/test_physical_receipt_financial_adoption.py::PhysicalAdoptionTests::'+name)
    targets.append('tests/test_g47_purchase_approval_integration.py::PurchaseApprovalMongoIntegration::test_real_standalone_rejects_before_any_draft_or_posting_write')
    result = subprocess.run([sys.executable, '-m', 'pytest', '--noconftest', '-p', 'no:cacheprovider',
                             '-v', '--tb=short', *targets, '--junitxml='+str(evidence/'contracts.xml')])
    cases = list(ET.parse(evidence/'contracts.xml').iter('testcase'))
    bad = [c.attrib for c in cases if any(c.find(t) is not None for t in ('failure','error','skipped'))]
    (evidence/'test-counts.json').write_text(json.dumps({'executed':len(cases),'nonpassing':bad},indent=2))
    record('TARGETED_CONTRACTS', 'PASS' if result.returncode == 0 and len(cases) == 13 and not bad else 'FAIL')
finally:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for log in logs:
        log.close()
    client.close()
assert all(value == 'PASS' for value in status.values()), status
