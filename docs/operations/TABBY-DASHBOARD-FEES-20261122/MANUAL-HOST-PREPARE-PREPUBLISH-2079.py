"""Run only after the reviewed staging helper succeeds, immediately before publish."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

APP = Path('/app')
EXPECTED = {
    'deployment_git_sha': None,
    'source_git_sha': 'd6443504c7d16eba00142526478abf10de336f99',
    'release_id': 'rg5-b26fcb18b80fb02cb6b77c0781819c2467f4a6a20d7d2350516febf22957fa11',
    'protocol_version': 5,
}
BASE = '02cd99e5b2b68c95341c87590abcf572c885e697'
TREE = '83fc2223913c996c19f4fa676da9cfa8c54ebb49'
ARTIFACT = 'd37d10537c6b55f4f0e4a4392ed40eca5bef962ea6cabaf822acc6a5e47a9d99'
ACTOR = 'codex-tabby-weekly-fees-2079-20261122'

def configured():
    if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value)
               for value in (BASE, TREE, EXPECTED['deployment_git_sha'], EXPECTED['source_git_sha'])):
        raise RuntimeError('Disabled release bundle; final commit/tree pins are not configured')
    if (not isinstance(EXPECTED['release_id'], str)
            or not re.fullmatch(r'rg5-[0-9a-f]{64}', EXPECTED['release_id'])
            or not isinstance(ARTIFACT, str) or not re.fullmatch(r'[0-9a-f]{64}', ARTIFACT)):
        raise RuntimeError('Disabled release bundle; final release/artifact pins are not configured')

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

def main():
    phase = 'preflight'
    guard = None
    try:
        configured()
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
        if git('ls-remote', '--exit-code', 'origin', 'refs/heads/hotfix/prod-snap-meta-final').split() != [
                EXPECTED['deployment_git_sha'], 'refs/heads/hotfix/prod-snap-meta-final']:
            raise RuntimeError('Remote Production changed')
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
        if (lease.get('frontend_build') or {}).get('artifact_tree_sha256') != ARTIFACT:
            raise RuntimeError('Prepared artifact differs from this release')
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


if __name__ == '__main__':
    main()
