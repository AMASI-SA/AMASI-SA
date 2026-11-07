"""Local synthetic Docker boundary. Never consult ambient Docker contexts."""
import json
import os
from pathlib import Path
import stat
import subprocess

SERVICES = ('web', 'reports', 'sync', 'accounting', 'mongo')

def run(args, *, env=None, timeout=90, input=None):
    result = subprocess.run(args, input=input, capture_output=True, env=env, timeout=timeout)
    if result.returncode:
        # Provider diagnostics can contain credentials. Do not echo argv or stderr.
        raise RuntimeError('operation failed (exit %s)' % result.returncode)
    return result.stdout

def private_file(path):
    p = Path(path)
    info = p.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise ValueError('private regular file owned by operator required')
    return p

def docker(*args, **kwargs):
    return run(['docker', '-H', 'unix:///var/run/docker.sock', *args], **kwargs)

def containers(config):
    ids = docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + config['project']).decode().split()
    if not ids:
        raise ValueError('no local synthetic project found')
    rows = json.loads(docker('inspect', *ids))
    found = {}
    for row in rows:
        labels = row['Config'].get('Labels') or {}
        service = labels.get('com.docker.compose.service')
        if service == 'migration':
            continue
        if service not in SERVICES or service in found:
            raise ValueError('unexpected or duplicate project service')
        expected = ('mezan-vps-mongo:' if service == 'mongo' else 'mezan-vps:') + config['source_sha']
        if row['Config']['Image'] != expected:
            raise ValueError('source image mismatch')
        if service != 'mongo':
            env = dict(x.split('=', 1) for x in row['Config']['Env'] if '=' in x)
            if any(env.get(k) != v for k, v in {'APP_ENV': 'staging', 'MEZAN_SYNTHETIC_HOSTING': '1', 'DB_NAME': 'mezan_exit2d_staging'}.items()):
                raise ValueError('synthetic boundary missing')
        found[service] = row
    if set(found) != set(SERVICES):
        raise ValueError('five expected services required')
    return found

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.pending')
    descriptor = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(data, handle, sort_keys=True)
    os.replace(temp, path)
