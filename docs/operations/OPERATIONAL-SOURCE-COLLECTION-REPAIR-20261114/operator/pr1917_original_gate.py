#!/usr/bin/env python3
"""PR1917 prepare/prepublish only; root reviews and executes after rehearsal.

No publication, financial request, abort, lease removal, source mutation or
cleanup is performed by this wrapper. Only the official guard can change /app.
Each stage consumes a separate exclusive evidence prefix; never auto-retry.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


APP = Path('/app')
EVIDENCE = Path('/tmp/mezan-pr1917-original-jpg_gysb')
SOURCE = '8839d3aeb32b4bda2de2015efb0deca7fbb844a3'
BASE = '9c71435f4a19977c09f4331dee8fddbfa12a45eb'
INTENT_COMMIT = '1469f4916715458b8374beb3bfddfc1f3c01f125'
MERGE = '2db0edc9edb315026eee4b6edd43b420a569c37d'
TREE = '9d8844f32f9db18cd036ff1b55edaec2c0dbe1b8'
RELEASE = 'rg5-e81e97c26945236de423fc17e997db852574b424ba799a3fbba37f4a71d4b7a4'
ACTOR = 'codex-operational-source-collection-rootfix-1917'
BRANCH = 'hotfix/prod-snap-meta-final'
IDENTITY = {'source_git_sha': SOURCE, 'git_sha': SOURCE,
            'deployment_git_sha': MERGE, 'release_id': RELEASE, 'protocol_version': 5}
LEASE_IDENTITY = {**IDENTITY, 'source_base_git_sha': BASE, 'actor': ACTOR, 'branch': BRANCH}


class Stop(Exception):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    """Exclusive evidence: a partial or previous record must be reviewed."""
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def read_json(name):
    return json.loads((EVIDENCE / name).read_bytes())


def require_fields(value, expected, reason):
    if not isinstance(value, dict) or any(
            type(value.get(key)) is not type(wanted) or value.get(key) != wanted
            for key, wanted in expected.items()):
        raise Stop(reason)


def child(suffix, args, *, timeout=None):
    """Save actual exit/timing before parsing JSON or checking postconditions."""
    base = str(PREFIX) + suffix
    output, error = Path(base + '.stdout'), Path(base + '.stderr')
    argv = [sys.executable, '-B', 'scripts/production_release_guard.py', *args]
    started, clock = utc(), time.monotonic()
    proc, code, problem = None, None, None
    with output.open('xb') as stdout, error.open('xb') as stderr:
        try:
            proc = subprocess.Popen(argv, cwd=APP, env=CHILD_ENV,
                                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                    start_new_session=True)
            code = proc.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            problem = type(exc).__name__
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    code = proc.wait()
        except OSError as exc:
            problem = type(exc).__name__
        ended, elapsed = utc(), time.monotonic() - clock
        stdout.flush(); stderr.flush()
        os.fsync(stdout.fileno()); os.fsync(stderr.fileno())
    result = {'command': argv, 'cwd': str(APP), 'started_at': started,
              'ended_at': ended, 'elapsed_seconds': elapsed, 'actual_exit': code,
              'problem': problem, 'stdout': str(output), 'stderr': str(error)}
    save(Path(base + '.result.json'), result)
    if problem is not None or code != 0:
        raise Stop('official_guard_child_failed_see_saved_result')
    return json.loads(output.read_bytes()), result


def require_rehearsal():
    require_fields(read_json('outcome.json'), {
        'phase': 'build', 'ready': True, 'source_sha': SOURCE, 'deployment_sha': MERGE,
        'intent_commit': INTENT_COMMIT, 'tree': TREE, 'release_id': RELEASE,
        'untracked_count': 120, 'lease_created': False, 'evidence_dir': str(EVIDENCE)},
        'matching_successful_rehearsal_required')
    require_fields(read_json('22-build.result.json'), {
        'command': ['yarn', 'build'], 'cwd': str(APP / 'frontend'),
        'actual_exit': 0, 'problem': None}, 'actual_successful_build_result_required')
    if (EVIDENCE / '08-M-parents.stdout').read_text().split() != [MERGE, BASE, INTENT_COMMIT]:
        raise Stop('reviewed_J_B_merge_provenance_mismatch')
    if (EVIDENCE / '08-B-parent.stdout').read_text().split() != [INTENT_COMMIT, SOURCE]:
        raise Stop('reviewed_A_B_provenance_mismatch')


def require_owned(lease, prepared=None):
    require_fields(lease, {'active': True, **LEASE_IDENTITY}, 'active_lease_owner_or_identity_mismatch')
    if prepared is not None and lease != {'active': True, **prepared}:
        raise Stop('active_lease_differs_from_saved_prepare_provenance')


def prepared_provenance():
    summary = read_json('26-prepare.summary.json')
    require_fields(summary, {'stage': 'prepare', 'completed': True, 'actual_exit': 0,
                            'fresh_owned_active': True, **LEASE_IDENTITY},
                   'successful_prepare_summary_required')
    require_fields(read_json('26-prepare.result.json'), {'actual_exit': 0, 'problem': None},
                   'actual_successful_prepare_exit_required')
    raw = (EVIDENCE / '26-prepare.stdout').read_bytes()
    if hashlib.sha256(raw).hexdigest() != summary.get('stdout_sha256'):
        raise Stop('prepare_stdout_digest_mismatch')
    prepared = json.loads(raw)
    require_fields(prepared, LEASE_IDENTITY, 'saved_prepare_identity_mismatch')
    require_owned(read_json('26-prepare.after.stdout'), prepared)
    return prepared


def main(stage):
    require_rehearsal()
    prepared = prepared_provenance() if stage == 'prepublish' else None
    before, _ = child('.before', ['status'], timeout=15)
    if stage == 'prepare':
        if (not isinstance(before, dict) or set(before) != {'active'}
                or before.get('active') is not False):
            raise Stop('fresh_guard_not_exactly_inactive')
        result, execution = child('', ['prepare', '--actor', ACTOR])
        require_fields(result, LEASE_IDENTITY, 'official_prepare_identity_mismatch')
        prepared = result
    else:
        require_owned(before, prepared)
        result, execution = child('', ['prepublish'])
        require_fields(result, {'ready_to_publish': True, **IDENTITY},
                       'official_prepublish_readiness_or_identity_mismatch')
    after, _ = child('.after', ['status'], timeout=15)
    require_owned(after, prepared)
    summary = {'stage': stage, 'completed': True, **LEASE_IDENTITY,
               'intent_commit': INTENT_COMMIT, 'tree': TREE, 'fresh_owned_active': True,
               'actual_exit': execution['actual_exit'], 'elapsed_seconds': execution['elapsed_seconds'],
               'started_at': execution['started_at'], 'ended_at': execution['ended_at'],
               'stdout_sha256': hashlib.sha256(Path(str(PREFIX) + '.stdout').read_bytes()).hexdigest(),
               'prefix': str(PREFIX)}
    if stage == 'prepublish':
        summary['ready_to_publish'] = True
    save(Path(str(PREFIX) + '.summary.json'), summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('prepare', 'prepublish'), required=True)
    args = parser.parse_args()
    if (EVIDENCE.is_symlink() or not EVIDENCE.is_dir() or EVIDENCE.resolve() != EVIDENCE):
        raise SystemExit('Expected private rehearsal evidence directory is unavailable')
    metadata = EVIDENCE.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise SystemExit('Expected private operator-owned evidence directory')
    tag = '26-prepare' if args.stage == 'prepare' else '27-prepublish'
    PREFIX = EVIDENCE / tag
    if list(EVIDENCE.glob(tag + '*')):
        raise SystemExit('Stage evidence already exists; inspect it before any further action')
    os.umask(0o077)
    # Exclusive claim also prevents two processes from starting the same stage.
    save(Path(str(PREFIX) + '.started.json'), {'stage': args.stage, 'started_at': utc(), **LEASE_IDENTITY})
    print(json.dumps({'phase': 'started', 'stage': args.stage, 'prefix': str(PREFIX)}), flush=True)
    CHILD_ENV = os.environ.copy()
    CHILD_ENV['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        main(args.stage)
    except BaseException as exc:
        failure = {'stage': args.stage, 'completed': False,
                   'reason': str(exc) if isinstance(exc, Stop) else type(exc).__name__,
                   'prefix': str(PREFIX), 'finished_at': utc()}
        save(Path(str(PREFIX) + '.wrapper-failure.json'), failure)
        print(json.dumps(failure, ensure_ascii=True), flush=True)
        raise SystemExit(1)
