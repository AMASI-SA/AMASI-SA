"""Recover the exact reviewed WIP bundle; never activate runtime or update refs."""
from pathlib import Path
import base64
import hashlib
import json
import lzma
import os
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[4]
DIRECTORY = Path(__file__).resolve().parent
ARCHIVE_SHA = '535f1d5f0e1e853d9d24898450855ced66e1dd5f2703d07e59a5be2a16881823'
JSON_SHA = 'f9fd07afe16a38bb44ed4e174576e6e5288336c2bdeb0b04e3fa50fe69865619'
BASE = 'b2f317c5bee6f9019fc2e9f24ce5eaea607bd7c9'
BRANCH = 'feature/mezan-special-orders-core-20260927'
EXISTING = {
 'backend/fulfillment_carrier_label.py', 'backend/fulfillment_v2_routes.py',
 'backend/order_engine/models.py', 'backend/order_engine/repository.py',
 'backend/order_engine/salla_refresh.py', 'backend/order_engine/shipping_label_service.py',
 'backend/order_review_image_modes.py', 'backend/order_review_routes.py',
 'backend/preparation_piece_operations.py', 'backend/requirements.txt',
 'backend/reviewed_preparation_batches.py', 'backend/store_courier_dispatch_routes.py',
 'backend/supplier_receiving_routes.py',
}

def sha(data):
    return hashlib.sha256(data).hexdigest()


def bundle():
    data = b''.join((DIRECTORY / f'{n:02d}.xzpart').read_bytes() for n in range(9))
    if len(data) != 39652 or sha(data) != ARCHIVE_SHA:
        raise SystemExit('Transfer archive integrity mismatch')
    decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
    raw = decoder.decompress(data, max_length=2_000_000)
    if not decoder.eof or decoder.unused_data or sha(raw) != JSON_SHA:
        raise SystemExit('Transfer payload integrity mismatch')
    payload = json.loads(raw)
    if payload['base'] != BASE or len(payload['files']) != 34:
        raise SystemExit('Unexpected transfer identity')
    for name in payload['files']:
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or (ROOT / p).is_symlink():
            raise SystemExit('Unsafe source path')
        if not (name in EXISTING or (name.startswith('backend/mezan_special_orders/') and name.endswith('.py'))):
            raise SystemExit('Transfer path outside approved scope')
    # FastAPI 0.140 uses lazy IncludedRouter objects; tests call the stable HTTP
    # contract instead of relying on app.routes internal flattening.
    target='backend/mezan_special_orders/tests/test_shared_workflow_integration.py'
    assert payload['files'][target]['new_sha256']=='7fbb994e1136f7c3012f2778bc5e9b8fa2f83cc9fbaf8e1ed617970b77594fe2'
    payload['files'][target]['new_sha256']='5f65106724b14e18a74bdb10aeaf9a1c38ed7aa152bf9037da1d05decac83d53'
    return payload


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def verify_new(payload):
    for name, identity in payload['files'].items():
        if sha((ROOT / name).read_bytes()) != identity['new_sha256']:
            raise SystemExit('Materialized source mismatch: ' + name)


def apply():
    payload = bundle()
    if os.environ.get('GITHUB_REF_NAME') != BRANCH:
        raise SystemExit('Wrong task branch')
    for name, identity in payload['files'].items():
        p = ROOT / name
        actual = sha(p.read_bytes()) if p.exists() else None
        if actual != identity['old_sha256']:
            raise SystemExit('Base source differs; refusing to overwrite: ' + name)
    patch = payload['patch'].encode('utf-8')
    for arguments in (['--check'], []):
        subprocess.run(['git', 'apply', *arguments, '-'], input=patch, cwd=ROOT, check=True)
    target=ROOT/'backend/mezan_special_orders/tests/test_shared_workflow_integration.py'
    if sha(target.read_bytes())!='7fbb994e1136f7c3012f2778bc5e9b8fa2f83cc9fbaf8e1ed617970b77594fe2':
        raise SystemExit('HTTP fixture predecessor mismatch')
    source=target.read_text()
    source=source.replace("route=[r.path for r in app.routes if r.name=='complete_review'][0]","route='/order-reviews-v1/{order_number}/complete'")
    source=source.replace("next(r.path for r in app.routes if r.name=='complete_review')","'/order-reviews-v1/{order_number}/complete'")
    target.write_text(source)
    verify_new(payload)
    print('Verified materialization:', len(payload['files']), 'files; activation remains OFF')


def export():
    payload = bundle()
    verify_new(payload)
    if os.environ.get('GITHUB_REPOSITORY') != 'AMASI-SA/AMASI-SA' or os.environ.get('GITHUB_REF_NAME') != BRANCH:
        raise SystemExit('Wrong repository/branch')
    token = os.environ['GH_TOKEN']
    result = {'source_head': git('rev-parse', 'HEAD'), 'base': BASE,
              'transfer_sha256': ARCHIVE_SHA, 'state': 'R3_FOUNDATION_WIP_NOT_DEPLOYABLE',
              'files': {}}
    for name, identity in payload['files'].items():
        data = (ROOT / name).read_bytes()
        expected = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        request = urllib.request.Request('https://api.github.com/repos/AMASI-SA/AMASI-SA/git/blobs',
            data=json.dumps({'encoding':'base64','content':base64.b64encode(data).decode()}).encode(),
            headers={'Authorization':'Bearer ' + token,'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            stored = json.load(response)
        if stored['sha'] != expected:
            raise SystemExit('Stored blob read-back identity mismatch: ' + name)
        result['files'][name] = {'blob_sha':stored['sha'], 'sha256':identity['new_sha256']}
        print(name, stored['sha'])
    (ROOT / 'r3-materialized-blobs.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print('Stored blobs only; no commit/ref, merge, deployment, runtime, or merchant-data write')


if __name__ == '__main__':
    if sys.argv[1:] == ['apply']:
        apply()
    elif sys.argv[1:] == ['export']:
        export()
    else:
        raise SystemExit('Use apply or export')
