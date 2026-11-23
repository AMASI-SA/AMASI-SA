"""Capture the original production verifier and its final lease status."""
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

APP = Path('/app')
OUT = Path('/tmp/mezan-inventory-side-options-r4-20261124/publication')
A = 'deeefceffd9a1e37968513ac4f3833314bb415fd'
D = '5cfaac21a1775a0d0761d05aa571f94dbfc02d32'
RELEASE = 'rg5-ffe0d4437c779b1e455f084be407f8f2d24735f5bf8e994f77c5bf4cbeb1c55f'
ARTIFACT = 'df85c5736ec64571f44a5db506cb0daf34c1ac584ca3115e4e3ce86ebe80898c'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', GIT_TERMINAL_PROMPT='0')

def run(name, args, timeout=180):
    with (OUT / (name + '.stdout.json')).open('x') as stdout, (OUT / (name + '.stderr.log')).open('x') as stderr:
        completed = subprocess.run(['python', '-B', 'scripts/production_release_guard.py', *args],
                                   cwd=APP, env=ENV, stdout=stdout, stderr=stderr, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(name + '-original-guard-failed-exit-' + str(completed.returncode))
    return json.loads((OUT / (name + '.stdout.json')).read_text())

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def main():
    started = time.monotonic()
    summary = {'exit_code': 1, 'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        require(not (OUT / 'verify-result.json').exists(), 'verification-result-already-exists')
        status = run('before-verify', ['status'])
        require(status.get('active') is True and status.get('actor') == 'inventory-side-options-r4'
                and status.get('source_git_sha') == A and status.get('deployment_git_sha') == D
                and status.get('release_id') == RELEASE, 'not-the-owned-release-lease')
        verified = run('verify', ['verify', '--url', 'https://mezansalla.com'], timeout=3600)
        require(verified.get('verified') is True and verified.get('checks') == 3
                and verified.get('source_git_sha') == A and verified.get('deployment_git_sha') == D
                and verified.get('release_id') == RELEASE
                and verified.get('frontend_artifact_tree_sha256') == ARTIFACT, 'unexpected-verification-result')
        final = run('after-verify', ['status'])
        require(final.get('active') is False and not final.get('incomplete_lease_directory'), 'lease-not-closed')
        raw = (OUT / 'verify.stdout.json').read_bytes()
        summary.update(exit_code=0, verified=True, checks=verified['checks'], source_git_sha=A,
                       source_base_git_sha=verified['source_base_git_sha'], deployment_git_sha=D,
                       release_id=RELEASE, frontend_artifact_tree_sha256=ARTIFACT,
                       frontend_checked_requests_per_probe=len(verified['frontend_checked_requests']),
                       boot_started_at=verified['boot_started_at'],
                       boot_started_at_observations=verified['boot_started_at_observations'],
                       backend_runtime_source=verified['backend_runtime_source'],
                       release_control_source=verified['release_control_source'],
                       frontend_shell_cache=verified['frontend_shell_cache'],
                       frontend_service_workers=verified['frontend_service_workers'],
                       original_verify_stdout_bytes=len(raw),
                       original_verify_stdout_sha256=hashlib.sha256(raw).hexdigest(), guard=final)
    except Exception as error:
        summary.update(error_type=type(error).__name__, error=str(error))
    summary.update(elapsed_seconds=round(time.monotonic() - started, 3),
                   finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    with (OUT / 'verify-result.json').open('x') as target:
        json.dump(summary, target, indent=2)
        target.write('\n')
    print('INVENTORY_R4_PRODUCTION_VERIFY_RESULT', json.dumps(summary), flush=True)
    return summary['exit_code']

if __name__ == '__main__':
    raise SystemExit(main())
