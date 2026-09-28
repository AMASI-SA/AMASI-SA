"""Materialize the exact scoped R4 patch in disposable CI; never deploy/update refs."""
from pathlib import Path
import base64
import hashlib
import json
import lzma
import os
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
BASE='e0ce8e46e63541866ac250b25b0ce2a290a64256'
BRANCH='feature/mezan-special-orders-core-20260927'
ARCHIVE_SHA='ca9799c7a1c771923bf49f32cb2e3c41129dc06f24ea414e09a0c0dbe970117c'
JSON_SHA='62ac1371383e7ff6982182a75a485b4b828d0975ef523e64e0b6187033c65f28'
EXISTING={'backend/reviewed_preparation_batches.py','backend/store_delivery_domain.py',
 'backend/store_delivery_driver_app_routes.py','backend/store_delivery_handover_routes.py',
 'backend/store_delivery_payment_evidence_routes.py','backend/store_delivery_payment_review_routes.py',
 'backend/store_delivery_payment_resubmission_routes.py'}

def sha(data): return hashlib.sha256(data).hexdigest()

def load():
    compressed=b''.join((HERE/f'{n:02d}.xzpart').read_bytes() for n in range(5))
    if len(compressed)!=19124 or sha(compressed)!=ARCHIVE_SHA:
        raise SystemExit('R4 archive mismatch')
    decoder=lzma.LZMADecompressor(memlimit=128*1024*1024)
    raw=decoder.decompress(compressed,max_length=2000000)
    if not decoder.eof or decoder.unused_data or sha(raw)!=JSON_SHA:
        raise SystemExit('R4 source payload mismatch')
    payload=json.loads(raw)
    if payload['base']!=BASE or len(payload['files'])!=15:
        raise SystemExit('R4 identity mismatch')
    for name in payload['files']:
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or (ROOT/p).is_symlink():
            raise SystemExit('Unsafe R4 path')
        if not (name in EXISTING or name.startswith('backend/mezan_special_orders/') and name.endswith('.py')):
            raise SystemExit('R4 path outside approved source scope')
    return payload

def verify(payload):
    for name,meta in payload['files'].items():
        if sha((ROOT/name).read_bytes())!=meta['new_sha256']:
            raise SystemExit('R4 materialized source mismatch: '+name)

def apply():
    if os.getenv('GITHUB_REF_NAME')!=BRANCH: raise SystemExit('Wrong task branch')
    payload=load()
    for name,meta in payload['files'].items():
        path=ROOT/name
        old=sha(path.read_bytes()) if path.exists() else None
        if old!=meta['old_sha256']:
            raise SystemExit('R4 predecessor differs; do not overwrite: '+name)
    for args in (['--check'],[]):
        subprocess.run(['git','apply',*args,'-'],cwd=ROOT,input=payload['patch'].encode(),check=True)
    verify(payload)
    print('R4 source materialized: 15 exact files; all live activation remains OFF')

def export():
    payload=load();verify(payload)
    if os.getenv('GITHUB_REPOSITORY')!='AMASI-SA/AMASI-SA' or os.getenv('GITHUB_REF_NAME')!=BRANCH:
        raise SystemExit('Wrong repository/task branch')
    token=os.environ['GH_TOKEN']
    result={'source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'base':BASE,'transfer_sha256':ARCHIVE_SHA,'status':'R4_NATIVE_CHECKPOINT_NOT_RELEASE_ACCEPTANCE','files':{}}
    for name,meta in payload['files'].items():
        data=(ROOT/name).read_bytes()
        expected=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        req=urllib.request.Request('https://api.github.com/repos/AMASI-SA/AMASI-SA/git/blobs',
            data=json.dumps({'encoding':'base64','content':base64.b64encode(data).decode()}).encode(),
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=30) as response: stored=json.load(response)
        if stored['sha']!=expected: raise SystemExit('R4 stored blob identity mismatch')
        result['files'][name]={'blob_sha':stored['sha'],'sha256':meta['new_sha256']}
        print(name,stored['sha'])
    (ROOT/'r4-native-blobs.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print('Only source blob objects stored; no commit/ref/deploy or merchant-data operations')

if __name__=='__main__':
    if sys.argv[1:]==['apply']: apply()
    elif sys.argv[1:]==['export']: export()
    else: raise SystemExit('Use apply or export')
