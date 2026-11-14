#!/usr/bin/env python3
"""Run only after explicit platform completion; root owns execution."""
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
EVIDENCE = Path('/tmp/mezan-pr1915-original-qqo8fwy9')
PREFIX = EVIDENCE / '28-verify'
ACTOR = 'codex-operational-source-rootfix-1915'
SOURCE = 'a496dd428241b06d2292286de030c70d6241fcf0'
MERGE = '4860548f0b032e8e3beca18a4776413cd7e0368b'
RELEASE = 'rg5-ec82d7649d17b217dd5a6c422bbe4e42138c25fe28236a9f9e6531f16a6b845e'
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
    result = {'started_at': started, 'ended_at': ended, 'elapsed_seconds': elapsed,
              'actual_exit': code, 'problem': problem, 'stdout': str(out), 'stderr': str(err)}
    save(result_path, result)  # Save actual child outcome BEFORE parsing or assertions.
    if problem or code != 0:
        raise RuntimeError('official_guard_child_failed_see_saved_result')
    return json.loads(out.read_bytes()), result


def main():
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
    os.umask(0o077)
    print(json.dumps({'phase': 'started', 'prefix': str(PREFIX)}), flush=True)
    if EVIDENCE.is_symlink() or not EVIDENCE.is_dir():
        raise SystemExit('Expected evidence directory is unavailable')
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
