#!/usr/bin/env bash
# One reviewed owner command after the ordinary PR2086 merge and exact CI.
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
BASE = 'c2d244a3349013c06f3fb7824070bfa12bd0bfe0'
B = '104c6b45cd646418a719496fb8c02ee61ddf5483'
D = '76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43'
EVIDENCE = '2b08518f5448d742d1986a45c69e0fb5a66129d7'
PREFIX = 'docs/operations/TABBY-DASHBOARD-FEES-20261122/'
FILES = (
    ('MANUAL-HOST-ISOLATED-REHEARSAL-2086.py', '5fe424bfb7ace043cd29d31b7124643bcc8f7f53f7dfff570080a2e9d74931ba'),
    ('MANUAL-HOST-STAGE-2086.py', '7d2974136db042a4246bf0b30725cf575b600b837904f02d6abe45b981cd083f'),
    ('MANUAL-HOST-PREPARE-PREPUBLISH-2086.py', '55900e07e8967edf427dc5e5d655b0bd7457bbfadb2b5c6ce3348ae1c2b63cf4'),
    ('RUN-HOST-VERIFY-2086.sh', '4210c4c9a2e6d7cbc418154abb354c967dfc8fa44d4bd30d4bdb93d631ddc043'),
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
    'refs/heads/codex/tabby-weekly-fee-estimates-r4-20261123',
]).decode().splitlines()
refs = dict(line.split()[::-1] for line in remote_refs)
if (refs.get('refs/heads/hotfix/prod-snap-meta-final') != D
        or refs.get('refs/heads/codex/tabby-weekly-fee-estimates-r4-20261123') != B):
    raise SystemExit('STOP: remote release references changed; nothing updated')

work = Path(tempfile.mkdtemp(prefix='mezan-pr2086-deploy-', dir='/tmp'))
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
