#!/usr/bin/env python3
"""Read-only /app untracked-file recheck against this release's baseline.

Writes evidence only in the supplied private rehearsal directory. Run after
prepare, prepublish or verify. Does not run the release guard, alter /app,
restore files, inspect editor buffers, publish, or change a lease.
Keep this file beside the reviewed pr1921_original_rehearsal.py copy.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def exact_fields(value, expected, message):
    if not isinstance(value, dict) or any(
            type(value.get(k)) is not type(v) or value.get(k) != v
            for k, v in expected.items()):
        raise RuntimeError(message)


def exclusive_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', required=True, type=Path)
    parser.add_argument('--label', required=True, choices=(
        'after-prepare', 'after-prepublish', 'after-verify'))
    args = parser.parse_args()

    source = Path(__file__).with_name('pr1921_original_rehearsal.py')
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('pr1921_rehearsal_preservation', source)
    rehearsal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rehearsal)  # Definitions only; __main__ does not run.
    rehearsal.validate_reviewed_configuration()  # Unfilled templates fail before /app.

    evidence = args.evidence_dir
    if (evidence.parent != Path('/tmp') or not evidence.name.startswith('mezan-pr1921-original-')
            or evidence.is_symlink() or not evidence.is_dir() or evidence.resolve() != evidence):
        raise SystemExit('Expected PR1921 evidence directory is unavailable or unsafe')
    info = evidence.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise SystemExit('Expected private operator-owned evidence directory')
    outcome = json.loads((evidence / 'outcome.json').read_bytes())
    exact_fields(outcome, {
        'phase': 'build', 'ready': True, 'source_sha': rehearsal.SOURCE,
        'source_base_sha': rehearsal.BASE, 'deployment_sha': rehearsal.REVIEWED_M,
        'intent_commit': rehearsal.REVIEWED_B, 'tree': rehearsal.REVIEWED_B_TREE,
        'release_id': rehearsal.RELEASE, 'intent_sha256': rehearsal.INTENT_SHA256,
        'untracked_count': rehearsal.EXPECTED_UNTRACKED_COUNT,
        'evidence_dir': str(evidence), 'lease_created': False},
        'Matching successful rehearsal required')

    baseline = json.loads((evidence / '04-before.manifest.json').read_bytes())
    if not isinstance(baseline, dict) or len(baseline) != rehearsal.EXPECTED_UNTRACKED_COUNT:
        raise SystemExit('Reviewed original untracked baseline unavailable')
    old_prefix = None
    if rehearsal.APPEND_ONLY in baseline:
        old_prefix = (evidence / '04-before.stripped-env.bin').read_bytes()
        exact_fields(baseline[rehearsal.APPEND_ONLY], {
            'kind': 'file', 'size': len(old_prefix),
            'sha256': hashlib.sha256(old_prefix).hexdigest()},
            'Original append-only sidecar evidence differs from baseline')

    label = 'preservation-' + args.label
    if list(evidence.glob(label + '*')):
        raise SystemExit('Preservation evidence already exists; inspect before any repeat')
    os.umask(0o077)
    exclusive_json(evidence / (label + '.started.json'), {
        'label': args.label, 'started_at': rehearsal.utc(),
        'source_sha': rehearsal.SOURCE, 'deployment_sha': rehearsal.REVIEWED_M})
    rehearsal.EVIDENCE = evidence
    rehearsal.CHILD_ENV = os.environ.copy()
    rehearsal.CHILD_ENV['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        # This path invokes only `git ls-files` and reads the resulting files.
        rehearsal.preserved(label, baseline, old_prefix, allow_append=True)
        result = json.loads((evidence / (label + '.preservation.json')).read_bytes())
        exact_fields(result, {
            'before_count': rehearsal.EXPECTED_UNTRACKED_COUNT,
            'after_count': rehearsal.EXPECTED_UNTRACKED_COUNT,
            'changed_paths': [], 'preserved': True}, 'Untracked preservation failed')
        summary = {'phase': 'preserved', 'label': args.label, 'preserved': True,
                   'untracked_count': rehearsal.EXPECTED_UNTRACKED_COUNT,
                   'source_sha': rehearsal.SOURCE, 'deployment_sha': rehearsal.REVIEWED_M,
                   'evidence_dir': str(evidence), 'finished_at': rehearsal.utc()}
        exclusive_json(evidence / (label + '.summary.json'), summary)
        print(json.dumps(summary, ensure_ascii=True), flush=True)
    except BaseException as exc:
        failure = {'phase': 'stopped', 'label': args.label, 'preserved': False,
                   'error_type': type(exc).__name__, 'evidence_dir': str(evidence),
                   'finished_at': rehearsal.utc()}
        exclusive_json(evidence / (label + '.wrapper-failure.json'), failure)
        print(json.dumps(failure, ensure_ascii=True), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
