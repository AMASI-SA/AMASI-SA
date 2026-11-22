#!/usr/bin/env bash
# One reviewed owner command after the ordinary PR2079 merge and exact CI.
# Rehearses, preserves/stages, and prepares the original guard. Never publishes.
set -euo pipefail
cd /app
PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0 python -B - <<'PY'
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path('/app')
BASE = '02cd99e5b2b68c95341c87590abcf572c885e697'
B = 'd5036d9609b7155ee137d07ff802bbce5be265c5'
D = None
EVIDENCE = None
PREFIX = 'docs/operations/TABBY-DASHBOARD-FEES-20261122/'
FILES = (
    ('MANUAL-HOST-ISOLATED-REHEARSAL-2079.py', None),
    ('MANUAL-HOST-STAGE-2079.py', None),
    ('MANUAL-HOST-PREPARE-PREPUBLISH-2079.py', None),
    ('RUN-HOST-VERIFY-2079.sh', None),
)

def run(label, argv, cwd=APP):
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True,
            stdin=subprocess.DEVNULL, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit('STOP: ' + label + ' could not complete') from None
    if result.returncode:
        raise SystemExit('STOP: ' + label + ' exit=' + str(result.returncode))
    return result.stdout

if (not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value)
            for value in (BASE, B, D, EVIDENCE))
        or not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                   for _, value in FILES)):
    raise SystemExit('STOP: disabled release bundle; final release/helper pins are not configured')
if Path.cwd() != APP or APP.is_symlink() or APP.resolve() != APP:
    raise SystemExit('STOP: original real /app working directory required')
if any(os.environ.get(key) for key in (
    'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE', 'GIT_DIR',
    'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_CONFIG_PARAMETERS',
    'GIT_CONFIG_COUNT', 'GIT_CONFIG', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
    'GIT_TRACE', 'GIT_TRACE_PACKET', 'GIT_TRACE_CURL', 'GIT_CURL_VERBOSE',
    'GIT_TRACE2_EVENT', 'GIT_TRACE2_PERF',
)):
    raise SystemExit('STOP: unexpected CI or Git routing environment')
state = json.loads(run('original release guard', [
    sys.executable, '-B', 'scripts/production_release_guard.py', 'status',
]))
if state != {'active': False}:
    raise SystemExit('STOP: existing or incomplete lease; preserved')
head = run('shared HEAD', ['git', '--no-optional-locks', 'rev-parse', 'HEAD']).decode().strip()
dirty = run('shared source status', [
    'git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no',
])
if head != BASE or dirty.strip():
    raise SystemExit('STOP: shared source changed; nothing updated')
branch = run('shared branch', ['git', '--no-optional-locks', 'branch', '--show-current']).decode().strip()
if branch != 'hotfix/prod-snap-meta-final':
    raise SystemExit('STOP: shared branch changed; nothing updated')
remote_refs = run('reviewed remote tips', [
    'git', '--no-optional-locks', 'ls-remote', '--exit-code', 'origin',
    'refs/heads/hotfix/prod-snap-meta-final',
    'refs/heads/codex/tabby-weekly-fee-estimates-r3-20261122',
]).decode().splitlines()
refs = dict(line.split()[::-1] for line in remote_refs)
if (refs.get('refs/heads/hotfix/prod-snap-meta-final') != D
        or refs.get('refs/heads/codex/tabby-weekly-fee-estimates-r3-20261122') != B):
    raise SystemExit('STOP: remote release references changed; nothing updated')

work = Path(tempfile.mkdtemp(prefix='mezan-pr2079-deploy-', dir='/tmp'))
repo = work / 'repo'
run('isolated helper checkout', [
    'git', 'clone', '--local', '--no-hardlinks', '--no-checkout', str(APP), str(repo),
], cwd=work)
remote = run('origin lookup', ['git', 'remote', 'get-url', 'origin']).decode().strip()
run('fetch pinned helpers', [
    'git', '--no-optional-locks', 'fetch', '--no-tags', '--no-write-fetch-head',
    '--no-auto-maintenance', remote, EVIDENCE,
], cwd=repo)
del remote
payloads = []
for name, expected in FILES:
    content = run('read pinned helper', ['git', 'show', EVIDENCE + ':' + PREFIX + name], cwd=repo)
    if hashlib.sha256(content).hexdigest() != expected:
        raise SystemExit('STOP: helper checksum mismatch: ' + name)
    payloads.append((name, content))
    print('HELPER_VERIFIED', name, expected, flush=True)

verification = work / 'verify-after-success.sh'
verification.write_bytes(payloads[3][1])
verification.chmod(0o400)
print('VERIFY_COMMAND:', 'bash ' + str(verification), flush=True)
receipt = work / 'rehearsal-receipt.json'

for index, (name, content) in enumerate(payloads[:3]):
    options = ['--receipt', str(receipt)] if index < 2 else []
    result = subprocess.run(
        [sys.executable, '-B', '-c', content.decode('utf-8'), *options],
        cwd=APP, stdin=subprocess.DEVNULL,
    )
    if result.returncode:
        raise SystemExit('STOP: ' + name + ' failed; retain the printed recovery details and do not publish')
print('VERIFY_COMMAND:', 'bash ' + str(verification), flush=True)
print('Run VERIFY_COMMAND only after the new Deployment Succeeded.', flush=True)
PY
