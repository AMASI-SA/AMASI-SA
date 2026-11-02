"""One-shot, hash-locked source checkpoint on the isolated integration branch.

No runtime/imports/database/deployment commands. Never force-push. The workflow
is removed by the native connector after read-back; payload is removed here.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess

BASE = '86606a698648a4b4a3dfa4382e5cf3c466e94e76'
BRANCH = 'integration/operational-balances-unified-20261102'
PREFIX = 'docs/operations/OP-UNIFIED-BALANCES-20261102/source-checkpoint'
WORKFLOW = '.github/workflows/operational-remaining-source-checkpoint.yml'
ALLOW = frozenset({
    'backend/operational_balance_engine.py', 'backend/operational_balance_sources.py',
    'backend/operational_bundle_cost_projection.py', 'backend/operational_cost_completeness.py',
    'backend/operational_financial_position_core.py', 'backend/operational_open_cost_exposure.py',
    'backend/operational_product_cost_policy.py', 'backend/operational_remaining_bundle_sources.py',
    'backend/tests/test_operational_balance_cost_completeness.py',
    'backend/tests/test_operational_balance_remaining_products.py',
    'backend/tests/test_operational_balance_sources.py',
})
PATCH_HASH = '35d229c3675871dfe7a5a5d8815c2c9fdb274393626dd0894464a96b7da1f5e8'


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def regular(root, relative):
    path = root / relative
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root.parent):
        raise ValueError('symlink refused: ' + relative)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('not a regular source file: ' + relative)
    return path


def blob(value):
    return hashlib.sha1(('blob ' + str(len(value)) + '\0').encode() + value).hexdigest()


def apply_checked(root):
    directory = root / PREFIX
    manifest = json.loads(regular(root, PREFIX + '/manifest.json').read_text())
    if manifest['base_commit'] != BASE or set(manifest['files']) != ALLOW:
        raise ValueError('source/allowlist mismatch')
    patch = regular(root, PREFIX + '/changes.patch')
    if hashlib.sha256(patch.read_bytes()).hexdigest() != PATCH_HASH or manifest['patch_sha256'] != PATCH_HASH:
        raise ValueError('patch digest mismatch')
    patch_names = {line.split('\t', 2)[2] for line in git(root, 'apply', '--numstat', str(patch)).splitlines()}
    expected_existing = {name for name, entry in manifest['files'].items() if entry['before']}
    if patch_names != expected_existing:
        raise ValueError('patch writes outside the exact existing-file set')
    for name, entry in manifest['files'].items():
        if entry['before']:
            actual = regular(root, name).read_bytes()
            if blob(actual) != entry['before'] or git(root, 'rev-parse', 'HEAD:' + name) != entry['before']:
                raise ValueError('source advanced or before bytes changed: ' + name)
        else:
            if (root / name).exists() or git(root, 'ls-files', '--', name):
                raise ValueError('new source already exists: ' + name)
            incoming = regular(root, PREFIX + '/files/' + name).read_bytes()
            if blob(incoming) != entry['after'] or hashlib.sha256(incoming).hexdigest() != entry['sha256']:
                raise ValueError('new source digest mismatch: ' + name)
    subprocess.run(['git', 'apply', '--check', str(patch)], cwd=root, check=True)
    subprocess.run(['git', 'apply', '--index', str(patch)], cwd=root, check=True)
    for name, entry in manifest['files'].items():
        if not entry['before']:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(directory / 'files' / name, path)
        content = regular(root, name).read_bytes()
        if blob(content) != entry['after'] or hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise ValueError('result is not the locally tested source: ' + name)
    subprocess.run(['git', 'add', '--', *sorted(ALLOW)], cwd=root, check=True)
    subprocess.run(['git', 'rm', '-r', '--', PREFIX], cwd=root, check=True, stdout=subprocess.DEVNULL)
    changes = set(git(root, 'diff', '--cached', '--name-only').splitlines())
    if not ALLOW.issubset(changes) or any(name not in ALLOW and not name.startswith(PREFIX + '/') for name in changes):
        raise ValueError('staged checkpoint changes outside allowlist')
    subprocess.run(['git', 'diff', '--cached', '--check'], cwd=root, check=True)
    return manifest


def main():
    if (os.environ.get('GITHUB_REPOSITORY') != 'AMASI-SA/AMASI-SA'
            or os.environ.get('GITHUB_EVENT_NAME') != 'push'
            or os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH):
        raise SystemExit('Wrong repository/event/ref')
    root = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    head = os.environ['GITHUB_SHA']
    if not re.fullmatch('[0-9a-f]{40}', head) or git(root, 'rev-parse', 'HEAD') != head:
        raise SystemExit('Checkout identity mismatch')
    if git(root, 'status', '--porcelain'):
        raise SystemExit('Non-clean checkout')
    subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, head], cwd=root, check=True)
    source_diff = git(root, 'diff', '--name-only', BASE, head).splitlines()
    if any(p != WORKFLOW and not p.startswith(PREFIX + '/') for p in source_diff):
        raise SystemExit('Checkpoint base advanced outside transport-only changes')
    remote = git(root, 'ls-remote', 'origin', 'refs/heads/' + BRANCH).split()
    if len(remote) != 2 or remote[0] != head:
        raise SystemExit('Remote advanced; preserving newer source')
    apply_checked(root)
    subprocess.run(['git', 'config', 'user.name', 'mezan'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m',
        'WIP: wire operational remaining bundle costs, SAR and scoped evidence\n\n'
        'Apply the exact locally tested source with 1499 passing backend cases, '
        'including 51 real isolated Mongo remaining-product cases and 18 scoped '
        'completeness cases. Dashboard source and release identity unchanged. '
        'No financial recognition, production or lease operation. Native purchase '
        'financial matching and all other business gates remain.'], cwd=root, check=True)
    # Normal fast-forward only. A concurrent write causes rejection, never overwrite.
    subprocess.run(['git', 'push', 'origin', 'HEAD:refs/heads/' + BRANCH], cwd=root, check=True)
    print('SOURCE_CHECKPOINT=' + git(root, 'rev-parse', 'HEAD'))
    print('SOURCE_TREE=' + git(root, 'rev-parse', 'HEAD^{tree}'))


if __name__ == '__main__':
    main()
