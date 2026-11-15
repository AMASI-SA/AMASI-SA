#!/usr/bin/env python3
"""Pinned PR1945 original-workspace rehearsal; no publication or data repair.

Root owns independent review, checkpoint and execution after actual-M gates.
Main validates all pins before reading /app, importing its guard or creating evidence.
"""
import sys

sys.dont_write_bytecode = True

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

ROOT = Path('/app')
BASE = '8f1b0ddb6408781e8677212bcb8cf2d51eab42f7'
BASE_TREE = 'a8616930de7f98a43a509ff7dad287befd61ba5a'
SOURCE = '9b91e88c049166f111905c80a4c1fa07a96b51bd'
SOURCE_TREE = 'bb79134858e4f472a97e33cec100ec4ddfe095f7'
INTENT_COMMIT = '3227ff58776ff1bf99621940816b8ff8ca0b0a4b'
TREE = 'ff7e1e334bd755f844d0903d5fdba45541509854'
INTENT_SHA256 = 'fc151ac8b5c0faad002ca4773d510b6b16e6492ebb3bdf82b352fa1eaa4e5dc5'
RUNTIME = 'rg5-41884037a1555abb570d35f23b2a81edc8c24e1135035a37aabee30c2d16e8e7'
MERGE = 'db0d2c365f6cc13728cc5d8c334fb9880497fbe8'
PROOF_SHA256 = '381878cf3b8a30dde15d80014a1751df166b7ce420b9e4b84da6c347cab77f4f'
PREVIOUS_MANIFEST_SHA256 = 'cb0377cdfb8d1135140ff5a772563fb9ef38d1c1539e52b76f0c62b5b997f7be'
PREVIOUS_MANIFEST_BYTES = 3978

PREVIOUS_SOURCE = '2896f97bb3ffe5c919ade61a4f60e551a7a49162'
PREVIOUS_SOURCE_TREE = 'eb6973e7b7b2569c9da27d60e489ee32fafa27ed'
PREVIOUS_INTENT_COMMIT = 'fff5e81d715c6109d6e7b84cb284ec6b32c38ac1'
PREVIOUS_INTENT_SHA256 = 'ff7f91de77d2683063d45a3dcbd729f09bdaa2dcedcabf161675b5d14a2819c2'
PREVIOUS_RUNTIME = 'rg5-144ce747d2042279a6d9a86cb338a133bd0a7e05237f96b22331b905ce083cf3'
PREVIOUS_PROOF_SHA256 = '09d58d1afab57d3543d3697bc23897a986cd3c3d7bbc598b65f8cc74f1619e59'
PREVIOUS_VERIFY_SHA256 = 'f4208087aff434a55ed5c6973f3f235dc571994e677ced6b1a0dd20fe8e1c51e'
PREVIOUS_VERIFY_BYTES = 33369
EXPECTED_UNTRACKED_COUNT = 120

