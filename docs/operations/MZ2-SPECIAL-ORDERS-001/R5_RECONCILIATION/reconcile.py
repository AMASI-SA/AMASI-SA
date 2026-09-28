#!/usr/bin/env python3
"""Reproduce the reviewed R5 reconciliation in disposable CI, or verify only.

The archived R5 recovery guard remains unchanged. This tool verifies a distinct
cumulative source contract retaining the newer Production changes. It never
merges a PR, updates a ref, deploys, or opens a business database.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REPOSITORY = 'AMASI-SA/AMASI-SA'
BRANCH = 'feature/mezan-special-orders-core-20260927'
PRODUCTION = 'a7bcb1626e2ad4defba2260d4a113417f91f3120'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inputs():
    spec = importlib.util.spec_from_file_location('archived_r5_recovery', HERE.parent/'R5_RECOVERY/recover.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle, _ = module.bundle()
    manifest = json.loads((HERE/'manifest.json').read_text())
    required = {'backend/preparation_supplier_dispatch.py', 'frontend/src/lib/storeCourierLabelPrint.js', 'frontend/src/lib/storeCourierLabelPrint.test.js'}
    if set(manifest['reconciled_paths']) != required:
        raise ValueError('reconciliation_scope_mismatch')
    manifest['files'] = {n: {'archive_sha256': row['new_sha256'], 'result_sha256': row['new_sha256']} for n, row in bundle['files'].items()}
    for name, row in manifest['reconciled_paths'].items():
        manifest['files'][name] = row
    if manifest['archive_json_sha256'] != module.JSON_SHA256 or manifest['production_sha'] != PRODUCTION:
        raise ValueError('reconciliation_provenance_mismatch')
    for name, row in manifest['files'].items():
        if row['archive_sha256'] != bundle['files'][name]['new_sha256']:
            raise ValueError('archive_source_mismatch')
    return bundle, manifest


def verify():
    _, manifest = inputs()
    for name, row in manifest['files'].items():
        path = ROOT/name
        if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('source_missing_or_symlink:' + name)
        if sha(path.read_bytes()) != row['result_sha256']:
            raise ValueError('reconciled_source_differs:' + name)
    return {'status': 'R5_RECONCILED_SOURCE_VERIFIED', 'files': 33,
            'archive_unchanged': True, 'production_sha': PRODUCTION,
            'writes_performed': False, 'deployment_authorized': False}


def ci_only():
    if (os.getenv('GITHUB_ACTIONS') != 'true' or os.getenv('GITHUB_REPOSITORY') != REPOSITORY
            or os.getenv('GITHUB_REF_NAME') != BRANCH):
        raise ValueError('disposable_task_branch_ci_required')


def build():
    ci_only()
    bundle, manifest = inputs()
    if all((ROOT/n).is_file() and sha((ROOT/n).read_bytes()) == r['result_sha256'] for n, r in manifest['files'].items()):
        return verify()
    comparison = ROOT/'release-comparison'
    actual = subprocess.check_output(['git', '-C', str(comparison), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != PRODUCTION:
        raise ValueError('comparison_checkout_mismatch')
    originals = {}
    for name, row in bundle['files'].items():
        path = ROOT/name
        if path.is_symlink():
            raise ValueError('source_symlink')
        data = path.read_bytes() if path.exists() else None
        if (sha(data) if data is not None else None) != row['old_sha256']:
            raise ValueError('original_predecessor_changed:' + name)
        originals[name] = data
    for name, row in manifest['files'].items():
        if row.get('production_sha256') and sha((comparison/name).read_bytes()) != row['production_sha256']:
            raise ValueError('production_input_changed:' + name)
    subprocess.run(['git', 'apply', '--check', '-'], cwd=ROOT, input=bundle['patch'].encode(), check=True)
    subprocess.run(['git', 'apply', '-'], cwd=ROOT, input=bundle['patch'].encode(), check=True)
    for name, row in bundle['files'].items():
        if sha((ROOT/name).read_bytes()) != row['new_sha256']:
            raise ValueError('original_r5_output_changed:' + name)
    with tempfile.TemporaryDirectory(prefix='r5-reconcile-') as tmp:
        for name, row in manifest['files'].items():
            if not row.get('production_sha256'):
                continue
            path = ROOT/name
            old = Path(tmp)/'old'
            old.write_bytes(originals[name])
            if name.endswith('storeCourierLabelPrint.test.js'):
                # Both sides add tests at EOF: keep both, then update only the
                # new R5 expectation to Production's established currency format.
                production = (comparison/name).read_text()
                before = originals[name].decode()
                archived = path.read_text()
                if not production.startswith(before) or not archived.startswith(before):
                    raise ValueError('test_addition_shape_changed')
                text = archived + production[len(before):]
                expected = "expect(html).toContain('20 SAR'); expect(html).not.toContain('60 SAR');"
                if text.count(expected) != 1:
                    raise ValueError('r5_new_test_expectation_changed')
                text = text.replace(expected, "expect(html).toContain('20.00 SAR'); expect(html).not.toContain('60.00 SAR');")
                path.write_text(text)
            else:
                result = subprocess.run(['git', 'merge-file', '-p', str(path), str(old), str(comparison/name)], capture_output=True)
                if result.returncode:
                    raise ValueError('unresolved_three_way_conflict:' + name)
                path.write_bytes(result.stdout)
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    result = verify()
    result['writes_performed'] = 'isolated_CI_source_only'
    return result


def export():
    ci_only()
    result = verify()
    _, manifest = inputs()
    result['source_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    result['files'] = {}
    for name, row in manifest['files'].items():
        data = (ROOT/name).read_bytes()
        expected = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        request = urllib.request.Request('https://api.github.com/repos/'+REPOSITORY+'/git/blobs',
            data=json.dumps({'encoding':'base64','content':base64.b64encode(data).decode()}).encode(),
            headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'], 'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            stored = json.load(response)
        if stored['sha'] != expected:
            raise ValueError('exported_blob_mismatch')
        result['files'][name] = {'blob_sha': expected, 'sha256': row['result_sha256']}
    (ROOT/'r5-reconciled-blobs.json').write_text(json.dumps(result, sort_keys=True, indent=2)+'\n')
    return {'status':'SOURCE_BLOBS_ONLY_EXPORTED','count':33,'updates_refs':False,'deploys':False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['verify', 'build', 'export'], nargs='?', default='verify')
    args = parser.parse_args()
    try:
        output = {'verify':verify, 'build':build, 'export':export}[args.mode]()
        code = 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        output = {'status':'BLOCKED_RECONCILIATION', 'reason':str(exc), 'deployment_authorized':False}
        code = 2
    print(json.dumps(output, sort_keys=True))
    raise SystemExit(code)
