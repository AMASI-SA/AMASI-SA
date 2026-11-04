"""Separate synthetic hosting contract. Never supplies a Production V5 identity."""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def records(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()
            and '__pycache__' not in p.parts and p.suffix != '.pyc'
            and p.name != 'independent-staging-manifest.json'}


def write_manifest(source_sha):
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise ValueError('staging requires exact source commit')
    (ROOT / 'independent-staging-manifest.json').write_text(json.dumps({
        'kind': 'synthetic-hosting-only-v1', 'source_sha': source_sha,
        'files': records(ROOT)}, sort_keys=True), encoding='utf-8')


def validate(environment=None):
    env = os.environ if environment is None else environment
    if env.get('APP_ENV') != 'staging' or env.get('MEZAN_SYNTHETIC_HOSTING') != '1':
        raise RuntimeError('explicit synthetic hosting required')
    if env.get('DB_NAME') != 'mezan_exit2d_staging':
        raise RuntimeError('synthetic database required')
    url = urlsplit(env.get('MONGO_URL', ''))
    host = env.get('MEZAN_STAGING_MONGO_HOST', '')
    if (url.scheme != 'mongodb' or not host or url.hostname != host
            or url.port != 27017 or not url.username or not url.password
            or url.path not in {'', '/', '/mezan_exit2d_staging'}
            or url.query or url.fragment or ',' in url.netloc):
        raise RuntimeError('isolated authenticated staging Mongo required')
    forbidden = [k for k, v in env.items() if v and any(part in k.upper()
        for part in ('TOKEN_ENC_KEY', 'API_KEY', 'CLIENT_SECRET', 'SMTP_PASSWORD'))]
    if forbidden:
        raise RuntimeError('provider credentials forbidden in synthetic hosting')
    manifest = json.loads((ROOT / 'independent-staging-manifest.json').read_text('utf-8'))
    if (manifest.get('kind') != 'synthetic-hosting-only-v1'
            or not re.fullmatch(r'[0-9a-f]{40}', manifest.get('source_sha', ''))
            or manifest.get('files') != records(ROOT)):
        raise RuntimeError('staging source integrity failed')
    return 'staging:' + manifest['source_sha']
