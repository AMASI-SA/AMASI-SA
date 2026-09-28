#!/usr/bin/env python3
"""Verify layered R6 source, or reconstruct/export it only in disposable task CI.

Historical R5 archive and reconciliation records remain byte-identical. This
versioned contract overlays every R6 change and verifies every unaffected R5
path. It never changes refs, merges, deploys, or opens a business database.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import importlib.util
import json
import lzma
import os
import subprocess
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REPO = 'AMASI-SA/AMASI-SA'
BRANCH = 'feature/mezan-special-orders-core-20260927'
BASE = '0cefc65363f79933fbc0cffdde137dc4c3e25783'
JSON_SHA = 'd51eceddbab3ca011d72d4f9393df9ac7fa717edf1fa961e1f5f24f14a7876b8'
XZ_SHA = '1f9634f93ade165a26c2db56e21d682fdc6714a715cca4e399ccafac1616ac94'
EXTRA = {'backend/preparation_file_failure_safety.py', 'backend/supplier_receiving_routes.py',
         'backend/store_delivery_payment_evidence_routes.py',
         'docs/operations/MZ2-SPECIAL-ORDERS-001/tools/rollback_gate.py',
         'frontend/src/lib/storeCourierLabelPrint.test.js',
         'frontend/src/lib/storeCourierSpecialLabelPrint.test.js'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate_json_key')
        out[key] = value
    return out


def parse(data):
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid_json_number')))


def regular(path, *, required=True):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink_not_allowed')
    if path.exists() and not path.is_file():
        raise ValueError('regular_file_required')
    if required and not path.is_file():
        raise ValueError('source_missing:' + str(path.relative_to(ROOT)))
    return path.read_bytes() if path.is_file() else None


def inputs():
    data = b''.join(regular(HERE/f'{n:02d}.xzpart') for n in range(4))
    if len(data) != 19628 or sha(data) != XZ_SHA:
        raise ValueError('r6_archive_mismatch')
    decoder = lzma.LZMADecompressor(memlimit=128*1024*1024)
    raw = decoder.decompress(data, max_length=1_000_000)
    if not decoder.eof or decoder.unused_data or len(raw) != 86537 or sha(raw) != JSON_SHA:
        raise ValueError('r6_json_mismatch')
    bundle = parse(raw)
    if bundle.get('base') != BASE or len(bundle.get('files', {})) != 22:
        raise ValueError('r6_identity_mismatch')
    for name in bundle['files']:
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or str(p) != name:
            raise ValueError('unsafe_path')
        if not (name in EXTRA or name.startswith('backend/mezan_special_orders/') and name.endswith('.py')):
            raise ValueError('outside_r6_scope:' + name)
    spec = importlib.util.spec_from_file_location('r5_contract', HERE.parent/'R5_RECONCILIATION/reconcile.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, prior = module.inputs()
    expected = {name: row['result_sha256'] for name, row in prior['files'].items()}
    for name, row in bundle['files'].items():
        if name in expected and expected[name] != row['old_sha256']:
            raise ValueError('r5_r6_chain_broken:' + name)
        expected[name] = row['new_sha256']
    return bundle, expected


def verify():
    _, expected = inputs()
    for name, fingerprint in expected.items():
        if sha(regular(ROOT/name)) != fingerprint:
            raise ValueError('cumulative_source_mismatch:' + name)
    return {'status': 'R6_CUMULATIVE_SOURCE_VERIFIED', 'verified_paths': len(expected),
            'r6_changed_paths': 22, 'historical_r5_unchanged': True,
            'writes_performed': False, 'deployment_authorized': False}


def ci_only():
    if (os.getenv('GITHUB_ACTIONS') != 'true' or os.getenv('GITHUB_REPOSITORY') != REPO
            or os.getenv('GITHUB_REF_NAME') != BRANCH):
        raise ValueError('disposable_task_branch_ci_required')


def build():
    ci_only()
    bundle, _ = inputs()
    if all(regular(ROOT/n, required=False) is not None and
           sha(regular(ROOT/n)) == r['new_sha256'] for n, r in bundle['files'].items()):
        return verify()
    for name, row in bundle['files'].items():
        data = regular(ROOT/name, required=False)
        if (sha(data) if data is not None else None) != row['old_sha256']:
            raise ValueError('predecessor_or_partial_application:' + name)
    subprocess.run(['git', 'apply', '--check', '-'], cwd=ROOT, input=bundle['patch'].encode(), check=True)
    subprocess.run(['git', 'apply', '-'], cwd=ROOT, input=bundle['patch'].encode(), check=True)
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    result = verify()
    result['writes_performed'] = 'isolated_CI_source_only'
    return result


def export():
    ci_only()
    result = verify()
    bundle, _ = inputs()
    result['source_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    result['base_sha'] = BASE
    result['json_sha256'] = JSON_SHA
    result['files'] = {}
    for name, row in bundle['files'].items():
        data = regular(ROOT/name)
        expected = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        request = urllib.request.Request('https://api.github.com/repos/' + REPO + '/git/blobs',
            data=json.dumps({'encoding':'base64','content':base64.b64encode(data).decode()}).encode(),
            headers={'Authorization':'Bearer ' + os.environ['GH_TOKEN'],
                'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28',
                'Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            stored = json.load(response)
        if stored['sha'] != expected:
            raise ValueError('stored_blob_mismatch')
        result['files'][name] = {'blob_sha': expected, 'sha256': row['new_sha256']}
    (ROOT/'r6-source-blobs.json').write_text(json.dumps(result, sort_keys=True, indent=2)+'\n')
    return {'status':'R6_SOURCE_BLOBS_ONLY_EXPORTED', 'count':22, 'updates_refs':False, 'deploys':False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=['verify','build','export'], default='verify')
    args = parser.parse_args()
    try:
        out = {'verify':verify, 'build':build, 'export':export}[args.mode]()
        code = 0
    except (ValueError, OSError, lzma.LZMAError, subprocess.SubprocessError):
        out = {'status':'BLOCKED_R6_SOURCE_CONTRACT', 'deployment_authorized':False}
        code = 2
    print(json.dumps(out, sort_keys=True))
    raise SystemExit(code)