EVIDENCE = Path('/tmp/salla-webhook-release-1945/original')
PREVIOUS_EVIDENCE = Path('/tmp/salla-webhook-release-1942/original')
BUNDLE = Path(__file__).absolute().parent
HISTORY_BUNDLE = Path('/tmp/salla-webhook-release-1942/operator')
OLDER_HISTORY_BUNDLE = Path('/tmp/salla-webhook-release-1940/operator')
EXPECTED_PROOF = BUNDLE / 'expected-package-boundary-1945.json'
PREVIOUS_PROOF = HISTORY_BUNDLE / 'expected-package-boundary-1942.json'
PREVIOUS_VERIFY = BUNDLE / 'previous-production-verification-1942.json'
PREVIOUS_MANIFEST = BUNDLE / 'previous-original-1942-manifest.json'
HELPER_NAME = 'original_release_1942.py'
HELPER_SHA256 = 'b9cb6b3eedef177bff5dc7d78ea824875a6fc6ffc69a7ea1f72ad05c9c40bab1'
HELPERS = {HELPER_NAME: HELPER_SHA256}
COMMANDS = {
    'fetch': (['git', 'fetch', 'origin', 'hotfix/prod-snap-meta-final'], '/app'),
    'fast-forward': (['git', 'merge', '--ff-only', 'origin/hotfix/prod-snap-meta-final'], '/app'),
    'adapter': (['yarn', 'build'], '/app/frontend'),
    'packages': (['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'], '/app'),
}
REQUIRED_PREVIOUS_FILES = {
    'result.json', 'operator-pins.json', 'untracked-before.json',
    'untracked-after-sync.json', 'untracked-after.json', 'continuity-before.json',
    'continuity-after.json', 'old-quarantine-before.json', 'old-quarantine-after.json',
    'status-before.json', 'status-before-build.json', 'status-after.json',
} | {command + suffix for command in COMMANDS
     for suffix in ('.started.json', '.json', '.stdout', '.stderr')}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def assert_bound():
    for name in ('BASE', 'BASE_TREE', 'SOURCE', 'SOURCE_TREE', 'INTENT_COMMIT', 'TREE', 'MERGE'):
        value = globals()[name]
        if type(value) is not str or re.fullmatch(r'[0-9a-f]{40}', value) is None:
            raise RuntimeError('operator is unbound or malformed: ' + name)
    for name in ('INTENT_SHA256', 'PROOF_SHA256', 'PREVIOUS_MANIFEST_SHA256'):
        value = globals()[name]
        if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
            raise RuntimeError('operator is unbound or malformed: ' + name)
    if len({BASE, SOURCE, INTENT_COMMIT, MERGE}) != 4:
        raise RuntimeError('source, intent, base and normal merge must be distinct')
    if not re.fullmatch(r'rg5-[0-9a-f]{64}', RUNTIME):
        raise RuntimeError('runtime identity is unbound or malformed')


