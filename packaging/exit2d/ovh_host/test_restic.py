"""Actual local encrypted repository/check/restore; no external account or bucket."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from backup import verified_upload
from common import run

with tempfile.TemporaryDirectory(prefix='mezan-restic-test-') as directory:
    root = Path(directory)
    archive = root/'synthetic.archive.gz'; archive.write_bytes(secrets.token_bytes(2*1024*1024))
    manifest = root/'manifest.json'; manifest.write_text(json.dumps({'classification':'synthetic-only'}))
    password = root/'password'; password.write_text(secrets.token_urlsafe(32)); password.chmod(0o600)
    repository = root/'repository'
    env = {k:v for k,v in os.environ.items() if not k.startswith(('AWS_', 'RESTIC_'))}
    env.update(RESTIC_REPOSITORY=str(repository), RESTIC_PASSWORD_FILE=str(password))
    run(['restic','init'], env=env)
    verified_upload(archive, manifest, env)
    wrong = root/'wrong'; wrong.write_text(secrets.token_urlsafe(32)); wrong.chmod(0o600)
    try: run(['restic','snapshots'], env=dict(env, RESTIC_PASSWORD_FILE=str(wrong)))
    except RuntimeError: pass
    else: raise ValueError('wrong repository encryption password accepted')
    damaged = root/'damaged'; shutil.copytree(repository, damaged)
    pack = next((damaged/'data').rglob('*'+'[0-9a-f]'*64))
    content = bytearray(pack.read_bytes()); content[0] ^= 1; pack.write_bytes(content)
    try: run(['restic','check','--read-data'], env=dict(env, RESTIC_REPOSITORY=str(damaged)))
    except RuntimeError: pass
    else: raise ValueError('tampered encrypted pack accepted')
    print(json.dumps({'kind':'actual_local_restic_repository', 'archive_bytes':archive.stat().st_size,
                      'restore_hash_equal':True,'wrong_key_refused':True,'tamper_refused':True,
                      'external_s3_tested':False,'paid_resource_created':False}))
