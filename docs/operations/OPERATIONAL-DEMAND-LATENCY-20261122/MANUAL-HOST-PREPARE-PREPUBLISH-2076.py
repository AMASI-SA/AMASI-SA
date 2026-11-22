"""Run only after the reviewed staging helper succeeds, immediately before publish."""
import json
import os
import subprocess
import sys
from pathlib import Path

APP = Path('/app')
EXPECTED = {
    'deployment_git_sha': '02cd99e5b2b68c95341c87590abcf572c885e697',
    'source_git_sha': '108024c00b7f367fd1d6b15874d7a73d55de2620',
    'release_id': 'rg5-e4e41b98edc9f49b8e74adad2a905d362b7113b5b1a1fa83436a4c1ba8c4b918',
    'protocol_version': 5,
}
BASE = '24270ebdfdebb83282389308a2349cdb27e2b8e4'
TREE = '8f915a681de43fed9da1ec98685a56f87a4c8bcb'
ACTOR = 'codex-operational-demand-latency-2076-20261122'

def matches(value):
    return (isinstance(value, dict) and type(value.get('protocol_version')) is int
            and all(value.get(key) == expected for key, expected in EXPECTED.items()))

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
    if any(key in os.environ for key in (
        'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE', 'GIT_DIR',
        'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_SHALLOW_FILE', 'GIT_NAMESPACE', 'GIT_CONFIG_PARAMETERS',
        'GIT_CONFIG_COUNT', 'GIT_CONFIG', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
        'GIT_TRACE', 'GIT_TRACE_PACKET', 'GIT_TRACE_CURL', 'GIT_CURL_VERBOSE',
        'GIT_TRACE2_EVENT', 'GIT_TRACE2_PERF',
    )) or any(key.startswith('GIT_TRACE') for key in os.environ):
        raise RuntimeError('Unexpected CI or Git routing environment')
    if Path.cwd().resolve() != APP:
        raise RuntimeError('Original /app working directory required')
    sys.path.insert(0, str(APP))
    from scripts import production_release_guard as guard
    if (guard.REPO_ROOT.resolve() != APP
            or Path(guard.__file__).resolve() != APP / 'scripts/production_release_guard.py'
            or guard.PROTOCOL_VERSION != 5):
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
    # Match our lease under the unchanged original operation lock before
    # executing the original prepublish body. Never check another actor's lease.
    with guard._release_operation_lock():
        if (not guard.LEASE_PATH.is_file()
                or guard._read_json(guard.LEASE_PATH) != lease):
            raise RuntimeError('Own prepared lease changed before prepublish')
        ready = guard._prepublish_locked()
    if ready.get('ready_to_publish') is not True or not matches(ready):
        raise RuntimeError('Prepublish identity mismatch')
    state = guard.status()
    if state != {'active': True, **lease}:
        raise RuntimeError('Own prepared lease changed')
    show('OWN_LEASE_ACTIVE', state)
    show('PREPUBLISH_PASSED', ready)
    print('NEXT: click Re-publish changes exactly once now; wait for a new Deployment Succeeded.', flush=True)
except Exception as error:
    # Original errors may include source paths or remote details; retain the
    # failing phase and class, and do not expose arbitrary exception text.
    print('STOP', phase, type(error).__name__, flush=True)
    try:
        if guard is None:
            raise RuntimeError('Guard not imported')
        show('GUARD_AFTER_STOP', guard.status())
    except Exception:
        print('GUARD_AFTER_STOP unavailable', flush=True)
    print('Do not publish. No lease was manually aborted or cleared.', flush=True)
    raise SystemExit(1)
