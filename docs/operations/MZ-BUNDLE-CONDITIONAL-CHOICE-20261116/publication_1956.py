#!/usr/bin/env python3
"""One-shot PR1956 prepublish or verify; never prepare, publish, or abort.

Usage: python -B bundle-publication-1956.py {prepublish,verify} RECEIPT_DIRECTORY
Verify additionally reads deployment-success.json in that private directory.
"""
import sys
sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import traceback

ROOT = Path('/app')
J = 'b05cd3c9b25ce22df1b2dd06c23d97606bd632c6'
A = '7a1de33535081393fbf3597523d722ec308b2ca8'
B = '4da5d711717146088d879f4d8426cda9958f9d36'
TREE = '49140bb5e22b368788c181362f316218344e9d41'
RELEASE = 'rg5-4890bb5f409d7f6080ad2c19f881ed5edfe30b8f93b9502fbb3e45977f6a28f1'
ACTOR = 'codex-bundle-conditional-cost-1956-20261116'
BRANCH = 'hotfix/prod-snap-meta-final'
ORIGIN = 'https://mezansalla.com'
GUARD_SHA256 = '4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    require(path.resolve(strict=True) == path and stat.S_ISREG(path.lstat().st_mode),
            'Receipt must be a real regular file')
    raw = path.read_bytes()
    value = json.loads(raw)
    require(isinstance(value, dict), 'Receipt must contain a JSON object')
    return value, digest(raw)


