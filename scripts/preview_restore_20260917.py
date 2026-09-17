"""Recover missing Preview files without changing shared source or Supervisor.

Input bundle is built from the reviewed task checkpoint; contains no secrets.
Private runtime files can be ephemeral: retain this script and bundle in Git.
"""
import argparse
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tarfile

BASE = '836d36b831c1cd8aa9915f543f159b4e160ea438'
FRONTEND_BASE = '9f8b16998f62cbdb08dc9361bc2e443223d27d17'
PATCH = 'f5c125a941e657a1b312e2abb7b8d32cdc06aa59'
ROOT = Path('/opt/mezan-preview-runtime-20260916')
FRONT = Path('/opt/mezan-preview-signout-20260916')
ORIGIN = 'https://salla-analytics.preview.emergentagent.com'
CONF = Path('/etc/supervisor/conf.d/supervisord.conf')


def output(args):
    return subprocess.check_output(args, text=True).strip()


def preflight():
    assert socket.gethostname() == 'agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3'
    state = json.loads(output(['python', '-B', '/app/scripts/production_release_guard.py', 'status']))
    assert state.get('active') is False, 'Release lease active'
    assert output(['git', '-C', '/app', 'rev-parse', 'HEAD']) == BASE
    assert not output(['git', '-C', '/app', 'status', '--short'])
    c = configparser.RawConfigParser(); c.read(CONF)
    assert c.get('program:frontend', 'directory') == str(FRONT / 'frontend')
    assert c.get('program:frontend', 'command') == '/usr/local/bin/python ' + str(FRONT / 'start.py')
    assert c.get('program:backend', 'directory') == '/app/backend'
    assert c.get('program:backend', 'command') == '/root/.venv/bin/python -B ' + str(ROOT / 'preview_password_runtime.py') + ' serve'
    assert Path('/app/backend/release_identity.json').is_file()
    return CONF.read_bytes()


def prepare(bundle_path):
    before = preflight()
    assert not ROOT.exists() and not FRONT.exists(), 'Do not overwrite an existing runtime'
    bundle = json.loads(Path(bundle_path).read_text())
    assert set(bundle) == {'adapter', 'navigation', 'fixtures'}
    archive = subprocess.check_output(['git', '-C', '/app', 'archive', FRONTEND_BASE, 'frontend'])
    ROOT.mkdir(mode=0o700); FRONT.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for m in tar:
            p = Path(m.name)
            assert not p.is_absolute() and '..' not in p.parts and p.parts[0] == 'frontend'
            if any(s == '.env' or s.startswith('.env.') for s in p.parts):
                continue
            target = FRONT / p
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(m).read()); target.chmod(m.mode & 0o777)
            else:
                raise RuntimeError('Archive links forbidden')
    for rel, key in [('src/components/MezanV2NavigationShellLegacy.jsx', 'navigation'), ('src/demo/orderPreviewFixtures.js', 'fixtures')]:
        (FRONT / 'frontend' / rel).write_text(bundle[key])
    (ROOT / 'preview_password_runtime.py').write_text(bundle['adapter'])
    subprocess.run(['cp', '-a', '--reflink=auto', '/app/frontend/node_modules', str(FRONT / 'frontend/node_modules')], check=True)
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'NODE_ENV': 'production', 'REACT_APP_BACKEND_URL': ORIGIN}
    node = output(['which', 'node'])
    with (FRONT / 'build.log').open('w') as log:
        subprocess.run([node, 'node_modules/vite/bin/vite.js', 'build', '--mode', 'preview'], cwd=FRONT / 'frontend', env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    index = FRONT / 'frontend/build/index.html'
    assert index.is_file() and index.stat().st_size
    assert not (FRONT / 'frontend/build/build-meta.json').exists()
    meta = {'environment': 'preview', 'api_origin': ORIGIN, 'source_git_sha': FRONTEND_BASE,
            'source_patch_git_sha': PATCH, 'backend_base_git_sha': BASE,
            'index_sha256': hashlib.sha256(index.read_bytes()).hexdigest(), 'contains_production_release_identity': False}
    (FRONT / 'frontend/build/preview-meta.json').write_text(json.dumps(meta, indent=2) + '\n')
    (ROOT / 'frontend/build').mkdir(parents=True)
    (ROOT / 'frontend/build/preview-meta.json').write_text(json.dumps(meta) + '\n')
    launcher = 'import hashlib,json,os\nfrom pathlib import Path\nroot=Path(__file__).resolve().parent\nm=json.loads((root/"frontend/build/preview-meta.json").read_text())\nassert m["environment"]=="preview" and m["api_origin"]==ORIGIN\nassert hashlib.sha256((root/"frontend/build/index.html").read_bytes()).hexdigest()==m["index_sha256"]\nos.chdir(root/"frontend")\nos.execve(NODE,[NODE,"node_modules/vite/bin/vite.js","preview","--host","0.0.0.0","--port","3000","--strictPort","--mode","preview"],ENV)\n'
    (FRONT / 'start.py').write_text('ORIGIN=' + repr(ORIGIN) + '\nNODE=' + repr(node) + '\nENV=' + repr(env) + '\n' + launcher)
    # This is an internal signing key, never a user password or fabricated session.
    fd = os.open(ROOT / 'preview-session-signing.secret', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(secrets.token_urlsafe(64))
    subprocess.run(['/root/.venv/bin/python', '-B', '-c', 'import sys;sys.path.insert(0,"' + str(ROOT) + '");import preview_password_runtime as p;p.verify_boundary();print("Preview boundary PASS")'], check=True)
    assert CONF.read_bytes() == before
    preflight()
    (ROOT / 'recovery-ready.json').write_text(json.dumps({'config_sha256': hashlib.sha256(before).hexdigest(), 'patch': PATCH}))
    print('Prepared Preview recovery; services unchanged; fresh login required after activation.')


def activate():
    before = preflight()
    ready = json.loads((ROOT / 'recovery-ready.json').read_text())
    assert ready['config_sha256'] == hashlib.sha256(before).hexdigest()
    subprocess.run(['supervisorctl', 'start', 'frontend', 'backend'], check=True)
    assert CONF.read_bytes() == before
    print('Preview services started; readiness and normal login must be verified.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['prepare', 'activate']); p.add_argument('--bundle')
    a = p.parse_args()
    prepare(a.bundle) if a.action == 'prepare' else activate()
