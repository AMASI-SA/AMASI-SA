#!/usr/bin/env python3
"""Pinned PR1942 original-workspace rehearsal; no publication or data repair.

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
BASE = 'd7facb0ff4e2e30edbabf012b3865155d5902a85'
BASE_TREE = 'd5bb8d88f7f04610dd6796648edebca24d025c1c'
SOURCE = '2896f97bb3ffe5c919ade61a4f60e551a7a49162'
SOURCE_TREE = 'eb6973e7b7b2569c9da27d60e489ee32fafa27ed'
INTENT_COMMIT = 'fff5e81d715c6109d6e7b84cb284ec6b32c38ac1'
TREE = 'a8616930de7f98a43a509ff7dad287befd61ba5a'
INTENT_SHA256 = 'ff7f91de77d2683063d45a3dcbd729f09bdaa2dcedcabf161675b5d14a2819c2'
RUNTIME = 'rg5-144ce747d2042279a6d9a86cb338a133bd0a7e05237f96b22331b905ce083cf3'
MERGE = '8f1b0ddb6408781e8677212bcb8cf2d51eab42f7'
PROOF_SHA256 = '09d58d1afab57d3543d3697bc23897a986cd3c3d7bbc598b65f8cc74f1619e59'
PREVIOUS_MANIFEST_SHA256 = 'ce9ba44e9e28d52a70912de3b8bc6922fbce588af5eafe8035ce1528b4237b90'

PREVIOUS_SOURCE = 'a6c5bc5763fdca144b44473285d37130eb8213e9'
PREVIOUS_INTENT_COMMIT = 'ac1443d46d6c172983dfe74479c6f96c40b0c4ec'
PREVIOUS_INTENT_SHA256 = '812c3b0bdb27215b6647d40055616ded848d118fc0a7879b289f70a5c02a1c6d'
PREVIOUS_RUNTIME = 'rg5-f33cd3537f17e7a25804236a8b4ad0680c73618af6d3cddabc75fe8486f8d93a'
PREVIOUS_PROOF_SHA256 = '9d22c541d4697d7e23c0e8a21f09af2da69cf22010f7e4ec80dc18089136030e'
PREVIOUS_VERIFY_SHA256 = '988106b52adadfa096721f804adff7ff57526689f04cd58a452287378a5e327a'
PREVIOUS_VERIFY_BYTES = 33299
EXPECTED_UNTRACKED_COUNT = 120

EVIDENCE = Path('/tmp/salla-webhook-release-1942/original')
PREVIOUS_EVIDENCE = Path('/tmp/salla-webhook-release-1940/original')
BUNDLE = Path(__file__).absolute().parent
HISTORY_BUNDLE = Path('/tmp/salla-webhook-release-1940/operator')
EXPECTED_PROOF = BUNDLE / 'expected-package-boundary-1942.json'
PREVIOUS_PROOF = HISTORY_BUNDLE / 'expected-package-boundary-1940.json'
PREVIOUS_VERIFY = BUNDLE / 'previous-production-verification-1940.json'
PREVIOUS_MANIFEST = BUNDLE / 'previous-original-1940-manifest.json'
HELPERS = {
    'original_release_1940.py': '21e6d2e65fd816e10c3033bd18669403095b09fe2d6ce9fe4985845773fa9fd4',
    'original_release_1935.py': '5ea00d759706151f89914ae96c2afd5db9d3d485be917baf037cd25f6c785e27',
    'resume_release_1935.py': 'd68289c48715ed98d2b2523d841b39d7ff7ea09808a7875b4fd036a488f41b12',
}
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


def load_helper(name):
    # Load each historical helper from its preserved original bundle. Its own
    # __file__/BUNDLE and receipt-path keys remain exactly those used in PR1940.
    path = HISTORY_BUNDLE / name
    raw = regular_bytes(path)
    if digest(raw) != HELPERS[name]:
        raise RuntimeError('reviewed helper bytes differ: ' + name)
    spec = importlib.util.spec_from_file_location('_history_1942_' + path.stem, path)
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
    if digest(raw) != PREVIOUS_MANIFEST_SHA256:
        raise RuntimeError('reviewed PR1940 receipt manifest differs')
    manifest = json.loads(raw)
    if (type(manifest) is not dict
            or set(manifest) != {'schema_version', 'directory', 'files'}
            or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
            or manifest['directory'] != str(PREVIOUS_EVIDENCE)
            or type(manifest['files']) is not dict
            or not REQUIRED_PREVIOUS_FILES.issubset(manifest['files'])):
        raise RuntimeError('reviewed PR1940 receipt manifest schema differs')
    for name, record in manifest['files'].items():
        if (type(name) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)
                or type(record) is not dict or set(record) != {'bytes', 'sha256'}
                or type(record['bytes']) is not int or record['bytes'] < 0
                or type(record['sha256']) is not str
                or not re.fullmatch(r'[0-9a-f]{64}', record['sha256'])):
            raise RuntimeError('reviewed PR1940 receipt manifest contains an invalid record')
    actual, contents = directory_records(PREVIOUS_EVIDENCE, cache_helpers)
    if actual != manifest['files']:
        raise RuntimeError('actual PR1940 receipt bytes or path set differ from reviewed manifest')
    return file_record(raw), actual, contents


def validate_previous_release(history, cache_helpers, base, current, quarantine):
    manifest_record, records, contents = reviewed_previous_manifest(cache_helpers)
    verify_raw = regular_bytes(PREVIOUS_VERIFY)
    if (len(verify_raw) != PREVIOUS_VERIFY_BYTES or digest(verify_raw) != PREVIOUS_VERIFY_SHA256):
        raise RuntimeError('PR1940 official verification receipt bytes differ')
    verified = json.loads(verify_raw)
    if (verified.get('verified') is not True or type(verified.get('checks')) is not int
            or verified['checks'] != 3 or verified.get('source_git_sha') != PREVIOUS_SOURCE
            or verified.get('deployment_git_sha') != BASE
            or verified.get('release_id') != PREVIOUS_RUNTIME):
        raise RuntimeError('PR1940 is not the exactly verified predecessor')
    previous_proof_raw = regular_bytes(PREVIOUS_PROOF)
    if digest(previous_proof_raw) != PREVIOUS_PROOF_SHA256:
        raise RuntimeError('accepted PR1940 package proof bytes differ')
    expected_pins = {
        'operator_sha256': HELPERS['original_release_1940.py'], 'helpers': history.HELPERS,
        'base': history.BASE, 'merge': BASE, 'tree': BASE_TREE, 'source': PREVIOUS_SOURCE,
        'intent_commit': PREVIOUS_INTENT_COMMIT, 'intent_sha256': PREVIOUS_INTENT_SHA256,
        'package_proof_sha256': PREVIOUS_PROOF_SHA256,
        'previous_verification_sha256': history.OLD_VERIFY_SHA256,
    }
    if json.loads(contents['operator-pins.json']) != expected_pins:
        raise RuntimeError('PR1940 original operator pins differ')
    result = json.loads(contents['result.json'])
    expected_result = {
        'ok': True, 'scope': 'original_workspace_governed_rehearsal',
        'merge': BASE, 'tree': BASE_TREE, 'source_git_sha': PREVIOUS_SOURCE,
        'untracked_count': EXPECTED_UNTRACKED_COUNT, 'untracked_preserved': True,
        'previous_release_evidence_preserved': True, 'old_bytecode_quarantine_preserved': True,
        'package_proof_matches_accepted_B': True, 'active_lease': False,
    }
    for key, expected in expected_result.items():
        if type(result.get(key)) is not type(expected) or result[key] != expected:
            raise RuntimeError('PR1940 rehearsal result differs: ' + key)
    snapshots = [json.loads(contents[name]) for name in
                 ('untracked-before.json', 'untracked-after-sync.json', 'untracked-after.json')]
    for snapshot in snapshots:
        if type(snapshot) is not dict or len(snapshot) != EXPECTED_UNTRACKED_COUNT:
            raise RuntimeError('PR1940 preserved path count differs')
        base.assert_preserved(snapshot, current)
    if len(current) != EXPECTED_UNTRACKED_COUNT:
        raise RuntimeError('current unrelated path set does not have the preserved count')
    if (result.get('append_only_before_bytes') != snapshots[0].get(base.APPEND_ONLY, {}).get('bytes')
            or result.get('append_only_after_bytes') != snapshots[2].get(base.APPEND_ONLY, {}).get('bytes')):
        raise RuntimeError('PR1940 append-only byte count receipt differs')
    for command, (args, cwd) in COMMANDS.items():
        started, receipt = (json.loads(contents[command + suffix]) for suffix in ('.started.json', '.json'))
        if (started.get('args') != args or started.get('cwd') != cwd
                or type(receipt.get('exit_code')) is not int or receipt['exit_code'] != 0
                or started.get('started_at') != receipt.get('started_at')):
            raise RuntimeError('PR1940 command identity or outcome differs: ' + command)
        for stream in ('stdout', 'stderr'):
            if receipt.get(stream + '_sha256') != digest(contents[command + '.' + stream]):
                raise RuntimeError('PR1940 command output digest differs: ' + command)
    if json.loads(contents['packages.stdout']) != json.loads(previous_proof_raw):
        raise RuntimeError('PR1940 actual package result differs from accepted proof')
    for name in ('status-before.json', 'status-before-build.json', 'status-after.json'):
        state = json.loads(contents[name])
        if (state.get('active') is not False or state.get('lease_directory_exists') is not False
                or state.get('observed_under_official_operation_mutex') is not True):
            raise RuntimeError('PR1940 inactive-status evidence differs: ' + name)
    if json.loads(contents['continuity-before.json']) != json.loads(contents['continuity-after.json']):
        raise RuntimeError('PR1940 historical receipt continuity differs')
    for name in ('old-quarantine-before.json', 'old-quarantine-after.json'):
        if json.loads(contents[name]) != quarantine:
            raise RuntimeError('PR1940 preserved old quarantine differs')
    return {
        'reviewed_manifest': manifest_record,
        'receipt_files': records,
        'official_verification': file_record(verify_raw),
        'accepted_package_proof': file_record(previous_proof_raw),
    }, snapshots, json.loads(contents['continuity-after.json'])


def continuity(base, cache_helpers, history):
    # The historical module keeps every original PR1935/1940 constant intact.
    # Its continuity() validates the PR1935 generation against its own source.
    old_records, old_snapshots, current, quarantine = history.continuity(base, cache_helpers)
    original_records, original_before, original_after_sync = cache_helpers.original_receipts(base)
    recorded_originals = json.loads(regular_bytes(history.OLD_RESUME / 'original-receipts.json'))
    if original_records != recorded_originals:
        raise RuntimeError('original PR1935 failure receipt metadata differs from its successful resume')
    previous, previous_snapshots, linked_old_records = validate_previous_release(
        history, cache_helpers, base, current, quarantine)
    if linked_old_records != old_records:
        raise RuntimeError('PR1940 does not link the unchanged PR1935 receipt generation')
    old_original_inventory, _ = directory_records(history.OLD_ORIGINAL, cache_helpers)
    old_resume_inventory, _ = directory_records(history.OLD_RESUME, cache_helpers)
    historical_bundle_inventory, _ = directory_records(HISTORY_BUNDLE, cache_helpers)
    for name, expected in HELPERS.items():
        if historical_bundle_inventory[name]['sha256'] != expected:
            raise RuntimeError('historical operator helper changed: ' + name)
    records = {
        'historical_1935_continuity': old_records,
        'original_1935_failure_receipts': original_records,
        'original_1935_all_files': old_original_inventory,
        'resume_1935_all_files': old_resume_inventory,
        'historical_1940_operator_bundle': historical_bundle_inventory,
        'previous_1940': previous,
    }
    snapshots = [*old_snapshots, original_before, original_after_sync, *previous_snapshots]
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


def run_under_lock(base, cache_helpers, history, guard):
    EVIDENCE.mkdir(parents=True, exist_ok=False)
    base.EVIDENCE = EVIDENCE
    try:
        base.inactive_status('status-before', guard)
        pin_source(base, cache_helpers, history, after_merge=False)
        proof_raw = regular_bytes(EXPECTED_PROOF)
        if digest(proof_raw) != PROOF_SHA256:
            raise RuntimeError('accepted PR1942 B package proof bytes differ')
        proof_expected = json.loads(proof_raw)
        if proof_expected.get('source_git_sha') != SOURCE:
            raise RuntimeError('accepted PR1942 package proof source differs')
        prior_receipts, prior_snapshots, before, prior_quarantine = continuity(base, cache_helpers, history)
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
            raise RuntimeError('actual package proof differs from accepted PR1942 B artifacts')
        identity = json.loads(regular_bytes(ROOT / 'backend/release_identity.json'))
        if identity.get('source_git_sha') != SOURCE or identity.get('release_id') != RUNTIME:
            raise RuntimeError('actual materialized runtime identity differs')
        pin_source(base, cache_helpers, history, after_merge=True)
        after = base.snapshot()
        base.save('untracked-after.json', after)
        for previous in (*prior_snapshots, before, after_sync):
            base.assert_preserved(previous, after)
        receipts_after, _, _, quarantine_after = continuity(base, cache_helpers, history)
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
            'functional_acceptance_hold': 'authenticated_incident_recovery_and_worker_completion_pending',
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
    history = load_helper('original_release_1940.py')
    base = load_helper('original_release_1935.py')
    cache_helpers = load_helper('resume_release_1935.py')
    if (history.BUNDLE != HISTORY_BUNDLE
            or history.OLD_VERIFY != HISTORY_BUNDLE / 'previous-production-verification-1935.json'):
        raise RuntimeError('historical helper receipt location differs from original PR1940')
    pin_source(base, cache_helpers, history, after_merge=False)
    if cache_helpers.entry_exists(ROOT / '.git/mezan-production-release.lock'):
        raise RuntimeError('an active or incomplete release lease owns the workspace')
    sys.path.insert(0, str(ROOT / 'scripts'))
    import production_release_guard as guard
    with guard._release_operation_lock():
        run_under_lock(base, cache_helpers, history, guard)


if __name__ == '__main__':
    main()
