#!/usr/bin/env python3
"""PR1916 verification; run only after explicit new platform completion.

Root supplies the reviewed deployment SHA, matching lease actor, and the private
evidence directory produced by this release's successful rehearsal. No lease
is created here. The official verifier has no wrapper timeout.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

APP = Path('/app')
SOURCE = '8839d3aeb32b4bda2de2015efb0deca7fbb844a3'
RELEASE = 'rg5-e81e97c26945236de423fc17e997db852574b424ba799a3fbba37f4a71d4b7a4'
ORIGIN = 'https://mezansalla.com'


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + chr(10))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def child(label, args, timeout=None):
    base = str(PREFIX) + label
    out, err = Path(base + '.stdout'), Path(base + '.stderr')
    result_path = Path(base + '.result.json')
    started, clock = utc(), time.monotonic()
    proc, code, problem = None, None, None
    with out.open('xb') as stdout, err.open('xb') as stderr:
        try:
            proc = subprocess.Popen([sys.executable, '-B', 'scripts/production_release_guard.py', *args],
                                    cwd=APP, env=CHILD_ENV, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, start_new_session=True)
            code = proc.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            problem = type(exc).__name__
            if proc is not None:
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
    result = {'command': [sys.executable, '-B', 'scripts/production_release_guard.py', *args],
              'cwd': str(APP), 'started_at': started, 'ended_at': ended, 'elapsed_seconds': elapsed,
              'actual_exit': code, 'problem': problem, 'stdout': str(out), 'stderr': str(err)}
    save(result_path, result)  # Save actual child outcome BEFORE parsing or assertions.
    if problem or code != 0:
        raise RuntimeError('official_guard_child_failed_see_saved_result')
    return json.loads(out.read_bytes()), result


def main():
    rehearsal = json.loads((EVIDENCE / 'outcome.json').read_bytes())
    expected_rehearsal = {'phase': 'build', 'ready': True, 'source_sha': SOURCE,
                          'deployment_sha': MERGE, 'release_id': RELEASE,
                          'untracked_count': 120, 'lease_created': False}
    if not isinstance(rehearsal, dict) or any(rehearsal.get(key) != value for key, value in expected_rehearsal.items()):
        raise RuntimeError('successful_matching_rehearsal_required')
    build = json.loads((EVIDENCE / '22-build.result.json').read_bytes())
    if (not isinstance(build, dict) or type(build.get('actual_exit')) is not int
            or build.get('actual_exit') != 0 or build.get('problem') is not None):
        raise RuntimeError('successful_actual_build_exit_required')
    lease, _ = child('.before', ['status'], timeout=15)
    expected = {'active': True, 'actor': ACTOR, 'source_git_sha': SOURCE,
                'git_sha': SOURCE, 'deployment_git_sha': MERGE, 'release_id': RELEASE,
                'protocol_version': 5}
    if not isinstance(lease, dict) or any(lease.get(key) != value for key, value in expected.items()):
        raise RuntimeError('active_lease_owner_or_identity_mismatch')
    if lease.get('active') is not True:
        raise RuntimeError('lease_is_not_active')
    result, execution = child('', ['verify', '--url', ORIGIN])  # No wrapper timeout.
    expected_result = {'verified': True, 'source_git_sha': SOURCE, 'git_sha': SOURCE,
                       'deployment_git_sha': MERGE, 'release_id': RELEASE,
                       'protocol_version': 5, 'url': ORIGIN, 'checks': 3}
    if not isinstance(result, dict) or any(result.get(key) != value for key, value in expected_result.items()):
        raise RuntimeError('official_verification_result_mismatch')
    if result.get('verified') is not True or type(result.get('checks')) is not int:
        raise RuntimeError('official_verification_result_types_invalid')
    requests = result.get('frontend_checked_requests')
    if not isinstance(requests, list) or not requests:
        raise RuntimeError('frontend_checked_requests_missing')
    if result.get('frontend_artifact_tree_sha256') != lease['frontend_build']['artifact_tree_sha256']:
        raise RuntimeError('verified_frontend_tree_differs_from_prepared_lease')
    status, _ = child('.after', ['status'], timeout=15)
    if not isinstance(status, dict) or status.get('active') is not False or status.get('incomplete_lease_directory'):
        raise RuntimeError('fresh_guard_status_not_inactive')
    summary = {'phase': 'verified', 'source_git_sha': SOURCE, 'deployment_git_sha': MERGE,
               'release_id': RELEASE, 'checks': result['checks'],
               'frontend_checked_request_count': len(requests), 'fresh_guard_inactive': True,
               'actual_exit': execution['actual_exit'], 'elapsed_seconds': execution['elapsed_seconds'],
               'started_at': execution['started_at'], 'ended_at': execution['ended_at'],
               'stdout_sha256': hashlib.sha256(Path(str(PREFIX) + '.stdout').read_bytes()).hexdigest(),
               'prefix': str(PREFIX)}
    save(Path(str(PREFIX) + '.summary.json'), summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    def commit_sha(value):
        if len(value) != 40 or any(char not in '0123456789abcdef' for char in value):
            raise argparse.ArgumentTypeError('A complete lowercase Git SHA is required')
        return value

    def actor_name(value):
        if not 1 <= len(value) <= 100 or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for char in value):
            raise argparse.ArgumentTypeError('The exact safe lease actor name is required')
        return value

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', required=True, type=Path)
    parser.add_argument('--deployment-sha', required=True, type=commit_sha)
    parser.add_argument('--actor', required=True, type=actor_name)
    ARGS = parser.parse_args()
    EVIDENCE, MERGE, ACTOR = ARGS.evidence_dir, ARGS.deployment_sha, ARGS.actor
    if (EVIDENCE.parent != Path('/tmp') or not EVIDENCE.name.startswith('mezan-pr1917-original-')
            or EVIDENCE.is_symlink() or not EVIDENCE.is_dir()
            or EVIDENCE.resolve() != EVIDENCE):
        raise SystemExit('Expected PR1916 evidence directory is unavailable or unsafe')
    metadata = EVIDENCE.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise SystemExit('Expected private operator-owned evidence directory')
    PREFIX = EVIDENCE / '28-verify'
    os.umask(0o077)
    print(json.dumps({'phase': 'started', 'prefix': str(PREFIX)}), flush=True)
    if list(EVIDENCE.glob('28-verify*')):
        raise SystemExit('Verification evidence already exists; inspect it before any repeat')
    CHILD_ENV = os.environ.copy()
    CHILD_ENV['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        main()
    except Exception as exc:
        failure = {'phase': 'stopped', 'error_type': type(exc).__name__, 'prefix': str(PREFIX)}
        save(Path(str(PREFIX) + '.wrapper-failure.json'), failure)
        print(json.dumps(failure), flush=True)
        raise SystemExit(1)
