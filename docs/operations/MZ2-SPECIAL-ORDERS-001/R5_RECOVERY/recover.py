#!/usr/bin/env python3
"""Verify preserved R5 source; optionally CHECK applicability. Never applies it.

This is a source recovery helper, not a production rollback or database restore.
"""
import argparse
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import subprocess

BASE = 'd81876a9ed62fc12547d7cf79f6fc77772eee2ed'
ARCHIVE_SHA256 = '1f3c4bf1e20fbf319b25d0addff30c6736a456c62df8a9af90f5040826225a06'
JSON_SHA256 = '67786854114c808aa31916ff0294d8774422b601d6cea530d79df70977408529'
CHUNKS = ('f58c3ab0ae8a9b8b0cabbb38d0787027df21339a',
          '17fee15307bfe109c5566d2997ab0a473c2fb065',
          'ae11ae67ad183c599d27b70eaaa114bfc4c15f83',
          '4d468c69bf5d97857a4ad5099583aee7298902bd')
HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def bundle():
    parts = []
    for index, expected in enumerate(CHUNKS):
        path = HERE / f'{index:02d}.xzpart'
        if path.is_symlink():
            raise ValueError('symlink_chunk')
        data = path.read_bytes()
        if len(data) != 6804 or git_blob(data) != expected:
            raise ValueError('chunk_integrity_failed')
        parts.append(data)
    compressed = b''.join(parts)
    if sha(compressed) != ARCHIVE_SHA256:
        raise ValueError('archive_integrity_failed')
    decoder = lzma.LZMADecompressor(memlimit=128*1024*1024)
    raw = decoder.decompress(compressed, max_length=2_000_000)
    if not decoder.eof or decoder.unused_data or len(raw) != 121556 or sha(raw) != JSON_SHA256:
        raise ValueError('source_integrity_failed')
    payload = json.loads(raw)
    if payload.get('base') != BASE or len(payload.get('files',{})) != 33:
        raise ValueError('source_identity_failed')
    for name, meta in payload['files'].items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not re.fullmatch(r'[A-Za-z0-9_./-]+',name):
            raise ValueError('unsafe_source_path')
        if not (name.startswith('backend/') or name.startswith('frontend/src/')):
            raise ValueError('unexpected_source_scope')
        for key in ('old_sha256','new_sha256'):
            value = meta.get(key)
            if key == 'old_sha256' and value is None:
                continue
            if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value):
                raise ValueError('invalid_source_hash')
    paths = re.findall(r'^diff --git a/(\S+) b/\1$',payload['patch'],re.MULTILINE)
    if len(paths) != 33 or set(paths) != set(payload['files']):
        raise ValueError('patch_manifest_mismatch')
    return payload, raw


def check(root, payload):
    root = root.resolve(strict=True)
    before, after, conflicts = [], [], []
    for name, meta in payload['files'].items():
        path = root/name
        if any(p.is_symlink() for p in (path,*path.parents) if p != root):
            raise ValueError('symlink_source_path')
        actual = sha(path.read_bytes()) if path.exists() else None
        before.append(actual == meta['old_sha256'])
        after.append(actual == meta['new_sha256'])
        if actual not in (meta['old_sha256'],meta['new_sha256']):
            conflicts.append(name)
    if all(after):
        return {'status':'ALREADY_MATERIALIZED_DO_NOT_REAPPLY','writes_performed':False}
    if not all(before):
        return {'status':'BLOCKED_PREDECESSOR_OR_PARTIAL_APPLICATION','conflicts':conflicts,'writes_performed':False}
    result = subprocess.run(['git','apply','--check','-'],cwd=root,
                            input=payload['patch'].encode(),capture_output=True,timeout=30)
    if result.returncode:
        return {'status':'BLOCKED_GIT_APPLY_CHECK','writes_performed':False}
    return {'status':'APPLICABLE_CHECK_ONLY','files':33,'writes_performed':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',type=Path,help='Existing isolated checkout to check; never writes its files.')
    parser.add_argument('--extract',type=Path,help='Create a NEW directory containing the source JSON, diff and hash manifest only.')
    args = parser.parse_args()
    try:
        payload, raw = bundle()
        result = {'status':'ARCHIVE_VERIFIED','base_sha':BASE,'files':33,'json_sha256':sha(raw),'applies_patch':False}
        if args.check:
            result.update(check(args.check,payload))
        if args.extract:
            if result['status'].startswith('BLOCKED'):
                raise ValueError('blocked_check_cannot_extract')
            args.extract.mkdir(parents=True,exist_ok=False)
            (args.extract/'r5-source-transfer.json').write_bytes(raw)
            (args.extract/'r5.patch').write_text(payload['patch'],encoding='utf-8')
            (args.extract/'files.json').write_text(json.dumps(payload['files'],indent=2,sort_keys=True)+'\n',encoding='utf-8')
            result['exported_source_only'] = True
    except (OSError,ValueError,lzma.LZMAError,subprocess.SubprocessError):
        result={'status':'BLOCKED_INVALID_RECOVERY_INPUT','applies_patch':False}
    print(json.dumps(result,sort_keys=True))
    return 2 if result['status'].startswith('BLOCKED') else 0

if __name__ == '__main__':
    raise SystemExit(main())
