#!/usr/bin/env python3
"""PR1921 verification; run only after explicit new platform completion.

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
SOURCE = '0dd26a620a0acc4235d6fdfa0a3a8e714982fcae'
RELEASE = 'rg5-5a1f26faa5108bc63621c816cdbd90697e422290a4245a460ff6020e6c97dc30'
ORIGIN = 'https://mezansalla.com'
BASE = '29ec1e07eb7d1a23647cadff5bac26149accfa99'
BRANCH = 'hotfix/prod-snap-meta-final'
EXPECTED_ACTOR = 'codex-financial-section-pages-1921'

REVIEWED_B = '5fc08eef0b430b53911934d9f866ec7b13b61b47'
REVIEWED_M = '94f6dfb8c1f46bcb7d2dcba6ca0feeea628dc894'
REVIEWED_B_TREE = 'd636970f1a954530cf7845e408e05362af160cfa'
INTENT_SHA256 = 'ad0a524b2de8da1a734e46bfeb4d851f9743ae28293eedb361075c41fcc4a4b2'
EXPECTED_UNTRACKED_COUNT = 120  # Fresh original read-only inventory at 2026-11-14T16:32:20Z.


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


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    """Never replace evidence from an earlier or concurrent verification."""
    with path.open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(value, ensure_ascii=True, indent=2) + chr(10))
        handle.flush()
        os.fsync(handle.fileno())


def require_fields(value, expected, reason):
    if not isinstance(value, dict) or any(
            type(value.get(key)) is not type(wanted) or value.get(key) != wanted
            for key, wanted in expected.items()):
        raise RuntimeError(reason)


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
                          'source_base_sha': BASE, 'intent_commit': REVIEWED_B, 'tree': REVIEWED_B_TREE,
                          'intent_sha256': INTENT_SHA256,
                          'untracked_count': EXPECTED_UNTRACKED_COUNT, 'lease_created': False}
    require_fields(rehearsal, expected_rehearsal, 'successful_matching_rehearsal_required')
    build = json.loads((EVIDENCE / '22-build.result.json').read_bytes())
    if (not isinstance(build, dict) or type(build.get('actual_exit')) is not int
            or build.get('actual_exit') != 0 or build.get('problem') is not None):
        raise RuntimeError('successful_actual_build_exit_required')
    prepublish = json.loads((EVIDENCE / '27-prepublish.summary.json').read_bytes())
    required_prepublish = {'completed': True, 'ready_to_publish': True, 'actual_exit': 0,
        'actor': EXPECTED_ACTOR, 'source_git_sha': SOURCE, 'source_base_git_sha': BASE,
        'deployment_git_sha': MERGE, 'release_id': RELEASE, 'intent_commit': REVIEWED_B,
        'tree': REVIEWED_B_TREE, 'fresh_owned_active': True}
    require_fields(prepublish, required_prepublish, 'successful_matching_prepublish_required')
    prepublish_exit = json.loads((EVIDENCE / '27-prepublish.result.json').read_bytes())
    require_fields(prepublish_exit, {'actual_exit': 0, 'problem': None},
                   'successful_actual_prepublish_exit_required')
    prepublish_raw = (EVIDENCE / '27-prepublish.stdout').read_bytes()
    if hashlib.sha256(prepublish_raw).hexdigest() != prepublish.get('stdout_sha256'):
        raise RuntimeError('prepublish_stdout_digest_mismatch')
    require_fields(json.loads(prepublish_raw), {
        'ready_to_publish': True, 'source_git_sha': SOURCE, 'git_sha': SOURCE,
        'deployment_git_sha': MERGE, 'release_id': RELEASE, 'protocol_version': 5},
        'saved_official_prepublish_result_mismatch')
    prepared = json.loads((EVIDENCE / '26-prepare.stdout').read_bytes())
    lease, _ = child('.before', ['status'], timeout=15)
    expected = {'active': True, 'actor': ACTOR, 'source_git_sha': SOURCE,
                'git_sha': SOURCE, 'deployment_git_sha': MERGE, 'release_id': RELEASE,
                'source_base_git_sha': BASE, 'branch': BRANCH,
                'protocol_version': 5}
    require_fields(lease, expected, 'active_lease_owner_or_identity_mismatch')
    if not isinstance(prepared, dict) or lease != {'active': True, **prepared}:
        raise RuntimeError('active_lease_differs_from_saved_prepare_provenance')
    result, execution = child('', ['verify', '--url', ORIGIN])  # No wrapper timeout.
    expected_result = {'verified': True, 'source_git_sha': SOURCE, 'source_base_git_sha': BASE, 'git_sha': SOURCE,
                       'deployment_git_sha': MERGE, 'release_id': RELEASE,
                       'protocol_version': 5, 'url': ORIGIN, 'checks': 3}
    require_fields(result, expected_result, 'official_verification_result_mismatch')
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
    validate_reviewed_configuration()
    if ARGS.deployment_sha != REVIEWED_M or ARGS.actor != EXPECTED_ACTOR:
        parser.error("Deployment SHA or actor differ from reviewed PR1921 identities")
    EVIDENCE, MERGE, ACTOR = ARGS.evidence_dir, ARGS.deployment_sha, ARGS.actor
    if (EVIDENCE.parent != Path('/tmp') or not EVIDENCE.name.startswith('mezan-pr1921-original-')
            or EVIDENCE.is_symlink() or not EVIDENCE.is_dir()
            or EVIDENCE.resolve() != EVIDENCE):
        raise SystemExit('Expected PR1921 evidence directory is unavailable or unsafe')
    metadata = EVIDENCE.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise SystemExit('Expected private operator-owned evidence directory')
    PREFIX = EVIDENCE / '28-verify'
    os.umask(0o077)
    print(json.dumps({'phase': 'started', 'prefix': str(PREFIX)}), flush=True)
    if list(EVIDENCE.glob('28-verify*')):
        raise SystemExit('Verification evidence already exists; inspect it before any repeat')
    save(Path(str(PREFIX) + '.started.json'), {
        'phase': 'started', 'started_at': utc(), 'source_git_sha': SOURCE,
        'deployment_git_sha': MERGE, 'release_id': RELEASE, 'actor': ACTOR})
    CHILD_ENV = os.environ.copy()
    CHILD_ENV['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        main()
    except Exception as exc:
        failure = {'phase': 'stopped', 'error_type': type(exc).__name__, 'prefix': str(PREFIX)}
        save(Path(str(PREFIX) + '.wrapper-failure.json'), failure)
        print(json.dumps(failure), flush=True)
        raise SystemExit(1)