def regular_bytes(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.absolute() != path.resolve(strict=True):
        raise RuntimeError('expected real regular evidence file: ' + str(path))
    return path.read_bytes()


def load_predecessor():
    # The original file location preserves every historical BUNDLE and receipt
    # key. Never rebind a prior generation's source constants or run its main.
    path = HISTORY_BUNDLE / HELPER_NAME
    raw = regular_bytes(path)
    if digest(raw) != HELPER_SHA256:
        raise RuntimeError('reviewed PR1942 helper bytes differ')
    spec = importlib.util.spec_from_file_location('_history_1945_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def file_record(raw):
    return {'bytes': len(raw), 'sha256': digest(raw)}


def directory_records(path, cache_helpers):
    cache_helpers.real_directory(path)
    contents = {entry.name: regular_bytes(entry) for entry in sorted(path.iterdir())}
    return {name: file_record(raw) for name, raw in contents.items()}, contents


def reviewed_previous_manifest(cache_helpers):
    raw = regular_bytes(PREVIOUS_MANIFEST)
    if len(raw) != PREVIOUS_MANIFEST_BYTES or digest(raw) != PREVIOUS_MANIFEST_SHA256:
        raise RuntimeError('reviewed PR1942 receipt manifest differs')
    manifest = json.loads(raw)
    if (type(manifest) is not dict
            or set(manifest) != {'schema_version', 'directory', 'files'}
            or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
            or manifest['directory'] != str(PREVIOUS_EVIDENCE)
            or type(manifest['files']) is not dict
            or set(manifest['files']) != REQUIRED_PREVIOUS_FILES):
        raise RuntimeError('reviewed PR1942 complete receipt manifest schema differs')
    for name, record in manifest['files'].items():
        if (type(name) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)
                or type(record) is not dict or set(record) != {'bytes', 'sha256'}
                or type(record['bytes']) is not int or record['bytes'] < 0
                or type(record['sha256']) is not str
                or not re.fullmatch(r'[0-9a-f]{64}', record['sha256'])):
            raise RuntimeError('reviewed PR1942 receipt manifest contains an invalid record')
    actual, contents = directory_records(PREVIOUS_EVIDENCE, cache_helpers)
    if actual != manifest['files']:
        raise RuntimeError('actual PR1942 receipt bytes or path set differ from reviewed manifest')
    return file_record(raw), actual, contents


def validate_previous_release(predecessor, cache_helpers, base, current, quarantine):
    manifest_record, records, contents = reviewed_previous_manifest(cache_helpers)
    verify_raw = regular_bytes(PREVIOUS_VERIFY)
    if (len(verify_raw) != PREVIOUS_VERIFY_BYTES or digest(verify_raw) != PREVIOUS_VERIFY_SHA256):
        raise RuntimeError('PR1942 official verification receipt bytes differ')
    verified = json.loads(verify_raw)
    if (verified.get('verified') is not True or type(verified.get('checks')) is not int
            or verified['checks'] != 3 or verified.get('source_git_sha') != PREVIOUS_SOURCE
            or verified.get('deployment_git_sha') != BASE
            or verified.get('release_id') != PREVIOUS_RUNTIME):
        raise RuntimeError('PR1942 is not the exactly verified predecessor')
    previous_proof_raw = regular_bytes(PREVIOUS_PROOF)
    if digest(previous_proof_raw) != PREVIOUS_PROOF_SHA256:
        raise RuntimeError('accepted PR1942 package proof bytes differ')
    expected_pins = {
        'operator_sha256': HELPER_SHA256, 'helpers': predecessor.HELPERS,
        'historical_operator_bundle': str(OLDER_HISTORY_BUNDLE),
        'base': predecessor.BASE, 'base_tree': predecessor.BASE_TREE,
        'merge': BASE, 'tree': BASE_TREE, 'source': PREVIOUS_SOURCE,
        'source_tree': PREVIOUS_SOURCE_TREE, 'runtime': PREVIOUS_RUNTIME,
        'intent_commit': PREVIOUS_INTENT_COMMIT, 'intent_sha256': PREVIOUS_INTENT_SHA256,
        'package_proof_sha256': PREVIOUS_PROOF_SHA256,
        'previous_verification_sha256': predecessor.PREVIOUS_VERIFY_SHA256,
        'previous_rehearsal_manifest_sha256': predecessor.PREVIOUS_MANIFEST_SHA256,
    }
    if json.loads(contents['operator-pins.json']) != expected_pins:
        raise RuntimeError('PR1942 original operator pins differ')
    result = json.loads(contents['result.json'])
    expected_result = {
        'ok': True, 'scope': 'original_workspace_governed_rehearsal',
        'merge': BASE, 'tree': BASE_TREE, 'source_git_sha': PREVIOUS_SOURCE,
        'release_id': PREVIOUS_RUNTIME,
        'untracked_count': EXPECTED_UNTRACKED_COUNT, 'untracked_preserved': True,
        'previous_release_evidence_preserved': True, 'old_bytecode_quarantine_preserved': True,
        'package_proof_matches_accepted_B': True, 'active_lease': False,
        'functional_acceptance_hold': 'authenticated_incident_recovery_and_worker_completion_pending',
    }
    for key, expected in expected_result.items():
        if type(result.get(key)) is not type(expected) or result[key] != expected:
            raise RuntimeError('PR1942 rehearsal result differs: ' + key)
    snapshots = [json.loads(contents[name]) for name in
                 ('untracked-before.json', 'untracked-after-sync.json', 'untracked-after.json')]
    for snapshot in snapshots:
        if type(snapshot) is not dict or len(snapshot) != EXPECTED_UNTRACKED_COUNT:
            raise RuntimeError('PR1942 preserved path count differs')
        base.assert_preserved(snapshot, current)
    if len(current) != EXPECTED_UNTRACKED_COUNT:
        raise RuntimeError('current unrelated path set does not have the preserved count')
    if (result.get('append_only_before_bytes') != snapshots[0].get(base.APPEND_ONLY, {}).get('bytes')
            or result.get('append_only_after_bytes') != snapshots[2].get(base.APPEND_ONLY, {}).get('bytes')):
        raise RuntimeError('PR1942 append-only byte count receipt differs')
    for command, (args, cwd) in COMMANDS.items():
        started, receipt = (json.loads(contents[command + suffix]) for suffix in ('.started.json', '.json'))
        if (started.get('args') != args or started.get('cwd') != cwd
                or type(receipt.get('exit_code')) is not int or receipt['exit_code'] != 0
                or started.get('started_at') != receipt.get('started_at')):
            raise RuntimeError('PR1942 command identity or outcome differs: ' + command)
        for stream in ('stdout', 'stderr'):
            if receipt.get(stream + '_sha256') != digest(contents[command + '.' + stream]):
                raise RuntimeError('PR1942 command output digest differs: ' + command)
    if json.loads(contents['packages.stdout']) != json.loads(previous_proof_raw):
        raise RuntimeError('PR1942 actual package result differs from accepted proof')
    for name in ('status-before.json', 'status-before-build.json', 'status-after.json'):
        state = json.loads(contents[name])
        if (state.get('active') is not False or state.get('lease_directory_exists') is not False
                or state.get('observed_under_official_operation_mutex') is not True):
            raise RuntimeError('PR1942 inactive-status evidence differs: ' + name)
    if json.loads(contents['continuity-before.json']) != json.loads(contents['continuity-after.json']):
        raise RuntimeError('PR1942 historical receipt continuity differs')
    for name in ('old-quarantine-before.json', 'old-quarantine-after.json'):
        if json.loads(contents[name]) != quarantine:
            raise RuntimeError('PR1942 preserved old quarantine differs')
    return {
        'reviewed_manifest': manifest_record,
        'receipt_files': records,
        'official_verification': file_record(verify_raw),
        'accepted_package_proof': file_record(previous_proof_raw),
    }, snapshots, json.loads(contents['continuity-after.json'])


def continuity(base, cache_helpers, history, predecessor):
    # Each prior generation validates its own original constants and paths.
    # This extends the proven chain without reinterpreting old identities.
    old_records, old_snapshots, current, quarantine = predecessor.continuity(
        base, cache_helpers, history)
    previous, previous_snapshots, linked_old_records = validate_previous_release(
        predecessor, cache_helpers, base, current, quarantine)
    if linked_old_records != old_records:
        raise RuntimeError('PR1942 does not link the unchanged earlier receipt generations')
    historical_bundle_inventory, _ = directory_records(HISTORY_BUNDLE, cache_helpers)
    required_bundle_hashes = {
        HELPER_NAME: HELPER_SHA256,
        'expected-package-boundary-1942.json': PREVIOUS_PROOF_SHA256,
        'previous-production-verification-1940.json': predecessor.PREVIOUS_VERIFY_SHA256,
        'previous-original-1940-manifest.json': predecessor.PREVIOUS_MANIFEST_SHA256,
    }
    for name, expected in required_bundle_hashes.items():
        if historical_bundle_inventory.get(name, {}).get('sha256') != expected:
            raise RuntimeError('original PR1942 operator bundle member changed: ' + name)
    records = {
        'historical_through_1940': old_records,
        'historical_1942_operator_bundle': historical_bundle_inventory,
        'previous_1942': previous,
    }
    snapshots = [*old_snapshots, *previous_snapshots]
    for previous_snapshot in snapshots:
        base.assert_preserved(previous_snapshot, current)
    return records, snapshots, current, quarantine


def pin_source(base, cache_helpers, history, *, after_merge):
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
    history.no_source_bytecode(base, cache_helpers)
    expected_intent = INTENT_SHA256 if after_merge else PREVIOUS_INTENT_SHA256
    if digest(regular_bytes(ROOT / 'release/release-intent-v5.json')) != expected_intent:
        raise RuntimeError('tracked intent bytes differ from the pinned generation')


def verify_fetched_handoff(base):
    if base.git('rev-parse', 'origin/' + base.BRANCH) != MERGE:
        raise RuntimeError('production branch advanced beyond the reviewed merge')
    if base.git('rev-parse', SOURCE + '^{tree}') != SOURCE_TREE:
        raise RuntimeError('source A tree differs')
    if base.git('rev-list', '--parents', '-n', '1', SOURCE).split() != [SOURCE, BASE]:
        raise RuntimeError('reviewed source A is not the direct child of the pinned base')
    if (base.git('rev-parse', INTENT_COMMIT + '^{tree}') != TREE
            or base.git('rev-parse', MERGE + '^{tree}') != TREE):
        raise RuntimeError('accepted B and actual M trees differ')
    if base.git('rev-list', '--parents', '-n', '1', INTENT_COMMIT).split() != [INTENT_COMMIT, SOURCE]:
        raise RuntimeError('B is not the direct intent-only child of reviewed A')
    if base.git('rev-list', '--parents', '-n', '1', MERGE).split() != [MERGE, BASE, INTENT_COMMIT]:
        raise RuntimeError('actual normal merge parents differ')
    if base.git('diff', '--name-only', SOURCE, INTENT_COMMIT).splitlines() != ['release/release-intent-v5.json']:
        raise RuntimeError('B changes more than the tracked intent')
    base.git('merge-base', '--is-ancestor', BASE, SOURCE)
    base.git('merge-base', '--is-ancestor', SOURCE, MERGE)


def run_under_lock(base, cache_helpers, history, predecessor, guard):
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    base.EVIDENCE = EVIDENCE
    try:
        base.inactive_status('status-before', guard)
        pin_source(base, cache_helpers, history, after_merge=False)
        proof_raw = regular_bytes(EXPECTED_PROOF)
        if digest(proof_raw) != PROOF_SHA256:
            raise RuntimeError('accepted PR1945 B package proof bytes differ')
        proof_expected = json.loads(proof_raw)
        if (proof_expected.get('source_git_sha') != SOURCE
                or proof_expected.get('release_id') != RUNTIME):
            raise RuntimeError('accepted PR1945 package proof source or runtime differs')
        prior_receipts, prior_snapshots, before, prior_quarantine = continuity(
            base, cache_helpers, history, predecessor)
        base.save('continuity-before.json', prior_receipts)
        base.save('old-quarantine-before.json', prior_quarantine)
        base.save('untracked-before.json', before)
        base.save('operator-pins.json', {
            'operator_sha256': digest(regular_bytes(Path(__file__).absolute())), 'helpers': HELPERS,
            'historical_operator_bundle': str(HISTORY_BUNDLE),
            'base': BASE, 'base_tree': BASE_TREE, 'merge': MERGE, 'tree': TREE,
            'source': SOURCE, 'source_tree': SOURCE_TREE, 'runtime': RUNTIME,
            'intent_commit': INTENT_COMMIT, 'intent_sha256': INTENT_SHA256,
            'package_proof_sha256': PROOF_SHA256,
            'previous_verification_sha256': PREVIOUS_VERIFY_SHA256,
            'previous_rehearsal_manifest_sha256': PREVIOUS_MANIFEST_SHA256,
        })
        base.command('fetch', ['git', 'fetch', 'origin', base.BRANCH])
        verify_fetched_handoff(base)
        base.command('fast-forward', ['git', 'merge', '--ff-only', 'origin/' + base.BRANCH])
        pin_source(base, cache_helpers, history, after_merge=True)
        after_sync = base.snapshot()
        base.save('untracked-after-sync.json', after_sync)
        for previous in (*prior_snapshots, before):
            base.assert_preserved(previous, after_sync)
        if cache_helpers.cache_snapshot(history.OLD_QUARANTINE) != prior_quarantine:
            raise RuntimeError('old quarantine changed during fast-forward')
        base.inactive_status('status-before-build', guard)
        environment = dict(os.environ, REACT_APP_BACKEND_URL='https://mezansalla.com',
                           PYTHONDONTWRITEBYTECODE='1')
        base.command('adapter', ['yarn', 'build'], ROOT / 'frontend', environment)
        proof = json.loads(base.command('packages',
            ['python', '-B', 'scripts/emergent_deployment_adapter.py', 'verify-packages'], environment=environment))
        if proof != proof_expected or digest(regular_bytes(EXPECTED_PROOF)) != PROOF_SHA256:
            raise RuntimeError('actual package proof differs from accepted PR1945 B artifacts')
        identity = json.loads(regular_bytes(ROOT / 'backend/release_identity.json'))
        if identity.get('source_git_sha') != SOURCE or identity.get('release_id') != RUNTIME:
            raise RuntimeError('actual materialized runtime identity differs')
        pin_source(base, cache_helpers, history, after_merge=True)
        after = base.snapshot()
        base.save('untracked-after.json', after)
        for previous in (*prior_snapshots, before, after_sync):
            base.assert_preserved(previous, after)
        receipts_after, _, _, quarantine_after = continuity(base, cache_helpers, history, predecessor)
        if receipts_after != prior_receipts or quarantine_after != prior_quarantine:
            raise RuntimeError('historical release evidence or quarantine changed')
        base.save('continuity-after.json', receipts_after)
        base.save('old-quarantine-after.json', quarantine_after)
        base.inactive_status('status-after', guard)
        result = {
            'ok': True, 'scope': 'original_workspace_governed_rehearsal',
            'merge': MERGE, 'tree': TREE, 'source_git_sha': SOURCE, 'release_id': RUNTIME,
            'untracked_count': len(before), 'untracked_preserved': True,
            'previous_release_evidence_preserved': True, 'old_bytecode_quarantine_preserved': True,
            'package_proof_matches_accepted_B': True, 'active_lease': False,
            'append_only_before_bytes': before.get(base.APPEND_ONLY, {}).get('bytes'),
            'append_only_after_bytes': after.get(base.APPEND_ONLY, {}).get('bytes'),
            'functional_acceptance_hold': 'authenticated_owner_binding_recovery_and_worker_completion_pending',
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
    assert_bound()  # Must stay before every /app access, helper import or write.
    if (BUNDLE != BUNDLE.resolve(strict=True) or BUNDLE.is_relative_to(ROOT)
            or BUNDLE.is_relative_to(HISTORY_BUNDLE)):
        raise RuntimeError('operator bundle must remain a real directory outside /app')
    predecessor = load_predecessor()
    if (predecessor.BUNDLE != HISTORY_BUNDLE
            or predecessor.HISTORY_BUNDLE != OLDER_HISTORY_BUNDLE
            or predecessor.EXPECTED_PROOF != PREVIOUS_PROOF
            or predecessor.PREVIOUS_VERIFY != HISTORY_BUNDLE / 'previous-production-verification-1940.json'
            or predecessor.PREVIOUS_MANIFEST != HISTORY_BUNDLE / 'previous-original-1940-manifest.json'):
        raise RuntimeError('historical PR1942 helper paths differ from their original locations')
    history = predecessor.load_helper('original_release_1940.py')
    base = predecessor.load_helper('original_release_1935.py')
    cache_helpers = predecessor.load_helper('resume_release_1935.py')
    if (history.BUNDLE != OLDER_HISTORY_BUNDLE
            or history.OLD_VERIFY != OLDER_HISTORY_BUNDLE / 'previous-production-verification-1935.json'):
        raise RuntimeError('historical helper receipt location differs from original PR1940')
    pin_source(base, cache_helpers, history, after_merge=False)
    if cache_helpers.entry_exists(ROOT / '.git/mezan-production-release.lock'):
        raise RuntimeError('an active or incomplete release lease owns the workspace')
    sys.path.insert(0, str(ROOT / 'scripts'))
    import production_release_guard as guard
    with guard._release_operation_lock():
        run_under_lock(base, cache_helpers, history, predecessor, guard)


if __name__ == '__main__':
    main()
