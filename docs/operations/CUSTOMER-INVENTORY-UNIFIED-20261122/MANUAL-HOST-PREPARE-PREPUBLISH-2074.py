"""Run only after the reviewed staging helper succeeds, immediately before publish."""
import json
import os
import subprocess
import sys
from pathlib import Path

APP = Path('/app')
EXPECTED = {
    'deployment_git_sha': '24270ebdfdebb83282389308a2349cdb27e2b8e4',
    'source_git_sha': '4e6c8a61bd1566e061a4f312e797ce239b582475',
    'release_id': 'rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f',
    'protocol_version': 5,
}
BASE = '06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35'
TREE = '6a906db6cf4fd3dda4b550483a0ef96067c6f1ed'
ACTOR = 'codex-customer-inventory-unified-2074-20261122'

def matches(value):
    return all(value.get(key) == expected for key, expected in EXPECTED.items())

def show(label, value):
    keys = (*EXPECTED, 'source_base_git_sha', 'actor', 'prepared_at', 'active',
            'incomplete_lease_directory', 'ready_to_publish')
    print(label, json.dumps({key: value[key] for key in keys if key in value}), flush=True)

def git(*args):
    result = subprocess.run(
        ['git', '--no-optional-locks', *args], cwd=APP,
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )
    if result.returncode:
        raise RuntimeError('Original Git preflight failed; remote details omitted')
    return result.stdout.strip()

phase = 'preflight'
guard = None
try:
    if any(os.environ.get(key) for key in (
        'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE', 'GIT_DIR',
        'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_CONFIG_PARAMETERS',
        'GIT_CONFIG_COUNT', 'GIT_CONFIG', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
        'GIT_TRACE', 'GIT_TRACE_PACKET', 'GIT_TRACE_CURL', 'GIT_CURL_VERBOSE',
        'GIT_TRACE2_EVENT', 'GIT_TRACE2_PERF',
    )):
        raise RuntimeError('Unexpected CI or Git routing environment')
    if Path.cwd().resolve() != APP:
        raise RuntimeError('Original /app working directory required')
    sys.path.insert(0, str(APP))
    from scripts import production_release_guard as guard
    if guard.REPO_ROOT.resolve() != APP:
        raise RuntimeError('Original /app guard required')
    if git('branch', '--show-current') != 'hotfix/prod-snap-meta-final':
        raise RuntimeError('Shared branch changed')
    if git('rev-parse', 'HEAD') != EXPECTED['deployment_git_sha']:
        raise RuntimeError('Shared deployment commit changed')
    if git('rev-parse', 'HEAD^{tree}') != TREE:
        raise RuntimeError('Shared deployment tree changed')
    if git('status', '--porcelain', '--untracked-files=no'):
        raise RuntimeError('Shared tracked source is dirty')
    state = guard.status()
    show('GUARD_BEFORE_PREPARE', state)
    if state != {'active': False}:
        raise RuntimeError('Existing or incomplete lease; preserved without changes')

    phase = 'prepare'
    lease = guard.prepare(ACTOR)
    if not matches(lease) or lease.get('actor') != ACTOR:
        raise RuntimeError('Prepared lease differs from this release')
    if lease.get('source_base_git_sha') != BASE:
        raise RuntimeError('Prepared source base differs from this release')
    show('PREPARED', lease)

    phase = 'prepublish'
    ready = guard.prepublish()
    if ready.get('ready_to_publish') is not True or not matches(ready):
        raise RuntimeError('Prepublish identity mismatch')
    state = guard.status()
    if state != {'active': True, **lease}:
        raise RuntimeError('Own prepared lease changed')
    show('OWN_LEASE_ACTIVE', state)
    show('PREPUBLISH_PASSED', ready)
    print('NEXT: click Re-publish changes exactly once now; wait for a new Deployment Succeeded.', flush=True)
except Exception as error:
    reason = str(error)
    if reason.startswith('git '):
        reason = 'Original Git check failed; remote details omitted'
    elif reason.startswith('another production release is active:'):
        reason = 'Another release became active; its lease was preserved'
    print('STOP', phase, type(error).__name__, reason, flush=True)
    try:
        if guard is None:
            raise RuntimeError('Guard not imported')
        show('GUARD_AFTER_STOP', guard.status())
    except Exception:
        print('GUARD_AFTER_STOP unavailable', flush=True)
    print('Do not publish. No lease was manually aborted or cleared.', flush=True)
    raise SystemExit(1)
