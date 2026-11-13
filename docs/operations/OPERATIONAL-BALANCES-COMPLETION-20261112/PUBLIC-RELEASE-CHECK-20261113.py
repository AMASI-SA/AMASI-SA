import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import production_release_guard as guard

ROOT = Path(__file__).resolve().parent
ORIGIN = 'https://mezansalla.com'
raw_intent = (ROOT / 'release/release-intent-v5.json').read_bytes()
assert hashlib.sha256(raw_intent).hexdigest() == '3973ea6676e1cc19365e3d72f8ec8673ebaf2363fc45521d5701f83748e63fd8'
intent = json.loads(raw_intent)
runtime = intent['runtime_identity']
assert runtime['source_git_sha'] == '1f8f1d673854f594086a6e87dec6c98633da0061'
assert runtime['release_id'] == 'rg5-380f5ea8b7afe39ebfc3ab2b3c28a4431d9ec77f61519ab4414bf80b7bda3e35'
backend, control = guard._runtime_source_summaries(runtime)
expected = {
    'identity_kind': runtime['kind'],
    'identity_schema_version': runtime['schema_version'],
    **{key: runtime[key] for key in (
        'release_id', 'source_git_sha', 'source_base_git_sha', 'branch',
        'protocol_version', 'critical_file_hashes', 'frontend_build',
        'frontend_reproducibility',
    )},
    'backend_runtime_source': backend,
    'release_control_source': control,
}
report = {
    'kind': 'targeted-public-release-check',
    'source_git_sha': runtime['source_git_sha'],
    'release_id': runtime['release_id'],
    'frontend_artifact_tree_sha256': runtime['frontend_build']['artifact_tree_sha256'],
    'official_guard_verify_run': False,
    'operator_lease_closed': False,
    'authenticated_financial_acceptance': False,
    'health_observations': [],
    'public_file_checks': [],
}
prepared_at = datetime.fromisoformat('2026-11-13T15:38:12.898552+00:00')
for index in range(3):
    health = guard._health_payload(ORIGIN)
    release = health.get('release') or {}
    assert health.get('ok') is True
    for key in (
        'verified_identity_available', 'critical_file_hashes_match',
        'backend_runtime_source_verified', 'release_control_source_bound',
        'frontend_build_verified',
    ):
        assert release.get(key) is True, key
    assert release.get('git_sha') == runtime['source_git_sha']
    mismatches = [key for key, value in expected.items() if not guard.exact_json_equal(release.get(key), value)]
    assert not mismatches, mismatches
    boot = datetime.fromisoformat(release['boot_started_at'])
    assert prepared_at - timedelta(minutes=5) <= boot <= datetime.now(timezone.utc) + timedelta(minutes=5)
    observation = {'checked_at': datetime.now(timezone.utc).isoformat(), 'boot_started_at': release['boot_started_at'], 'exact_identity_match': True}
    report['health_observations'].append(observation)
    print('HEALTH_PASS ' + json.dumps({'probe': index + 1, **observation}), flush=True)

build = runtime['frontend_build']
selected = [record for record in build['public_files'] if (
    record['path'] in ('sw.js', 'service-worker.js')
    or any(fragment in record['path'] for fragment in (
        'OperationalFinancialPosition-', 'visibleOperationalRead-', 'assets/index-',
    ))
)]
targets = [('/build-meta.json', build['build_meta']), ('/', build['index']), ('/index.html', build['index']), ('/operational-balances/financial-position', build['index'])]
targets.extend(('/' + record['path'], record) for record in selected)
for path, record in targets:
    accepts = ('application/json', '*/*') if path == '/build-meta.json' else ('*/*',)
    for accept in accepts:
        for cache_bust in (False, True):
            response = guard._fetch_production(ORIGIN, path, cache_bust=cache_bust, accept=accept, timeout=30, service_worker_script=path in guard.STANDARD_SERVICE_WORKER_PATHS)
            label = path + ('?release_check' if cache_bust else '') + ' [' + accept + ']'
            if path == '/build-meta.json':
                guard._assert_build_meta_response(response, label=label)
            guard._assert_public_bytes(response, expected=record, label=label)
            if not cache_bust and path in ('/', '/index.html', '/operational-balances/financial-position'):
                guard._assert_html_shell_headers(response, path)
            if path in guard.STANDARD_SERVICE_WORKER_PATHS:
                guard._assert_service_worker_headers(response, path)
            report['public_file_checks'].append({'path': path, 'accept': accept, 'cache_bust': cache_bust, 'bytes': record['bytes'], 'sha256': record['sha256'], 'passed': True})
    print('PUBLIC_FILE_PASS ' + path, flush=True)

report['completed_at'] = datetime.now(timezone.utc).isoformat()
report['targeted_checks_passed'] = True
(ROOT / 'targeted-public-check-result.json').write_text(json.dumps(report, indent=2))
print('TARGETED_PUBLIC_PASS ' + json.dumps({key: report[key] for key in ('source_git_sha', 'release_id', 'frontend_artifact_tree_sha256', 'completed_at', 'official_guard_verify_run', 'operator_lease_closed', 'authenticated_financial_acceptance')} | {'health_checks': len(report['health_observations']), 'public_file_requests': len(report['public_file_checks'])}), flush=True)
