#!/usr/bin/env python3
"""Preserving original-workspace PR1940 rehearsal; no publication or data repair."""
import sys

sys.dont_write_bytecode = True

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
from datetime import datetime, timezone

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

ROOT = Path('/app')
BASE = '331b7917b3c01d064ee2a17affb4714f3a296331'
BASE_TREE = '62632e21efa7d1ed1e6f42740d1e73321e947c1d'
MERGE = 'd7facb0ff4e2e30edbabf012b3865155d5902a85'
TREE = 'd5bb8d88f7f04610dd6796648edebca24d025c1c'
SOURCE = 'a6c5bc5763fdca144b44473285d37130eb8213e9'
INTENT_COMMIT = 'ac1443d46d6c172983dfe74479c6f96c40b0c4ec'
INTENT_SHA256 = '812c3b0bdb27215b6647d40055616ded848d118fc0a7879b289f70a5c02a1c6d'
PROOF_SHA256 = '9d22c541d4697d7e23c0e8a21f09af2da69cf22010f7e4ec80dc18089136030e'
OLD_VERIFY_SHA256 = '780a91d64b17d80a2d65cfafef43773a8b1bec66ce566299c7380e76c860c381'
OLD_SOURCE = '8c11053b602f8f56a1facbaee09779f2896b0ada'
OLD_RUNTIME = 'rg5-8c765a3025bcafa7098d2fbdffe572d5582ecfe52c4be96f8c254ecd498d79f0'
EVIDENCE = Path('/tmp/salla-webhook-release-1940/original')
OLD_ORIGINAL = Path('/tmp/salla-webhook-release-1935/original')
OLD_RESUME = Path('/tmp/salla-webhook-release-1935/resume-bytecode-1')
OLD_QUARANTINE = Path('/app/.git/salla-webhook-release-1935-bytecode/backend-__pycache__')
BUNDLE = Path(__file__).absolute().parent
EXPECTED_PROOF = BUNDLE / 'expected-package-boundary-1940.json'
OLD_VERIFY = BUNDLE / 'previous-production-verification-1935.json'
HELPERS = {
    'original_release_1935.py': '5ea00d759706151f89914ae96c2afd5db9d3d485be917baf037cd25f6c785e27',
    'resume_release_1935.py': 'd68289c48715ed98d2b2523d841b39d7ff7ea09808a7875b4fd036a488f41b12',
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def regular_bytes(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.absolute() != path.resolve(strict=True):
        raise RuntimeError('evidence must be a real regular file: ' + str(path))
    return path.read_bytes()


def load_helper(name):
    path = BUNDLE / name
    raw = regular_bytes(path)
    if digest(raw) != HELPERS[name]:
        raise RuntimeError('reviewed helper bytes differ: ' + name)
    spec = importlib.util.spec_from_file_location('_pinned_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def no_source_bytecode(base, cache_helpers):
    cache_helpers.cache_layout(base, expected_present=False)
    def failed_walk(error):
        raise error
    for relative in ('scripts', '.github'):
        scope = ROOT / relative
        if not scope.exists():
            continue
        for directory, directories, files in os.walk(scope, followlinks=False, onerror=failed_walk):
            if '__pycache__' in directories or any(Path(name).suffix.lower() in {'.pyc', '.pyo'} for name in files):
                raise RuntimeError('unexpected release-control bytecode; inspect before continuing')


def pin_source(base, cache_helpers, *, after_merge):
    for path in (ROOT, ROOT / '.git', ROOT / 'backend'):
        cache_helpers.real_directory(path)
    if base.git('rev-parse', '--absolute-git-dir') != str(ROOT / '.git'):
        raise RuntimeError('original Git directory differs')
    if base.git('branch', '--show-current') != base.BRANCH:
        raise RuntimeError('original branch differs')
    expected_commit, expected_tree = (MERGE, TREE) if after_merge else (BASE, BASE_TREE)
    if base.git('rev-parse', 'HEAD') != expected_commit or base.git('rev-parse', 'HEAD^{tree}') != expected_tree:
        raise RuntimeError('original source identity differs')
    base.clean_tracked()
    no_source_bytecode(base, cache_helpers)
    if after_merge and digest(regular_bytes(ROOT / 'release/release-intent-v5.json')) != INTENT_SHA256:
        raise RuntimeError('new intent bytes differ')


def continuity(base, cache_helpers):
    paths = [OLD_ORIGINAL / 'untracked-before.json', OLD_ORIGINAL / 'untracked-after-sync.json',
             OLD_RESUME / 'untracked-after.json', OLD_RESUME / 'result.json',
             OLD_RESUME / 'quarantine.json', OLD_VERIFY]
    raw = {str(path): regular_bytes(path) for path in paths}
    if digest(raw[str(OLD_VERIFY)]) != OLD_VERIFY_SHA256:
        raise RuntimeError('previous official verification receipt bytes differ')
    verified = json.loads(raw[str(OLD_VERIFY)])
    if (verified.get('verified') is not True or type(verified.get('checks')) is not int
            or verified['checks'] != 3 or verified.get('source_git_sha') != OLD_SOURCE
            or verified.get('deployment_git_sha') != BASE or verified.get('release_id') != OLD_RUNTIME):
        raise RuntimeError('previous publication is not the verified prepared release')
    result = json.loads(raw[str(OLD_RESUME / 'result.json')])
    if (result.get('ok') is not True or result.get('merge') != BASE or result.get('tree') != BASE_TREE
            or result.get('untracked_preserved') is not True
            or result.get('original_attempt_preserved') is not True
            or result.get('package_proof_matches_accepted_B') is not True or result.get('active_lease') is not False):
        raise RuntimeError('previous preserving rehearsal result differs')
    snapshots = [json.loads(raw[str(path)]) for path in paths[:3]]
    current = base.snapshot()
    for previous in snapshots:
        base.assert_preserved(previous, current)
    if result.get('untracked_count') != len(current):
        raise RuntimeError('preserved untracked count differs from previous success')
    quarantine_receipt = json.loads(raw[str(OLD_RESUME / 'quarantine.json')])
    quarantine = cache_helpers.cache_snapshot(OLD_QUARANTINE)
    if (quarantine_receipt.get('destination') != str(OLD_QUARANTINE)
            or quarantine_receipt.get('preserved') is not True
            or quarantine_receipt.get('metadata') != quarantine):
        raise RuntimeError('previous preserved bytecode quarantine differs')
    fingerprints = {name: {'bytes': len(data), 'sha256': digest(data)} for name, data in raw.items()}
    return fingerprints, snapshots, current, quarantine


def run_under_lock(base, cache_helpers, guard):
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    base.EVIDENCE = EVIDENCE
    try:
        base.inactive_status('status-before', guard)
        pin_source(base, cache_helpers, after_merge=False)
        proof_bytes = regular_bytes(EXPECTED_PROOF)
        if digest(proof_bytes) != PROOF_SHA256:
            raise RuntimeError('accepted B package proof bytes differ')
        proof_expected = json.loads(proof_bytes)
        if proof_expected.get('source_git_sha') != SOURCE:
            raise RuntimeError('accepted package proof source differs')
        prior_receipts, prior_snapshots, before, prior_quarantine = continuity(base, cache_helpers)
        base.save('continuity-before.json', prior_receipts)
        base.save('old-quarantine-before.json', prior_quarantine)
        base.save('untracked-before.json', before)
        base.save('operator-pins.json', {
            'operator_sha256': digest(regular_bytes(Path(__file__).absolute())), 'helpers': HELPERS,
            'base': BASE, 'merge': MERGE, 'tree': TREE, 'source': SOURCE,
            'intent_commit': INTENT_COMMIT, 'intent_sha256': INTENT_SHA256,
            'package_proof_sha256': PROOF_SHA256, 'previous_verification_sha256': OLD_VERIFY_SHA256,
        })
        base.command('fetch', ['git', 'fetch', 'origin', base.BRANCH])
        if base.git('rev-parse', 'origin/' + base.BRANCH) != MERGE:
            raise RuntimeError('production branch advanced beyond the reviewed merge')
        base.command('fast-forward', ['git', 'merge', '--ff-only', 'origin/' + base.BRANCH])
        pin_source(base, cache_helpers, after_merge=True)
        after_sync = base.snapshot()
        base.save('untracked-after-sync.json', after_sync)
        for previous in (*prior_snapshots, before):
            base.assert_preserved(previous, after_sync)
        if cache_helpers.cache_snapshot(OLD_QUARANTINE) != prior_quarantine:
            raise RuntimeError('old quarantine changed during fast-forward')
        base.inactive_status('status-before-build', guard)
        environment = dict(os.environ, REACT_APP_BACKEND_URL='https://mezansalla.com',
                           PYTHONDONTWRITEBYTECODE='1')
        base.command('adapter', ['yarn', 'build'], ROOT / 'frontend', environment)
        proof = json.loads(base.command('packages',
            ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'], environment=environment))
        if proof != proof_expected or digest(regular_bytes(EXPECTED_PROOF)) != PROOF_SHA256:
            raise RuntimeError('actual package proof differs from accepted B artifacts')
        pin_source(base, cache_helpers, after_merge=True)
        after = base.snapshot()
        base.save('untracked-after.json', after)
        for previous in (*prior_snapshots, before, after_sync):
            base.assert_preserved(previous, after)
        receipts_after, _, _, quarantine_after = continuity(base, cache_helpers)
        if receipts_after != prior_receipts or quarantine_after != prior_quarantine:
            raise RuntimeError('previous release evidence or quarantine changed')
        base.save('continuity-after.json', receipts_after)
        base.save('old-quarantine-after.json', quarantine_after)
        base.inactive_status('status-after', guard)
        result = {
            'ok': True, 'scope': 'original_workspace_governed_rehearsal',
            'merge': MERGE, 'tree': TREE, 'source_git_sha': SOURCE,
            'untracked_count': len(before), 'untracked_preserved': True,
            'previous_release_evidence_preserved': True, 'old_bytecode_quarantine_preserved': True,
            'package_proof_matches_accepted_B': True, 'active_lease': False,
            'append_only_before_bytes': before.get(base.APPEND_ONLY, {}).get('bytes'),
            'append_only_after_bytes': after.get(base.APPEND_ONLY, {}).get('bytes'),
            'functional_acceptance_hold': 'incident_quarantine_recovery_pending',
            'finished_at': datetime.now(timezone.utc).isoformat(),
        }
        base.save('result.json', result)
        print(json.dumps(result, sort_keys=True), flush=True)
    except Exception as exc:
        failed = {'ok': False, 'error_type': type(exc).__name__, 'reason': str(exc),
                  'observed_at': datetime.now(timezone.utc).isoformat()}
        base.save('failed.json', failed)
        print(json.dumps(failed, sort_keys=True), flush=True)
        raise


def main():
    if BUNDLE != BUNDLE.resolve(strict=True) or BUNDLE.is_relative_to(ROOT):
        raise RuntimeError('operator bundle must remain a real directory outside /app')
    base, cache_helpers = load_helper('original_release_1935.py'), load_helper('resume_release_1935.py')
    pin_source(base, cache_helpers, after_merge=False)
    if cache_helpers.entry_exists(ROOT / '.git/mezan-production-release.lock'):
        raise RuntimeError('an active or incomplete release lease owns the workspace')
    sys.path.insert(0, str(ROOT / 'scripts'))
    import production_release_guard as guard
    with guard._release_operation_lock():
        run_under_lock(base, cache_helpers, guard)


if __name__ == '__main__':
    main()