def write_once(directory, name, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode('utf-8')
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    return digest(raw)


def git(*arguments):
    return subprocess.check_output(['git', '-C', str(ROOT), *arguments]).decode().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepublish', 'verify'))
    parser.add_argument('receipts', type=Path)
    args = parser.parse_args()
    directory = args.receipts.absolute()
    require(directory.parent == Path('/tmp') and directory.name.startswith('mz-bundle-release-1956-'),
            'Unexpected receipt directory')
    require(directory.resolve(strict=True) == directory and stat.S_ISDIR(directory.lstat().st_mode)
            and stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0,
            'Receipt directory must be real and private')
    require(ROOT.resolve(strict=True) == ROOT and ROOT.is_dir()
            and (ROOT / '.git').is_dir() and not (ROOT / '.git').is_symlink(),
            'Expected original real /app Git worktree')
    require(ROOT not in Path(__file__).resolve().parents, 'Wrapper must remain outside /app')
    guard_path = ROOT / 'scripts/production_release_guard.py'
    require(guard_path.resolve(strict=True) == guard_path and stat.S_ISREG(guard_path.lstat().st_mode)
            and digest(guard_path.read_bytes()) == GUARD_SHA256, 'Official guard bytes changed')
    sys.path.insert(0, str(ROOT))
    from scripts import production_release_guard as guard
    require(guard.REPO_ROOT == ROOT and guard.GIT_DIR == ROOT / '.git', 'Unexpected guard worktree')

    prepared, prepare_sha256 = read_json(directory / 'prepare.json')
    expected = {'actor': ACTOR, 'source_git_sha': A, 'git_sha': A, 'source_base_git_sha': J,
                'release_id': RELEASE, 'branch': BRANCH, 'protocol_version': 5}
    require('active' not in prepared and all(guard.exact_json_equal(prepared.get(k), v)
            for k, v in expected.items()), 'Prepare receipt is not the owned PR1956 lease')
    merged = prepared.get('deployment_git_sha')
    require(isinstance(merged, str) and re.fullmatch('[0-9a-f]{40}', merged), 'Invalid deployment SHA')
    prepared_at, prepared_time = guard._aware_timestamp(prepared.get('prepared_at'), 'prepared_at')
    require(prepared_at == prepared['prepared_at'], 'Noncanonical prepared timestamp')
    require(guard._validated_release_lease(prepared) == (A, merged, RELEASE), 'Invalid prepared identity')
    expected.update(deployment_git_sha=merged, prepared_at=prepared_at)
    binding = {**expected, 'pr': 1956, 'prepare_sha256': prepare_sha256}
    result_identity = {key: expected[key] for key in
                       ('source_git_sha', 'git_sha', 'deployment_git_sha', 'release_id', 'protocol_version')}

    deployment_sha256 = None
    if args.mode == 'verify':
        prepublish, prepublish_sha256 = read_json(directory / 'prepublish.json')
        success, _ = read_json(directory / 'prepublish-success.json')
        require(success.get('ok') is True and success.get('mode') == 'prepublish'
                and success.get('canonical_result_sha256') == prepublish_sha256
                and all(guard.exact_json_equal(success.get(k), v) for k, v in binding.items()),
                'No successful prepublish for this exact prepare receipt')
        require(guard.exact_json_equal(prepublish, {'ready_to_publish': True, **result_identity}),
                'Prepublish result differs from the owned identity')
        _, prepublish_started = guard._aware_timestamp(success.get('started_at'), 'prepublish started_at')
        _, prepublish_finished = guard._aware_timestamp(success.get('finished_at'), 'prepublish finished_at')
        deployment, deployment_sha256 = read_json(directory / 'deployment-success.json')
        deployment_expected = {'pr': 1956, 'prepare_sha256': prepare_sha256,
                               'deployment_git_sha': merged, 'release_id': RELEASE,
                               'status': 'Deployment Succeeded', 'observed_by': 'root'}
        require(all(guard.exact_json_equal(deployment.get(k), v) for k, v in deployment_expected.items()),
                'No root-recorded UI deployment success for this prepared release')
        versions = [deployment.get(key) for key in ('previous_platform_version', 'platform_version')]
        require(all(isinstance(value, str) and value == value.strip() and 0 < len(value) <= 128
                    for value in versions) and versions[0] != versions[1], 'A new platform version is required')
        _, published = guard._aware_timestamp(deployment.get('published_at'), 'published_at')
        _, observed = guard._aware_timestamp(deployment.get('observed_at'), 'observed_at')
        require(prepared_time <= prepublish_started <= prepublish_finished <= published <= observed
                <= datetime.now(timezone.utc), 'UI success must follow this prepare and successful prepublish')

    os.umask(0o077)
    started_at = now()
    # Exclusive attempt receipt prevents a second canonical invocation, even after failure.
    write_once(directory, args.mode + '-start.json', {
        **binding, 'mode': args.mode, 'started_at': started_at, 'pid': os.getpid(),
        'wrapper_sha256': digest(Path(__file__).read_bytes()),
        'deployment_success_sha256': deployment_sha256,
    })
    try:
        before = guard.status()  # Public status acquires and releases its own mutex.
        write_once(directory, args.mode + '-status-before.json', before)
        require(guard.exact_json_equal(before, {'active': True, **prepared}),
                'Active canonical status differs from the owned prepare receipt')
        with guard._release_operation_lock():
            # Recheck the complete lease under the same mutex as the actual operation.
            guard._assert_active_lease_unchanged(prepared)
            require(git('branch', '--show-current') == BRANCH and git('rev-parse', 'HEAD') == merged,
                    'Original worktree no longer has the prepared deployment')
            require(git('show', '-s', '--format=%P', merged).split() == [J, B], 'Merge parents differ')
            require(git('rev-parse', merged + '^{tree}') == TREE
                    and git('rev-parse', B + '^{tree}') == TREE, 'Reviewed deployment tree differs')
            if args.mode == 'prepublish':
                result = guard._prepublish_locked()
            else:
                result = guard._verify_locked(ORIGIN)  # Exactly one official three-round verification.
            result_sha256 = write_once(directory, args.mode + '.json', result)
            require(all(guard.exact_json_equal(result.get(k), v) for k, v in result_identity.items()),
                    'Canonical result identity differs')
            if args.mode == 'prepublish':
                require(result.get('ready_to_publish') is True, 'Canonical prepublish did not succeed')
            else:
                require(result.get('verified') is True and result.get('checks') == 3
                        and result.get('source_base_git_sha') == J and result.get('url') == ORIGIN,
                        'Canonical verification did not complete all three rounds')
                require(not guard.LEASE_DIR.exists() and not guard.LEASE_DIR.is_symlink(),
                        'Canonical verification did not fully close the lease')
        if args.mode == 'verify':
            after = guard.status()  # Outside the previous mutex: no nested lock.
            write_once(directory, 'verify-status-after.json', after)
            require(guard.exact_json_equal(after, {'active': False}), 'Ending canonical status is not inactive')
        write_once(directory, args.mode + '-success.json', {
            **binding, 'ok': True, 'mode': args.mode, 'started_at': started_at,
            'finished_at': now(), 'canonical_result_sha256': result_sha256,
            'deployment_success_sha256': deployment_sha256,
        })
        outcome = ({'ready_to_publish': True} if args.mode == 'prepublish'
                   else {'verified': True, 'checks': 3, 'active': False})
        print(json.dumps({'ok': True, 'mode': args.mode, 'source_git_sha': A,
                          'deployment_git_sha': merged, 'release_id': RELEASE, **outcome}, sort_keys=True), flush=True)
    except Exception as error:
        write_once(directory, args.mode + '-failure.json', {
            **binding, 'ok': False, 'mode': args.mode, 'observed_at': now(),
            'error_type': type(error).__name__, 'detail': str(error), 'traceback': traceback.format_exc(),
        })
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__}, sort_keys=True), flush=True)
        raise SystemExit(1)
