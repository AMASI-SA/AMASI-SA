#!/usr/bin/env python3
"""PR1928 prepare/prepublish only; root reviews and executes after rehearsal.

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
EVIDENCE = Path('/tmp/mezan-pr1928-original-<SUCCESSFUL_REHEARSAL_REQUIRED>')
SOURCE = 'd41cd73132793209d13758ef07b2eda4fcfe018a'
BASE = 'af9caabb3119aeb9cf450af0a8b89d376cef8c74'

REVIEWED_B = 'db6971b59b12bdda3a3585712ca881ac22d6bc0f'
REVIEWED_M = '3e9dc3b25e41c663e001822a666323c802e5ec5a'
REVIEWED_B_TREE = '355fdae884cf4a849b0acdf1c29081730556c6b0'
INTENT_SHA256 = '57ff9aacd5490ce42605ee22d9ab9cf1f2e8c6855fc9e2664266992438efd279'
EXPECTED_UNTRACKED_COUNT = 120  # Required fresh /app inventory; never reuse a prior count.

INTENT_COMMIT = REVIEWED_B
MERGE = REVIEWED_M
TREE = REVIEWED_B_TREE
RELEASE = 'rg5-12003f05673102a08c649a74e75e894372796da07bd75ada680a7f07aec65951'
ACTOR = 'codex-financial-source-read-1928'
BRANCH = 'hotfix/prod-snap-meta-final'
IDENTITY = {'source_git_sha': SOURCE, 'git_sha': SOURCE,
            'deployment_git_sha': MERGE, 'release_id': RELEASE, 'protocol_version': 5}
LEASE_IDENTITY = {**IDENTITY, 'source_base_git_sha': BASE, 'actor': ACTOR, 'branch': BRANCH}

def validate_reviewed_configuration():
    """Unfilled templates stop before creating evidence or touching /app."""
    identities = {"SOURCE": SOURCE, "BASE": BASE, "INTENT_COMMIT": REVIEWED_B,
                  "MERGE": REVIEWED_M, "TREE": REVIEWED_B_TREE}
    for label, value in identities.items():
        if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            raise SystemExit("Fill the exact reviewed " + label + " before running this template")
    if not isinstance(RELEASE, str) or not RELEASE.startswith("rg5-") or len(RELEASE) != 68 or any(c not in "0123456789abcdef" for c in RELEASE[4:]):
        raise SystemExit("Fill the exact reviewed deterministic runtime release ID")
    if not isinstance(INTENT_SHA256, str) or len(INTENT_SHA256) != 64 or any(c not in "0123456789abcdef" for c in INTENT_SHA256):
        raise SystemExit("Fill the reviewed intent byte SHA256")
    if type(EXPECTED_UNTRACKED_COUNT) is not int or EXPECTED_UNTRACKED_COUNT < 0:
        raise SystemExit("Review the current untracked inventory and fill its exact count")
    if (EVIDENCE.parent != Path('/tmp') or not EVIDENCE.name.startswith('mezan-pr1928-original-')
            or '<' in str(EVIDENCE) or '>' in str(EVIDENCE)):
        raise SystemExit("Fill this release's reviewed successful rehearsal evidence directory")


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
        'source_base_sha': BASE, 'intent_sha256': INTENT_SHA256,
        'untracked_count': EXPECTED_UNTRACKED_COUNT, 'lease_created': False, 'evidence_dir': str(EVIDENCE)},
        'matching_successful_rehearsal_required')
    require_fields(read_json('22-build.result.json'), {
        'command': ['yarn', 'build'], 'cwd': str(APP / 'frontend'),
        'actual_exit': 0, 'problem': None}, 'actual_successful_build_result_required')
    require_fields(read_json('24-after-build.preservation.json'), {
        'before_count': EXPECTED_UNTRACKED_COUNT, 'after_count': EXPECTED_UNTRACKED_COUNT,
        'changed_paths': [], 'preserved': True}, 'successful_untracked_preservation_required')
    require_fields(read_json('25-guard-after-build.stdout'), {'active': False},
                   'inactive_guard_after_rehearsal_required')
    if hashlib.sha256((EVIDENCE / '11-intent.stdout').read_bytes()).hexdigest() != INTENT_SHA256:
        raise Stop('rehearsal_intent_bytes_digest_mismatch')
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
    validate_reviewed_configuration()
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
